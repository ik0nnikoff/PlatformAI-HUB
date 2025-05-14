import asyncio
import logging
import os
import json
import uuid
import time
from typing import Dict, Optional, Any, List

import httpx # Added for async HTTP requests
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from langchain_core.messages import BaseMessage, HumanMessage, AIMessage
from datetime import datetime, timezone

# Imports from the old runner_main.py that will be needed
import redis.asyncio as redis # type: ignore
from redis import exceptions as redis_exceptions # ADDED

from app.agent_runner.langgraph_factory import create_agent_app
from app.agent_runner.langgraph_models import TokenUsageData # AgentState is used by factory
from app.db.alchemy_models import ChatMessageDB, SenderType
from app.db.crud import chat_crud
from app.core.config import settings
from app.core.base.runnable_component import RunnableComponent
from app.core.base.status_updater import StatusUpdater

# Logging utilities (can be kept separate or integrated if they don't need to be global)
# For now, assuming setup_logging_for_agent will be called externally and logger passed in.

# --- Helper Functions (some might become methods or stay as utilities) ---

async def fetch_config_static(config_url: str, logger: logging.Logger) -> Optional[Dict]:
    """Fetches agent configuration from the management service using httpx."""
    logger.info(f"Fetching configuration from: {config_url}")
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.get(config_url)
            response.raise_for_status()  # Raises HTTPStatusError for 4xx/5xx responses
            config_data = response.json()
        logger.info("Configuration fetched successfully.")
        return config_data
    except httpx.TimeoutException:
        logger.error(f"Timeout fetching configuration from {config_url}.")
    except httpx.RequestError as e:
        logger.error(f"Failed to fetch configuration from {config_url} due to request error: {e}")
    except json.JSONDecodeError as e:
        logger.error(f"Failed to decode JSON from configuration response ({config_url}): {e}")
    except Exception as e: # Catch-all for unexpected errors
        logger.error(f"Unexpected error fetching configuration from {config_url}: {e}", exc_info=True)
    return None


def convert_db_history_to_langchain_static(db_messages: List[ChatMessageDB], logger: logging.Logger) -> List[BaseMessage]:
    """Converts messages from DB format (ChatMessageDB) to LangChain BaseMessage list."""
    converted = []
    if not ChatMessageDB or not SenderType:
        logger.error("ChatMessageDB model or SenderType Enum not available for history conversion.")
        return []

    for msg in db_messages:
        if not isinstance(msg, ChatMessageDB):
             logger.warning(f"Skipping message conversion due to unexpected type: {type(msg)}")
             continue
        if msg.sender_type == SenderType.USER:
            converted.append(HumanMessage(content=msg.content))
        elif msg.sender_type == SenderType.AGENT:
            converted.append(AIMessage(content=msg.content))
        else:
            logger.warning(f"Skipping message conversion due to unhandled sender_type: {msg.sender_type}")
    return converted


class AgentRunner(RunnableComponent, StatusUpdater):
    """
    Manages the lifecycle and execution of a single agent instance.
    """
    def __init__(self, 
                 agent_id: str, 
                 config_url: str, 
                 db_session_factory: Optional[async_sessionmaker[AsyncSession]],
                 logger_adapter: logging.LoggerAdapter): # Removed redis_pubsub_url

        super().__init__(logger=logger_adapter) # Initialize RunnableComponent with the logger
        # StatusUpdater and RedisClientManager are initialized via their own super() calls if needed.

        self.agent_id = agent_id
        self._component_id = agent_id  # Required by StatusUpdater
        self._status_key_prefix = "agent_status:"  # Required by StatusUpdater

        self.config_url = config_url
        self.db_session_factory = db_session_factory
        
        self.agent_config: Optional[Dict] = None
        self.agent_app: Optional[Any] = None # Consider more specific type if possible

        # For Pub/Sub specific to this agent's command listening
        self.redis_pubsub_client: Optional[redis.Redis] = None
        self.pubsub_handler_task: Optional[asyncio.Task] = None

        self.logger.info(f"AgentRunner for {self.agent_id} initialized. Config URL: {self.config_url}. PubSub will use REDIS_URL from settings: {settings.REDIS_URL}")

    async def _initialize_pubsub_client(self):
        """Initializes and pings the Redis Pub/Sub client using settings.REDIS_URL."""
        redis_url_to_use = str(settings.REDIS_URL)
        try:
            self.redis_pubsub_client = redis.from_url(redis_url_to_use)
            await self.redis_pubsub_client.ping()
            self.logger.info(f"Redis Pub/Sub client connected and pinged successfully for {self.agent_id} at {redis_url_to_use}.")
        except redis_exceptions.ConnectionError as e: # CHANGED
            self.logger.error(f"[{self.agent_id}] Failed to connect to Redis Pub/Sub at {redis_url_to_use}: {e}")
            self.redis_pubsub_client = None # Ensure client is None if connection failed
            raise  # Re-raise to be caught by setup
        except Exception as e:
            self.logger.error(f"[{self.agent_id}] An unexpected error occurred during Pub/Sub client initialization using {redis_url_to_use}: {e}", exc_info=True)
            self.redis_pubsub_client = None
            raise # Re-raise

    async def setup(self) -> None:
        """
        Sets up the AgentRunner:
        - Initializes StatusUpdater and Redis connection for status.
        - Fetches agent configuration.
        - Creates the LangGraph agent application.
        - Initializes Redis Pub/Sub client for command listening.
        """
        self.logger.info(f"[{self.agent_id}] Starting setup...")
        try:
            # Initialize StatusUpdater (uses default REDIS_URL from settings for status updates)
            # _component_id and _status_key_prefix are already set in __init__
            await self.setup_status_updater() 
            await self.mark_as_initializing()

            self.logger.info(f"[{self.agent_id}] Fetching configuration from {self.config_url}...")
            self.agent_config = await fetch_config_static(self.config_url, self.logger)
            if not self.agent_config:
                error_msg = f"[{self.agent_id}] Failed to fetch or invalid configuration from {self.config_url}."
                self.logger.error(error_msg)
                await self.mark_as_error(error_msg)
                raise ValueError(error_msg)
            
            self.logger.info(f"[{self.agent_id}] Configuration fetched successfully. Creating agent app...")
            # Ensuring this line is as follows for the TypeError fix:
            self.agent_app, static_state_config = create_agent_app(self.agent_config, self.agent_id)
            self.static_state_config = static_state_config # Store for use in _process_message
            self.logger.info(f"[{self.agent_id}] LangGraph application created successfully.")

            # Initialize dedicated Redis client for Pub/Sub
            self.logger.info(f"[{self.agent_id}] Initializing Redis Pub/Sub client...")
            await self._initialize_pubsub_client()
            # If _initialize_pubsub_client raises, setup will fail here, which is desired.

            await self.mark_as_running(pid=os.getpid() if hasattr(os, 'getpid') else None)
            self.logger.info(f"[{self.agent_id}] Setup complete. Agent is running.")

        except Exception as e:
            error_detail = f"Setup failed for agent {self.agent_id}: {str(e)}"
            self.logger.error(error_detail, exc_info=True)
            # Attempt to mark as error in Redis, but ensure Redis client for status is up
            if hasattr(self, '_redis_client') and self._redis_client: # Check if StatusUpdater's client was set up
                 await self.mark_as_error(error_detail)
            # Re-raise the exception so RunnableComponent's run() method can catch it and call cleanup.
            raise

    async def _handle_pubsub_message(self, message: Dict[str, Any]):
        """Handles incoming messages from Redis Pub/Sub."""
        # ... (Implementation of message handling as in the original runner_main.py's control_listener)
        # This will involve:
        # - Parsing the message
        # - Getting chat history (convert_db_history_to_langchain_static)
        # - Invoking self.agent_app.ainvoke
        # - Saving response and token usage to DB
        # - Publishing response back to Redis
        # - Updating last_active_time
        self.logger.debug(f"[{self.agent_id}] Received PubSub message: {message}")
        try:
            data_str = message.get('data')
            if isinstance(data_str, bytes):
                data_str = data_str.decode('utf-8')
            
            if not data_str:
                self.logger.warning(f"[{self.agent_id}] Empty message data received.")
                return

            payload = json.loads(data_str)
            self.logger.info(f"[{self.agent_id}] Processing message for chat_id: {payload.get('chat_id')}")

            chat_id = payload.get("chat_id")
            user_input = payload.get("text")
            interaction_id = payload.get("interaction_id") # Important for tracking

            if not chat_id or user_input is None or not interaction_id:
                self.logger.warning(f"[{self.agent_id}] Missing chat_id, text, or interaction_id in payload: {payload}")
                return

            # Determine context memory depth from agent_config
            enable_context_memory = self.agent_config.get("enableContextMemory", True)
            context_memory_depth = self.agent_config.get("contextMemoryDepth", 10) # Default to 10 if not specified
            if not enable_context_memory:
                self.logger.info(f"[{self.agent_id}] Context memory is disabled by agent config. History will not be loaded with depth.")
                actual_history_limit = 0
            else:
                actual_history_limit = context_memory_depth
            
            self.logger.info(f"[{self.agent_id}] Using history limit: {actual_history_limit} (enabled: {enable_context_memory}, configured depth: {context_memory_depth})")

            history_messages_db: List[ChatMessageDB] = []
            if self.db_session_factory:
                async with self.db_session_factory() as db_session:
                    # Save user message
                    user_msg_db = await chat_crud.db_add_chat_message(
                        db_session,
                        chat_id=chat_id,
                        agent_id=self.agent_id,
                        interaction_id=interaction_id, # Use interaction_id from payload
                        sender_type=SenderType.USER,
                        message_text=user_input,
                        platform_specific_data={"platform": "pubsub"} # Example
                    )
                    # Fetch history using the determined limit
                    history_messages_db = await chat_crud.db_get_chat_history(db_session, chat_id, agent_id=self.agent_id, limit=actual_history_limit)
            
            langchain_history = convert_db_history_to_langchain_static(history_messages_db, self.logger)

            # Invoke agent
            self.logger.debug(f"[{self.agent_id}] Invoking agent for interaction_id: {interaction_id}...")
            agent_response_stream = self.agent_app.astream(
                {"messages": langchain_history + [HumanMessage(content=user_input)]},
                config={"configurable": {"thread_id": chat_id, "agent_id": self.agent_id}}
            )
            
            full_response_content = ""
            token_usage_data: Optional[TokenUsageData] = None
            
            async for event_part in agent_response_stream:
                # Assuming the structure of event_part needs to be inspected to find AIMessage chunks
                # This part is highly dependent on how your LangGraph app streams AIMessage content.
                # The example below is a common pattern.
                if isinstance(event_part, dict):
                    # Look for AIMessage content within the dict structure
                    # This might be nested, e.g., event_part.get('agent_name', {}).get('messages', [{}])[0].content
                    # For simplicity, let's assume a flatter structure or a known key for AIMessage chunks
                    # Example: if 'messages' key contains a list of BaseMessage objects
                    messages_chunk = event_part.get('messages', [])
                    if messages_chunk and isinstance(messages_chunk, list):
                        for msg_item in messages_chunk:
                            if isinstance(msg_item, AIMessage):
                                full_response_content += msg_item.content
                                # If token usage is part of the AIMessage or its metadata
                                if msg_item.usage_metadata and msg_item.usage_metadata.get("token_usage"):
                                    token_usage_data = TokenUsageData(**msg_item.usage_metadata["token_usage"])
                                    self.logger.debug(f"[{self.agent_id}] Token usage data found in AIMessage metadata: {token_usage_data}")
                                break # Assuming one AIMessage per relevant event part for streaming

                # Check for final token usage if it comes at the end or in a specific event type
                # This depends on your LangGraph setup.
                # Example: if token_usage is a top-level key in some event_parts
                if "token_usage" in event_part and event_part["token_usage"]:
                     try:
                        token_usage_data = TokenUsageData(**event_part["token_usage"])
                        self.logger.debug(f"[{self.agent_id}] Token usage data found in event part: {token_usage_data}")
                     except Exception as e_token:
                        self.logger.warning(f"[{self.agent_id}] Could not parse token_usage from event_part: {event_part.get('token_usage')}, error: {e_token}")


            self.logger.info(f"[{self.agent_id}] Agent response for interaction_id {interaction_id}: {full_response_content[:100]}...")

            if self.db_session_factory:
                async with self.db_session_factory() as db_session:
                    # Save AI message
                    ai_msg_db = await chat_crud.db_add_chat_message(
                        db_session,
                        chat_id=chat_id,
                        agent_id=self.agent_id,
                        interaction_id=interaction_id, # Use same interaction_id
                        sender_type=SenderType.AI,
                        message_text=full_response_content,
                        platform_specific_data={"platform": "pubsub"}, # Example
                        token_usage_data=token_usage_data.model_dump() if token_usage_data else None
                    )
            
            # Publish response back to a response channel (e.g., agent_responses:{chat_id})
            response_channel = f"agent_responses:{chat_id}"
            response_payload = {
                "chat_id": chat_id,
                "agent_id": self.agent_id,
                "interaction_id": interaction_id,
                "text": full_response_content,
                "timestamp": datetime.now(timezone.utc).isoformat()
            }
            if self.redis_pubsub_client:
                await self.redis_pubsub_client.publish(response_channel, json.dumps(response_payload))
                self.logger.info(f"[{self.agent_id}] Published response to {response_channel}")

            await self.update_last_active_time()

        except json.JSONDecodeError as e:
            self.logger.error(f"[{self.agent_id}] JSONDecodeError processing PubSub message: {e}. Data: {data_str}", exc_info=True)
        except Exception as e:
            self.logger.error(f"[{self.agent_id}] Error processing PubSub message: {e}", exc_info=True)
            # Optionally, publish an error response
            if 'payload' in locals() and 'chat_id' in payload and 'interaction_id' in payload:
                error_response_channel = f"agent_responses:{payload['chat_id']}"
                error_payload = {
                    "chat_id": payload['chat_id'],
                    "agent_id": self.agent_id,
                    "interaction_id": payload['interaction_id'],
                    "error": str(e),
                    "timestamp": datetime.now(timezone.utc).isoformat()
                }
                if self.redis_pubsub_client:
                    try:
                        await self.redis_pubsub_client.publish(error_response_channel, json.dumps(error_payload))
                        self.logger.info(f"[{self.agent_id}] Published error notification to {error_response_channel}")
                    except Exception as pub_err:
                        self.logger.error(f"[{self.agent_id}] Failed to publish error notification: {pub_err}", exc_info=True)


    async def _pubsub_listener_loop(self):
        """Listens to Redis Pub/Sub for incoming commands."""
        if not self.redis_pubsub_client:
            self.logger.error(f"[{self.agent_id}] PubSub client not initialized. Cannot start listener loop.")
            return

        pubsub = self.redis_pubsub_client.pubsub()
        command_channel = f"agent_commands:{self.agent_id}" # Hardcoded channel name
        
        try:
            await pubsub.subscribe(command_channel)
            self.logger.info(f"[{self.agent_id}] Subscribed to Redis channel: {command_channel}")

            while self._running: # Check flag from RunnableComponent
                try:
                    # Timeout allows the loop to periodically check self._running
                    message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=0.1)
                    if message:
                        await self._handle_pubsub_message(message)
                    # Yield control to allow other tasks to run, and for self._running to be checked
                    await asyncio.sleep(0.01) # Short sleep to prevent tight loop if no messages
                except redis_exceptions.ConnectionError as e: # CHANGED
                    self.logger.error(f"[{self.agent_id}] PubSub connection error in listener loop: {e}. Attempting to reconnect...", exc_info=True)
                    # Implement reconnection logic if needed, or rely on setup/run to re-establish
                    await asyncio.sleep(5) # Wait before retrying or exiting
                    # Potentially re-subscribe or re-initialize client if connection is lost.
                    # For now, we'll let the main run loop handle component restart if it fails critically.
                    # If re-subscription is needed:
                    # await pubsub.subscribe(command_channel)
                except asyncio.CancelledError:
                    self.logger.info(f"[{self.agent_id}] PubSub listener loop cancelled.")
                    break # Exit loop on cancellation
                except Exception as e:
                    self.logger.error(f"[{self.agent_id}] Error in PubSub listener loop: {e}", exc_info=True)
                    await asyncio.sleep(1) # Brief pause before continuing

        finally:
            if pubsub:
                try:
                    await pubsub.unsubscribe(command_channel)
                    await pubsub.close() # Close the pubsub object itself
                    self.logger.info(f"[{self.agent_id}] Unsubscribed from {command_channel} and closed pubsub object.")
                except Exception as e_close_pubsub:
                    self.logger.error(f"[{self.agent_id}] Error closing pubsub resources: {e_close_pubsub}", exc_info=True)


    async def run_loop(self) -> None:
        """
        The main operational loop for the AgentRunner.
        Starts and manages the Pub/Sub listener task.
        """
        self.logger.info(f"[{self.agent_id}] Starting run_loop...")
        
        if not self.redis_pubsub_client: # Should have been initialized in setup
            self.logger.error(f"[{self.agent_id}] Redis Pub/Sub client not available at start of run_loop. Stopping.")
            self._running = False # Signal to stop
            return

        # Start the Pub/Sub listener as a background task managed by this component
        self.pubsub_handler_task = asyncio.create_task(self._pubsub_listener_loop())
        
        try:
            while self._running:
                if self.pubsub_handler_task.done():
                    # If the listener task stopped unexpectedly, log it and potentially stop the agent.
                    try:
                        self.pubsub_handler_task.result() # Raise exception if task failed
                        self.logger.info(f"[{self.agent_id}] PubSub listener task finished gracefully.")
                    except asyncio.CancelledError:
                         self.logger.info(f"[{self.agent_id}] PubSub listener task was cancelled.")
                    except Exception as e:
                        self.logger.error(f"[{self.agent_id}] PubSub listener task failed: {e}", exc_info=True)
                        await self.mark_as_error(f"PubSub listener failed: {e}")
                    
                    self._running = False # Stop the agent if listener stops
                    break 
                
                await self.update_last_active_time() # Periodically update last active time
                await asyncio.sleep(settings.AGENT_RUNNER_HEARTBEAT_INTERVAL) # Check periodically

            # If loop exits due to self._running becoming False (e.g. signal)
            if self.pubsub_handler_task and not self.pubsub_handler_task.done():
                self.logger.info(f"[{self.agent_id}] run_loop stopping, cancelling PubSub listener task...")
                self.pubsub_handler_task.cancel()
                try:
                    await self.pubsub_handler_task
                except asyncio.CancelledError:
                    self.logger.info(f"[{self.agent_id}] PubSub listener task successfully cancelled during run_loop stop.")
                except Exception as e: # Should not happen if cancellation is handled in _pubsub_listener_loop
                    self.logger.error(f"[{self.agent_id}] Unexpected error waiting for PubSub listener task cancellation: {e}", exc_info=True)

        except asyncio.CancelledError:
            self.logger.info(f"[{self.agent_id}] run_loop was cancelled.")
            if self.pubsub_handler_task and not self.pubsub_handler_task.done():
                self.pubsub_handler_task.cancel()
                # Wait for it to actually cancel
                try:
                    await self.pubsub_handler_task
                except asyncio.CancelledError:
                    self.logger.info(f"[{self.agent_id}] PubSub listener task successfully cancelled during run_loop cancellation.")
        finally:
            self.logger.info(f"[{self.agent_id}] Exiting run_loop.")


    async def cleanup(self) -> None:
        """Cleans up resources used by the AgentRunner."""
        self.logger.info(f"[{self.agent_id}] Starting cleanup...")
        
        # Stop the Pub/Sub listener task if it's running
        if self.pubsub_handler_task and not self.pubsub_handler_task.done():
            self.logger.info(f"[{self.agent_id}] Cancelling PubSub listener task during cleanup...")
            self.pubsub_handler_task.cancel()
            try:
                await self.pubsub_handler_task
            except asyncio.CancelledError:
                self.logger.info(f"[{self.agent_id}] PubSub listener task successfully cancelled during cleanup.")
            except Exception as e:
                 self.logger.error(f"[{self.agent_id}] Error during PubSub listener task cancellation in cleanup: {e}", exc_info=True)
        self.pubsub_handler_task = None

        # Close the dedicated Pub/Sub Redis client
        if self.redis_pubsub_client:
            try:
                await self.redis_pubsub_client.close()
                # await self.redis_pubsub_client.connection_pool.disconnect() # If needed
                self.logger.info(f"[{self.agent_id}] Redis Pub/Sub client closed.")
            except Exception as e:
                self.logger.error(f"[{self.agent_id}] Error closing Redis Pub/Sub client: {e}", exc_info=True)
            finally:
                self.redis_pubsub_client = None
        
        # Mark as stopped in Redis (via StatusUpdater)
        # Decide if status should be cleared from Redis on normal shutdown.
        # For now, just mark as stopped. ProcessManager might have other policies.
        await self.mark_as_stopped(reason="AgentRunner cleanup initiated.")
        
        # Cleanup StatusUpdater's Redis client (the one used for status updates)
        # clear_status_on_cleanup=False by default, meaning the "stopped" status remains.
        # Set to True if the key itself should be deleted.
        await self.cleanup_status_updater(clear_status_on_cleanup=False) 

        self.logger.info(f"[{self.agent_id}] Cleanup complete.")

# Helper function convert_db_history_to_langchain_static can remain as is or be moved
# into the class if it makes more sense contextually.
# For now, it's kept as a static helper.
