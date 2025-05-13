\
import asyncio
import json
import logging
import signal
from datetime import datetime
from typing import Dict, Any, Optional

import redis.asyncio as redis
from redis.exceptions import ConnectionError as RedisConnectionError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.session import get_async_session_factory
from app.db.crud.token_usage_crud import db_add_token_usage_log
from app.db.crud.chat_crud import db_get_chat_message_by_interaction_id # Added import
from app.api.schemas.common_schemas import SenderType # Added import

# TokenUsageLogDB is implicitly handled by db_add_token_usage_log if data matches schema

# Configure logging
logging.basicConfig(
    level=settings.LOG_LEVEL, 
    format='%(asctime)s - %(levelname)s - %(name)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Graceful shutdown handling
shutdown_requested = False

def signal_handler(signum, frame):
    global shutdown_requested
    logger.info(f"Signal {signum} received, initiating graceful shutdown for token_usage_worker.")
    shutdown_requested = True

async def save_token_usage_to_db_async(async_session_factory, data: Dict[str, Any]):
    async with async_session_factory() as db:
        try:
            # Convert timestamp string to datetime object if necessary
            # TokenUsageLogDB expects a datetime object for timestamp
            if 'timestamp' in data and isinstance(data['timestamp'], str):
                try:
                    data['timestamp'] = datetime.fromisoformat(data['timestamp'])
                except ValueError:
                    logger.warning(f"Could not parse timestamp string: {data['timestamp']}. Falling back to None or DB default if applicable.")
                    # Decide handling: remove, set to None, or let DB handle if server_default=func.now()
                    # For now, let's remove it if it's not parsable and rely on server_default if set on model
                    data.pop('timestamp', None)


            # Ensure all required fields for TokenUsageLogDB are present or handled by db_add_token_usage_log
            # Required fields based on TokenUsageLogDB: agent_id, call_type, model_id, prompt_tokens, completion_tokens, total_tokens
            # Optional/auto-generated: id, chat_message_id, interaction_id, timestamp (if server_default)
            
            # The db_add_token_usage_log function expects a dictionary that can be unpacked into TokenUsageLogDB
            saved_log = await db_add_token_usage_log(db, token_usage_data=data)
            
            if saved_log:
                logger.info(f"Successfully saved token usage for agent_id: {data.get('agent_id')}, interaction_id: {data.get('interaction_id')}, db_id: {saved_log.id}")
            else:
                logger.error(f"Failed to save token usage to DB (db_add_token_usage_log returned None) for agent_id: {data.get('agent_id')}, interaction_id: {data.get('interaction_id')}")

        except Exception as e:
            # db_add_token_usage_log already logs errors and rollbacks, but we can log context here
            logger.error(f"Exception during save_token_usage_to_db_async for interaction_id {data.get('interaction_id')}: {e}", exc_info=True)


async def main_loop():
    global shutdown_requested
    logger.info("Starting token usage worker.")
    
    redis_url = settings.REDIS_URL
    if not redis_url:
        logger.critical("REDIS_URL not configured in settings. Token usage worker cannot start.")
        return
        
    redis_client = None
    try:
        redis_client = await redis.from_url(redis_url, decode_responses=True)
        await redis_client.ping()
        logger.info(f"Successfully connected to Redis at {redis_url}")
    except Exception as e:
        logger.critical(f"Could not connect to Redis: {e}", exc_info=True)
        return # Cannot operate without Redis

    token_usage_queue_name = getattr(settings, "REDIS_TOKEN_USAGE_QUEUE", "token_usage_queue")
    logger.info(f"Listening to Redis queue: {token_usage_queue_name}")

    async_session_factory = get_async_session_factory()

    while not shutdown_requested:
        try:
            # Blocking pop with a timeout to allow checking shutdown_requested
            item = await redis_client.blpop(token_usage_queue_name, timeout=1)
            if item:
                _, task_json = item
                try:
                    task_data = json.loads(task_json)
                    logger.debug(f"Received token usage data: {task_data}")
                    
                    agent_id = task_data.get('agent_id')
                    interaction_id = task_data.get('interaction_id')
                    message_id_to_save = None

                    if agent_id and interaction_id:
                        logger.info(f"Attempting to find message_id for agent_id: {agent_id}, interaction_id: {interaction_id}")
                        for attempt in range(3):
                            async with async_session_factory() as db_session:
                                # Try to get the AI message first, as token usage is typically for AI response
                                chat_message = await db_get_chat_message_by_interaction_id(
                                    db_session, 
                                    agent_id=agent_id, 
                                    interaction_id=interaction_id,
                                    sender_type=SenderType.AGENT # Исправлено с SenderType.AI на SenderType.AGENT
                                )
                                if not chat_message: # If AI message not found, try any message for that interaction
                                    logger.debug(f"AI message not found for interaction_id {interaction_id}, attempt {attempt+1}. Trying any sender type.")
                                    chat_message = await db_get_chat_message_by_interaction_id(
                                        db_session, 
                                        agent_id=agent_id, 
                                        interaction_id=interaction_id
                                    )

                                if chat_message:
                                    message_id_to_save = chat_message.id
                                    logger.info(f"Found message_id: {message_id_to_save} for interaction_id: {interaction_id} on attempt {attempt+1}")
                                    break # Exit retry loop if message_id is found
                                else:
                                    logger.warning(f"Could not find message_id for interaction_id: {interaction_id} (agent: {agent_id}) on attempt {attempt+1}. Retrying in {attempt+2}s...")
                                    await asyncio.sleep(attempt + 2) # Wait a bit longer each attempt
                        if not message_id_to_save:
                            logger.error(f"Failed to find message_id for interaction_id: {interaction_id} (agent: {agent_id}) after multiple attempts.")
                    else:
                        logger.warning(f"Missing agent_id or interaction_id in task_data, cannot fetch message_id. Data: {task_data}")

                    if message_id_to_save:
                        task_data['message_id'] = message_id_to_save
                    else:
                        # Ensure message_id is not present or is None if not found, to avoid old/wrong values
                        task_data.pop('message_id', None) 

                    # Clean up old/temporary fields
                    if 'thread_id' in task_data:
                        logger.debug(f"Removing 'thread_id': {task_data['thread_id']} from token usage data before saving.")
                        del task_data['thread_id']
                    
                    if 'chat_message_id' in task_data: # Should not be present if runner_main is correct
                        logger.warning(f"Removing deprecated 'chat_message_id': {task_data['chat_message_id']} from token usage data.")
                        del task_data['chat_message_id']
                        
                    await save_token_usage_to_db_async(async_session_factory, task_data)
                except json.JSONDecodeError:
                    logger.error(f"Failed to decode JSON from queue: {task_json}")
                except Exception as e:
                    logger.error(f"Error processing task: {task_json}, Error: {e}", exc_info=True)
            
            # Yield control briefly if no item, to make shutdown responsive
            if not item:
                await asyncio.sleep(0.01)

        except RedisConnectionError as e:
            logger.error(f"Redis connection error: {e}. Attempting to reconnect...")
            if redis_client:
                await redis_client.close() # Close the old connection
            
            # Reconnection attempt loop
            reconnected = False
            for i in range(5): # Try to reconnect 5 times
                await asyncio.sleep(5 + i*2) # Exponential backoff
                try:
                    redis_client = await redis.from_url(redis_url, decode_responses=True)
                    await redis_client.ping()
                    logger.info("Successfully reconnected to Redis.")
                    reconnected = True
                    break
                except Exception as recon_e:
                    logger.error(f"Redis reconnection attempt {i+1} failed: {recon_e}")
            if not reconnected:
                logger.critical("Failed to reconnect to Redis after multiple attempts. Worker will exit.")
                shutdown_requested = True # Trigger shutdown
                
        except Exception as e:
            logger.error(f"An unexpected error occurred in the main loop: {e}", exc_info=True)
            await asyncio.sleep(1) # Avoid busy-looping on unexpected errors

    if redis_client:
        await redis_client.close()
        logger.info("Redis client closed.")
    logger.info("Token usage worker has shut down.")

if __name__ == "__main__":
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    # Alembic handles database migrations. No need for Base.metadata.create_all here.

    try:
        asyncio.run(main_loop())
    except KeyboardInterrupt:
        logger.info("Token usage worker interrupted by user (KeyboardInterrupt).")
    except Exception as e:
        logger.critical(f"Token usage worker failed to start or run: {e}", exc_info=True)
    finally:
        logger.info("Token usage worker application finished.")

