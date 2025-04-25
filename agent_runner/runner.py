import os
import asyncio
import logging
import json
import argparse
import signal
import sys
from typing import Dict, Optional, Any
from dotenv import load_dotenv
import requests
import redis.asyncio as redis
import time

# Import from sibling modules
from .graph_factory import create_agent_app # Импортируем фабрику графа
# from .models import AgentState # AgentState используется внутри graph_factory
from langchain_core.messages import HumanMessage, AIMessage # Ensure AIMessage is imported

# --- Configuration & Setup ---
def load_environment():
    """Loads environment variables from .env file."""
    # Assume .env is in the parent directory (project root)
    dotenv_path = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '.env'))
    if os.path.exists(dotenv_path):
        load_dotenv(dotenv_path=dotenv_path)
        print(f"Agent Runner: Loaded environment variables from {dotenv_path}")
        return True
    else:
        print(f"Agent Runner: Warning! .env file not found at {dotenv_path}")
        return False

# --- Custom Logging Filter ---
class AgentIdFilter(logging.Filter):
    """Adds a default agent_id if it's missing from the log record."""
    def __init__(self, default_agent_id="system"):
        super().__init__()
        self.default_agent_id = default_agent_id

    def filter(self, record):
        if not hasattr(record, 'agent_id'): # <--- Формат требует agent_id
            record.agent_id = self.default_agent_id
        return True

def setup_logging(agent_id: str):
    """Configures logging for the agent runner."""
    # Remove existing handlers if any to avoid duplicate logs on reconfigure
    # Important: Get root logger *before* basicConfig if modifying handlers later
    root_logger = logging.getLogger()
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(levelname)s - %(agent_id)s - %(message)s",
        handlers=[
            logging.StreamHandler(sys.stdout) # Log to stdout
            # Add FileHandler if needed: logging.FileHandler(f"agent_{agent_id}.log")
        ],
        # Force=True might be needed if basicConfig was called before elsewhere
        # force=True
    )

    # Add the filter to all handlers configured by basicConfig
    # This ensures logs from external libraries get the default agent_id
    log_filter = AgentIdFilter(default_agent_id='-') # Use '-' or 'system' as default
    for handler in root_logger.handlers:
         # Check if the filter is already added to prevent duplicates if setup_logging is called multiple times
         if not any(isinstance(f, AgentIdFilter) for f in handler.filters):
              handler.addFilter(log_filter)


    # Return a logger adapter with the specific agent_id context for runner's own logs
    # Use logging.getLogger(__name__) to get the logger for the current module
    logger = logging.getLogger(__name__)
    # Set the specific agent_id for logs originating from this runner instance via the adapter
    adapter = logging.LoggerAdapter(logger, {'agent_id': agent_id})
    return adapter

# Global flags for graceful shutdown and restart
running = True
needs_restart = False # Новый флаг для внутреннего перезапуска

# --- Signal Handler ---
def shutdown_handler(signum, frame):
    """Sets the global running flag to False on SIGINT or SIGTERM."""
    global running, needs_restart
    logger = logging.getLogger(__name__) # Use base logger here
    if running:
        print("\nShutdown signal received. Attempting graceful shutdown...")
        logger.info("Shutdown signal received. Attempting graceful shutdown...")
        running = False
        needs_restart = False # Предотвращаем перезапуск при внешнем сигнале завершения
    else:
        print("Multiple shutdown signals received. Forcing exit.")
        logger.warning("Multiple shutdown signals received. Forcing exit.")
        os._exit(1) # Force exit if already shutting down

# --- Control Channel Listener ---
async def control_listener(agent_id: str, redis_client: redis.Redis):
    """Listens to the agent's control channel for commands like shutdown or restart."""
    global running, needs_restart # Добавляем needs_restart
    control_channel = f"agent_control:{agent_id}"
    log_adapter = logging.LoggerAdapter(logging.getLogger(__name__), {'agent_id': agent_id})
    pubsub = None
    log_adapter.info("Control listener task started.") # Добавим лог старта

    while running and not needs_restart: # Добавляем проверку needs_restart
        try:
            if pubsub is None:
                pubsub = redis_client.pubsub()
                await pubsub.subscribe(control_channel)
                log_adapter.info(f"Subscribed to control channel: {control_channel}")

            # Добавим лог перед ожиданием сообщения
            log_adapter.debug("Control listener waiting for message...")
            message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)

            if message and message.get("type") == "message":
                # Добавим лог получения сообщения
                log_adapter.debug(f"Control listener received raw message: {message}")
                try:
                    data = json.loads(message['data'])
                    command = data.get("command")
                    log_adapter.info(f"Received command on control channel: {command}") # Этот лог уже есть
                    if command == "shutdown":
                        log_adapter.info("Shutdown command received via Redis. Initiating graceful shutdown.")
                        running = False # Устанавливаем флаг для завершения основного цикла
                        log_adapter.debug("Setting running = False and breaking control loop.") # Лог перед break
                        break # Выходим из цикла прослушивания команд
                    elif command == "restart":
                        log_adapter.info("Restart command received via Redis. Initiating internal restart.")
                        needs_restart = True # Устанавливаем флаг для перезапуска
                        log_adapter.debug("Setting needs_restart = True and breaking control loop.") # Лог перед break
                        break # Выходим из цикла прослушивания команд
                except json.JSONDecodeError:
                    log_adapter.error(f"Received invalid JSON on control channel: {message['data'][:200]}...")
                except Exception as e:
                    log_adapter.error(f"Error processing control command: {e}", exc_info=True)

            # Убрали sleep(0.1), т.к. get_message с таймаутом уже дает паузу

        except redis.exceptions.ConnectionError as e:
            log_adapter.error(f"Redis connection error in control listener: {e}. Retrying...")
            if pubsub:
                try:
                    await pubsub.unsubscribe(control_channel)
                    await pubsub.aclose()
                except Exception: pass # Игнорируем ошибки при закрытии старого pubsub
                pubsub = None
            await asyncio.sleep(5) # Пауза перед повторной попыткой
        except asyncio.CancelledError:
            log_adapter.info("Control listener task cancelled.")
            break # Выходим из цикла при отмене
        except Exception as e:
            log_adapter.error(f"Unexpected error in control listener: {e}", exc_info=True)
            await asyncio.sleep(5) # Пауза перед продолжением
    # Cleanup
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
        # TODO: Add authentication headers if manager requires it
        response = requests.get(config_url, timeout=15)
        response.raise_for_status()
        config_data = response.json()
        # log_adapter.info(f"Configuration fetched successfully. Raw data received: {json.dumps(config_data)}")
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
        # Ensure PID is removed if status is stopped/error/restarting
        if status in ["stopped", "restarting"] or status.startswith("error_"):
             await redis_client.hdel(status_key, "pid")

    if error_detail:
        mapping["error_detail"] = error_detail
    else:
        # Remove old error detail if status is now ok
        if not status.startswith("error_"):
             await redis_client.hdel(status_key, "error_detail")

    # Update last active time only when setting to running or during normal operation
    if status == "running":
         mapping["last_active"] = time.time()

    try:
        await redis_client.hset(status_key, mapping=mapping)
        log_adapter.info(f"Status updated to: {status} (PID: {pid if pid is not None else 'None'})")
    except Exception as e:
        log_adapter.error(f"Failed to update Redis status to {status}: {e}")


async def redis_listener(app, agent_id: str, redis_client: redis.Redis, static_state_config: Dict[str, Any]):
    """Listens to Redis input channel, processes messages, and publishes output."""
    log_adapter = logging.LoggerAdapter(logging.getLogger(__name__), {'agent_id': agent_id}) # Use __name__ for logger
    input_channel = f"agent:{agent_id}:input"
    output_channel = f"agent:{agent_id}:output"
    status_key = f"agent_status:{agent_id}"

    pubsub = None # Initialize pubsub to None

    async def update_status(status: str, error_detail: Optional[str] = None):
        """Helper to update Redis status using the outer function."""
        # Используем глобальный running флаг для определения, нужно ли обновлять PID
        # При перезапуске PID не меняется, но статус может быть restarting
        pid_to_set = os.getpid() if running else None
        await update_redis_status(redis_client, status_key, status, pid_to_set, error_detail, log_adapter)


    try:
        pubsub = redis_client.pubsub()
        await pubsub.subscribe(input_channel)
        log_adapter.info(f"Subscribed to Redis channel: {input_channel}")
        # Не устанавливаем running сразу, ждем завершения инициализации в main_loop
        # await update_status("running") # Set status to running after successful setup

        global running, needs_restart # Добавляем needs_restart
        last_active_update_time = 0
        update_interval = 30 # Update last_active every 30 seconds

        while running and not needs_restart: # Проверяем флаги running и needs_restart
            try:
                # Update last active time periodically
                current_time = time.time()
                if current_time - last_active_update_time > update_interval:
                    await redis_client.hset(status_key, "last_active", current_time)
                    last_active_update_time = current_time

                # Listen for messages with timeout
                message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=0.5)
                if not running or needs_restart: # Проверяем флаги после get_message
                     break

                if message and message.get("type") == "message":
                    # ... (обработка входящих сообщений как и раньше) ...
                    raw_data = message['data']
                    # Decode JSON first
                    try:
                        data = json.loads(raw_data)
                        log_adapter.info(f"Received message from Redis: {data}")
                    except json.JSONDecodeError:
                        log_adapter.error(f"Received invalid JSON from Redis: {raw_data[:200]}...")
                        continue # Skip processing invalid message

                    # Update last active time immediately on message
                    await redis_client.hset(status_key, "last_active", time.time())
                    last_active_update_time = time.time()

                    thread_id = "unknown" # Default thread_id
                    try:
                        user_message = data.get("message")
                        thread_id = data.get("thread_id") # Update thread_id if available
                        user_data = data.get("user_data", {})
                        channel = data.get("channel", "unknown")

                        if not user_message or not thread_id:
                             log_adapter.error("Missing 'message' or 'thread_id' in Redis payload.")
                             continue

                        # Prepare initial state for the graph
                        graph_input = {
                            "messages": [HumanMessage(content=user_message)],
                            "user_data": user_data,
                            "channel": channel,
                            "original_question": user_message,
                            "question": user_message,
                            "rewrite_count": 0,
                            "documents": [],
                            **static_state_config # Add static config here
                        }
                        config = {"configurable": {"thread_id": str(thread_id), "agent_id": agent_id}}

                        log_adapter.info(f"Invoking graph for thread_id: {thread_id}")
                        final_response_content = "No response generated."
                        final_message_object = None

                        async for output in app.astream(graph_input, config, stream_mode="updates"):
                            if not running or needs_restart: # Check flags during stream
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

                        if not running or needs_restart: break # Exit main loop if flags set during stream

                        log_adapter.info(f"Graph execution finished. Final response: {final_response_content[:100]}...")

                        # Serialize response payload
                        response_payload_dict = {
                            "thread_id": thread_id,
                            "response": final_response_content,
                            "message_object": None
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
                         if running and not needs_restart: raise # Re-raise only if not due to shutdown/restart
                    except Exception as e:
                        log_adapter.error(f"Error processing message: {e}", exc_info=True)
                        error_payload = json.dumps({"thread_id": thread_id, "error": f"Agent error: {e}"})
                        await redis_client.publish(output_channel, error_payload)

            except asyncio.CancelledError:
                log_adapter.info("Redis listener task cancelled.")
                break # Выходим из цикла
            except redis.exceptions.ConnectionError as e:
                 log_adapter.error(f"Redis connection error: {e}. Attempting to reconnect...")
                 await update_status("error_redis", f"Connection failed: {e}")
                 await asyncio.sleep(5) # Wait before next attempt
                 if pubsub:
                     try:
                         # Проверяем running и needs_restart перед переподпиской
                         if running and not needs_restart:
                             await pubsub.unsubscribe(input_channel)
                             await pubsub.subscribe(input_channel)
                             log_adapter.info("Resubscribed after connection error.")
                             await update_status("running") # Back to running if resubscribed
                         elif running and not needs_restart: # Если флаги ОК, но pubsub не подключен
                              log_adapter.warning("Pubsub disconnected, cannot resubscribe yet.")
                              pubsub = redis_client.pubsub()
                              await pubsub.subscribe(input_channel)
                              log_adapter.info("Recreated pubsub and subscribed.")
                              await update_status("running")

                     except Exception as resub_e:
                         log_adapter.error(f"Failed to resubscribe after connection error: {resub_e}")
                         await asyncio.sleep(10) # Longer wait if resubscribe fails
            except Exception as e:
                log_adapter.error(f"Error in Redis listener loop: {e}", exc_info=True)
                await update_status("error_listener_unexpected", f"Listener loop error: {e}")
                await asyncio.sleep(1) # Avoid tight loop on unexpected errors

    except Exception as setup_e:
         log_adapter.error(f"Failed during Redis listener setup: {setup_e}", exc_info=True)
         await update_status("error_listener_setup", f"Listener setup failed: {setup_e}")
    finally:
        # Cleanup on exit
        log_adapter.info("Cleaning up Redis listener...")
        if pubsub:
            try:
                await pubsub.unsubscribe(input_channel)
                await pubsub.aclose()
                log_adapter.info("Unsubscribed from Redis and closed pubsub connection.")
            except Exception as clean_e:
                 log_adapter.error(f"Error during Redis cleanup: {clean_e}")
        # Final status update depends on why we exited
        if needs_restart:
             log_adapter.info("Redis listener finished due to restart request.")
             # Статус будет обновлен в main_loop
        elif not running:
             log_adapter.info("Redis listener finished due to shutdown request.")
             await update_status("stopped")
        else: # If loop exited due to error
             log_adapter.warning("Redis listener finished unexpectedly (error).")
             await update_status("error_listener_unexpected", "Listener unexpectedly stopped")


# --- Main Execution Loop ---
async def main_loop(agent_id: str, config_url: str, redis_url: str):
    """Contains the main logic that can be restarted internally."""
    global running, needs_restart
    log_adapter = setup_logging(agent_id) # Setup logging for this run
    status_key = f"agent_status:{agent_id}"
    redis_client = None
    app = None
    static_state_config = {}
    listener_task = None
    control_task = None
    log_adapter.info("Entering main_loop.") # Лог входа в main_loop

    try:
        # 1. Initial Status Update
        await update_status_external(redis_url, status_key, "initializing", log_adapter)

        # 2. Initialize Redis Client
        log_adapter.info(f"Connecting to Redis at {redis_url}")
        try:
            redis_client = await redis.from_url(redis_url, decode_responses=True)
            await redis_client.ping()
            log_adapter.info("Redis connection successful.")
        except Exception as e:
            log_adapter.error(f"Failed to connect to Redis: {e}", exc_info=True)
            await update_status_external(redis_url, status_key, "error_redis", log_adapter, f"Connection failed: {e}")
            return # Exit loop if Redis connection fails

        # 3. Fetch Agent Configuration
        log_adapter.info(f"Fetching agent configuration from {config_url}")
        try:
            agent_config = await fetch_config(config_url, log_adapter)
            log_adapter.info("Agent configuration fetched successfully.")
        except Exception as e:
            log_adapter.error(f"Failed to fetch agent configuration: {e}", exc_info=True)
            await update_redis_status(redis_client, status_key, "error_config", os.getpid(), f"Fetch failed: {e}", log_adapter)
            return # Exit loop on config error

        # 4. Create Agent App (Graph) and get static config
        try:
            app, static_state_config = create_agent_app(agent_config, agent_id, redis_client)
            if not app: raise ValueError("create_agent_app returned None for app")
            log_adapter.info("Agent application created successfully.")
        except Exception as e:
            log_adapter.error(f"Failed to create agent application: {e}", exc_info=True)
            await update_redis_status(redis_client, status_key, "error_app_create", os.getpid(), f"App creation failed: {e}", log_adapter)
            return # Exit loop on app creation error

        # 5. Start Redis Listener and Control Listener
        await update_redis_status(redis_client, status_key, "running", os.getpid(), None, log_adapter) # Set status to running
        listener_task = asyncio.create_task(redis_listener(app, agent_id, redis_client, static_state_config), name="RedisListener")
        control_task = asyncio.create_task(control_listener(agent_id, redis_client), name="ControlListener")
        log_adapter.info("Listener tasks created.")

        # Wait for tasks to complete (due to shutdown, restart, or error)
        log_adapter.info("Waiting for listener tasks to complete...")
        done, pending = await asyncio.wait(
            [listener_task, control_task],
            return_when=asyncio.FIRST_COMPLETED
        )
        log_adapter.info(f"Asyncio.wait finished. Done tasks: {[t.get_name() for t in done]}. Pending tasks: {[t.get_name() for t in pending]}.")

        # Process completed tasks and check for errors/signals
        for task in done:
            task_name = task.get_name()
            log_adapter.info(f"Processing completed task: {task_name}")
            try:
                task.result() # Check for exceptions
                log_adapter.info(f"Task {task_name} completed without raising an exception.")
                # If a task finished without error but running=True and needs_restart=False, it's unexpected
                if running and not needs_restart:
                     log_adapter.error(f"Task {task_name} finished unexpectedly while running=True and needs_restart=False.")
                     running = False # Trigger shutdown
            except asyncio.CancelledError:
                 log_adapter.info(f"Task {task_name} was cancelled.")
            except Exception as e:
                 log_adapter.error(f"Task {task_name} failed with error: {e}", exc_info=True)
                 running = False # Trigger shutdown on task error
                 needs_restart = False # Don't restart if a task failed
                 # Update status if not already error
                 try: # Добавим try/except для обновления статуса
                     current_status_info = await redis_client.hgetall(status_key)
                     current_status = current_status_info.get("status", "unknown")
                     if not current_status.startswith("error_"):
                         await update_redis_status(redis_client, status_key, "error_unexpected", os.getpid(), f"Task {task_name} failed: {e}", log_adapter)
                 except Exception as status_update_err:
                      log_adapter.error(f"Failed to update status after task error: {status_update_err}")


        # Cancel pending tasks before cleanup/restart
        if pending:
             log_adapter.info(f"Cancelling {len(pending)} pending tasks...")
             for task in pending:
                 log_adapter.info(f"Cancelling pending task {task.get_name()}...")
                 task.cancel()
             # Wait for cancellations to complete
             results = await asyncio.gather(*pending, return_exceptions=True)
             log_adapter.info(f"Pending tasks cancellation results: {results}")
        else:
             log_adapter.info("No pending tasks to cancel.")


    except Exception as e:
        log_adapter.error(f"An unexpected error occurred in main_loop: {e}", exc_info=True)
        running = False # Ensure shutdown on major error
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
        # Cleanup resources for this run
        log_adapter.info("Cleaning up resources for current run...")
        if redis_client:
            await redis_client.aclose()
            log_adapter.info("Redis client closed for current run.")
        # Graph object 'app' will be garbage collected

        # Добавляем логи перед проверкой флагов
        log_adapter.info(f"Exiting main_loop. Final flags: running={running}, needs_restart={needs_restart}")
        if needs_restart:
            log_adapter.info("Preparing for internal restart...")
            await update_status_external(redis_url, status_key, "restarting", log_adapter)
            await asyncio.sleep(1) # Short delay before restarting loop
        elif not running:
            log_adapter.info("Preparing for shutdown...")
            await update_status_external(redis_url, status_key, "stopped", log_adapter) # Set final stopped status
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
    # Load environment variables first
    load_environment()

    parser = argparse.ArgumentParser(description="Run a configurable agent.")
    parser.add_argument("--agent-id", type=str, required=True, help="Unique ID for this agent instance.")
    parser.add_argument("--config-url", type=str, required=True, help="URL to fetch the agent's JSON configuration.")
    parser.add_argument("--redis-url", type=str, required=True, help="URL for the Redis server.")
    args = parser.parse_args()

    # --- Setup basic logging with filter BEFORE getting main_logger ---
    # This ensures even the initial logs from run_agent_process have the filter
    root_logger = logging.getLogger()
    # Remove existing handlers if any (important if run multiple times in same process)
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(levelname)s - %(agent_id)s - %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)],
        # force=True # Might be needed if basicConfig was called before
    )
    log_filter = AgentIdFilter(default_agent_id='-') # Default ID for logs before agent context
    for handler in root_logger.handlers:
         if not any(isinstance(f, AgentIdFilter) for f in handler.filters):
              handler.addFilter(log_filter)
    # --- End basic logging setup ---


    # Setup signal handlers
    signal.signal(signal.SIGINT, shutdown_handler)
    signal.signal(signal.SIGTERM, shutdown_handler)

    print(f"Starting agent runner process for ID: {args.agent_id} (PID: {os.getpid()})")
    # Теперь main_logger будет использовать базовую конфигурацию с фильтром
    main_logger = logging.getLogger(__name__) # Логгер для основного процесса

    # --- Main Process Loop ---
    async def run_agent_process():
        global running, needs_restart
        loop_count = 0
        # Используем адаптер для логов внутри цикла, где agent_id точно известен
        loop_log_adapter = logging.LoggerAdapter(main_logger, {'agent_id': args.agent_id})
        while running:
            loop_count += 1
            # Используем адаптер для логирования с agent_id
            loop_log_adapter.info(f"--- Starting main process loop iteration {loop_count} ---")
            needs_restart = False # Reset restart flag at the beginning of each loop
            loop_log_adapter.info(f"Before main_loop: running={running}, needs_restart={needs_restart}")
            # setup_logging внутри main_loop перенастроит логирование с agent_id
            await main_loop(args.agent_id, args.config_url, args.redis_url)
            # Лог после возврата из main_loop
            loop_log_adapter.info(f"After main_loop: running={running}, needs_restart={needs_restart}")
            if not needs_restart: # If main_loop exited without restart flag, break
                 loop_log_adapter.info("needs_restart is False, breaking main process loop.")
                 break
            else:
                 loop_log_adapter.info("needs_restart is True, continuing main process loop for restart.")
            # If needs_restart is True, the loop continues for another iteration
        loop_log_adapter.info("--- Exited main process loop ---")


    try:
        asyncio.run(run_agent_process())
    except KeyboardInterrupt:
         print("KeyboardInterrupt caught in main, exiting.")
    except Exception as e:
         print(f"Unhandled exception in asyncio.run: {e}")
         # Attempt to set error status before exiting
         asyncio.run(update_status_external(args.redis_url, f"agent_status:{args.agent_id}", "error_unexpected", logging.getLogger(__name__), f"Unhandled exit: {e}"))
         sys.exit(1)

    print(f"Agent runner {args.agent_id} has shut down.")
    sys.exit(0)

