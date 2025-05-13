import asyncio
import json
import logging
import signal
from datetime import datetime, timezone
from typing import Dict, Any, Optional

import redis.asyncio as redis
from redis.exceptions import ConnectionError as RedisConnectionError # Added
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.session import get_async_session_factory
# Assuming the following paths and names, adjust if necessary:
from app.db.crud.chat_crud import db_add_chat_message # Исправленный путь
from app.api.schemas.chat_schemas import ChatMessageCreate, SenderType 
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
    logger.info(f"Signal {signum} received, initiating graceful shutdown for history_saver_worker.")
    shutdown_requested = True

async def save_chat_message_to_db_async(async_session_factory: callable, data: Dict[str, Any]):
    """
    Validates chat message data and saves it to the database.
    """
    try:
        # Convert timestamp string to datetime object if necessary
        if 'timestamp' in data and isinstance(data['timestamp'], str):
            try:
                # Attempt to parse ISO format, add timezone if naive
                dt = datetime.fromisoformat(data['timestamp'])
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc) # Assume UTC if naive
                data['timestamp'] = dt
            except ValueError:
                logger.warning(f"Could not parse timestamp string: {data['timestamp']}. Using current UTC time.")
                data['timestamp'] = datetime.now(timezone.utc)
        elif 'timestamp' not in data:
             data['timestamp'] = datetime.now(timezone.utc)


        # Validate sender_type if present
        if 'sender_type' in data and not isinstance(data['sender_type'], SenderType):
            try:
                data['sender_type'] = SenderType(data['sender_type'])
            except ValueError:
                logger.warning(f"Invalid sender_type value: {data['sender_type']}. Setting to 'user'.")
                data['sender_type'] = SenderType.USER # Default or handle as error

        # Validate with Pydantic schema
        message_create_schema = ChatMessageCreate(**data)

    except ValidationError as e:
        logger.error(f"Validation error for chat message data: {data}, errors: {e.errors()}", exc_info=True)
        return
    except Exception as e:
        logger.error(f"Error preparing chat message data {data}: {e}", exc_info=True)
        return

    async with async_session_factory() as db:
        try:
            # Извлекаем данные из Pydantic модели для передачи в db_add_chat_message
            saved_message = await db_add_chat_message(
                db,
                agent_id=message_create_schema.agent_id,
                thread_id=message_create_schema.thread_id,
                sender_type=message_create_schema.sender_type,
                content=message_create_schema.content,
                channel=message_create_schema.channel,
                timestamp=message_create_schema.timestamp,
                interaction_id=message_create_schema.interaction_id
            )
            if saved_message:
                logger.info(f"Successfully saved chat message for agent_id: {message_create_schema.agent_id}, interaction_id: {message_create_schema.interaction_id}, db_id: {saved_message.id}")
            else:
                logger.error(f"Failed to save chat message to DB (db_add_chat_message returned None) for agent_id: {message_create_schema.agent_id}, interaction_id: {message_create_schema.interaction_id}")
        except Exception as e:
            logger.error(f"Exception during save_chat_message_to_db_async for interaction_id {message_create_schema.interaction_id}: {e}", exc_info=True)


async def main_loop():
    global shutdown_requested # Added global declaration here
    logger.info("Starting chat history saver worker.")

    redis_url = settings.REDIS_URL
    if not redis_url:
        logger.critical("REDIS_URL not configured in settings. History saver worker cannot start.")
        return

    redis_client = None
    try:
        redis_client = await redis.from_url(redis_url, decode_responses=True)
        await redis_client.ping()
        logger.info(f"Successfully connected to Redis at {redis_url}")
    except Exception as e:
        logger.critical(f"Could not connect to Redis: {e}", exc_info=True)
        return

    history_queue_name = settings.REDIS_HISTORY_QUEUE_NAME
    logger.info(f"Listening to Redis queue: {history_queue_name}")

    async_session_factory = get_async_session_factory()

    while not shutdown_requested:
        try:
            item = await redis_client.blpop(history_queue_name, timeout=1)
            if item:
                _, task_json = item
                try:
                    task_data = json.loads(task_json)
                    logger.debug(f"Received chat history data: {task_data}")
                    await save_chat_message_to_db_async(async_session_factory, task_data)
                except json.JSONDecodeError:
                    logger.error(f"Failed to decode JSON from queue: {task_json}")
                except Exception as e:
                    logger.error(f"Error processing task: {task_json}, Error: {e}", exc_info=True)
            
            if not item: # Yield control briefly if no item, to make shutdown responsive
                await asyncio.sleep(0.01)

        except RedisConnectionError as e: # Changed redis.exceptions.ConnectionError
            logger.error(f"Redis connection error: {e}. Attempting to reconnect...")
            if redis_client:
                try:
                    await redis_client.close()
                except Exception as close_exc:
                    logger.error(f"Error closing old Redis connection: {close_exc}")
            
            reconnected = False
            for i in range(settings.REDIS_RECONNECT_ATTEMPTS):
                await asyncio.sleep(settings.REDIS_RECONNECT_DELAY * (i + 1))
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
            await asyncio.sleep(1) # Avoid busy-looping

    if redis_client:
        try:
            await redis_client.close()
            logger.info("Redis client closed.")
        except Exception as e:
            logger.error(f"Error closing Redis client during shutdown: {e}")
            
    logger.info("Chat history saver worker has shut down.")

if __name__ == "__main__":
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    try:
        asyncio.run(main_loop())
    except KeyboardInterrupt:
        logger.info("History saver worker interrupted by user (KeyboardInterrupt).")
    except Exception as e:
        logger.critical(f"History saver worker failed to start or run: {e}", exc_info=True)
    finally:
        logger.info("History saver worker application finished.")
