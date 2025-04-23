import os
import asyncio
import logging
import json
import argparse
import signal
import sys
from dotenv import load_dotenv
import requests
import redis.asyncio as redis

# Import from sibling modules
from .graph_factory import create_agent_app
from .models import AgentState # Import AgentState if needed here, though graph handles it

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

def handle_signal(sig, frame):
    """Handles termination signals for graceful shutdown."""
    global running
    logger = logging.getLogger(__name__) # Use base logger here
    logger.warning(f"Received signal {sig}, initiating shutdown...")
    running = False

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

async def update_redis_status(r: redis.Redis, status_key: str, status: str, pid: Optional[int] = None, log_adapter: logging.LoggerAdapter = None):
    """Updates the agent's status in Redis."""
    if not log_adapter:
        log_adapter = logging.getLogger(__name__) # Fallback logger
    try:
        mapping = {"status": status}
        if pid is not None:
            mapping["pid"] = pid
        else:
            # Ensure PID is removed if status is stopped/error without PID
             if status in ["stopped", "error_config", "error_app_create", "error_unexpected"]:
                 await r.hdel(status_key, "pid") # Explicitly remove potentially stale PID

        await r.hset(status_key, mapping=mapping)
        log_adapter.debug(f"Updated Redis status ({status_key}): {mapping}")
    except Exception as e:
        log_adapter.error(f"Failed to update Redis status ({status_key}) to {status}: {e}")


async def redis_listener(app, static_state: Dict, agent_id: str, redis_client: redis.Redis, log_adapter: logging.LoggerAdapter):
    """Listens for messages on Redis input channel and runs the agent graph."""
    input_channel = f"agent:{agent_id}:input"
    output_channel = f"agent:{agent_id}:output"
    status_key = f"agent_status:{agent_id}"

    pubsub = None
    try:
        pubsub = redis_client.pubsub()
        await pubsub.subscribe(input_channel)
        log_adapter.info(f"Subscribed to Redis channel: {input_channel}")

        # Set status to running in Redis
        await update_redis_status(redis_client, status_key, "running", os.getpid(), log_adapter)
        log_adapter.info("Agent status set to 'running'")

        while running:
            try:
                # Update last active time periodically
                current_time = asyncio.get_event_loop().time()
                await redis_client.hset(status_key, "last_active", current_time)

                message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
                if message and message.get("type") == "message":
                    log_adapter.info(f"Received message from Redis: {message['data'][:200]}...") # Log truncated data
                    try:
                        data = json.loads(message["data"])
                        user_message = data.get("message")
                        thread_id = data.get("thread_id")
                        user_data = data.get("user_data", {})
                        channel = data.get("channel", "unknown")

                        if not user_message or not thread_id:
                            log_adapter.error("Missing 'message' or 'thread_id' in Redis payload.")
                            continue

                        # Prepare input for the graph, including static state
                        graph_input = {
                            "messages": [("user", user_message)],
                            "user_data": user_data,
                            "channel": channel,
                            # Inject static config elements needed by nodes/edges if not using 'config' param
                            **static_state # Merge static state into the input dictionary
                        }
                        # Config for the checkpointer and potentially passing agent_id
                        invocation_config = {
                            "configurable": {
                                "thread_id": str(thread_id),
                                "agent_id": agent_id # Pass agent_id for logging in nodes/edges
                            }
                        }

                        log_adapter.info(f"Invoking graph for thread_id: {thread_id}")
                        final_response_content = "No response generated." # Default
                        final_message_object = None

                        async for output in app.astream(graph_input, config=invocation_config, stream_mode="updates"):
                            # Log graph progression (optional)
                            # log_adapter.debug(f"Graph update: {output}")
                            # Find the last AIMessage in the output state
                            if isinstance(output, dict):
                                for key, value in output.items():
                                     # Check if the value contains messages and find the last AI message
                                     if isinstance(value, dict) and "messages" in value and value["messages"]:
                                         last_msg = value["messages"][-1]
                                         if hasattr(last_msg, 'content'): # Check if it has content (AIMessage, HumanMessage etc.)
                                             # Store the content of the latest message in the stream
                                             final_response_content = last_msg.content
                                             # Store the object if it's an AIMessage (for potential future use)
                                             # if isinstance(last_msg, AIMessage):
                                             #     final_message_object = last_msg


                        log_adapter.info(f"Graph execution finished. Final response: {final_response_content[:100]}...")

                        # Publish the final response back to Redis
                        response_payload = json.dumps({
                            "thread_id": thread_id,
                            "response": final_response_content,
                        })
                        await redis_client.publish(output_channel, response_payload)
                        log_adapter.info(f"Published response to Redis channel: {output_channel}")

                        # Update last active time after processing
                        await redis_client.hset(status_key, "last_active", asyncio.get_event_loop().time())

                    except json.JSONDecodeError:
                        log_adapter.error(f"Failed to decode JSON from Redis message: {message['data']}")
                    except Exception as e:
                        log_adapter.error(f"Error processing message or running graph: {e}", exc_info=True)
                        # Publish error back to Redis output channel
                        error_payload = json.dumps({
                            "thread_id": data.get("thread_id", "unknown") if 'data' in locals() else "unknown",
                            "error": f"An internal error occurred in the agent: {e}"
                        })
                        try:
                            await redis_client.publish(output_channel, error_payload)
                        except Exception as pub_e:
                             log_adapter.error(f"Failed to publish error back to Redis: {pub_e}")

                # Brief sleep to prevent tight loop when no messages
                await asyncio.sleep(0.1)

            except asyncio.TimeoutError:
                continue # Ignore timeout, just check running flag again
            except redis.exceptions.ConnectionError as e:
                log_adapter.error(f"Redis connection error: {e}. Attempting to reconnect...")
                await update_redis_status(redis_client, status_key, "error_redis_connection", log_adapter=log_adapter)
                await asyncio.sleep(5)
                try:
                    # Re-establish connection and subscription
                    await redis_client.ping()
                    if pubsub: # Resubscribe if pubsub object exists
                         await pubsub.subscribe(input_channel)
                    log_adapter.info("Reconnected to Redis and re-subscribed.")
                    await update_redis_status(redis_client, status_key, "running", os.getpid(), log_adapter) # Set back to running
                except Exception as recon_e:
                    log_adapter.error(f"Failed to reconnect to Redis: {recon_e}")
                    await asyncio.sleep(10) # Longer wait if reconnect fails
            except Exception as e:
                log_adapter.error(f"Redis listener error: {e}", exc_info=True)
                await update_redis_status(redis_client, status_key, "error_listener_unexpected", log_adapter=log_adapter)
                await asyncio.sleep(5) # Pause before retrying on major errors

    except Exception as e:
         # Catch errors during initial subscription or setup
         log_adapter.critical(f"Failed to start Redis listener: {e}", exc_info=True)
         await update_redis_status(redis_client, status_key, "error_listener_setup", log_adapter=log_adapter)
    finally:
        log_adapter.info("Redis listener loop exiting.")
        # Clean up Redis status on exit
        await update_redis_status(redis_client, status_key, "stopped", log_adapter=log_adapter)
        log_adapter.info(f"Set status to 'stopped' for {agent_id} in Redis.")

        if pubsub and pubsub.is_connected:
            try:
                await pubsub.unsubscribe(input_channel)
                await pubsub.close()
                log_adapter.info("Unsubscribed and closed Redis pubsub.")
            except Exception as e:
                log_adapter.error(f"Error closing Redis pubsub: {e}")


async def main(agent_id: str, config_url: str, redis_url: str):
    """Main function for the agent runner process."""
    log_adapter = setup_logging(agent_id)
    log_adapter.info(f"Starting agent runner for agent_id: {agent_id}")

    redis_client = None
    listener_task = None
    app = None
    static_state = None

    try:
        # 1. Connect to Redis early to update status during setup
        log_adapter.info(f"Connecting to Redis at {redis_url}...")
        redis_client = await redis.from_url(redis_url, decode_responses=True)
        await redis_client.ping()
        log_adapter.info("Connected to Redis successfully.")
        status_key = f"agent_status:{agent_id}"
        await update_redis_status(redis_client, status_key, "initializing", os.getpid(), log_adapter)

        # 2. Fetch configuration
        agent_config = await fetch_config(config_url, log_adapter)

        # 3. Create LangGraph App
        log_adapter.info("Creating LangGraph application...")
        app, static_state = create_agent_app(agent_config, agent_id) # Get app and static state
        log_adapter.info("LangGraph application created.")

        # 4. Start Redis Listener Task
        log_adapter.info("Starting Redis listener task...")
        listener_task = asyncio.create_task(
            redis_listener(app, static_state, agent_id, redis_client, log_adapter)
        )

        # Wait for listener task to complete (e.g., on shutdown signal)
        await listener_task
        log_adapter.info("Listener task finished.")

    except (requests.exceptions.RequestException, ConnectionError) as e:
         log_adapter.critical(f"Failed to fetch config or connect: {e}", exc_info=True)
         if redis_client:
             await update_redis_status(redis_client, status_key, "error_config", log_adapter=log_adapter)
    except Exception as e:
        log_adapter.critical(f"Unhandled exception during agent startup or runtime: {e}", exc_info=True)
        if redis_client:
             status_key = f"agent_status:{agent_id}"
             current_status = await redis_client.hget(status_key, "status")
             # Don't overwrite specific error statuses set by listener
             if not current_status or not current_status.startswith("error_"):
                 await update_redis_status(redis_client, status_key, "error_unexpected", log_adapter=log_adapter)
    finally:
        log_adapter.info("Agent runner shutting down...")
        # Ensure listener task is cancelled if main exits unexpectedly
        if listener_task and not listener_task.done():
            log_adapter.info("Cancelling listener task...")
            listener_task.cancel()
            try:
                await listener_task # Wait for cleanup within the listener
            except asyncio.CancelledError:
                 log_adapter.info("Listener task cancelled.")
            except Exception as e:
                 log_adapter.error(f"Error awaiting cancelled listener task: {e}")

        # Cleanup Redis connection
        if redis_client:
            # Final status update (redundant if listener cleaned up, but safe)
            status_key = f"agent_status:{agent_id}"
            await update_redis_status(redis_client, status_key, "stopped", log_adapter=log_adapter)
            await redis_client.close()
            log_adapter.info("Redis connection closed.")
        log_adapter.info(f"Agent runner for {agent_id} finished.")


# --- Entry Point ---
if __name__ == "__main__":
    if not load_environment():
        print("Warning: .env file not loaded. Some features might not work without API keys/URLs.")

    parser = argparse.ArgumentParser(description="Configurable Agent Runner")
    parser.add_argument("--agent-id", required=True, help="Unique ID for this agent instance")
    parser.add_argument("--config-url", required=True, help="URL to fetch agent configuration JSON from the manager")
    parser.add_argument("--redis-url", required=True, help="URL for Redis server (e.g., redis://localhost:6379)")
    args = parser.parse_args()

    # Setup signal handlers for graceful shutdown
    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    try:
        asyncio.run(main(args.agent_id, args.config_url, args.redis_url))
    except KeyboardInterrupt:
        # Should be caught by handle_signal, but handle here just in case
        print("KeyboardInterrupt received in __main__, shutting down.")
    except Exception as e:
        # Use root logger here as adapter might not be set if error is very early
        logging.critical(f"Unhandled exception in runner __main__: {e}", exc_info=True)
        sys.exit(1) # Exit with error code

    print("Agent runner process exited.")
    sys.exit(0) # Ensure clean exit code 0 on normal shutdown
import os
import asyncio
import logging
import json
import argparse
from dotenv import load_dotenv
import requests
import redis.asyncio as redis
from pydantic import BaseModel, Field, FieldValidationInfo, field_validator
from typing import Annotated, Any, Dict, Literal, Sequence, TypedDict, List, Optional
from qdrant_client import QdrantClient, models
from langchain_qdrant import QdrantVectorStore
# from langchain import hub # Not used directly now
from langchain_openai import OpenAIEmbeddings, ChatOpenAI
from langchain_core.messages import BaseMessage, HumanMessage, AIMessage, ToolMessage
from langchain_core.prompts import PromptTemplate
from langchain_core.tools import tool, Tool
from langchain.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain.tools.retriever import create_retriever_tool
from langchain_community.tools.tavily_search import TavilySearchResults # Removed TavilySearchAPIWrapper as it wasn't used directly
from langgraph.graph.message import add_messages
from langgraph.graph import END, StateGraph, START
from langgraph.prebuilt import ToolNode, tools_condition, InjectedState
from langgraph.checkpoint.memory import MemorySaver
import signal # For graceful shutdown
from functools import partial
import time # For last_active updates

# --- Load Environment Variables ---
# Load secrets like API keys, Qdrant URL, Redis URL etc.
# Load from the specified path in the user's home directory structure
# Use relative path from project root for consistency
dotenv_path = os.path.join(os.path.dirname(__file__), '..', '.env') # Assumes runner.py is in agent_runner/
if os.path.exists(dotenv_path):
    load_dotenv(dotenv_path=dotenv_path)
    # print(f"Loaded environment variables from {dotenv_path}") # Debug print
else:
    print(f"Warning: .env file not found at {dotenv_path}")


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(agent_id)s - %(message)s", # Add agent_id to logs
)
logger = logging.getLogger(__name__)

# Global flag for graceful shutdown
running = True

# --- Agent State ---
class AgentState(TypedDict):
    messages: Annotated[Sequence[BaseMessage], add_messages]
    documents: List[str]
    question: str # The current question being processed (might be rewritten)
    original_question: str # The initial question from the user for this turn
    rewrite_count: int
    channel: str # e.g., 'web', 'telegram'
    user_data: Dict[str, any] # Info about the user if available

# --- Tool Definitions ---
# Keep predefined tools like auth_tool, get_bonus_points, get_user_info_tool for now,
# but the goal is to replace them with dynamic API tools if configured.

@tool
def auth_tool() -> str:
    """
    Сall authorization function

    Returns:
        str: Trigger to call external authorization function.
    """
    return "необходима авторизация. Допиши в ответе: [AUTH_REQUIRED]"

@tool
def get_bonus_points(state: Annotated[dict, InjectedState]) -> str:
    """
    Getting the user's bonus points balance

    Args:
        state (user_data): user data (phone number).

    Returns:
        str: The number of bonus points on the user's balance that can be spent or accumulated.
    """
    # Get logger adapter from state if passed, otherwise use default logger
    log_adapter = state.get("log_adapter", logger)

    user_data = state.get("user_data", {})
    if user_data.get("is_authenticated"):
        user_phone = user_data.get("phone_number")
        # Ensure phone number format if necessary
        if not user_phone:
            return "Номер телефона пользователя не найден для проверки баллов."
        try:
            # Consider making URL configurable
            url = f"http://airsoft-rus.ru/obmen_rus/bals.php?type=info&tel={user_phone}"
            response = requests.get(url, timeout=10) # Add timeout
            response.raise_for_status()
            # Basic check if response looks like a number
            if response.text.strip().isdigit():
                 return f"Количество бонусных баллов на балансе: {response.text}"
            else:
                 # Handle cases where the API might return error messages as text
                 log_adapter.warning(f"Bonus points API returned non-numeric text: {response.text}")
                 return f"Не удалось получить баланс бонусных баллов. Ответ API: {response.text}"

        except requests.exceptions.Timeout:
             log_adapter.error("Timeout error fetching bonus points.")
             return "Ошибка: Не удалось связаться с сервисом бонусных баллов (таймаут)."
        except requests.exceptions.RequestException as e:
            log_adapter.error(f"Request exception fetching bonus points: {e}")
            return f"Ошибка: Не удалось получить бонусные баллы ({e})."
    return "Необходима авторизация для просмотра бонусных баллов. Запусти авторизацию."


@tool
def get_user_info_tool(state: Annotated[dict, InjectedState]) -> str:
    """
    Obtaining information about the user, his phone number, first name,
    last name, authorization status and the channel through which the user communicates.

    Args:
        state (chanel): the channel (messenger) through which the user communicates.
        state (user_data): user data (phone number, first name, last name, authorization status).

    Returns:
        dict: information about the user.
    """
    channel = state.get("channel", "unknown")
    user_data = state.get("user_data", {})
    if user_data.get("is_authenticated"):
        user_fname = user_data.get("first_name", "N/A")
        user_lname = user_data.get("last_name", "N/A")
        user_phone = user_data.get("phone_number", "N/A")
        user_id = user_data.get("user_id", "N/A")

        return f"""Информация о пользователе:\n
                First Name : {user_fname}\n
                Last Name: {user_lname}\n
                User phone number: {user_phone}\n
                User ID: {user_id}\n
                User messenger: {channel}"""
    else:
        return f"User messenger: {channel}. Информация о пользователе недоступна без авторизации. Запусти авторизацию."


# --- Generic API Request Function ---
def make_api_request(
    # Arguments passed from the LLM call (tool input)
    input_args: Optional[Dict[str, Any]] = None,
    # Arguments bound from the tool configuration using functools.partial
    api_config: Dict[str, Any] = None,
    agent_state: Optional[Dict[str, Any]] = None, # Access to agent state if needed
    log_adapter: Optional[logging.LoggerAdapter] = None
) -> str:
    """
    Makes an HTTP request based on the provided API configuration and input arguments.

    Args:
        input_args: Dictionary of arguments provided by the LLM when calling the tool.
                    These often correspond to query parameters or request body fields.
        api_config: Dictionary containing the configuration for this specific API tool
                    (URL, method, headers, parameter mapping, etc.). Bound via partial.
        agent_state: The current state of the agent graph (optional).
        log_adapter: Logger adapter for logging (optional).

    Returns:
        A string containing the API response text or an error message.
    """
    if not api_config:
        return "Error: API tool called without configuration."
    # Use default logger if adapter not provided (should be passed via partial)
    effective_logger = log_adapter if log_adapter else logger

    url = api_config.get("url")
    method = api_config.get("method", "GET").upper()
    headers = api_config.get("headers", {})
    params_config = api_config.get("parameters", []) # Config for expected params
    body_config = api_config.get("body", {}) # Config for request body (e.g., for POST)

    if not url:
        return f"Error: API configuration for tool '{api_config.get('name', 'unnamed')}' is missing 'url'."

    effective_logger.info(f"Executing API tool '{api_config.get('name', 'unnamed')}'")
    effective_logger.debug(f"API Config: {api_config}")
    effective_logger.debug(f"Input Args: {input_args}")

    query_params = {}
    request_body = None
    request_files = None # For file uploads, if needed later

    # --- Prepare Headers ---
    # Example: Add authentication from agent state if needed
    # if agent_state and agent_state.get("user_data", {}).get("api_key"):
    #     headers["Authorization"] = f"Bearer {agent_state['user_data']['api_key']}"

    # --- Prepare Query Parameters and Request Body ---
    processed_args = input_args or {}

    # Map input_args to query parameters based on params_config
    for param_conf in params_config:
        param_name = param_conf.get("name")
        param_in = param_conf.get("in", "query") # Default to query parameter
        required = param_conf.get("required", False)
        schema_type = param_conf.get("schema", {}).get("type", "string") # Basic type check

        if param_name in processed_args:
            value = processed_args[param_name]
            # TODO: Add type validation/conversion based on schema_type
            if param_in == "query":
                query_params[param_name] = value
            elif param_in == "path":
                # Replace placeholders in URL, e.g., /users/{userId}
                url = url.replace(f"{{{param_name}}}", str(value))
            elif param_in == "header":
                headers[param_name] = str(value)
            # Add other 'in' types like 'cookie' if needed
        elif required:
            return f"Error: Missing required parameter '{param_name}' for API tool '{api_config.get('name', 'unnamed')}'."

    # Prepare request body (only for methods like POST, PUT, PATCH)
    if method in ["POST", "PUT", "PATCH"] and body_config:
        # Assuming body_config describes a JSON body for now
        # Look for schema under application/json content type
        json_schema = body_config.get("content", {}).get("application/json", {}).get("schema")
        if json_schema:
            request_body = {}
            # Extract properties based on schema (simplified example)
            properties = json_schema.get("properties", {})
            required_body_fields = json_schema.get("required", [])

            for prop_name, prop_schema in properties.items():
                if prop_name in processed_args:
                    request_body[prop_name] = processed_args[prop_name]
                elif prop_name in required_body_fields:
                     return f"Error: Missing required field '{prop_name}' in request body for API tool '{api_config.get('name', 'unnamed')}'."

            # Set default content type if sending JSON body
            if "Content-Type" not in headers:
                headers["Content-Type"] = "application/json"
        else:
            # Handle other content types (form-data, etc.) if needed
            pass

    # --- Make Request ---
    try:
        effective_logger.info(f"Making {method} request to {url}")
        effective_logger.debug(f"Headers: {headers}")
        effective_logger.debug(f"Query Params: {query_params}")
        effective_logger.debug(f"Request Body: {request_body}")

        response = requests.request(
            method=method,
            url=url,
            headers=headers,
            params=query_params,
            json=request_body if headers.get("Content-Type") == "application/json" else None,
            data=request_body if headers.get("Content-Type") != "application/json" else None, # Handle other body types if needed
            # files=request_files, # Add files if needed
            timeout=15 # Add a reasonable timeout
        )
        response.raise_for_status() # Raise HTTPError for bad responses (4xx or 5xx)

        # Try to return JSON response if possible, otherwise text
        try:
            json_response = response.json()
            # Convert JSON to string for Langchain tool output
            return json.dumps(json_response, ensure_ascii=False, indent=2)
        except json.JSONDecodeError:
            return response.text # Return raw text if not JSON

    except requests.exceptions.Timeout:
        effective_logger.error(f"Timeout error making {method} request to {url}")
        return f"Error: API request timed out for tool '{api_config.get('name', 'unnamed')}'."
    except requests.exceptions.HTTPError as e:
         effective_logger.error(f"HTTP error making {method} request to {url}: {e.response.status_code} {e.response.text}")
         return f"Error: API request failed for tool '{api_config.get('name', 'unnamed')}' with status {e.response.status_code}. Response: {e.response.text}"
    except requests.exceptions.RequestException as e:
        effective_logger.error(f"Request exception making {method} request to {url}: {e}")
        return f"Error: Failed to make API request for tool '{api_config.get('name', 'unnamed')}': {e}."
    except Exception as e:
        effective_logger.error(f"Unexpected error in API tool '{api_config.get('name', 'unnamed')}': {e}", exc_info=True)
        return f"Error: An unexpected error occurred in API tool '{api_config.get('name', 'unnamed')}': {e}."


# --- Agent Factory Function (Modified) ---
def create_agent_app(agent_config: Dict, agent_id: str, redis_client: redis.Redis):
    """Creates the LangGraph application for a given agent configuration."""
    log_adapter = logging.LoggerAdapter(logger, {'agent_id': agent_id})
    status_key = f"agent_status:{agent_id}"

    async def update_status(status: str, error_detail: Optional[str] = None):
        """Helper to update Redis status."""
        mapping = {"status": status, "pid": os.getpid()}
        if error_detail:
            mapping["error_detail"] = error_detail
        try:
            await redis_client.hset(status_key, mapping=mapping)
            log_adapter.info(f"Status updated to: {status}")
        except Exception as e:
            log_adapter.error(f"Failed to update Redis status to {status}: {e}")

    log_adapter.info("Creating agent graph...")
    # Validate config structure
    if not isinstance(agent_config, dict) or "config" not in agent_config:
         log_adapter.error("Invalid agent configuration structure: 'config' key missing.")
         raise ValueError("Invalid agent configuration: 'config' key missing.")

    # Expecting the structure returned by /config endpoint
    # which should be AgentConfigOutput.model_dump()
    config_data = agent_config.get("config", {}) # This is AgentConfigStructure model dump

    if not config_data:
         log_adapter.error("Agent configuration is missing 'config' data.")
         raise ValueError("Invalid agent configuration: missing 'config' data")

    # Extract settings (adjust based on actual structure from API)
    # Assuming the API returns the structure like AgentConfigOutput model dump
    # where config_data = agent_config['config']
    model_settings = config_data.get("model", {})
    tool_settings = config_data.get("tools", []) # List of tool configs
    system_prompt_template = config_data.get("systemPrompt", "You are a helpful AI assistant.") # Default prompt

    # --- Model Configuration ---
    model_id = model_settings.get("modelId", "gpt-4o-mini")
    temperature = model_settings.get("temperature", 0.2)
    limit_to_kb = model_settings.get("limitToKnowledgeBase", False) # Not used currently in prompt logic
    answer_in_user_lang = model_settings.get("answerInUserLanguage", True) # Not used currently in prompt logic
    use_context_memory = model_settings.get("useContextMemory", True) # Handled by LangGraph memory

    log_adapter.info(f"Model: {model_id}, Temperature: {temperature}")

    # --- Tool Configuration ---
    configured_tools = []
    safe_tools_list = [] # Tools that don't need grading (API, WebSearch, predefined)
    datastore_tool_list = [] # Retriever tools
    datastore_tool_names = set()
    max_rewrites = 3 # Default

    # Get max_rewrites from config, default to 3
    # Look for it within a retriever tool's settings or a general setting
    retriever_config = next((t for t in tool_settings if t.get("type") == "knowledgeBase"), None)
    if retriever_config:
        retriever_settings = retriever_config.get("settings", {})
        max_rewrites = retriever_settings.get("rewriteAttempts", max_rewrites)
    log_adapter.info(f"Using max_rewrites: {max_rewrites}")

    # --- Knowledge Base / Retriever Tools ---
    kb_configs = [t for t in tool_settings if t.get("type") == "knowledgeBase"]
    if kb_configs:
        log_adapter.info(f"Configuring {len(kb_configs)} Knowledge Base tool(s)...")
        # Currently supports only one KB tool, take the first one
        kb_config = kb_configs[0]
        kb_settings = kb_config.get("settings", {})
        kb_id = kb_config.get("id", "knowledge_base_retriever") # Use ID from config
        kb_name = kb_config.get("name", "Knowledge Base Search") # Name for LLM
        kb_description = kb_settings.get("description", "Searches the knowledge base for relevant information.")
        search_limit = kb_settings.get("searchLimit", 3)
        # Qdrant filter needs client_id and datasource_id from the config
        # These should ideally be part of the agent's core config, not tool settings?
        # For now, assume they might be in kb_settings or agent_config root
        client_id = agent_config.get("userId", "unknown_client") # Map userId to client_id?
        datasource_id = kb_settings.get("datasourceId", "unknown_datasource") # Need this in config

        qdrant_url = os.getenv("QDRANT_URL")
        qdrant_collection = os.getenv("QDRANT_COLLECTION")

        if not qdrant_url or not qdrant_collection:
             log_adapter.warning("QDRANT_URL or QDRANT_COLLECTION not set. Knowledge base tool disabled.")
        else:
            try:
                embeddings = OpenAIEmbeddings()
                qdrant_client = QdrantClient(qdrant_url)
                vectorstore = QdrantVectorStore(
                    client=qdrant_client,
                    collection_name=qdrant_collection,
                    embedding=embeddings,
                )
                # Define filter based on config
                qdrant_filter = models.Filter(
                    must=[
                        models.FieldCondition(key="metadata.client_id", match=models.MatchValue(value=client_id)),
                        models.FieldCondition(key="metadata.datasource_id", match=models.MatchValue(value=datasource_id)),
                    ]
                )
                retriever = vectorstore.as_retriever(
                    search_kwargs={"k": search_limit, "filter": qdrant_filter}
                )
                retriever_tool = create_retriever_tool(
                    retriever,
                    kb_id, # Use ID from config
                    kb_description, # Use description from config
                    document_separator="\n---RETRIEVER_DOC---\n",
                )
                datastore_tool_list.append(retriever_tool)
                datastore_tool_names.add(kb_id)
                log_adapter.info(f"Configured Knowledge Base tool '{kb_name}' (ID: {kb_id})")
            except Exception as e:
                log_adapter.error(f"Failed to initialize Qdrant/Retriever tool: {e}", exc_info=True)
                # Optionally update status to error
                # await update_status("error_tool_config", f"Qdrant init failed: {e}")

    # --- Web Search Tool ---
    web_search_configs = [t for t in tool_settings if t.get("type") == "webSearch"]
    if web_search_configs:
        ws_config = web_search_configs[0] # Assume one web search tool
        ws_settings = ws_config.get("settings", {})
        ws_id = ws_config.get("id", "web_search") # Use ID from config
        ws_name = ws_config.get("name", "Web Search") # Name for LLM
        ws_description = ws_settings.get("description", "Performs a web search for recent information.")
        search_limit = ws_settings.get("searchLimit", 3)

        tavily_api_key = os.getenv("TAVILY_API_KEY")
        if not tavily_api_key:
            log_adapter.warning("TAVILY_API_KEY not set. Web search tool disabled.")
        else:
            try:
                web_search_tool = TavilySearchResults(
                    max_results=search_limit,
                    name=ws_id, # Use ID from config
                    description=ws_description, # Use description from config
                    # api_key=tavily_api_key # Handled by env var by default
                )
                safe_tools_list.append(web_search_tool)
                log_adapter.info(f"Configured Web Search tool '{ws_name}' (ID: {ws_id})")
            except Exception as e:
                log_adapter.error(f"Failed to create Web Search tool: {e}", exc_info=True)


    # --- Dynamic API Request Tools ---
    api_request_configs = [t for t in tool_settings if t.get("type") == "apiRequest"]
    for api_config in api_request_configs:
        tool_id = api_config.get("id")
        tool_name = api_config.get("name") # This is often the description for the LLM
        api_settings = api_config.get("settings", {}) # URL, method, params etc. are here
        tool_description = api_settings.get("description", f"Calls the {tool_name} API.") # Use settings.description

        if not tool_id or not tool_name or not api_settings.get("url"):
            log_adapter.warning(f"Skipping apiRequest tool due to missing id, name, or settings.url: {api_config}")
            continue

        # --- Create the partial function ---
        # Bind the static API configuration and logger to the generic request function
        configured_request_func = partial(
            make_api_request,
            api_config=api_settings, # Pass the specific settings for this tool
            log_adapter=log_adapter
            # agent_state can be accessed via InjectedState if needed, or passed here if static
        )

        # --- Define input schema (optional but recommended) ---
        # TODO: Dynamically generate Pydantic model from api_settings["parameters"] / ["body"]
        # For now, assume input is a dict
        args_schema = None # type: ignore

        # --- Create the Langchain Tool ---
        try:
            api_tool = Tool.from_function(
                func=configured_request_func,
                name=tool_id, # Use ID as the internal tool name
                description=tool_description, # Use settings.description for LLM
                args_schema=args_schema, # Add schema later
                # return_direct=False # Default
            )
            safe_tools_list.append(api_tool)
            log_adapter.info(f"Configured dynamic API tool '{tool_name}' (ID: {tool_id})")
        except Exception as e:
            log_adapter.error(f"Failed to create dynamic API tool '{tool_name}' (ID: {tool_id}): {e}", exc_info=True)


    # --- Add Predefined Tools (if not dynamically configured) ---
    predefined_safe_tools_map = {
        "auth_tool": auth_tool,
        "get_user_info_tool": get_user_info_tool,
        "get_bonus_points": get_bonus_points
    }
    configured_dynamic_ids = {t.name for t in safe_tools_list if isinstance(t, Tool) or isinstance(t, TavilySearchResults)} # Get IDs of dynamically created/configured tools

    for tool_key, tool_instance in predefined_safe_tools_map.items():
        # Check if a dynamic tool with the same name/ID was already added
        if tool_instance.name not in configured_dynamic_ids:
             # Also check if this specific tool instance is already in the list
             is_already_added = any(t.name == tool_instance.name for t in safe_tools_list)
             if not is_already_added:
                 safe_tools_list.append(tool_instance)
                 log_adapter.info(f"Added predefined tool: {tool_instance.name}")
        else:
             log_adapter.info(f"Predefined tool '{tool_instance.name}' was already configured dynamically or via other settings. Skipping duplicate.")


    if safe_tools_list:
        configured_tools.extend(safe_tools_list)
    if datastore_tool_list:
        configured_tools.extend(datastore_tool_list)

    # --- System Prompt Construction ---
    # Use the template from config
    final_system_prompt = system_prompt_template
    log_adapter.debug(f"Using system prompt: {final_system_prompt}")

    # --- Nodes Definition ---
    async def agent_node(state: AgentState, config: dict):
        """Agent node modified to include logger adapter in state for tools."""
        log_adapter.info("---CALL AGENT---")
        messages = state["messages"]
        prompt = ChatPromptTemplate.from_messages([
            ("system", final_system_prompt),
            MessagesPlaceholder(variable_name="messages")
        ])
        model = ChatOpenAI(temperature=temperature, streaming=True, model=model_id)
        if configured_tools:
             valid_tools = [t for t in configured_tools if t is not None]
             if valid_tools:
                 model = model.bind_tools(valid_tools)
             else:
                  log_adapter.warning("Agent called but no valid tools were configured after filtering.")
        else:
             log_adapter.warning("Agent called but no tools are configured.")

        chain = prompt | model
        # Inject logger adapter into state for tools like get_bonus_points
        state_with_logger = {**state, "log_adapter": log_adapter}
        response = await chain.ainvoke(state_with_logger, config=config)
        return {"messages": [response]}

    async def grade_documents_node(state: AgentState):
        """Grade documents node."""
        log_adapter.info("---CHECK RELEVANCE---")
        class grade(BaseModel):
            """Binary score for relevance check."""
            binary_score: str = Field(description="Relevance score 'yes' or 'no'")

        messages = state["messages"]
        current_question = state["question"]
        log_adapter.info(f"Grading documents for question: {current_question}")

        last_message = messages[-1] if messages else None
        if not isinstance(last_message, ToolMessage) or last_message.name not in datastore_tool_names:
             log_adapter.warning(f"Grade documents called, but last message is not a valid ToolMessage from retriever. Message: {last_message}")
             return {"documents": [], "question": current_question}

        docs = last_message.content.split("\n---RETRIEVER_DOC---\n")
        if not docs or all(not d for d in docs):
             log_adapter.info("No documents retrieved to grade.")
             return {"documents": [], "question": current_question}

        prompt = PromptTemplate(
            template="""Вы оцениваете релевантность извлеченного документа для вопроса пользователя. \n
                    Вот извлеченный документ: \n\n {context} \n\n
                    Вот вопрос пользователя: {question} \n
                    Если документ содержит ключевые слова или семантическое значение, связанные с вопросом пользователя, оцените его как релевантный. \n
                    Дайте двоичную оценку 'yes' или 'no', чтобы указать, соответствует ли документ вопросу.""",
            input_variables=["context", "question"],
        )
        model = ChatOpenAI(temperature=0, model=model_id)
        llm_with_tool = model.with_structured_output(grade)

        async def process_doc(doc):
            chain = prompt | llm_with_tool
            try:
                scored_result = await chain.ainvoke({"question": current_question, "context": doc})
                log_adapter.debug(f"Doc: '{doc[:50]}...' Score: {scored_result.binary_score}")
                return doc, scored_result.binary_score
            except Exception as e:
                log_adapter.error(f"Error processing document for grading: {e}", exc_info=True)
                return doc, "no"

        filtered_docs = []
        tasks = [process_doc(d) for d in docs if d]
        results = await asyncio.gather(*tasks)
        filtered_docs = [doc for doc, score in results if score == "yes"]
        log_adapter.info(f"Found {len(filtered_docs)} relevant documents out of {len(docs)}.")

        return {"documents": filtered_docs, "question": current_question}

    async def rewrite_node(state: AgentState):
        """Rewrite node."""
        log_adapter.info("---TRANSFORM QUERY---")
        original_question = state["original_question"]
        messages = state["messages"]
        rewrite_count = state.get("rewrite_count", 0)

        log_adapter.info(f"Rewrite attempt {rewrite_count + 1}/{max_rewrites}")

        if rewrite_count < max_rewrites:
            log_adapter.info(f"Rewriting original question: {original_question}")
            prompt_msg = HumanMessage(
                content=f"""You are an expert at rephrasing questions for better retrieval.
Look at the original question and the chat history. The previous retrieval attempt failed to find relevant documents.
Rephrase the original question to be more specific or clearer, considering the context of the conversation.
Do not add conversational filler, just output the rephrased question.

Chat History:
{messages}

Original Question: {original_question}

Rephrased Question:"""
            )

            model = ChatOpenAI(temperature=0, model=model_id, streaming=False)
            response = await model.ainvoke([prompt_msg])
            rewritten_question = response.content.strip()
            log_adapter.info(f"Rewritten question: {rewritten_question}")

            trigger_message = HumanMessage(content=f"Переформулируй запрос так: {rewritten_question}")

            return {
                "messages": [trigger_message],
                "question": rewritten_question,
                "rewrite_count": rewrite_count + 1
            }
        else:
            log_adapter.warning(f"Max rewrites ({max_rewrites}) reached for original question: {original_question}")
            no_answer_message = AIMessage(content="К сожалению, я не смог найти релевантную информацию по вашему запросу даже после его уточнения. Попробуйте задать вопрос по-другому.")
            return {
                "messages": [no_answer_message],
                "rewrite_count": 0 # Reset count for next turn
            }

    async def generate_node(state: AgentState):
        """Generate node."""
        log_adapter.info("---GENERATE---")
        messages = state["messages"]
        current_question = state["question"]
        documents = state["documents"]

        if not documents:
             log_adapter.warning("Generate called with no relevant documents.")
             if messages and isinstance(messages[-1], AIMessage) and "не смог найти релевантную информацию" in messages[-1].content:
                  log_adapter.info("Passing through 'max rewrites reached' message.")
                  return {"messages": [messages[-1]]}
             else:
                  no_answer_response = AIMessage(content="К сожалению, я не смог найти информацию по вашему запросу в доступных источниках.")
                  return {"messages": [no_answer_response]}

        log_adapter.info(f"Generating answer for question: {current_question} using {len(documents)} documents.")
        documents_str = "\n\n".join(documents)

        prompt = PromptTemplate(
            template="""Ты помощник для задач с ответами на вопросы. Используйте следующие фрагменты извлеченного контекста, чтобы ответить на вопрос.
            Если у тебя нет ответа на вопрос, просто скажи что у тебя нет данных для ответа на этот вопрос, предложи переформулировать фопрос.
            Старайся отвечать кратко и содержательно.\n
                Вопрос: {question} \n
                Контекст: {context} \n
                Ответ:""",
            input_variables=["context", "question"],
        )
        llm = ChatOpenAI(model_name=model_id, temperature=temperature, streaming=True)
        rag_chain = prompt | llm
        response = await rag_chain.ainvoke({"context": documents_str, "question": current_question})
        return {"messages": [response]}

    # --- Edges Definition ---
    async def decide_to_generate(state: AgentState) -> Literal["generate", "rewrite"]:
        """Decides whether to generate an answer or rewrite the question."""
        log_adapter.info("---ASSESS GRADED DOCUMENTS---")
        filtered_documents = state["documents"]
        rewrite_count = state.get("rewrite_count", 0)

        if not filtered_documents:
            if rewrite_count < max_rewrites:
                 log_adapter.info("---DECISION: NO RELEVANT DOCUMENTS, REWRITE---")
                 return "rewrite"
            else:
                 log_adapter.warning(f"---DECISION: NO RELEVANT DOCUMENTS AND MAX REWRITES ({max_rewrites}) REACHED, GENERATE (NO DOCS)---")
                 return "generate"
        else:
            log_adapter.info("---DECISION: RELEVANT DOCUMENTS FOUND, GENERATE---")
            return "generate"

    def route_tools(state: AgentState) -> Literal["retrieve", "safe_tools", "__end__"]:
        """Routes to the appropriate tool node or ends if no tool is called."""
        log_adapter.info("---ROUTE TOOLS---")
        next_node = tools_condition(state)
        if next_node == END:
            log_adapter.info("---DECISION: NO TOOLS CALLED, END---")
            return END
        messages = state["messages"]
        last_message = messages[-1]
        if not hasattr(last_message, "tool_calls") or not last_message.tool_calls:
             log_adapter.warning("Routing tools, but last message has no tool calls. Ending.")
             return END

        # Simple routing based on the first tool call
        first_tool_call = last_message.tool_calls[0]
        tool_name = first_tool_call["name"]
        log_adapter.info(f"Tool call detected: {tool_name}")

        if tool_name in datastore_tool_names:
            log_adapter.info(f"---DECISION: ROUTE TO RETRIEVE ({tool_name})---")
            return "retrieve"
        # Check if the tool exists in the safe_tools_list
        elif any(t.name == tool_name for t in safe_tools_list):
            log_adapter.info(f"---DECISION: ROUTE TO SAFE TOOLS ({tool_name})---")
            return "safe_tools"
        else:
             log_adapter.warning(f"Tool call '{tool_name}' does not match any configured tool node. Ending.")
             return END


    # --- Graph Definition ---
    workflow = StateGraph(AgentState)

    workflow.add_node("agent", agent_node)
    if datastore_tool_list:
         retrieve_node = ToolNode(datastore_tool_list, name="retrieve_node")
         workflow.add_node("retrieve", retrieve_node)
         workflow.add_node("grade_documents", grade_documents_node)
         workflow.add_node("rewrite", rewrite_node)
         workflow.add_node("generate", generate_node)
    else:
         log_adapter.info("No datastore tools configured. Retrieval/Grading/Rewrite/Generate nodes skipped.")

    if safe_tools_list:
         valid_safe_tools = [t for t in safe_tools_list if t is not None]
         if valid_safe_tools:
             safe_tools_node = ToolNode(valid_safe_tools, name="safe_tools_node")
             workflow.add_node("safe_tools", safe_tools_node)
         else:
              log_adapter.info("No valid safe tools configured after filtering. Safe_tools node skipped.")
    else:
         log_adapter.info("No safe tools configured. Safe_tools node skipped.")

    # Define edges
    workflow.add_edge(START, "agent")

    if "safe_tools" in workflow.nodes:
        workflow.add_edge("safe_tools", "agent")

    if "retrieve" in workflow.nodes:
        workflow.add_edge("retrieve", "grade_documents")
        workflow.add_edge("rewrite", "agent") # After rewrite, call agent again
        workflow.add_edge("generate", END)

        workflow.add_conditional_edges(
            "grade_documents",
            decide_to_generate,
            {"rewrite": "rewrite", "generate": "generate"},
        )

    # Conditional edge for agent routing
    possible_routes = {}
    if "safe_tools" in workflow.nodes:
        possible_routes["safe_tools"] = "safe_tools"
    if "retrieve" in workflow.nodes:
        possible_routes["retrieve"] = "retrieve"
    possible_routes[END] = END

    if "safe_tools" in workflow.nodes or "retrieve" in workflow.nodes:
        workflow.add_conditional_edges(
            "agent",
            route_tools,
            possible_routes
        )
    else:
         # If no tools configured, agent output goes directly to END
         workflow.add_edge("agent", END)


    # --- Compile ---
    memory = MemorySaver()
    try:
        app = workflow.compile(checkpointer=memory)
        log_adapter.info("Agent graph compiled successfully.")
        # await update_status("initializing") # Update status after successful compile
        return app
    except Exception as e:
        log_adapter.error(f"Failed to compile agent graph: {e}", exc_info=True)
        # await update_status("error_app_create", f"Graph compile failed: {e}")
        raise # Re-raise exception to prevent runner from starting


# --- Redis Communication & Main Loop ---

async def redis_listener(app, agent_id: str, redis_client: redis.Redis):
    """Listens to Redis input channel, processes messages, and publishes output."""
    log_adapter = logging.LoggerAdapter(logger, {'agent_id': agent_id})
    input_channel = f"agent:{agent_id}:input"
    output_channel = f"agent:{agent_id}:output"
    status_key = f"agent_status:{agent_id}"

    pubsub = None # Initialize pubsub to None

    async def update_status(status: str, error_detail: Optional[str] = None):
        """Helper to update Redis status."""
        mapping = {"status": status, "pid": os.getpid()}
        if error_detail:
            mapping["error_detail"] = error_detail
        try:
            await redis_client.hset(status_key, mapping=mapping)
            log_adapter.info(f"Status updated to: {status}")
        except Exception as e:
            log_adapter.error(f"Failed to update Redis status to {status}: {e}")

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
                    log_adapter.info(f"Received message from Redis: {message['data']}")
                    # Update last active time immediately on message
                    await redis_client.hset(status_key, "last_active", time.time())
                    last_active_update_time = time.time()

                    try:
                        data = json.loads(message["data"])
                        user_message = data.get("message")
                        thread_id = data.get("thread_id")
                        user_data = data.get("user_data", {})
                        channel = data.get("channel", "unknown")

                        if not user_message or not thread_id:
                             log_adapter.error("Missing 'message' or 'thread_id' in Redis payload.")
                             continue

                        graph_input = {
                            "messages": [HumanMessage(content=user_message)], # Ensure it's a BaseMessage
                            "user_data": user_data,
                            "channel": channel,
                            "original_question": user_message,
                            "question": user_message,
                            "rewrite_count": 0,
                            "documents": []
                        }
                        config = {"configurable": {"thread_id": str(thread_id)}}

                        log_adapter.info(f"Invoking graph for thread_id: {thread_id}")
                        final_response_content = "No response generated."
                        final_message_object = None

                        async for output in app.astream(graph_input, config, stream_mode="updates"):
                            # Check for cancellation request during streaming
                            if not running:
                                log_adapter.warning("Shutdown requested during graph stream.")
                                break # Exit stream loop if shutdown requested

                            for key, value in output.items():
                                log_adapter.debug(f"Graph node '{key}' output: {value}")
                                if key == "agent" or key == "generate":
                                    if "messages" in value and value["messages"]:
                                        last_msg = value["messages"][-1]
                                        if isinstance(last_msg, AIMessage):
                                             final_response_content = last_msg.content
                                             final_message_object = last_msg

                        if not running: break # Exit main loop if shutdown requested during stream

                        log_adapter.info(f"Graph execution finished. Final response: {final_response_content}")

                        response_payload = json.dumps({
                            "thread_id": thread_id,
                            "response": final_response_content,
                            # Serialize the Pydantic model if it exists
                            "message_object": final_message_object.dict() if final_message_object else None
                        })
                        await redis_client.publish(output_channel, response_payload)
                        log_adapter.info(f"Published response to Redis channel: {output_channel}")

                    except json.JSONDecodeError:
                        log_adapter.error("Failed to decode JSON message from Redis.")
                    except asyncio.CancelledError:
                         log_adapter.info("Graph invocation cancelled.")
                         raise # Re-raise cancellation
                    except Exception as e:
                        log_adapter.error(f"Error processing message: {e}", exc_info=True)
                        # Publish error back?
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
                         await pubsub.unsubscribe(input_channel)
                         await pubsub.subscribe(input_channel)
                         log_adapter.info("Resubscribed after connection error.")
                         await update_status("running") # Back to running if resubscribed
                     except Exception as resub_e:
                         log_adapter.error(f"Failed to resubscribe after connection error: {resub_e}")
                         await asyncio.sleep(10) # Longer wait if resubscribe fails
            except Exception as e:
                log_adapter.error(f"Error in Redis listener loop: {e}", exc_info=True)
                await update_status("error", f"Listener loop error: {e}")
                await asyncio.sleep(1) # Avoid tight loop on unexpected errors

    except Exception as setup_e:
         log_adapter.error(f"Failed during Redis listener setup: {setup_e}", exc_info=True)
         await update_status("error_redis", f"Listener setup failed: {setup_e}")
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
             await update_status("error", "Listener unexpectedly stopped")
        else: # If loop exited due to shutdown signal
             await update_status("stopped")


# --- Signal Handler ---
def shutdown_handler(signum, frame):
    """Sets the global running flag to False on SIGINT or SIGTERM."""
    global running
    if running:
        print("\nShutdown signal received. Attempting graceful shutdown...")
        logger.info("Shutdown signal received. Attempting graceful shutdown...")
        running = False
    else:
        print("Multiple shutdown signals received. Forcing exit.")
        logger.warning("Multiple shutdown signals received. Forcing exit.")
        os._exit(1) # Force exit if already shutting down


# --- Main Execution ---
async def main(agent_id: str, config_url: str, redis_url: str):
    """Main function to fetch config, create app, and start listener."""
    log_adapter = logging.LoggerAdapter(logger, {'agent_id': agent_id})
    status_key = f"agent_status:{agent_id}"
    redis_client = None # Initialize

    async def update_status(status: str, error_detail: Optional[str] = None):
        """Helper to update Redis status, even before full client init."""
        temp_redis = None
        try:
            temp_redis = redis.from_url(redis_url)
            mapping = {"status": status, "pid": os.getpid()}
            if error_detail:
                mapping["error_detail"] = error_detail
            await temp_redis.hset(status_key, mapping=mapping)
            log_adapter.info(f"Status updated to: {status}")
        except Exception as e:
            log_adapter.error(f"Failed to update initial Redis status to {status}: {e}")
        finally:
            if temp_redis:
                await temp_redis.close()

    try:
        # 1. Initial Status Update
        await update_status("initializing")

        # 2. Initialize Redis Client
        log_adapter.info(f"Connecting to Redis at {redis_url}")
        try:
            redis_client = redis.from_url(redis_url)
            await redis_client.ping()
            log_adapter.info("Redis connection successful.")
        except Exception as e:
            log_adapter.error(f"Failed to connect to Redis: {e}", exc_info=True)
            await update_status("error_redis", f"Connection failed: {e}")
            return # Exit if Redis connection fails

        # 3. Fetch Agent Configuration
        log_adapter.info(f"Fetching agent configuration from {config_url}")
        try:
            response = requests.get(config_url, timeout=10)
            response.raise_for_status()
            agent_config = response.json()
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

        # 4. Create Agent App (Graph)
        try:
            app = create_agent_app(agent_config, agent_id, redis_client)
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
                 await update_status("error", f"Listener task failed: {e}")
        else:
             # Shutdown was requested while listener was running
             log_adapter.info("Shutdown initiated, cancelling listener task...")
             listener_task.cancel()
             try:
                 await listener_task
             except asyncio.CancelledError:
                 log_adapter.info("Listener task successfully cancelled.")

    except Exception as e:
        log_adapter.error(f"An unexpected error occurred in main: {e}", exc_info=True)
        if redis_client: # Try to update status if possible
             await update_status("error", f"Main loop error: {e}")
    finally:
        if redis_client:
            await redis_client.close()
            log_adapter.info("Redis client closed.")
        log_adapter.info("Agent runner main loop finished.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run a configurable agent.")
    parser.add_argument("--agent-id", type=str, required=True, help="Unique ID for this agent instance.")
    parser.add_argument("--config-url", type=str, required=True, help="URL to fetch the agent's JSON configuration.")
    parser.add_argument("--redis-url", type=str, required=True, help="URL for the Redis server.")
    args = parser.parse_args()

    # Setup signal handlers
    signal.signal(signal.SIGINT, shutdown_handler)
    signal.signal(signal.SIGTERM, shutdown_handler)

    # Configure logger with agent_id for the main execution context
    main_log_adapter = logging.LoggerAdapter(logger, {'agent_id': args.agent_id})
    main_log_adapter.info(f"Starting agent runner for ID: {args.agent_id}")

    try:
        asyncio.run(main(args.agent_id, args.config_url, args.redis_url))
    except KeyboardInterrupt:
         main_log_adapter.info("KeyboardInterrupt caught in main, exiting.")
    except Exception as e:
         main_log_adapter.critical(f"Unhandled exception in asyncio.run: {e}", exc_info=True)

    main_log_adapter.info(f"Agent runner {args.agent_id} has shut down.")

