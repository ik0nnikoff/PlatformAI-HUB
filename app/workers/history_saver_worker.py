import asyncio
import logging
from datetime import datetime, timezone
from typing import Dict, Any, Callable, Optional # Added Optional
from sqlalchemy.ext.asyncio import AsyncSession # Added AsyncSession
# Removed signal, json, redis.asyncio, RedisConnectionError as they are handled by base or not directly used by the class logic

from pydantic import ValidationError

from app.core.config import settings
from app.db.session import get_async_session_factory
from app.db.crud.chat_crud import db_add_chat_message
from app.api.schemas.chat_schemas import ChatMessageCreate, SenderType
from app.workers.base_worker import QueueWorker

# Configure logging - This might be handled by a central logging config now.
# If app.core.logging_config.setup_logging() is called at entry, this basicConfig might be redundant or even conflict.
# For worker-specific logger, it's better to get it via logging.getLogger(__name__)
# and rely on root configuration.
# logging.basicConfig(
# level=settings.LOG_LEVEL,
# format='%(asctime)s - %(levelname)s - %(name)s - %(message)s'
# )
# logger = logging.getLogger(__name__) # Logger is now part of BaseWorker/RunnableComponent

class HistorySaverWorker(QueueWorker):
    """
    Worker to save chat history messages from a Redis queue to the database.
    """
    def __init__(self):
        super().__init__(
            component_id="history_saver_worker", # Unique ID for this worker instance
            queue_names=[settings.REDIS_HISTORY_QUEUE_NAME],
            status_key_prefix="worker_status:history_saver:" # Specific status key prefix
        )
        # self.async_session_factory will be initialized in setup
        self.async_session_factory: Optional[Callable[[], AsyncSession]] = None # Added Optional and type hint

    async def setup(self):
        """
        Initializes the database session factory.
        """
        await super().setup()
        self.async_session_factory = get_async_session_factory()
        self.logger.info(f"[{self._component_id}] Database session factory initialized.")

    async def process_message(self, message_data: Dict[str, Any]) -> None:
        """
        Validates chat message data and saves it to the database.
        This method is called by the parent QueueWorker for each message from the queue.
        """
        self.logger.debug(f"[{self._component_id}] Received chat history data: {message_data}")

        if not self.async_session_factory:
            self.logger.error(f"[{self._component_id}] Async session factory not initialized. Cannot process message.")
            # Optionally, re-raise or handle as a permanent failure for this message.
            return

        try:
            # Convert timestamp string to datetime object if necessary
            if 'timestamp' in message_data and isinstance(message_data['timestamp'], str):
                try:
                    dt = datetime.fromisoformat(message_data['timestamp'])
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=timezone.utc) # Assume UTC if naive
                    message_data['timestamp'] = dt
                except ValueError:
                    self.logger.warning(f"[{self._component_id}] Could not parse timestamp string: {message_data['timestamp']}. Using current UTC time.")
                    message_data['timestamp'] = datetime.now(timezone.utc)
            elif 'timestamp' not in message_data:
                 message_data['timestamp'] = datetime.now(timezone.utc)

            # Validate sender_type if present
            if 'sender_type' in message_data and not isinstance(message_data['sender_type'], SenderType):
                try:
                    message_data['sender_type'] = SenderType(message_data['sender_type'])
                except ValueError:
                    self.logger.warning(f"[{self._component_id}] Invalid sender_type value: {message_data['sender_type']}. Setting to 'user'.")
                    message_data['sender_type'] = SenderType.USER

            message_create_schema = ChatMessageCreate(**message_data)

        except ValidationError as e:
            self.logger.error(f"[{self._component_id}] Validation error for chat message data: {message_data}, errors: {e.errors()}", exc_info=True)
            return # Message cannot be processed
        except Exception as e:
            self.logger.error(f"[{self._component_id}] Error preparing chat message data {message_data}: {e}", exc_info=True)
            return # Message cannot be processed

        async with self.async_session_factory() as db: # type: ignore
            try:
                saved_message = await db_add_chat_message(
                    db=db, # type: ignore
                    agent_id=message_create_schema.agent_id,
                    thread_id=message_create_schema.thread_id,
                    sender_type=message_create_schema.sender_type,
                    content=message_create_schema.content,
                    channel=message_create_schema.channel,
                    timestamp=message_create_schema.timestamp,
                    interaction_id=message_create_schema.interaction_id
                )
                if saved_message:
                    self.logger.info(f"[{self._component_id}] Successfully saved chat message for agent_id: {message_create_schema.agent_id}, interaction_id: {message_create_schema.interaction_id}, db_id: {saved_message.id}")
                else:
                    self.logger.error(f"[{self._component_id}] Failed to save chat message to DB (db_add_chat_message returned None) for agent_id: {message_create_schema.agent_id}, interaction_id: {message_create_schema.interaction_id}")
            except Exception as e:
                self.logger.error(f"[{self._component_id}] Exception during save_chat_message_to_db for interaction_id {message_create_schema.interaction_id}: {e}", exc_info=True)
                # Depending on the error, you might want to implement a retry mechanism
                # or move the message to a dead-letter queue. For now, just logging.

# Removed old signal_handler, save_chat_message_to_db_async, and main_loop functions.
# Graceful shutdown is handled by RunnableComponent.
# Redis connection and message polling are handled by QueueWorker.

if __name__ == "__main__":
    # It's good practice to ensure logging is configured before anything else.
    # If you have a central logging setup (e.g., app.core.logging_config.setup_logging()),
    # ensure it's called here or in an even earlier entry point.
    # For simplicity, if not centrally configured, basicConfig can be set here.
    # However, RunnableComponent and BaseWorker already get a logger.
    # This basicConfig would apply to the root logger if no handlers are configured.
    logging.basicConfig(
        level=settings.LOG_LEVEL,
        format='%(asctime)s - %(levelname)s - %(name)s - [%(component_id)s] - %(message)s'
    )
    
    # Create a preliminary logger for the __main__ context
    main_logger = logging.getLogger("history_saver_worker_main")
    # Add a filter to make component_id available for the format string if needed,
    # or ensure the worker's logger is used for worker-specific messages.
    # For this main_logger, component_id might not be directly applicable unless set.

    main_logger.info("Initializing HistorySaverWorker...")
    
    worker = HistorySaverWorker()

    try:
        asyncio.run(worker.run())
    except KeyboardInterrupt:
        main_logger.info("HistorySaverWorker interrupted by user (KeyboardInterrupt).")
        # The worker.run() method's finally block (from RunnableComponent) should handle cleanup.
    except Exception as e:
        main_logger.critical(f"HistorySaverWorker failed to start or run: {e}", exc_info=True)
    finally:
        main_logger.info("HistorySaverWorker application finished.")
