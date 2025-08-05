"""
Воркер для сохранения истории чатов в базу данных.

Модуль содержит HistorySaverWorker - воркер для обработки сообщений
из Redis очереди и сохранения их в PostgreSQL.
"""

import asyncio
import logging
from datetime import datetime, timezone
from typing import Dict, Any, Callable, Optional
from sqlalchemy.ext.asyncio import AsyncSession

from pydantic import ValidationError

from app.core.config import settings
from app.db.session import get_async_session_factory
from app.db.crud.chat_crud import db_add_chat_message
from app.api.schemas.chat_schemas import ChatMessageCreate, SenderType
from app.workers.base_worker import QueueWorker


class HistorySaverWorker(QueueWorker):
    """Воркер для сохранения сообщений истории чата из очереди Redis в базу данных."""

    def __init__(self):
        """Инициализировать HistorySaverWorker."""
        super().__init__(
            component_id="history_saver_worker",  # Unique ID for this worker instance
            queue_names=[settings.REDIS_HISTORY_QUEUE_NAME],
            status_key_prefix="worker_status:history_saver:",  # Specific status key prefix
        )
        self.async_session_factory: Optional[Callable[[], AsyncSession]] = None

    async def setup(self):
        """
        Инициализировать фабрику асинхронных сессий базы данных.

        Вызывает `super().setup()` для выполнения базовой настройки воркера,
        а затем получает и сохраняет фабрику сессий из `get_async_session_factory()`.
        """
        await super().setup()
        self.async_session_factory = get_async_session_factory()
        self.logger.info("[%s] Database session factory initialized.", self._component_id)

    async def process_message(self, message_data: Dict[str, Any]) -> None:
        """
        Валидировать данные сообщения чата и сохранить их в базу данных.

        Этот метод вызывается родительским классом `QueueWorker` для каждого
        сообщения, полученного из очереди Redis.

        Args:
            message_data: Данные сообщения для обработки
        """
        self.logger.debug("[%s] Received chat history data: %s", self._component_id, message_data)

        if not self._is_session_factory_ready():
            return

        try:
            message_schema = self._prepare_message_schema(message_data)
            await self._save_message_to_database(message_schema)
        except ValidationError as e:
            self.logger.error(
                "[%s] Validation error for chat message data: %s, errors: %s",
                self._component_id,
                message_data,
                e.errors(),
                exc_info=True,
            )
        except (ValueError, TypeError) as e:
            self.logger.error(
                "[%s] Error preparing chat message data %s: %s",
                self._component_id,
                message_data,
                e,
                exc_info=True,
            )

    def _is_session_factory_ready(self) -> bool:
        """Проверить, что фабрика сессий инициализирована."""
        if not self.async_session_factory:
            self.logger.error(
                "[%s] Async session factory not initialized. Cannot process message.",
                self._component_id,
            )
            return False
        return True

    def _prepare_message_schema(self, message_data: Dict[str, Any]) -> ChatMessageCreate:
        """
        Подготовить и валидировать схему сообщения.

        Args:
            message_data: Исходные данные сообщения

        Returns:
            ChatMessageCreate: Валидированная схема сообщения

        Raises:
            ValidationError: При ошибке валидации
            ValueError: При ошибке обработки данных
        """
        processed_data = message_data.copy()
        self._process_timestamp(processed_data)
        self._process_sender_type(processed_data)

        return ChatMessageCreate(**processed_data)

    def _process_timestamp(self, message_data: Dict[str, Any]) -> None:
        """Обработать и нормализовать timestamp в данных сообщения."""
        if "timestamp" in message_data and isinstance(message_data["timestamp"], str):
            try:
                dt = datetime.fromisoformat(message_data["timestamp"])
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)  # Assume UTC if naive
                message_data["timestamp"] = dt
            except ValueError:
                self.logger.warning(
                    "[%s] Could not parse timestamp string: %s. Using current UTC time.",
                    self._component_id,
                    message_data["timestamp"],
                )
                message_data["timestamp"] = datetime.now(timezone.utc)
        elif "timestamp" not in message_data:
            message_data["timestamp"] = datetime.now(timezone.utc)

    def _process_sender_type(self, message_data: Dict[str, Any]) -> None:
        """Обработать и валидировать sender_type в данных сообщения."""
        if "sender_type" in message_data and not isinstance(
            message_data["sender_type"], SenderType
        ):
            try:
                message_data["sender_type"] = SenderType(message_data["sender_type"])
            except ValueError:
                self.logger.warning(
                    "[%s] Invalid sender_type value: %s. Setting to 'user'.",
                    self._component_id,
                    message_data["sender_type"],
                )
                message_data["sender_type"] = SenderType.USER

    async def _save_message_to_database(self, message_schema: ChatMessageCreate) -> None:
        """
        Сохранить сообщение в базу данных.

        Args:
            message_schema: Валидированная схема сообщения

        Raises:
            Exception: При ошибке сохранения в БД
        """
        async with self.async_session_factory() as db:  # type: ignore
            try:
                saved_message = await db_add_chat_message(
                    db=db,  # type: ignore
                    agent_id=message_schema.agent_id,
                    thread_id=message_schema.thread_id,
                    sender_type=message_schema.sender_type,
                    content=message_schema.content,
                    channel=message_schema.channel,
                    timestamp=message_schema.timestamp,
                    interaction_id=message_schema.interaction_id,
                )
                self._log_save_result(saved_message, message_schema)
            except Exception as e:
                self.logger.error(
                    "[%s] Exception during save_chat_message_to_db for interaction_id %s: %s",
                    self._component_id,
                    message_schema.interaction_id,
                    e,
                    exc_info=True,
                )
                raise

    def _log_save_result(self, saved_message, message_schema: ChatMessageCreate) -> None:
        """Логировать результат сохранения сообщения."""
        if saved_message:
            self.logger.info(
                "[%s] Successfully saved chat message for agent_id: %s, "
                "interaction_id: %s, db_id: %s",
                self._component_id,
                message_schema.agent_id,
                message_schema.interaction_id,
                saved_message.id,
            )
        else:
            self.logger.error(
                "[%s] Failed to save chat message to DB (db_add_chat_message returned None) "
                "for agent_id: %s, interaction_id: %s",
                self._component_id,
                message_schema.agent_id,
                message_schema.interaction_id,
            )


if __name__ == "__main__":
    logging.basicConfig(
        level=settings.LOG_LEVEL,
        format="%(asctime)s - %(levelname)s - %(name)s - [%(component_id)s] - %(message)s",
    )

    main_logger = logging.getLogger("history_saver_worker_main")

    main_logger.info("Initializing HistorySaverWorker...")

    worker = HistorySaverWorker()

    try:
        asyncio.run(worker.run())
    except KeyboardInterrupt:
        main_logger.info("HistorySaverWorker interrupted by user (KeyboardInterrupt).")
        # The worker.run() method's finally block (from RunnableComponent) should handle cleanup.
    except Exception as e:
        main_logger.critical("HistorySaverWorker failed to start or run: %s", e, exc_info=True)
    finally:
        main_logger.info("HistorySaverWorker application finished.")
