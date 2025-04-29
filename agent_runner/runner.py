import os
import asyncio
import logging
import json
import argparse
import signal
import sys
# --- ИЗМЕНЕНИЕ: Добавляем импорты SQLAlchemy ---
from typing import Dict, Optional, Any, List
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker # Добавляем импорты
# --- КОНЕЦ ИЗМЕНЕНИЯ ---
from langchain_core.messages import BaseMessage, HumanMessage, AIMessage # Ensure AIMessage is imported
from datetime import datetime, timezone
from dotenv import load_dotenv
import requests
import redis.asyncio as redis
import time

# Import from sibling modules
from .graph_factory import create_agent_app # Импортируем фабрику графа
# from .models import AgentState # AgentState используется внутри graph_factory
# --- ИЗМЕНЕНИЕ: Импорты для работы с БД ---
from .db import get_db_session_factory, close_db_engine # Импортируем фабрику и функцию закрытия
# Импортируем CRUD из agent_manager (предполагаем, что он доступен в PYTHONPATH)
# Возможно, потребуется настроить PYTHONPATH или изменить структуру импорта
try:
    from agent_manager import crud
    # --- ИЗМЕНЕНИЕ: Добавляем SenderType ---
    from agent_manager.models import ChatMessageDB, SenderType # Импортируем модель и Enum
    # --- КОНЕЦ ИЗМЕНЕНИЯ ---
except ImportError:
    crud = None
    ChatMessageDB = None
    # --- ИЗМЕНЕНИЕ: Добавляем SenderType ---
    SenderType = None # Устанавливаем в None при ошибке импорта
    # --- КОНЕЦ ИЗМЕНЕНИЯ ---
    logging.warning("Could not import 'crud', 'ChatMessageDB', or 'SenderType' from 'agent_manager'. Database history loading will be disabled.")
# --- КОНЕЦ ИЗМЕНЕНИЯ ---


# --- Configuration & Setup ---
REDIS_HISTORY_QUEUE_NAME = "chat_history_queue" # Имя очереди по умолчанию
AGENT_HISTORY_LIMIT = 20 # Значение по умолчанию

def load_environment():
    """Loads environment variables from .env file."""
    global REDIS_HISTORY_QUEUE_NAME, AGENT_HISTORY_LIMIT
    dotenv_path = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '.env'))
    if os.path.exists(dotenv_path):
        load_dotenv(dotenv_path=dotenv_path)
        print(f"Agent Runner: Loaded environment variables from {dotenv_path}")
        REDIS_HISTORY_QUEUE_NAME = os.getenv("REDIS_HISTORY_QUEUE_NAME", REDIS_HISTORY_QUEUE_NAME)
        try:
            AGENT_HISTORY_LIMIT = int(os.getenv("AGENT_HISTORY_LIMIT", str(AGENT_HISTORY_LIMIT)))
        except ValueError:
            print(f"Agent Runner: Warning! Invalid AGENT_HISTORY_LIMIT value in .env. Using default: {AGENT_HISTORY_LIMIT}")
        print(f"Agent Runner: Using Redis history queue: {REDIS_HISTORY_QUEUE_NAME}")
        print(f"Agent Runner: History limit for context recovery: {AGENT_HISTORY_LIMIT}")
        return True
    else:
        print(f"Agent Runner: Warning! .env file not found at {dotenv_path}")
        print(f"Agent Runner: Using default Redis history queue: {REDIS_HISTORY_QUEUE_NAME}")
        print(f"Agent Runner: Using default history limit: {AGENT_HISTORY_LIMIT}")
        return False

# --- Custom Logging Filter ---
class AgentIdFilter(logging.Filter):
    """Adds a default agent_id if it's missing from the log record."""
    def __init__(self, default_agent_id="system"):
        super().__init__()
        self.default_agent_id = default_agent_id

    def filter(self, record):
        if not hasattr(record, 'agent_id'):
            record.agent_id = self.default_agent_id
        return True

def setup_logging(agent_id: str):
    """Configures logging for the agent runner."""
    root_logger = logging.getLogger()
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(levelname)s - %(agent_id)s - %(message)s",
        handlers=[
            logging.StreamHandler(sys.stdout)
        ],
    )

    log_filter = AgentIdFilter(default_agent_id='-')
    for handler in root_logger.handlers:
         if not any(isinstance(f, AgentIdFilter) for f in handler.filters):
              handler.addFilter(log_filter)

    logger = logging.getLogger(__name__)
    adapter = logging.LoggerAdapter(logger, {'agent_id': agent_id})
    return adapter

# Global flags for graceful shutdown and restart
running = True
needs_restart = False

# --- Signal Handler ---
def shutdown_handler(signum, frame):
    """Sets the global running flag to False on SIGINT or SIGTERM."""
    global running, needs_restart
    logger = logging.getLogger(__name__)
    if running:
        print("\nShutdown signal received. Attempting graceful shutdown...")
        logger.info("Shutdown signal received. Attempting graceful shutdown...")
        running = False
        needs_restart = False
    else:
        print("Multiple shutdown signals received. Forcing exit.")
        logger.warning("Multiple shutdown signals received. Forcing exit.")
        os._exit(1)

# --- Control Channel Listener ---
async def control_listener(agent_id: str, redis_client: redis.Redis):
    """Listens to the agent's control channel for commands like shutdown or restart."""
    global running, needs_restart
    control_channel = f"agent_control:{agent_id}"
    log_adapter = logging.LoggerAdapter(logging.getLogger(__name__), {'agent_id': agent_id})
    pubsub = None
    log_adapter.info("Control listener task started.")

    while running and not needs_restart:
        try:
            if pubsub is None:
                pubsub = redis_client.pubsub()
                await pubsub.subscribe(control_channel)
                log_adapter.info(f"Subscribed to control channel: {control_channel}")

            log_adapter.debug("Control listener waiting for message...")
            message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)

            if message and message.get("type") == "message":
                log_adapter.debug(f"Control listener received raw message: {message}")
                try:
                    data = json.loads(message['data'])
                    command = data.get("command")
                    log_adapter.info(f"Received command on control channel: {command}")
                    if command == "shutdown":
                        log_adapter.info("Shutdown command received via Redis. Initiating graceful shutdown.")
                        running = False
                        log_adapter.debug("Setting running = False and breaking control loop.")
                        break
                    elif command == "restart":
                        log_adapter.info("Restart command received via Redis. Initiating internal restart.")
                        needs_restart = True
                        log_adapter.debug("Setting needs_restart = True and breaking control loop.")
                        break
                except json.JSONDecodeError:
                    log_adapter.error(f"Received invalid JSON on control channel: {message['data'][:200]}...")
                except Exception as e:
                    log_adapter.error(f"Error processing control command: {e}", exc_info=True)

        except redis.exceptions.ConnectionError as e:
            log_adapter.error(f"Redis connection error in control listener: {e}. Retrying...")
            if pubsub:
                try:
                    await pubsub.unsubscribe(control_channel)
                    await pubsub.aclose()
                except Exception: pass
                pubsub = None
            await asyncio.sleep(5)
        except asyncio.CancelledError:
            log_adapter.info("Control listener task cancelled.")
            break
        except Exception as e:
            log_adapter.error(f"Unexpected error in control listener: {e}", exc_info=True)
            await asyncio.sleep(5)
    if pubsub:
        try:
            await pubsub.unsubscribe(control_channel)
            await pubsub.aclose()
            log_adapter.info("Unsubscribed from control channel.")
        except Exception as clean_e:
            log_adapter.error(f"Error closing control channel pubsub: {clean_e}")

    if needs_restart:
        log_adapter.info("Control listener finished due to restart request.")
    elif not running:
        log_adapter.info("Control listener finished due to shutdown request.")
    else:
         log_adapter.info("Control listener finished.")


# --- Agent Logic ---

async def fetch_config(config_url: str, log_adapter: logging.LoggerAdapter) -> Dict:
    """Fetches agent configuration from the management service."""
    log_adapter.info(f"Fetching configuration from: {config_url}")
    try:
        response = requests.get(config_url, timeout=15)
        response.raise_for_status()
        config_data = response.json()
        return config_data
    except requests.exceptions.Timeout:
        log_adapter.error(f"Timeout fetching configuration from {config_url}.")
        raise
    except requests.exceptions.RequestException as e:
        log_adapter.error(f"Failed to fetch configuration: {e}")
        raise

async def update_redis_status(redis_client: redis.Redis, status_key: str, status: str, pid: Optional[int], error_detail: Optional[str], log_adapter: logging.LoggerAdapter):
    """Updates the agent status hash in Redis."""
    mapping = {"status": status}
    if pid is not None:
        mapping["pid"] = pid
    else:
        if status in ["stopped", "restarting"] or status.startswith("error_"):
             await redis_client.hdel(status_key, "pid")

    if error_detail:
        mapping["error_detail"] = error_detail
    else:
        if not status.startswith("error_"):
             await redis_client.hdel(status_key, "error_detail")

    if status == "running":
         mapping["last_active"] = time.time()

    try:
        await redis_client.hset(status_key, mapping=mapping)
        log_adapter.info(f"Status updated to: {status} (PID: {pid if pid is not None else 'None'})")
    except Exception as e:
        log_adapter.error(f"Failed to update Redis status to {status}: {e}")

# --- ИЗМЕНЕНИЕ: Функция преобразования истории БД ---
def convert_db_history_to_langchain(db_messages: List[Any]) -> List[BaseMessage]: # Используем Any для db_messages, т.к. ChatMessageDB может быть None
    """Converts messages from DB format (ChatMessageDB) to LangChain BaseMessage list."""
    converted = []
    # --- ИЗМЕНЕНИЕ: Проверяем импорт ChatMessageDB и SenderType ---
    if not ChatMessageDB or not SenderType:
        logging.error("ChatMessageDB model or SenderType Enum not available for history conversion.")
        return []
    # --- КОНЕЦ ИЗМЕНЕНИЯ ---

    for msg in db_messages:
        # Проверяем, что msg - это ожидаемый объект (если ChatMessageDB импортирован)
        if not isinstance(msg, ChatMessageDB):
             logging.warning(f"Skipping message conversion due to unexpected type: {type(msg)}")
             continue

        # Используем атрибуты модели ChatMessageDB и Enum SenderType
        if msg.sender_type == SenderType.USER:
            converted.append(HumanMessage(content=msg.content))
        elif msg.sender_type == SenderType.AGENT:
            converted.append(AIMessage(content=msg.content))
        else:
            logging.warning(f"Skipping message conversion due to unhandled sender_type: {msg.sender_type}")
    return converted
# --- КОНЕЦ ИЗМЕНЕНИЯ ---


async def redis_listener(
    app,
    agent_id: str,
    redis_client: redis.Redis,
    static_state_config: Dict[str, Any],
    # --- ИЗМЕНЕНИЕ: Используем импортированные типы ---
    db_session_factory: Optional[async_sessionmaker[AsyncSession]]
    # --- КОНЕЦ ИЗМЕНЕНИЯ ---
):
    """Listens to Redis input channel, processes messages, publishes output, and queues history."""
    log_adapter = logging.LoggerAdapter(logging.getLogger(__name__), {'agent_id': agent_id})
    input_channel = f"agent:{agent_id}:input"
    output_channel = f"agent:{agent_id}:output"
    status_key = f"agent_status:{agent_id}"
    history_queue = REDIS_HISTORY_QUEUE_NAME
    loaded_threads_key = f"agent_loaded_threads:{agent_id}"

    pubsub = None

    # --- ИЗМЕНЕНИЕ: Проверяем импорт SenderType тоже ---
    can_load_history = crud is not None and db_session_factory is not None and ChatMessageDB is not None and SenderType is not None
    # --- КОНЕЦ ИЗМЕНЕНИЯ ---
    if not can_load_history:
        log_adapter.warning("Database history loading is disabled (CRUD, DB session factory, ChatMessageDB, or SenderType not available).")


    async def update_status(status: str, error_detail: Optional[str] = None):
        """Helper to update Redis status using the outer function."""
        pid_to_set = os.getpid() if running else None
        await update_redis_status(redis_client, status_key, status, pid_to_set, error_detail, log_adapter)

    async def queue_message_for_history(sender_type: str, thread_id: str, content: str, channel: Optional[str]):
        """Helper function to push message details to the history queue."""
        try:
            history_payload = {
                "agent_id": agent_id,
                "thread_id": thread_id,
                "sender_type": sender_type,
                "content": content,
                "channel": channel,
                "timestamp": datetime.now(timezone.utc).isoformat()
            }
            await redis_client.lpush(history_queue, json.dumps(history_payload))
            log_adapter.debug(f"Queued {sender_type} message for history (Thread: {thread_id})")
        except redis.RedisError as e:
            log_adapter.error(f"Failed to queue message for history (Thread: {thread_id}): {e}")
        except Exception as e:
            log_adapter.error(f"Unexpected error queuing message for history (Thread: {thread_id}): {e}", exc_info=True)


    try:
        pubsub = redis_client.pubsub()
        await pubsub.subscribe(input_channel)
        log_adapter.info(f"Subscribed to Redis channel: {input_channel}")

        global running, needs_restart
        last_active_update_time = 0
        update_interval = 30

        while running and not needs_restart:
            try:
                current_time = time.time()
                if current_time - last_active_update_time > update_interval:
                    await redis_client.hset(status_key, "last_active", current_time)
                    last_active_update_time = current_time

                message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=0.5)
                if not running or needs_restart:
                     break

                if message and message.get("type") == "message":
                    raw_data = message['data']
                    try:
                        data = json.loads(raw_data)
                        log_adapter.info(f"Received message from Redis: {data}")
                    except json.JSONDecodeError:
                        log_adapter.error(f"Received invalid JSON from Redis: {raw_data[:200]}...")
                        continue

                    await redis_client.hset(status_key, "last_active", time.time())
                    last_active_update_time = time.time()

                    thread_id = "unknown"
                    try:
                        user_message_content = data.get("message")
                        thread_id = data.get("thread_id")
                        user_data = data.get("user_data", {})
                        channel = data.get("channel", "unknown")
                        platform_user_id = data.get("platform_user_id") # <--- Извлекаем platform_user_id

                        if not user_message_content or not thread_id:
                             log_adapter.error("Missing 'message' or 'thread_id' in Redis payload.")
                             continue

                        await queue_message_for_history(
                            sender_type="user",
                            thread_id=thread_id,
                            content=user_message_content,
                            channel=channel
                        )

                        loaded_messages: List[BaseMessage] = []
                        if can_load_history:
                            try:
                                is_loaded = await redis_client.sismember(loaded_threads_key, thread_id)
                                if not is_loaded:
                                    log_adapter.info(f"Thread '{thread_id}' not found in cache '{loaded_threads_key}'. Loading history from DB.")
                                    async with db_session_factory() as session:
                                        history_from_db = await crud.db_get_recent_chat_history(
                                            db=session,
                                            agent_id=agent_id,
                                            thread_id=thread_id,
                                            limit=AGENT_HISTORY_LIMIT
                                        )
                                        # --- ИЗМЕНЕНИЕ: Передаем список объектов ChatMessageDB ---
                                        loaded_messages = convert_db_history_to_langchain(history_from_db)
                                        # --- КОНЕЦ ИЗМЕНЕНИЯ ---

                                    log_adapter.info(f"Loaded {len(loaded_messages)} messages from DB for thread '{thread_id}'.")

                                    await redis_client.sadd(loaded_threads_key, thread_id)
                                    log_adapter.info(f"Added thread '{thread_id}' to cache '{loaded_threads_key}'.")
                                else:
                                    log_adapter.info(f"Thread '{thread_id}' found in cache '{loaded_threads_key}'. Skipping DB load.")

                            except redis.RedisError as redis_err:
                                log_adapter.error(f"Redis error checking/adding thread cache for '{thread_id}': {redis_err}. Proceeding without history.")
                                loaded_messages = []
                            except Exception as db_err:
                                log_adapter.error(f"Database error loading history for thread '{thread_id}': {db_err}. Proceeding without history.", exc_info=True)
                                loaded_messages = []
                        else:
                             if not await redis_client.sismember(loaded_threads_key, thread_id):
                                 log_adapter.warning(f"Cannot load history for thread '{thread_id}' because DB/CRUD/Models are unavailable.") # Обновляем сообщение
                                 await redis_client.sadd(loaded_threads_key, thread_id)

                        graph_input = {
                            "messages": loaded_messages + [HumanMessage(content=user_message_content)],
                            "user_data": user_data,
                            "channel": channel,
                            "original_question": user_message_content,
                            "question": user_message_content,
                            "rewrite_count": 0,
                            "documents": [],
                            **static_state_config
                        }
                        config = {"configurable": {"thread_id": str(thread_id), "agent_id": agent_id}}

                        log_adapter.info(f"Invoking graph for thread_id: {thread_id} (Initial history messages: {len(loaded_messages)})")
                        final_response_content = "No response generated."
                        final_message_object = None

                        async for output in app.astream(graph_input, config, stream_mode="updates"):
                            if not running or needs_restart:
                                log_adapter.warning("Shutdown or restart requested during graph stream.")
                                break

                            for key, value in output.items():
                                log_adapter.debug(f"Graph node '{key}' output: {value}")
                                if key == "agent" or key == "generate":
                                    if "messages" in value and value["messages"]:
                                        last_msg = value["messages"][-1]
                                        if isinstance(last_msg, AIMessage):
                                             final_response_content = last_msg.content
                                             final_message_object = last_msg

                        if not running or needs_restart: break

                        log_adapter.info(f"Graph execution finished. Final response: {final_response_content[:100]}...")

                        await queue_message_for_history(
                            sender_type="agent",
                            thread_id=thread_id,
                            content=final_response_content,
                            channel=channel
                        )

                        response_payload_dict = {
                            "thread_id": thread_id,
                            "platform_user_id": platform_user_id, # <--- Добавляем platform_user_id
                            "response": final_response_content,
                            "message_object": None,
                            "channel": channel
                        }
                        if final_message_object:
                            try:
                                if hasattr(final_message_object, 'model_dump'):
                                    response_payload_dict["message_object"] = final_message_object.model_dump()
                                elif hasattr(final_message_object, 'dict'):
                                     response_payload_dict["message_object"] = final_message_object.dict()
                            except Exception as serial_err:
                                 log_adapter.error(f"Error serializing final message object: {serial_err}")

                        response_payload = json.dumps(response_payload_dict)
                        await redis_client.publish(output_channel, response_payload)
                        log_adapter.info(f"Published response to Redis channel: {output_channel}")

                    except asyncio.CancelledError:
                         log_adapter.info("Graph invocation cancelled.")
                         if running and not needs_restart: raise
                    except Exception as e:
                        log_adapter.error(f"Error processing message: {e}", exc_info=True)
                        error_payload = json.dumps({"thread_id": thread_id, "error": f"Agent error: {e}"})
                        await redis_client.publish(output_channel, error_payload)

            except asyncio.CancelledError:
                log_adapter.info("Redis listener task cancelled.")
                break
            except redis.exceptions.ConnectionError as e:
                 log_adapter.error(f"Redis connection error: {e}. Attempting to reconnect...")
                 await update_status("error_redis", f"Connection failed: {e}")
                 await asyncio.sleep(5)
                 if pubsub:
                     try:
                         if running and not needs_restart:
                             await pubsub.unsubscribe(input_channel)
                             await pubsub.subscribe(input_channel)
                             log_adapter.info("Resubscribed after connection error.")
                             await update_status("running")
                     except Exception as resub_e:
                         log_adapter.error(f"Failed to resubscribe after connection error: {resub_e}")
                         await asyncio.sleep(10)
            except Exception as e:
                log_adapter.error(f"Error in Redis listener loop: {e}", exc_info=True)
                await update_status("error_listener_unexpected", f"Listener loop error: {e}")
                await asyncio.sleep(1)

    except Exception as setup_e:
         log_adapter.error(f"Failed during Redis listener setup: {setup_e}", exc_info=True)
         await update_status("error_listener_setup", f"Listener setup failed: {setup_e}")
    finally:
        log_adapter.info("Cleaning up Redis listener...")
        if pubsub:
            try:
                await pubsub.unsubscribe(input_channel)
                await pubsub.aclose()
                log_adapter.info("Unsubscribed from Redis and closed pubsub connection.")
            except Exception as clean_e:
                 log_adapter.error(f"Error during Redis cleanup: {clean_e}")
        if needs_restart:
             log_adapter.info("Redis listener finished due to restart request.")
        elif not running:
             log_adapter.info("Redis listener finished due to shutdown request.")
             await update_status("stopped")
        else:
             log_adapter.warning("Redis listener finished unexpectedly (error).")


# --- Main Execution Loop ---
async def main_loop(
    agent_id: str,
    config_url: str,
    redis_url: str,
    # --- ИЗМЕНЕНИЕ: Используем импортированные типы ---
    db_session_factory: Optional[async_sessionmaker[AsyncSession]]
    # --- КОНЕЦ ИЗМЕНЕНИЯ ---
):
    """Contains the main logic that can be restarted internally."""
    global running, needs_restart
    log_adapter = setup_logging(agent_id)
    status_key = f"agent_status:{agent_id}"
    loaded_threads_key = f"agent_loaded_threads:{agent_id}"
    redis_client = None
    app = None
    static_state_config = {}
    listener_task = None
    control_task = None
    log_adapter.info("Entering main_loop.")

    try:
        await update_status_external(redis_url, status_key, "initializing", log_adapter)

        log_adapter.info(f"Connecting to Redis at {redis_url}")
        try:
            redis_client = await redis.from_url(redis_url, decode_responses=True)
            await redis_client.ping()
            log_adapter.info("Redis connection successful.")
            try:
                deleted_count = await redis_client.delete(loaded_threads_key)
                log_adapter.info(f"Cleared loaded threads cache '{loaded_threads_key}' (deleted: {deleted_count}) on start/restart.")
            except redis.RedisError as cache_clear_err:
                log_adapter.error(f"Failed to clear loaded threads cache '{loaded_threads_key}': {cache_clear_err}")
        except Exception as e:
            log_adapter.error(f"Failed to connect to Redis: {e}", exc_info=True)
            await update_status_external(redis_url, status_key, "error_redis", log_adapter, f"Connection failed: {e}")
            return

        log_adapter.info(f"Fetching agent configuration from {config_url}")
        try:
            agent_config = await fetch_config(config_url, log_adapter)
            log_adapter.info("Agent configuration fetched successfully.")
        except Exception as e:
            log_adapter.error(f"Failed to fetch agent configuration: {e}", exc_info=True)
            await update_redis_status(redis_client, status_key, "error_config", os.getpid(), f"Fetch failed: {e}", log_adapter)
            return

        try:
            app, static_state_config = create_agent_app(agent_config, agent_id, redis_client)
            if not app: raise ValueError("create_agent_app returned None for app")
            log_adapter.info("Agent application created successfully.")
        except Exception as e:
            log_adapter.error(f"Failed to create agent application: {e}", exc_info=True)
            await update_redis_status(redis_client, status_key, "error_app_create", os.getpid(), f"App creation failed: {e}", log_adapter)
            return

        await update_redis_status(redis_client, status_key, "running", os.getpid(), None, log_adapter)
        listener_task = asyncio.create_task(
            redis_listener(app, agent_id, redis_client, static_state_config, db_session_factory),
            name="RedisListener"
        )
        control_task = asyncio.create_task(control_listener(agent_id, redis_client), name="ControlListener")
        log_adapter.info("Listener tasks created.")

        log_adapter.info("Waiting for listener tasks to complete...")
        done, pending = await asyncio.wait(
            [listener_task, control_task],
            return_when=asyncio.FIRST_COMPLETED
        )
        log_adapter.info(f"Asyncio.wait finished. Done tasks: {[t.get_name() for t in done]}. Pending tasks: {[t.get_name() for t in pending]}.")

        for task in done:
            task_name = task.get_name()
            log_adapter.info(f"Processing completed task: {task_name}")
            try:
                task.result()
                log_adapter.info(f"Task {task_name} completed without raising an exception.")
                if running and not needs_restart:
                     log_adapter.error(f"Task {task_name} finished unexpectedly while running=True and needs_restart=False.")
                     running = False
            except asyncio.CancelledError:
                 log_adapter.info(f"Task {task_name} was cancelled.")
            except Exception as e:
                 log_adapter.error(f"Task {task_name} failed with error: {e}", exc_info=True)
                 running = False
                 needs_restart = False
                 try:
                     if redis_client:
                         current_status_info = await redis_client.hgetall(status_key)
                         current_status = current_status_info.get("status", "unknown")
                         if not current_status.startswith("error_"):
                             await update_redis_status(redis_client, status_key, "error_unexpected", os.getpid(), f"Task {task_name} failed: {e}", log_adapter)
                     else:
                         log_adapter.warning("Cannot update status after task error: Redis client is not available.")
                 except Exception as status_update_err:
                      log_adapter.error(f"Failed to update status after task error: {status_update_err}")

        if pending:
             log_adapter.info(f"Cancelling {len(pending)} pending tasks...")
             for task in pending:
                 log_adapter.info(f"Cancelling pending task {task.get_name()}...")
                 task.cancel()
             results = await asyncio.gather(*pending, return_exceptions=True)
             log_adapter.info(f"Pending tasks cancellation results: {results}")
        else:
             log_adapter.info("No pending tasks to cancel.")

    except Exception as e:
        log_adapter.error(f"An unexpected error occurred in main_loop: {e}", exc_info=True)
        running = False
        needs_restart = False
        if redis_client:
             try:
                 current_status_info = await redis_client.hgetall(status_key)
                 current_status = current_status_info.get("status", "unknown")
                 if not current_status.startswith("error_"):
                     await update_redis_status(redis_client, status_key, "error_unexpected", os.getpid(), f"Main loop error: {e}", log_adapter)
             except Exception as status_err:
                  log_adapter.error(f"Failed to update error status: {status_err}")
    finally:
        log_adapter.info("Cleaning up resources for current run...")
        if redis_client:
            await redis_client.aclose()
            log_adapter.info("Redis client closed for current run.")

        log_adapter.info(f"Exiting main_loop. Final flags: running={running}, needs_restart={needs_restart}")
        if needs_restart:
            log_adapter.info("Preparing for internal restart...")
            await update_status_external(redis_url, status_key, "restarting", log_adapter)
            await asyncio.sleep(1)
        elif not running:
            log_adapter.info("Preparing for shutdown...")
            await update_status_external(redis_url, status_key, "stopped", log_adapter)
        else:
             log_adapter.warning("main_loop finished unexpectedly without shutdown or restart signal.")

async def update_status_external(redis_url: str, status_key: str, status: str, log_adapter: logging.LoggerAdapter, error_detail: Optional[str] = None):
    """Helper to update Redis status using a temporary connection (used during init/shutdown/restart)."""
    temp_redis = None
    try:
        temp_redis = await redis.from_url(redis_url, decode_responses=True)
        await temp_redis.ping()
        pid_to_set = os.getpid() if status not in ["stopped", "restarting"] and not status.startswith("error_") else None
        await update_redis_status(temp_redis, status_key, status, pid_to_set, error_detail, log_adapter)
    except Exception as e:
        log_adapter.error(f"Failed to update external Redis status to {status}: {e}")
    finally:
        if temp_redis:
            await temp_redis.aclose()


# --- Main Entry Point ---
if __name__ == "__main__":
    load_environment()

    db_session_factory = get_db_session_factory()
    if not db_session_factory:
        print("Warning: Database session factory could not be created. History loading will be disabled.")

    parser = argparse.ArgumentParser(description="Run a configurable agent.")
    parser.add_argument("--agent-id", type=str, required=True, help="Unique ID for this agent instance.")
    parser.add_argument("--config-url", type=str, required=True, help="URL to fetch the agent's JSON configuration.")
    parser.add_argument("--redis-url", type=str, required=True, help="URL for the Redis server.")
    args = parser.parse_args()

    root_logger = logging.getLogger()
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(levelname)s - %(agent_id)s - %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)],
    )
    log_filter = AgentIdFilter(default_agent_id='-')
    for handler in root_logger.handlers:
         if not any(isinstance(f, AgentIdFilter) for f in handler.filters):
              handler.addFilter(log_filter)

    signal.signal(signal.SIGINT, shutdown_handler)
    signal.signal(signal.SIGTERM, shutdown_handler)

    print(f"Starting agent runner process for ID: {args.agent_id} (PID: {os.getpid()})")
    main_logger = logging.getLogger(__name__)

    async def run_agent_process():
        global running, needs_restart
        loop_count = 0
        loop_log_adapter = logging.LoggerAdapter(main_logger, {'agent_id': args.agent_id})
        while running:
            loop_count += 1
            loop_log_adapter.info(f"--- Starting main process loop iteration {loop_count} ---")
            needs_restart = False
            loop_log_adapter.info(f"Before main_loop: running={running}, needs_restart={needs_restart}")
            await main_loop(args.agent_id, args.config_url, args.redis_url, db_session_factory)
            loop_log_adapter.info(f"After main_loop: running={running}, needs_restart={needs_restart}")
            if not needs_restart:
                 loop_log_adapter.info("needs_restart is False, breaking main process loop.")
                 break
            else:
                 loop_log_adapter.info("needs_restart is True, continuing main process loop for restart.")
        loop_log_adapter.info("--- Exited main process loop ---")
        await close_db_engine()

    try:
        asyncio.run(run_agent_process())
    except KeyboardInterrupt:
         print("KeyboardInterrupt caught in main, exiting.")
         asyncio.run(close_db_engine())
    except Exception as e:
         print(f"Unhandled exception in asyncio.run: {e}")
         asyncio.run(update_status_external(args.redis_url, f"agent_status:{args.agent_id}", "error_unexpected", logging.getLogger(__name__), f"Unhandled exit: {e}"))
         asyncio.run(close_db_engine())
         sys.exit(1)

    print(f"Agent runner {args.agent_id} has shut down.")
    sys.exit(0)

