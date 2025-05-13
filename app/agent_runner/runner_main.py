import os
import asyncio
import logging
import json
import argparse
import signal
import sys
import uuid
from typing import Dict, Optional, Any, List

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from langchain_core.messages import BaseMessage, HumanMessage, AIMessage
from datetime import datetime, timezone
import requests # Using requests for sync fetch_config, consider httpx for async if manager is async
import redis.asyncio as redis
import time

from app.agent_runner.langgraph_factory import create_agent_app
from app.agent_runner.langgraph_models import TokenUsageData # AgentState is used by factory
from app.db.session import get_async_session_factory, close_db_engine
from app.db.alchemy_models import ChatMessageDB, SenderType
from app.db.crud import chat_crud # Assuming db_get_recent_chat_history is in chat_crud
from app.core.config import settings
# app.core.logging_config is expected to be imported by the main app or have set up basicConfig
# If not, basicConfig might need to be called here, but ideally it's centralized.

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

def setup_logging_for_agent(agent_id: str) -> logging.LoggerAdapter:
    """
    Configures logging for this specific agent runner instance.
    Assumes basicConfig has been called elsewhere (e.g., by app.core.logging_config).
    Adds AgentIdFilter to root handlers if not present and returns a LoggerAdapter.
    """
    # Ensure app.core.logging_config has been imported and configured basicConfig
    # For example, by importing it in the main entry point of your application that launches runners
    # Or, if this runner is the absolute entry point, ensure logging_config is imported here.
    # import app.core.logging_config # Uncomment if this is the very first point logging is needed

    root_logger = logging.getLogger()
    
    # Add filter to root handlers if not already present.
    # This ensures that the %(agent_id)s format specifier works for all logs.
    log_filter = AgentIdFilter(default_agent_id='-') 
    for handler in root_logger.handlers:
        if not any(isinstance(f, AgentIdFilter) for f in handler.filters):
            handler.addFilter(log_filter)
            
    # Create a logger for this module and wrap it with an adapter.
    # The adapter will ensure 'agent_id' is in the extra dict for logs from this logger.
    module_logger = logging.getLogger(__name__)
    adapter = logging.LoggerAdapter(module_logger, {'agent_id': agent_id})
    return adapter

# Global flags for graceful shutdown and restart
running = True
needs_restart = False

# --- Signal Handler ---
def shutdown_handler(signum, frame):
    """Sets the global running flag to False on SIGINT or SIGTERM."""
    global running, needs_restart
    # Use a generic logger here as the specific agent's adapter might not be set up yet
    # or if the signal comes from outside the agent's main context.
    logger = logging.getLogger(__name__) 
    if running:
        print("\\nShutdown signal received. Attempting graceful shutdown...")
        logger.info("Shutdown signal received. Attempting graceful shutdown...")
        running = False
        needs_restart = False # Ensure no restart on manual shutdown
    else:
        # If already shutting down, a second signal might mean force exit
        print("Multiple shutdown signals received. Forcing exit.")
        logger.warning("Multiple shutdown signals received. Forcing exit.")
        os._exit(1) # Force exit

# --- Control Channel Listener ---
async def control_listener(agent_id: str, redis_client: redis.Redis, log_adapter: logging.LoggerAdapter):
    """Listens to the agent's control channel for commands like shutdown or restart."""
    global running, needs_restart
    control_channel = f"agent_control:{agent_id}"
    pubsub = None
    log_adapter.info("Control listener task started.")

    while running and not needs_restart:
        try:
            if pubsub is None:
                pubsub = redis_client.pubsub()
                await pubsub.subscribe(control_channel)
                log_adapter.info(f"Subscribed to control channel: {control_channel}")

            log_adapter.debug("Control listener waiting for message...")
            # Use a timeout to allow the loop to check `running` and `needs_restart` flags
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
                        break 
                    elif command == "restart":
                        log_adapter.info("Restart command received via Redis. Initiating internal restart.")
                        needs_restart = True
                        break
                except json.JSONDecodeError:
                    log_adapter.error(f"Received invalid JSON on control channel: {message['data'][:200]}...")
                except Exception as e:
                    log_adapter.error(f"Error processing control command: {e}", exc_info=True)
        
        except redis.exceptions.ConnectionError as e:
            log_adapter.error(f"Redis connection error in control listener: {e}. Retrying...")
            if pubsub:
                try:
                    await pubsub.unsubscribe(control_channel) # Clean up before retrying
                    await pubsub.aclose()
                except Exception: pass # Ignore errors during cleanup
                pubsub = None # Force re-subscription
            await asyncio.sleep(5) # Wait before retrying connection
        except asyncio.CancelledError:
            log_adapter.info("Control listener task cancelled.")
            break # Exit loop if cancelled
        except Exception as e:
            log_adapter.error(f"Unexpected error in control listener: {e}", exc_info=True)
            await asyncio.sleep(5) # Wait before continuing
    
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
         log_adapter.info("Control listener finished (running and needs_restart are false).")


# --- Agent Logic ---
async def fetch_config(config_url: str, log_adapter: logging.LoggerAdapter) -> Optional[Dict]:
    """Fetches agent configuration from the management service."""
    log_adapter.info(f"Fetching configuration from: {config_url}")
    try:
        # Using requests for simplicity, as in the original. Consider httpx for async.
        response = requests.get(config_url, timeout=15)
        response.raise_for_status() # Raises HTTPError for bad responses (4XX or 5XX)
        config_data = response.json()
        log_adapter.info("Configuration fetched successfully.")
        return config_data
    except requests.exceptions.Timeout:
        log_adapter.error(f"Timeout fetching configuration from {config_url}.")
    except requests.exceptions.RequestException as e:
        log_adapter.error(f"Failed to fetch configuration: {e}")
    except json.JSONDecodeError as e:
        log_adapter.error(f"Failed to decode JSON from configuration response: {e}")
    return None


async def update_redis_status(
    redis_client: redis.Redis, 
    status_key: str, 
    status: str, 
    pid: Optional[int], 
    error_detail: Optional[str], 
    log_adapter: logging.LoggerAdapter
):
    """Updates the agent status hash in Redis, ensuring fields are correctly set or removed."""
    mapping_to_set = {"status": status}
    fields_to_delete = []

    # PID handling
    if pid is not None:
        mapping_to_set["pid"] = str(pid)
    
    # PID should be absent for these statuses, regardless of whether a pid was passed.
    if status in ["stopped", "restarting"] or status.startswith("error_"):
        fields_to_delete.append("pid")
        if "pid" in mapping_to_set: # If a PID was passed for an error/stopped state, don't set it.
            del mapping_to_set["pid"]


    # Error detail handling
    if error_detail:
        mapping_to_set["error_detail"] = error_detail
    elif not status.startswith("error_"): # If no new error detail AND status is not an error status, remove old detail
        fields_to_delete.append("error_detail")
    # If status IS an error status but error_detail is None, we preserve any existing error_detail by not adding it to fields_to_delete.

    # Last active handling
    if status == "running":
        mapping_to_set["last_active"] = str(time.time())
    else: # For any other status, last_active is not relevant
        fields_to_delete.append("last_active")
    
    try:
        async with redis_client.pipeline() as pipe:
            if mapping_to_set:
                pipe.hset(status_key, mapping=mapping_to_set)
            if fields_to_delete:
                # Remove duplicates just in case, though hdel handles multiple same fields fine
                unique_fields_to_delete = list(set(fields_to_delete))
                if unique_fields_to_delete:
                    pipe.hdel(status_key, *unique_fields_to_delete)
            await pipe.execute()
        
        log_adapter.info(f"Status updated to: {status} (PID: {pid if pid is not None and status == 'running' else 'None'}, Error: {'Provided' if error_detail else 'NoNewDetail'})")
    except Exception as e:
        log_adapter.error(f"Failed to update Redis status to {status}: {e}", exc_info=True)


def convert_db_history_to_langchain(db_messages: List[ChatMessageDB]) -> List[BaseMessage]:
    """Converts messages from DB format (ChatMessageDB) to LangChain BaseMessage list."""
    converted = []
    if not ChatMessageDB or not SenderType: # Should always be available due to direct imports
        logging.error("ChatMessageDB model or SenderType Enum not available for history conversion (should not happen).")
        return []

    for msg in db_messages:
        if not isinstance(msg, ChatMessageDB):
             logging.warning(f"Skipping message conversion due to unexpected type: {type(msg)}")
             continue
        if msg.sender_type == SenderType.USER:
            converted.append(HumanMessage(content=msg.content))
        elif msg.sender_type == SenderType.AGENT:
            converted.append(AIMessage(content=msg.content))
        # Not handling ToolMessage here as it's not typically stored directly as user/agent chat history
        else:
            logging.warning(f"Skipping message conversion due to unhandled sender_type: {msg.sender_type}")
    return converted


async def redis_listener(
    app: Any, # LangGraph compiled app
    agent_id: str,
    redis_client: redis.Redis,
    static_state_config: Dict[str, Any],
    db_session_factory: Optional[async_sessionmaker[AsyncSession]],
    log_adapter: logging.LoggerAdapter
):
    """Listens to Redis input channel, processes messages, publishes output, and queues history."""
    input_channel = f"agent:{agent_id}:input"
    output_channel = f"agent:{agent_id}:output"
    status_key = f"agent_status:{agent_id}"
    history_queue = settings.REDIS_HISTORY_QUEUE_NAME
    token_usage_queue = settings.REDIS_TOKEN_USAGE_QUEUE_NAME
    loaded_threads_key = f"agent_loaded_threads:{agent_id}" # Cache for threads whose history is loaded

    pubsub = None
    can_load_history = db_session_factory is not None and chat_crud is not None

    if not can_load_history:
        log_adapter.warning("Database history loading is disabled (DB session factory or chat_crud not available).")

    enable_context_memory = static_state_config.get("enableContextMemory", True)
    context_memory_depth = static_state_config.get("contextMemoryDepth", 10)
    
    if not enable_context_memory:
        log_adapter.info("Context memory is disabled by agent config. History will not be loaded from DB.")
        context_memory_depth = 0 # Ensure no history loading if disabled

    async def queue_message_for_history_worker(
        sender_type_val: str, # Use sender_type_val to avoid conflict with Enum
        thread_id: str,
        content: str,
        channel: Optional[str],
        interaction_id: Optional[str]
    ):
        try:
            history_payload = {
                "agent_id": agent_id,
                "thread_id": thread_id,
                "sender_type": sender_type_val,
                "content": content,
                "channel": channel,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "interaction_id": interaction_id
            }
            await redis_client.lpush(history_queue, json.dumps(history_payload))
            log_adapter.debug(f"Queued {sender_type_val} message for history (Thread: {thread_id}, InteractionID: {interaction_id})")
        except redis.RedisError as e:
            log_adapter.error(f"Failed to queue message for history (Thread: {thread_id}): {e}")
        except Exception as e: # Catch any other unexpected error during queueing
            log_adapter.error(f"Unexpected error queuing message for history (Thread: {thread_id}): {e}", exc_info=True)

    try:
        pubsub = redis_client.pubsub()
        await pubsub.subscribe(input_channel)
        log_adapter.info(f"Subscribed to Redis channel: {input_channel}")

        global running, needs_restart # Access global flags
        last_active_update_time = 0
        update_interval = 30 # seconds, for updating last_active in Redis

        while running and not needs_restart:
            try:
                current_time = time.time()
                if current_time - last_active_update_time > update_interval:
                    await redis_client.hset(status_key, "last_active", str(current_time))
                    last_active_update_time = current_time

                # Timeout allows checking running/needs_restart flags periodically
                message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=0.5)
                if not running or needs_restart: break # Exit if shutdown/restart initiated

                if message and message.get("type") == "message":
                    raw_data = message['data']
                    try:
                        data = json.loads(raw_data)
                        log_adapter.info(f"Received message from Redis: {data}")
                    except json.JSONDecodeError:
                        log_adapter.error(f"Received invalid JSON from Redis: {raw_data[:200]}...")
                        continue # Skip malformed message

                    # Update last_active immediately upon receiving a message to process
                    await redis_client.hset(status_key, "last_active", str(time.time()))
                    last_active_update_time = time.time()

                    thread_id = "unknown_thread" # Default
                    try:
                        user_message_content = data.get("message")
                        thread_id = data.get("thread_id", f"unknown_thread_{uuid.uuid4()}") # Ensure thread_id exists
                        user_data = data.get("user_data", {})
                        channel = data.get("channel", "unknown_channel")
                        platform_user_id = data.get("platform_user_id") 

                        if not user_message_content: # thread_id has a default now
                             log_adapter.error("Missing 'message' in Redis payload. Skipping.")
                             continue

                        current_interaction_id = str(uuid.uuid4())
                        log_adapter.info(f"Generated InteractionID: {current_interaction_id} for Thread: {thread_id}")

                        await queue_message_for_history_worker(
                            sender_type_val=SenderType.USER.value, # Use Enum value
                            thread_id=thread_id,
                            content=user_message_content,
                            channel=channel,
                            interaction_id=current_interaction_id
                        )

                        loaded_messages: List[BaseMessage] = []
                        if can_load_history and enable_context_memory and context_memory_depth > 0:
                            try:
                                # Check if thread history was already loaded (simple cache)
                                is_loaded_in_cache = await redis_client.sismember(loaded_threads_key, thread_id)
                                if not is_loaded_in_cache:
                                    log_adapter.info(f"Thread '{thread_id}' not in cache. Loading history from DB (depth: {context_memory_depth}).")
                                    async with db_session_factory() as session: # type: ignore
                                        history_from_db = await chat_crud.db_get_recent_chat_history(
                                            db=session,
                                            agent_id=agent_id,
                                            thread_id=thread_id,
                                            limit=context_memory_depth
                                        )
                                        loaded_messages = convert_db_history_to_langchain(history_from_db)
                                    log_adapter.info(f"Loaded {len(loaded_messages)} messages from DB for thread '{thread_id}'.")
                                    await redis_client.sadd(loaded_threads_key, thread_id) # Add to cache
                                else:
                                    log_adapter.info(f"Thread '{thread_id}' found in cache. Skipping DB load for history.")
                            except redis.RedisError as redis_err:
                                log_adapter.error(f"Redis error with thread cache for '{thread_id}': {redis_err}. Proceeding without history.")
                            except Exception as db_err: # Catch broader errors from DB interaction
                                log_adapter.error(f"Database error loading history for thread '{thread_id}': {db_err}. Proceeding without history.", exc_info=True)
                        # ... (logging for skipped history loading)

                        graph_input = {
                            "messages": loaded_messages + [HumanMessage(content=user_message_content)],
                            "user_data": user_data, "channel": channel,
                            "original_question": user_message_content, "question": user_message_content,
                            "rewrite_attempts": 0, "documents": [], # LangGraph factory expects rewrite_attempts
                            "current_interaction_id": current_interaction_id,
                            "token_usage_events": [],
                            **static_state_config
                        }
                        # Config for LangGraph invocation (thread_id for checkpointer, agent_id for logging within graph)
                        invocation_config = {"configurable": {"thread_id": str(thread_id), "agent_id": agent_id}}

                        log_adapter.info(f"Invoking graph for thread_id: {thread_id} (Initial history: {len(loaded_messages)} msgs)")
                        final_response_content = "Agent error: No response generated." # Default error response
                        final_message_object_dump = None # For storing AIMessage.model_dump()

                        async for output_chunk in app.astream(graph_input, invocation_config, stream_mode="updates"):
                            if not running or needs_restart:
                                log_adapter.warning("Shutdown or restart requested during graph stream. Breaking.")
                                break
                            # Process output_chunk: typically a dict with node name as key
                            # Example: {'agent_node': {'messages': [AIMessage(...)]}}
                            for node_name, node_output in output_chunk.items():
                                log_adapter.debug(f"Graph node '{node_name}' output: {node_output}")
                                # Extract final AIMessage if this node is the one producing it
                                if "messages" in node_output and isinstance(node_output["messages"], list) and node_output["messages"]:
                                    last_msg = node_output["messages"][-1]
                                    if isinstance(last_msg, AIMessage):
                                         final_response_content = last_msg.content
                                         try:
                                             final_message_object_dump = last_msg.model_dump()
                                         except Exception as dump_err:
                                             log_adapter.error(f"Error dumping final AIMessage: {dump_err}")


                        if not running or needs_restart: break # Check again after stream

                        log_adapter.info(f"Graph execution finished. Final response preview: {final_response_content[:100]}...")

                        await queue_message_for_history_worker(
                            sender_type_val=SenderType.AGENT.value,
                            thread_id=thread_id,
                            content=final_response_content,
                            channel=channel,
                            interaction_id=current_interaction_id
                        )
                        
                        # Get final state for token usage
                        final_graph_state_snapshot = app.get_state(invocation_config) # Not async
                        if final_graph_state_snapshot and final_graph_state_snapshot.values:
                            retrieved_final_state = final_graph_state_snapshot.values
                            token_events: List[TokenUsageData] = retrieved_final_state.get("token_usage_events", [])
                            if token_events:
                                log_adapter.info(f"Found {len(token_events)} token usage events for InteractionID: {current_interaction_id}.")
                                for token_data in token_events:
                                    try:
                                        # Ensure token_data is an instance of TokenUsageData if it comes from graph state
                                        if isinstance(token_data, TokenUsageData):
                                            token_payload = {
                                                "interaction_id": current_interaction_id, "agent_id": agent_id, "thread_id": thread_id,
                                                "call_type": token_data.call_type, "model_id": token_data.model_id,
                                                "prompt_tokens": token_data.prompt_tokens, "completion_tokens": token_data.completion_tokens,
                                                "total_tokens": token_data.total_tokens, "timestamp": token_data.timestamp
                                            }
                                            await redis_client.lpush(token_usage_queue, json.dumps(token_payload))
                                        else: # If it's a dict (e.g. from older checkpoint)
                                            # Attempt to construct TokenUsageData or handle dict directly
                                            log_adapter.warning(f"Token data is not TokenUsageData instance: {type(token_data)}")
                                            # Basic handling for dict if necessary, assuming keys match
                                            if isinstance(token_data, dict) and all(k in token_data for k in ["call_type", "model_id", "prompt_tokens", "completion_tokens", "total_tokens", "timestamp"]):
                                                token_payload = {"interaction_id": current_interaction_id, "agent_id": agent_id, "thread_id": thread_id, **token_data}
                                                await redis_client.lpush(token_usage_queue, json.dumps(token_payload))

                                    except redis.RedisError as e: # Specific Redis errors
                                        log_adapter.error(f"Redis error queuing token usage for InteractionID {current_interaction_id}: {e}")
                                    except Exception as e_token_queue: # Generic errors
                                        log_adapter.error(f"Unexpected error queuing token usage for InteractionID {current_interaction_id}: {e_token_queue}", exc_info=True)
                        else:
                            log_adapter.warning(f"Could not retrieve final graph state or token_usage_events for InteractionID: {current_interaction_id}.")


                        response_payload_dict = {
                            "thread_id": thread_id, "platform_user_id": platform_user_id,
                            "response": final_response_content,
                            "message_object": final_message_object_dump, # Serialized AIMessage
                            "channel": channel
                        }
                        await redis_client.publish(output_channel, json.dumps(response_payload_dict))
                        log_adapter.info(f"Published response to Redis channel: {output_channel}")

                    except asyncio.CancelledError: # If the message processing task itself is cancelled
                         log_adapter.info(f"Message processing for thread {thread_id} cancelled.")
                         if running and not needs_restart: raise # Re-raise if not part of shutdown
                    except Exception as e_msg_proc: # Catch all for a single message processing cycle
                        log_adapter.error(f"Error processing message for thread {thread_id}: {e_msg_proc}", exc_info=True)
                        # Publish an error message back to the output channel
                        error_payload = json.dumps({"thread_id": thread_id, "error": f"Agent error: {e_msg_proc}"})
                        try:
                            await redis_client.publish(output_channel, error_payload)
                        except Exception as e_pub_err:
                            log_adapter.error(f"Failed to publish error payload for thread {thread_id}: {e_pub_err}")
            
            except asyncio.CancelledError: # If the main while loop's iteration is cancelled
                log_adapter.info("Redis listener task's main loop iteration cancelled.")
                break # Exit main while loop
            except redis.exceptions.ConnectionError as e_conn:
                 log_adapter.error(f"Redis connection error in listener loop: {e_conn}. Attempting to reconnect...")
                 await update_redis_status(redis_client, status_key, "error_redis", None, f"Connection failed: {e_conn}", log_adapter)
                 await asyncio.sleep(5) # Wait before trying to re-establish pubsub
                 if pubsub: # Attempt to clean up old pubsub
                     try: 
                         await pubsub.unsubscribe(input_channel)
                         await pubsub.aclose()
                     except: pass
                     pubsub = None # Force re-subscription in the next iteration
            except Exception as e_loop: # Catch-all for unexpected errors in the listener's while loop
                log_adapter.error(f"Error in Redis listener main loop: {e_loop}", exc_info=True)
                await update_redis_status(redis_client, status_key, "error_listener_unexpected", None, f"Listener loop error: {e_loop}", log_adapter)
                await asyncio.sleep(1) # Brief pause before continuing loop

    except Exception as setup_e: # Errors during initial pubsub setup
         log_adapter.error(f"Failed during Redis listener setup (e.g., initial subscribe): {setup_e}", exc_info=True)
         await update_redis_status(redis_client, status_key, "error_listener_setup", None, f"Listener setup failed: {setup_e}", log_adapter)
    finally:
        log_adapter.info("Cleaning up Redis listener...")
        if pubsub:
            try:
                await pubsub.unsubscribe(input_channel)
                await pubsub.aclose()
                log_adapter.info("Unsubscribed from Redis and closed pubsub connection.")
            except Exception as clean_e:
                 log_adapter.error(f"Error during Redis listener cleanup: {clean_e}")
        
        # Final status update based on how the listener exited
        if needs_restart:
             log_adapter.info("Redis listener finished due to restart request.")
        elif not running: # Normal shutdown
             log_adapter.info("Redis listener finished due to shutdown request.")
             # Status update for "stopped" will be handled by main_loop's finally block
        else: # Unexpected exit from listener while agent was supposed to be running
             log_adapter.warning("Redis listener finished unexpectedly (running=True, needs_restart=False). This might indicate an issue.")


# --- Main Execution Loop ---
async def main_loop(
    agent_id: str,
    log_adapter: logging.LoggerAdapter, # Use the adapter passed from run_agent_process
    db_session_factory: Optional[async_sessionmaker[AsyncSession]],
    redis_url_str: str, # Added
    agent_config_url_str: str # Added
):
    """Contains the main logic that can be restarted internally."""
    global running, needs_restart # Control global flags
    
    status_key = f"agent_status:{agent_id}"
    loaded_threads_key = f"agent_loaded_threads:{agent_id}" # For caching loaded thread histories
    redis_client = None
    app = None # LangGraph app
    static_state_config = {} # Config derived from agent's JSON config
    listener_task = None
    control_task = None

    # Use passed arguments instead of settings
    redis_url = redis_url_str
    agent_specific_config_url = agent_config_url_str


    log_adapter.info(f"Entering main_loop for agent {agent_id}. PID: {os.getpid()}")

    try:
        # Initial status update (using a temporary client if main one fails)
        await update_status_external(redis_url, status_key, "initializing", log_adapter)

        log_adapter.info(f"Connecting to Redis at specified URL") # Updated log message
        try:
            redis_client = await redis.from_url(redis_url, decode_responses=True)
            await redis_client.ping() # Verify connection
            log_adapter.info("Redis connection successful.")
            # Clear any stale loaded threads cache for this agent on start/restart
            try:
                deleted_count = await redis_client.delete(loaded_threads_key)
                log_adapter.info(f"Cleared loaded threads cache '{loaded_threads_key}' (deleted: {deleted_count}).")
            except redis.RedisError as cache_clear_err: # Catch specific Redis errors
                log_adapter.error(f"Failed to clear loaded threads cache '{loaded_threads_key}': {cache_clear_err}")
        except Exception as e_redis_conn: # Catch all Redis connection errors
            log_adapter.error(f"Failed to connect to Redis: {e_redis_conn}", exc_info=True)
            await update_status_external(redis_url, status_key, "error_redis", log_adapter, f"Redis connection failed: {e_redis_conn}")
            return # Cannot proceed without Redis

        log_adapter.info(f"Fetching agent configuration from {agent_specific_config_url}")
        try:
            agent_config_json = await fetch_config(agent_specific_config_url, log_adapter)
            if agent_config_json is None:
                raise ValueError("Fetched agent configuration is None.")
            log_adapter.info("Agent configuration fetched successfully.")
        except Exception as e_fetch_config: # Catch all config fetch errors
            log_adapter.error(f"Failed to fetch agent configuration: {e_fetch_config}", exc_info=True)
            await update_redis_status(redis_client, status_key, "error_config", None, f"Config fetch failed: {e_fetch_config}", log_adapter)
            return # Cannot proceed without agent config

        try:
            # Create agent app (LangGraph) using the factory
            app, static_state_config = create_agent_app(agent_config_json, agent_id) # Убрана передача redis_client
            if not app: raise ValueError("create_agent_app returned None for app.")
            log_adapter.info("Agent application (LangGraph) created successfully.")
        except Exception as e_app_create: # Catch all app creation errors
            log_adapter.error(f"Failed to create agent application: {e_app_create}", exc_info=True)
            await update_redis_status(redis_client, status_key, "error_app_create", None, f"App creation failed: {e_app_create}", log_adapter)
            return # Cannot proceed if app creation fails

        # If all setup is successful, mark as running
        await update_redis_status(redis_client, status_key, "running", os.getpid(), None, log_adapter)

        # Start listener tasks
        listener_task = asyncio.create_task(
            redis_listener(app, agent_id, redis_client, static_state_config, db_session_factory, log_adapter),
            name=f"RedisListener-{agent_id}"
        )
        control_task = asyncio.create_task(
            control_listener(agent_id, redis_client, log_adapter), 
            name=f"ControlListener-{agent_id}"
        )
        log_adapter.info("Redis listener and control listener tasks created.")

        # Wait for either task to complete (e.g., due to error, shutdown, or restart command)
        done, pending = await asyncio.wait(
            [listener_task, control_task],
            return_when=asyncio.FIRST_COMPLETED
        )
        log_adapter.info(f"Asyncio.wait finished. Done tasks: {[t.get_name() for t in done]}. Pending: {[t.get_name() for t in pending]}.")

        for task in done: # Process the task(s) that completed
            task_name = task.get_name()
            log_adapter.info(f"Processing completed task: {task_name}")
            try:
                task.result() # Raise exception if task failed
                log_adapter.info(f"Task {task_name} completed without raising an exception.")
                # If a task finishes cleanly but agent is still 'running' and not 'restarting', it's unexpected.
                if running and not needs_restart:
                     log_adapter.error(f"Task {task_name} finished unexpectedly. Initiating shutdown.")
                     running = False # Signal shutdown for other parts
            except asyncio.CancelledError:
                 log_adapter.info(f"Task {task_name} was cancelled.")
            except Exception as e_task_err: # Task failed with an exception
                 log_adapter.error(f"Task {task_name} failed with error: {e_task_err}", exc_info=True)
                 running = False # Signal shutdown
                 needs_restart = False # Do not restart on critical task failure
                 # Update status to reflect error, if not already an error status
                 try:
                     if redis_client: # Ensure redis_client is available
                         current_status_info = await redis_client.hgetall(status_key)
                         current_status = current_status_info.get("status", "unknown") if current_status_info else "unknown"
                         if not current_status.startswith("error_"):
                             await update_redis_status(redis_client, status_key, "error_task", None, f"Task {task_name} failed: {e_task_err}", log_adapter)
                 except Exception as status_update_err: # Catch errors during status update
                      log_adapter.error(f"Failed to update Redis status after task error: {status_update_err}")
        
        # Cancel any pending tasks
        if pending:
             log_adapter.info(f"Cancelling {len(pending)} pending tasks...")
             for task in pending:
                 log_adapter.info(f"Cancelling pending task {task.get_name()}...")
                 task.cancel()
             # Wait for pending tasks to acknowledge cancellation
             await asyncio.gather(*pending, return_exceptions=True) 
             log_adapter.info("Pending tasks cancellation processed.")
        else:
             log_adapter.info("No pending tasks to cancel.")

    except Exception as e_main_loop: # Catch-all for unexpected errors in main_loop setup
        log_adapter.error(f"An unexpected error occurred in main_loop for agent {agent_id}: {e_main_loop}", exc_info=True)
        running = False # Ensure shutdown
        needs_restart = False # Prevent restart loop on critical error
        if redis_client: # Try to update status if Redis client exists
             try:
                 current_status_info = await redis_client.hgetall(status_key)
                 current_status = current_status_info.get("status", "unknown") if current_status_info else "unknown"
                 if not current_status.startswith("error_"):
                     await update_redis_status(redis_client, status_key, "error_main_loop", None, f"Main loop critical error: {e_main_loop}", log_adapter)
             except Exception as status_err: # Catch errors during this final status update
                  log_adapter.error(f"Failed to update Redis error status in main_loop's except block: {status_err}")
    finally:
        log_adapter.info("Cleaning up resources in main_loop...")
        if redis_client:
            await redis_client.aclose() # Close the main Redis client for this loop
            log_adapter.info("Main Redis client closed for current run.")

        log_adapter.info(f"Exiting main_loop. Final flags: running={running}, needs_restart={needs_restart}")
        # Update status based on exit reason, using external helper for robustness
        if needs_restart:
            log_adapter.info("Preparing for internal restart...")
            await update_status_external(redis_url, status_key, "restarting", log_adapter)
            # No PID is set by update_status_external for "restarting" status
            await asyncio.sleep(1) # Brief pause before allowing outer loop to restart
        elif not running: # Normal shutdown or shutdown due to error
            log_adapter.info("Preparing for shutdown...")
            # Check current status before blindly setting to "stopped" to preserve error states
            current_status_val = "unknown"
            temp_redis_check = None
            try:
                # redis is imported at the file level
                temp_redis_check = await redis.from_url(redis_url, decode_responses=True)
                await temp_redis_check.ping()
                status_info = await temp_redis_check.hgetall(status_key)
                if status_info and "status" in status_info:
                    current_status_val = status_info["status"]
                log_adapter.info(f"Current status before final 'stopped' update attempt: {current_status_val}")
            except Exception as e_check_status:
                log_adapter.warning(f"Could not check current status before final update in main_loop: {e_check_status}")
            finally:
                if temp_redis_check:
                    await temp_redis_check.aclose()

            if not current_status_val.startswith("error_"):
                log_adapter.info(f"Current status '{current_status_val}' is not an error. Setting to 'stopped'.")
                await update_status_external(redis_url, status_key, "stopped", log_adapter)
            else:
                log_adapter.info(f"Current status '{current_status_val}' is an error. Not overwriting with 'stopped' to preserve error details.")
        else: # Should not happen if loop exited correctly (running is True, but loop is exiting and not restarting)
             log_adapter.warning("main_loop finished unexpectedly (running=True, needs_restart=False). This path should ideally not be reached.")
             await update_status_external(redis_url, status_key, "error_unexpected_exit", log_adapter, "Main loop exited unexpectedly but running=True and no restart")


async def update_status_external(
    redis_url: str, 
    status_key: str, 
    status_val: str, # Renamed to avoid conflict
    log_adapter: logging.LoggerAdapter, 
    error_detail_val: Optional[str] = None # Renamed
):
    """Helper to update Redis status using a temporary connection (used during init/shutdown/restart)."""
    temp_redis = None
    try:
        temp_redis = await redis.from_url(redis_url, decode_responses=True)
        await temp_redis.ping()
        # Determine PID: None if stopping/restarting or error, otherwise current PID
        pid_to_set = os.getpid() if status_val not in ["stopped", "restarting"] and not status_val.startswith("error_") else None
        
        # The refined update_redis_status will correctly handle clearing or preserving error_detail.
        await update_redis_status(temp_redis, status_key, status_val, pid_to_set, error_detail_val, log_adapter)
    except Exception as e: # Catch all errors during this external update
        log_adapter.error(f"Failed to update external Redis status to {status_val}: {e}", exc_info=True)
    finally:
        if temp_redis:
            await temp_redis.aclose()


# --- Main Entry Point ---
if __name__ == "__main__":
    # Explicitly setup logging using the project's standard configuration
    # This ensures basicConfig is called once with desired settings before agent-specific logging.
    from app.core.logging_config import setup_logging
    setup_logging()

    # No load_environment() needed, settings are imported from app.core.config

    # Initialize DB session factory (raw sessionmaker)
    # This should be available for the agent runner if it needs DB access (e.g. for history)
    db_session_factory = get_async_session_factory() 
    if not db_session_factory:
        # Log this, but the runner might still function if DB history is not critical or disabled by config
        logging.warning("Database session factory could not be created. History loading might be affected.")

    parser = argparse.ArgumentParser(description="Run a configurable agent.")
    parser.add_argument("--agent-id", type=str, required=True, help="Unique ID for this agent instance.")
    parser.add_argument("--config-url", type=str, required=True, help="URL to fetch agent configuration")
    parser.add_argument("--redis-url", type=str, required=True, help="URL for Redis connection")
    parser.add_argument("--manager-host", required=False, help="Hostname of the manager API (optional).")
    parser.add_argument("--manager-port", required=False, type=int, help="Port of the manager API (optional).")
    args = parser.parse_args()

    # Initial, basic logger for messages before agent-specific adapter is ready
    # app.core.logging_config should have set up basicConfig.
    # If not, this will use Python's default basicConfig on first logging call.
    initial_logger = logging.getLogger("runner_main") 
    initial_logger.info(f"Preparing to start agent runner process for ID: {args.agent_id} (PID: {os.getpid()})")

    # Setup signal handlers for graceful shutdown
    signal.signal(signal.SIGINT, shutdown_handler)
    signal.signal(signal.SIGTERM, shutdown_handler)

    async def run_agent_process():
        global running, needs_restart # Allow modification of global flags
        
        # Setup agent-specific logging
        # This adapter will be used for all logs related to this agent instance
        log_adapter = setup_logging_for_agent(args.agent_id)
        
        loop_count = 0
        while running: # Outer loop for handling restarts
            loop_count += 1
            log_adapter.info(f"--- Starting main process loop iteration {loop_count} for agent {args.agent_id} ---")
            needs_restart = False # Reset restart flag for current iteration
            
            # Pass redis_url and config_url from args
            await main_loop(args.agent_id, log_adapter, db_session_factory, args.redis_url, args.config_url)
            
            log_adapter.info(f"--- Main_loop iteration {loop_count} finished. Flags: running={running}, needs_restart={needs_restart} ---")
            if not needs_restart: # If not explicitly asked to restart, break the outer loop
                 log_adapter.info("needs_restart is False. Exiting main process loop.")
                 break 
            else: # needs_restart is True
                 log_adapter.info("needs_restart is True. Proceeding with agent restart.")
                 # `running` should still be True to allow restart. If it became False, loop will exit.
        
        log_adapter.info(f"--- Exited main process loop for agent {args.agent_id} ---")
        # Clean up database engine connections if they were initialized
        await close_db_engine()
        log_adapter.info("Database engine closed (if initialized).")

    try:
        asyncio.run(run_agent_process())
    except KeyboardInterrupt: # Should be caught by signal handler, but as a fallback
         initial_logger.info("KeyboardInterrupt caught in asyncio.run. Exiting.")
         # Try to close DB engine cleanly, might not run if process is killed too fast
         asyncio.run(close_db_engine())
    except Exception as e_outer: # Catch-all for truly unexpected errors at the top level
         initial_logger.critical(f"Unhandled exception in asyncio.run for agent {args.agent_id}: {e_outer}", exc_info=True)
         # Try to update status to error using a temporary Redis client
         redis_url_for_error = args.redis_url
         status_key_for_error = f"agent_status:{args.agent_id}"
         # Use a basic logger for this emergency status update
         emergency_logger = logging.getLogger("emergency_status_updater")
         asyncio.run(update_status_external(redis_url_for_error, status_key_for_error, "error_runner_crash", emergency_logger, f"Unhandled exit: {e_outer}"))
         asyncio.run(close_db_engine()) # Final attempt to clean up DB
         sys.exit(1) # Exit with error code

    initial_logger.info(f"Agent runner {args.agent_id} has shut down gracefully.")
    sys.exit(0) # Normal exit
