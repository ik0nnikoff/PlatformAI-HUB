import asyncio
import logging
from datetime import datetime
from typing import Dict, Any, Optional, Callable

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.session import get_async_session_factory
from app.db.crud.token_usage_crud import db_add_token_usage_log
from app.db.crud.chat_crud import db_get_chat_message_by_interaction_id
from app.api.schemas.common_schemas import SenderType
from app.workers.base_worker import QueueWorker

class TokenUsageWorker(QueueWorker):
    """
    Worker to save token usage information from a Redis queue to the database.
    """
    def __init__(self):
        # Исправлено: используем REDIS_TOKEN_USAGE_QUEUE_NAME, чтобы совпадало с агентом
        token_usage_queue_name = getattr(settings, "REDIS_TOKEN_USAGE_QUEUE_NAME", "token_usage_queue")
        super().__init__(
            component_id="token_usage_worker", # Unique ID for this worker instance
            queue_names=[token_usage_queue_name],
            status_key_prefix="worker_status:token_usage:" # Specific status key prefix
        )
        self.async_session_factory: Optional[Callable[[], AsyncSession]] = None
        self.logger.info(f"[{self._component_id}] Initialized. Listening to queue: {token_usage_queue_name}")

    async def setup(self):
        """
        Initializes the database session factory.
        """
        await super().setup()
        self.async_session_factory = get_async_session_factory()
        self.logger.info(f"[{self._component_id}] Database session factory initialized.")
        # Логируем имя очереди и её длину для диагностики (await redis client)
        try:
            queue_name = self.queue_names[0] if self.queue_names else None
            if queue_name:
                redis_client = await self.redis_client
                length = await redis_client.llen(queue_name)
                self.logger.info(f"[{self._component_id}] Redis queue '{queue_name}' length at setup: {length}")
        except Exception as e:
            self.logger.warning(f"[{self._component_id}] Could not check Redis queue length: {e}")

    async def _save_token_usage_to_db(self, db: AsyncSession, data: Dict[str, Any]):
        """
        Helper method to save token usage data to the database.
        """
        try:
            # Convert timestamp string to datetime object if necessary
            if 'timestamp' in data and isinstance(data['timestamp'], str):
                try:
                    data['timestamp'] = datetime.fromisoformat(data['timestamp'])
                except ValueError:
                    self.logger.warning(f"[{self._component_id}] Could not parse timestamp string: {data['timestamp']}. Relying on DB default if set.")
                    data.pop('timestamp', None)
            
            saved_log = await db_add_token_usage_log(db, token_usage_data=data)
            
            if saved_log:
                self.logger.info(f"[{self._component_id}] Successfully saved token usage for agent_id: {data.get('agent_id')}, interaction_id: {data.get('interaction_id')}, db_id: {saved_log.id}")
            else:
                self.logger.error(f"[{self._component_id}] Failed to save token usage to DB (db_add_token_usage_log returned None) for agent_id: {data.get('agent_id')}, interaction_id: {data.get('interaction_id')}")

        except Exception as e:
            self.logger.error(f"[{self._component_id}] Exception during _save_token_usage_to_db for interaction_id {data.get('interaction_id')}: {e}", exc_info=True)

    async def process_message(self, task_data: Dict[str, Any]) -> None:
        """
        Processes a single token usage message from the queue.
        """
        self.logger.info(f"[{self._component_id}] RAW token usage data: {task_data}")
        self.logger.debug(f"[{self._component_id}] Received token usage data: {task_data}")

        if not self.async_session_factory:
            self.logger.error(f"[{self._component_id}] Async session factory not initialized. Cannot process message.")
            return

        agent_id = task_data.get('agent_id')
        interaction_id = task_data.get('interaction_id')
        message_id_to_save = None

        if agent_id and interaction_id:
            self.logger.info(f"[{self._component_id}] Attempting to find message_id for agent_id: {agent_id}, interaction_id: {interaction_id}")
            for attempt in range(3):
                async with self.async_session_factory() as db_session: # type: ignore
                    chat_message = await db_get_chat_message_by_interaction_id(
                        db_session, 
                        agent_id=agent_id, 
                        interaction_id=interaction_id,
                        sender_type=SenderType.AGENT
                    )
                    if not chat_message:
                        self.logger.debug(f"[{self._component_id}] AGENT message not found for interaction_id {interaction_id}, attempt {attempt+1}. Trying any sender type.")
                        chat_message = await db_get_chat_message_by_interaction_id(
                            db_session, 
                            agent_id=agent_id, 
                            interaction_id=interaction_id
                        )

                    if chat_message:
                        message_id_to_save = chat_message.id
                        self.logger.info(f"[{self._component_id}] Found message_id: {message_id_to_save} for interaction_id: {interaction_id} on attempt {attempt+1}")
                        break 
                    else:
                        self.logger.warning(f"[{self._component_id}] Could not find message_id for interaction_id: {interaction_id} (agent: {agent_id}) on attempt {attempt+1}. Retrying in {attempt+2}s...")
                        await asyncio.sleep(attempt + 2) 
            
            if not message_id_to_save:
                self.logger.error(f"[{self._component_id}] Failed to find message_id for interaction_id: {interaction_id} (agent: {agent_id}) after multiple attempts.")
        else:
            self.logger.warning(f"[{self._component_id}] Missing agent_id or interaction_id in task_data, cannot fetch message_id. Data: {task_data}")

        if message_id_to_save:
            task_data['message_id'] = message_id_to_save
        else:
            task_data.pop('message_id', None) 

        # Clean up old/temporary fields
        if 'thread_id' in task_data:
            self.logger.debug(f"[{self._component_id}] Removing 'thread_id': {task_data['thread_id']} from token usage data before saving.")
            del task_data['thread_id']
        
        if 'chat_message_id' in task_data: 
            self.logger.warning(f"[{self._component_id}] Removing deprecated 'chat_message_id': {task_data['chat_message_id']} from token usage data.")
            del task_data['chat_message_id']
            
        async with self.async_session_factory() as db_session: # type: ignore
            await self._save_token_usage_to_db(db_session, task_data)

if __name__ == "__main__":
    logging.basicConfig(
        level=settings.LOG_LEVEL,
        format='%(asctime)s - %(levelname)s - %(name)s - [%(component_id)s] - %(message)s'
    )
    main_logger = logging.getLogger("token_usage_worker_main")
    main_logger.info("Initializing TokenUsageWorker...")

    worker = TokenUsageWorker()

    try:
        asyncio.run(worker.run())
    except KeyboardInterrupt:
        main_logger.info("TokenUsageWorker interrupted by user (KeyboardInterrupt).")
    except Exception as e:
        main_logger.critical(f"TokenUsageWorker failed to start or run: {e}", exc_info=True)
    finally:
        main_logger.info("TokenUsageWorker application finished.")

