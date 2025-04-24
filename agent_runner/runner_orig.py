import os
import asyncio
import logging
import json
import argparse
import signal
import sys
from typing import Dict, Optional
from dotenv import load_dotenv
import requests
import redis.asyncio as redis
import time # For last_active updates

# Import from sibling modules
from .graph_factory import create_agent_app # Импортируем фабрику графа
# from .models import AgentState # AgentState используется внутри graph_factory

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

def setup_logging(agent_id: str):
    """Configures logging for the agent runner."""
    # Remove existing handlers if any to avoid duplicate logs on reconfigure
    for handler in logging.root.handlers[:]:
        logging.root.removeHandler(handler)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(levelname)s - %(agent_id)s - %(message)s",
        handlers=[
            logging.StreamHandler(sys.stdout) # Log to stdout
            # Add FileHandler if needed: logging.FileHandler(f"agent_{agent_id}.log")
        ]
    )
    # Return a logger adapter with the agent_id context
    return logging.LoggerAdapter(logging.getLogger(__name__), {'agent_id': agent_id})

# Global flag for graceful shutdown
running = True

# --- Signal Handler ---
def shutdown_handler(signum, frame):
    """Sets the global running flag to False on SIGINT or SIGTERM."""
    global running
    logger = logging.getLogger(__name__) # Use base logger here
    if running:
        print("\nShutdown signal received. Attempting graceful shutdown...")
        logger.info("Shutdown signal received. Attempting graceful shutdown...")
        running = False
    else:
        print("Multiple shutdown signals received. Forcing exit.")
        logger.warning("Multiple shutdown signals received. Forcing exit.")
        os._exit(1) # Force exit if already shutting down


# --- Agent Logic ---

async def fetch_config(config_url: str, log_adapter: logging.LoggerAdapter) -> Dict:
    """Fetches agent configuration from the management service."""
    log_adapter.info(f"Fetching configuration from: {config_url}")
    try:
        # TODO: Add authentication headers if manager requires it
        response = requests.get(config_url, timeout=15)
        response.raise_for_status()
        config_data = response.json()
        log_adapter.info("Configuration fetched successfully.")
        return config_data
    except requests.exceptions.Timeout:
        log_adapter.error(f"Timeout fetching configuration from {config_url}.")
        raise
    except requests.exceptions.RequestException as e:
        log_adapter.error(f"Failed to fetch configuration: {e}")
        raise

async def update_redis_status(r: redis.Redis, status_key: str, status: str, pid: Optional[int] = None, error_detail: Optional[str] = None, log_adapter: logging.LoggerAdapter = None):
    """Updates the agent's status in Redis."""
    if not log_adapter:
        log_adapter = logging.getLogger(__name__) # Fallback logger
    try:
        mapping = {"status": status}
        if pid is not None:
            mapping["pid"] = pid
        else:
            # Ensure PID is removed if status is stopped/error without PID
             if status in ["stopped", "error_config", "error_app_create", "error_unexpected", "error_redis", "error_listener_setup", "error_listener_unexpected"]:
                 await r.hdel(status_key, "pid") # Explicitly remove potentially stale PID

        if error_detail:
             mapping["error_detail"] = error_detail
        else:
             # Remove error detail if status is now okay
             if status in ["running", "initializing", "stopped"]:
                 await r.hdel(status_key, "error_detail")


        await r.hset(status_key, mapping=mapping)
        log_adapter.debug(f"Updated Redis status ({status_key}): {mapping}")
    except Exception as e:
        log_adapter.error(f"Failed to update Redis status ({status_key}) to {status}: {e}")


async def redis_listener(app, agent_id: str, redis_client: redis.Redis):
    """Listens to Redis input channel, processes messages, and publishes output."""
    log_adapter = logging.LoggerAdapter(logging.getLogger(__name__), {'agent_id': agent_id}) # Use __name__ for logger
    input_channel = f"agent:{agent_id}:input"
    output_channel = f"agent:{agent_id}:output"
    status_key = f"agent_status:{agent_id}"

    pubsub = None # Initialize pubsub to None

    async def update_status(status: str, error_detail: Optional[str] = None):
        """Helper to update Redis status using the outer function."""
        await update_redis_status(redis_client, status_key, status, os.getpid(), error_detail, log_adapter)


    try:
        pubsub = redis_client.pubsub()
        await pubsub.subscribe(input_channel)
        log_adapter.info(f"Subscribed to Redis channel: {input_channel}")
        await update_status("running") # Set status to running after successful setup

        global running
        last_active_update_time = 0
        update_interval = 30 # Update last_active every 30 seconds

        while running:
            try:
                # Update last active time periodically
                current_time = time.time()
                if current_time - last_active_update_time > update_interval:
                    await redis_client.hset(status_key, "last_active", current_time)
                    last_active_update_time = current_time

                # Listen for messages with timeout
                message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
                if message and message.get("type") == "message":
                    log_adapter.info(f"Received message from Redis: {message['data'][:200]}...") # Log truncated data
                    # Update last active time immediately on message
                    await redis_client.hset(status_key, "last_active", time.time())
                    last_active_update_time = time.time()

                    thread_id = "unknown" # Default thread_id
                    try:
                        data = json.loads(message["data"])
                        user_message = data.get("message")
                        thread_id = data.get("thread_id") # Update thread_id if available
                        user_data = data.get("user_data", {})
                        channel = data.get("channel", "unknown")

                        if not user_message or not thread_id:
                             log_adapter.error("Missing 'message' or 'thread_id' in Redis payload.")
                             continue

                        # Import HumanMessage here or at the top if not already done
                        from langchain_core.messages import HumanMessage
                        graph_input = {
                            "messages": [HumanMessage(content=user_message)], # Ensure it's a BaseMessage
                            "user_data": user_data,
                            "channel": channel,
                            "original_question": user_message, # Add original question
                            "question": user_message, # Initial question
                            "rewrite_count": 0, # Initial rewrite count
                            "documents": [] # Initial empty documents
                        }
                        config = {"configurable": {"thread_id": str(thread_id)}}

                        log_adapter.info(f"Invoking graph for thread_id: {thread_id}")
                        final_response_content = "No response generated."
                        # Import AIMessage if needed for type checking
                        from langchain_core.messages import AIMessage
                        final_message_object = None

                        async for output in app.astream(graph_input, config, stream_mode="updates"):
                            # Check for cancellation request during streaming
                            if not running:
                                log_adapter.warning("Shutdown requested during graph stream.")
                                break # Exit stream loop if shutdown requested

                            for key, value in output.items():
                                log_adapter.debug(f"Graph node '{key}' output: {value}")
                                # Check if the output is from the agent or generate node
                                # and contains the final AIMessage
                                if key == "agent" or key == "generate":
                                    if "messages" in value and value["messages"]:
                                        last_msg = value["messages"][-1]
                                        # Check if it's the final AI response
                                        if isinstance(last_msg, AIMessage):
                                             final_response_content = last_msg.content
                                             final_message_object = last_msg # Store the object

                        if not running: break # Exit main loop if shutdown requested during stream

                        log_adapter.info(f"Graph execution finished. Final response: {final_response_content[:100]}...")

                        response_payload = json.dumps({
                            "thread_id": thread_id,
                            "response": final_response_content,
                            # Serialize the Pydantic model if it exists and has dict() method
                            "message_object": final_message_object.dict() if final_message_object and hasattr(final_message_object, 'dict') else None
                        })
                        await redis_client.publish(output_channel, response_payload)
                        log_adapter.info(f"Published response to Redis channel: {output_channel}")

                    except json.JSONDecodeError:
                        log_adapter.error("Failed to decode JSON message from Redis.")
                        error_payload = json.dumps({"thread_id": thread_id, "error": "Invalid input format."})
                        await redis_client.publish(output_channel, error_payload)
                    except asyncio.CancelledError:
                         log_adapter.info("Graph invocation cancelled.")
                         raise # Re-raise cancellation
                    except Exception as e:
                        log_adapter.error(f"Error processing message: {e}", exc_info=True)
                        # Publish error back
                        error_payload = json.dumps({"thread_id": thread_id, "error": f"Agent error: {e}"})
                        await redis_client.publish(output_channel, error_payload)

            except asyncio.CancelledError:
                log_adapter.info("Redis listener task cancelled.")
                running = False # Ensure loop terminates
                break
            except redis.exceptions.ConnectionError as e:
                 log_adapter.error(f"Redis connection error: {e}. Attempting to reconnect...")
                 await update_status("error_redis", f"Connection failed: {e}")
                 await asyncio.sleep(5) # Wait before next attempt
                 # Try to resubscribe in the next loop iteration if connection recovers
                 if pubsub:
                     try:
                         # Ensure pubsub is still valid before unsub/sub
                         if pubsub.is_connected:
                             await pubsub.unsubscribe(input_channel)
                             await pubsub.subscribe(input_channel)
                             log_adapter.info("Resubscribed after connection error.")
                             await update_status("running") # Back to running if resubscribed
                         else:
                              log_adapter.warning("Pubsub disconnected, cannot resubscribe yet.")
                              # Recreate pubsub object?
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
        if pubsub and pubsub.is_connected:
            try:
                await pubsub.unsubscribe(input_channel)
                await pubsub.close()
                log_adapter.info("Unsubscribed from Redis and closed pubsub connection.")
            except Exception as clean_e:
                 log_adapter.error(f"Error during Redis cleanup: {clean_e}")
        # Final status update
        if running: # If loop exited due to error, not shutdown signal
             await update_status("error_listener_unexpected", "Listener unexpectedly stopped")
        else: # If loop exited due to shutdown signal
             await update_status("stopped")


# --- Main Execution ---
async def main(agent_id: str, config_url: str, redis_url: str):
    """Main function to fetch config, create app, and start listener."""
    log_adapter = setup_logging(agent_id) # Setup logging first
    status_key = f"agent_status:{agent_id}"
    redis_client = None # Initialize

    async def update_status(status: str, error_detail: Optional[str] = None):
        """Helper to update Redis status, even before full client init."""
        temp_redis = None
        try:
            # Use await for async connection
            temp_redis = await redis.from_url(redis_url, decode_responses=True)
            await temp_redis.ping() # Verify connection
            # Use the outer update_redis_status function for consistency
            await update_redis_status(temp_redis, status_key, status, os.getpid(), error_detail, log_adapter)
        except Exception as e:
            log_adapter.error(f"Failed to update initial Redis status to {status}: {e}")
        finally:
            if temp_redis:
                await temp_redis.close()

    listener_task = None # Initialize listener_task

    try:
        # 1. Initial Status Update
        await update_status("initializing")

        # 2. Initialize Redis Client
        log_adapter.info(f"Connecting to Redis at {redis_url}")
        try:
            redis_client = await redis.from_url(redis_url, decode_responses=True)
            await redis_client.ping()
            log_adapter.info("Redis connection successful.")
        except Exception as e:
            log_adapter.error(f"Failed to connect to Redis: {e}", exc_info=True)
            await update_status("error_redis", f"Connection failed: {e}")
            return # Exit if Redis connection fails

        # 3. Fetch Agent Configuration
        log_adapter.info(f"Fetching agent configuration from {config_url}")
        try:
            # Use await fetch_config for async operation if needed, currently sync requests
            agent_config = await fetch_config(config_url, log_adapter) # Assuming fetch_config is async or wrapped
            log_adapter.info("Agent configuration fetched successfully.")
            # TODO: Validate config structure using Pydantic model if available
        except requests.exceptions.RequestException as e:
            log_adapter.error(f"Failed to fetch agent configuration: {e}", exc_info=True)
            await update_status("error_config", f"Fetch failed: {e}")
            return
        except json.JSONDecodeError as e:
            log_adapter.error(f"Failed to decode agent configuration JSON: {e}", exc_info=True)
            await update_status("error_config", f"JSON decode failed: {e}")
            return
        except Exception as e: # Catch potential errors in fetch_config itself
             log_adapter.error(f"Error during config fetch: {e}", exc_info=True)
             await update_status("error_config", f"Fetch error: {e}")
             return


        # 4. Create Agent App (Graph)
        try:
            # Pass redis_client if needed by the factory (e.g., for status updates during creation)
            app = create_agent_app(agent_config, agent_id, redis_client)
            if not app:
                 raise ValueError("create_agent_app returned None")
            log_adapter.info("Agent application created successfully.")
        except Exception as e:
            log_adapter.error(f"Failed to create agent application: {e}", exc_info=True)
            await update_status("error_app_create", f"App creation failed: {e}")
            return

        # 5. Start Redis Listener
        listener_task = asyncio.create_task(redis_listener(app, agent_id, redis_client))

        # Keep main running while listener is active, checking the 'running' flag
        while running and not listener_task.done():
            await asyncio.sleep(0.5)

        # If loop exits, either shutdown was requested or listener task finished/failed
        if listener_task.done():
             try:
                 listener_task.result() # Check for exceptions in the listener task
             except asyncio.CancelledError:
                 log_adapter.info("Listener task was cancelled.")
             except Exception as e:
                 log_adapter.error(f"Listener task finished with error: {e}", exc_info=True)
                 # Status might have been set by listener, but set error here just in case
                 await update_status("error_listener_unexpected", f"Listener task failed: {e}")
        else:
             # Shutdown was requested while listener was running
             log_adapter.info("Shutdown initiated, cancelling listener task...")
             listener_task.cancel()
             try:
                 await listener_task
             except asyncio.CancelledError:
                 log_adapter.info("Listener task successfully cancelled.")
             # Status should be set to 'stopped' by the listener's finally block

    except Exception as e:
        log_adapter.error(f"An unexpected error occurred in main: {e}", exc_info=True)
        # Try to update status if possible, avoid overwriting specific errors
        if redis_client:
             current_status_info = await redis_client.hgetall(status_key)
             current_status = current_status_info.get("status", "unknown")
             if not current_status.startswith("error_"):
                 await update_status("error_unexpected", f"Main loop error: {e}")
    finally:
        if redis_client:
            # Ensure status is stopped if runner exits cleanly after shutdown signal
            if not running:
                 await update_status("stopped")
            await redis_client.close()
            log_adapter.info("Redis client closed.")
        log_adapter.info("Agent runner main loop finished.")


if __name__ == "__main__":
    # Load environment variables first
    load_environment()

    parser = argparse.ArgumentParser(description="Run a configurable agent.")
    parser.add_argument("--agent-id", type=str, required=True, help="Unique ID for this agent instance.")
    parser.add_argument("--config-url", type=str, required=True, help="URL to fetch the agent's JSON configuration.")
    parser.add_argument("--redis-url", type=str, required=True, help="URL for the Redis server.")
    args = parser.parse_args()

    # Setup signal handlers
    signal.signal(signal.SIGINT, shutdown_handler)
    signal.signal(signal.SIGTERM, shutdown_handler)

    # Configure logger with agent_id for the main execution context
    # setup_logging should be called inside main to use the adapter correctly
    # logger = logging.getLogger(__name__) # Get base logger for initial messages

    print(f"Starting agent runner process for ID: {args.agent_id}")
    # logger.info(f"Starting agent runner process for ID: {args.agent_id}") # Use print before logging is fully set up

    try:
        asyncio.run(main(args.agent_id, args.config_url, args.redis_url))
    except KeyboardInterrupt:
         print("KeyboardInterrupt caught in main, exiting.")
         # logger.info("KeyboardInterrupt caught in main, exiting.")
    except Exception as e:
         print(f"Unhandled exception in asyncio.run: {e}")
         # logger.critical(f"Unhandled exception in asyncio.run: {e}", exc_info=True)
         sys.exit(1) # Exit with error code if main fails critically


    print(f"Agent runner {args.agent_id} has shut down.")
    # logger.info(f"Agent runner {args.agent_id} has shut down.")
    sys.exit(0) # Ensure clean exit code 0 on normal shutdown

