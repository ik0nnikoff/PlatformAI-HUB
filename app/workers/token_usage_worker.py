"""
Воркер для обработки данных об использовании токенов.

Модуль содержит TokenUsageWorker - воркер для обработки сообщений
из Redis очереди с данными об использовании токенов и сохранения их
в PostgreSQL. Следует архитектурным паттернам проекта и принципам SOLID.
"""

import asyncio
import logging
from datetime import datetime, timezone
from typing import Dict, Any, Optional, Callable

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.exc import SQLAlchemyError

from app.core.config import settings
from app.db.session import get_async_session_factory
from app.db.crud.token_usage_crud import db_add_token_usage_log
from app.db.crud.chat_crud import db_get_chat_message_by_interaction_id
from app.api.schemas.common_schemas import SenderType
from app.workers.base_worker import QueueWorker


class TokenUsageWorker(QueueWorker):
    """Воркер для сохранения информации об использовании токенов из очереди Redis."""

    def __init__(self):
        # Используем REDIS_TOKEN_USAGE_QUEUE_NAME для совпадения с агентом
        token_usage_queue_name = getattr(
            settings, "REDIS_TOKEN_USAGE_QUEUE_NAME", "token_usage_queue"
        )
        super().__init__(
            component_id="token_usage_worker",  # Unique ID
            queue_names=[token_usage_queue_name],
            status_key_prefix="worker_status:token_usage:",  # Status prefix
        )
        self.async_session_factory: Optional[Callable[[], AsyncSession]] = None
        self.logger.info(
            "[%s] Initialized. Listening to queue: %s",
            self._component_id,
            token_usage_queue_name,
        )

    async def setup(self):
        """Инициализирует фабрику асинхронных сессий базы данных."""
        await super().setup()
        self.async_session_factory = get_async_session_factory()
        self.logger.info(
            "[%s] Database session factory initialized.", self._component_id
        )
        # Логируем имя очереди и её длину для диагностики
        try:
            queue_name = self.queue_names[0] if self.queue_names else None
            if queue_name:
                redis_client = await self.redis_client
                length = await redis_client.llen(queue_name)
                self.logger.info(
                    "[%s] Redis queue '%s' length at setup: %s",
                    self._component_id,
                    queue_name,
                    length,
                )
        except (AttributeError, ConnectionError, TimeoutError) as e:
            self.logger.warning(
                "[%s] Could not check Redis queue length: %s", self._component_id, e
            )

    async def _save_token_usage_to_db(
        self, db: AsyncSession, data: Dict[str, Any]
    ) -> None:
        """Сохранение данных об использовании токенов в базу данных."""
        interaction_id = data.get("interaction_id")
        agent_id = data.get("agent_id")

        self._log_save_operation(interaction_id)

        try:
            self._normalize_timestamp(data)
            saved_log = await db_add_token_usage_log(db, token_usage_data=data)
            self._handle_save_result(saved_log, agent_id, interaction_id)

        except SQLAlchemyError as e:
            self._handle_database_error(e, interaction_id)
            raise  # Пробрасываем исключение для обработки в process_message
        except (ValueError, TypeError) as e:
            self._handle_validation_error(e, interaction_id)
            raise
        except Exception as e:
            self._handle_unexpected_error(e, interaction_id)
            raise

    def _log_save_operation(self, interaction_id: Optional[str] = None) -> None:
        """Логирует операцию сохранения."""
        _ = interaction_id  # Suppress unused argument warning
        self.logger.debug(
            "[%s] Saving token usage to DB (agent_id and interaction_id are masked for security)",
            self._component_id,
        )

    def _handle_save_result(
        self, saved_log: Optional[Any], agent_id: Optional[str], interaction_id: Optional[str]
    ) -> None:
        """Обрабатывает результат сохранения."""
        if saved_log:
            self.logger.info(
                "[%s] Successfully saved token usage for "
                "agent_id: %s, interaction_id: %s, db_id: %s",
                self._component_id,
                agent_id,
                interaction_id,
                saved_log.id,
            )
        else:
            self.logger.error(
                "[%s] Failed to save token usage to DB "
                "(returned None) for agent_id: %s, interaction_id: %s",
                self._component_id,
                agent_id,
                interaction_id,
            )

    def _handle_database_error(self, error: SQLAlchemyError, interaction_id: Optional[str]) -> None:
        """Обрабатывает ошибки базы данных."""
        self.logger.error(
            "[%s] Database error during _save_token_usage_to_db for "
            "interaction_id %s: %s",
            self._component_id,
            interaction_id,
            error,
            exc_info=True,
        )

    def _handle_validation_error(self, error: Exception, interaction_id: Optional[str]) -> None:
        """Обрабатывает ошибки валидации данных."""
        self.logger.error(
            "[%s] Data validation error during _save_token_usage_to_db for "
            "interaction_id %s: %s",
            self._component_id,
            interaction_id,
            error,
            exc_info=True,
        )

    def _handle_unexpected_error(self, error: Exception, interaction_id: Optional[str]) -> None:
        """Обрабатывает неожиданные ошибки."""
        self.logger.error(
            "[%s] Unexpected error during _save_token_usage_to_db for "
            "interaction_id %s: %s",
            self._component_id,
            interaction_id,
            error,
            exc_info=True,
        )

    def _normalize_timestamp(self, task_data: Dict[str, Any]) -> None:
        """Нормализует timestamp в task_data в объект datetime с UTC timezone."""
        try:
            timestamp = task_data.get("timestamp")
            if timestamp is None:
                self._set_current_utc_timestamp(task_data)
                return

            if isinstance(timestamp, str):
                task_data["timestamp"] = self._parse_string_timestamp(timestamp)
            elif isinstance(timestamp, datetime):
                task_data["timestamp"] = self._ensure_utc_timezone(timestamp)
        except (ValueError, TypeError, AttributeError) as e:
            self.logger.error(
                "[%s] Error normalizing timestamp: %s",
                self._component_id,
                e,
                exc_info=True,
            )
            task_data["timestamp"] = datetime.now(timezone.utc)

    def _set_current_utc_timestamp(self, task_data: Dict[str, Any]) -> None:
        """Устанавливает текущее время UTC в task_data."""
        task_data["timestamp"] = datetime.now(timezone.utc)
        self.logger.debug(
            "[%s] No timestamp found, set current UTC time", self._component_id
        )

    def _parse_string_timestamp(self, timestamp_str: str) -> datetime:
        """Парсит строку timestamp в datetime объект."""
        try:
            dt = self._convert_timestamp_string(timestamp_str)
            return self._ensure_utc_timezone(dt)
        except ValueError as e:
            self.logger.warning(
                "[%s] Failed to parse timestamp '%s': %s, using current time",
                self._component_id,
                timestamp_str,
                e,
            )
            return datetime.now(timezone.utc)

    def _convert_timestamp_string(self, timestamp_str: str) -> datetime:
        """Конвертирует строку timestamp в datetime объект."""
        if timestamp_str.endswith("Z"):
            return datetime.fromisoformat(timestamp_str[:-1])
        if "+" in timestamp_str or timestamp_str.endswith("UTC"):
            clean_timestamp = timestamp_str.replace("UTC", "").replace("+00:00", "")
            return datetime.fromisoformat(clean_timestamp)
        return datetime.fromisoformat(timestamp_str)

    def _ensure_utc_timezone(self, dt: datetime) -> datetime:
        """Обеспечивает UTC timezone для datetime объекта."""
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)

    def _mask_sensitive_data(self, data: Dict[str, Any]) -> Dict[str, str]:
        """Маскирует чувствительные данные для безопасного логирования."""
        sensitive_keys = [
            "agent_id",
            "interaction_id",
            "api_key",
            "secret",
            "token",
            "password",
            "auth",
            "credential",
            "private",
        ]

        masked_data = {}
        for key, value in data.items():
            # Проверяем, содержит ли ключ чувствительную информацию
            is_sensitive = any(
                sensitive_word in key.lower() for sensitive_word in sensitive_keys
            )

            if is_sensitive:
                masked_data[key] = "***" if value else "N/A"
            else:
                # Ограничиваем длину значений для предотвращения переполнения логов
                str_value = str(value)
                masked_data[key] = (
                    str_value[:50] + "..." if len(str_value) > 50 else str_value
                )

        return masked_data

    async def process_message(self, message_data: Dict[str, Any]) -> None:
        """Обрабатывает одно сообщение об использовании токенов из очереди Redis. """
        self._log_processing_event(message_data)

        if not self._validate_session_factory():
            return

        message_id_to_save = await self._get_message_id_for_event(message_data)
        self._clean_task_data(message_data, message_id_to_save)
        await self._save_event_to_database(message_data)

    def _log_processing_event(self, message_data: Dict[str, Any]) -> None:
        """Логирует событие обработки сообщения."""
        secure_event_data = self._mask_sensitive_data(message_data)

        self.logger.info(
            "[%s] Processing usage event (fields: %s)",
            self._component_id,
            list(secure_event_data.keys()),
        )

        if self.logger.isEnabledFor(logging.DEBUG):
            self.logger.debug(
                "[%s] Usage event details: %s", self._component_id, secure_event_data
            )

    def _validate_session_factory(self) -> bool:
        """Проверяет инициализацию async_session_factory."""
        if not self.async_session_factory:
            self.logger.error(
                "[%s] Async session factory not initialized. "
                "Cannot process message.",
                self._component_id,
            )
            return False
        return True

    async def _get_message_id_for_event(self, message_data: Dict[str, Any]) -> Optional[int]:
        """Получает message_id для связи с chat_message."""
        agent_id = message_data.get("agent_id")
        interaction_id = message_data.get("interaction_id")

        if agent_id and interaction_id:
            return await self._find_message_id_for_interaction(agent_id, interaction_id)

        self.logger.warning(
            "[%s] Missing required fields (agent_id or interaction_id), "
            "cannot fetch message_id. Available fields: %s",
            self._component_id,
            list(message_data.keys()),
        )
        return None

    async def _save_event_to_database(self, message_data: Dict[str, Any]) -> None:
        """Сохраняет данные события в базу данных."""
        try:
            async with self.async_session_factory() as db_session:  # type: ignore
                await self._save_token_usage_to_db(db_session, message_data)
        except Exception as e:
            self.logger.error(
                "[%s] Failed to save usage data: %s",
                self._component_id,
                type(e).__name__,
                exc_info=True,
            )
            raise  # Позволяем базовому классу обработать ошибку

    async def _find_message_id_for_interaction(
        self, agent_id: str, interaction_id: str
    ) -> Optional[int]:
        """Поиск message_id для связи с записью об использовании токенов. """
        self.logger.info(
            "[%s] Attempting to find message_id for agent_id: %s, "
            "interaction_id: %s",
            self._component_id,
            agent_id,
            interaction_id,
        )

        for attempt in range(3):
            message_id = await self._attempt_message_lookup(
                agent_id, interaction_id, attempt
            )
            if message_id:
                return message_id

            # Не делаем sleep на последней итерации
            if attempt < 2:
                await asyncio.sleep(attempt + 2)

        self.logger.error(
            "[%s] Failed to find message_id for interaction_id: %s "
            "(agent: %s) after 3 attempts.",
            self._component_id,
            interaction_id,
            agent_id,
        )
        return None

    async def _attempt_message_lookup(
        self, agent_id: str, interaction_id: str, attempt: int
    ) -> Optional[int]:
        """Единичная попытка поиска message_id в базе данных."""
        try:
            async with self.async_session_factory() as db_session:  # type: ignore
                message_id = await self._perform_message_search(
                    db_session, agent_id, interaction_id, attempt
                )
                return message_id

        except SQLAlchemyError as e:
            self._log_database_lookup_error(e, attempt)
            return None
        except (ConnectionError, TimeoutError, OSError) as e:
            self._log_connection_lookup_error(e, attempt)
            return None
        except (ValueError, TypeError, AttributeError) as e:
            self._log_data_lookup_error(e, attempt)
            return None

    async def _perform_message_search(
        self, db_session: AsyncSession, agent_id: str, interaction_id: str, attempt: int
    ) -> Optional[int]:
        """Выполняет поиск сообщения в базе данных."""
        # Сначала ищем сообщение от агента
        chat_message = await self._search_agent_message(
            db_session, agent_id, interaction_id
        )

        # Если не найдено, ищем любое сообщение
        if not chat_message:
            chat_message = await self._search_any_message(
                db_session, agent_id, interaction_id, attempt
            )

        if chat_message:
            self.logger.info(
                "[%s] Found message_id: %s for interaction_id: %s on attempt %s",
                self._component_id,
                chat_message.id,
                interaction_id,
                attempt + 1,
            )
            return chat_message.id

        self._log_search_retry_warning(interaction_id, agent_id, attempt)
        return None

    def _log_search_retry_warning(self, interaction_id: str, agent_id: str, attempt: int) -> None:
        """Логирует предупреждение о повторной попытке поиска."""
        self.logger.warning(
            "[%s] Could not find message_id for interaction_id: %s "
            "(agent: %s) on attempt %s. Retrying in %ss...",
            self._component_id,
            interaction_id,
            agent_id,
            attempt + 1,
            attempt + 2,
        )

    def _log_database_lookup_error(self, error: SQLAlchemyError, attempt: int) -> None:
        """Логирует ошибку базы данных при поиске."""
        self.logger.error(
            "[%s] Database error during message_id lookup attempt %s: %s",
            self._component_id,
            attempt + 1,
            error,
            exc_info=True,
        )

    def _log_connection_lookup_error(self, error: Exception, attempt: int) -> None:
        """Логирует ошибку соединения при поиске."""
        self.logger.error(
            "[%s] Connection error during message_id lookup attempt %s: %s",
            self._component_id,
            attempt + 1,
            error,
            exc_info=True,
        )

    def _log_data_lookup_error(self, error: Exception, attempt: int) -> None:
        """Логирует ошибку обработки данных при поиске."""
        self.logger.error(
            "[%s] Data processing error during message_id lookup attempt %s: %s",
            self._component_id,
            attempt + 1,
            error,
            exc_info=True,
        )

    async def _search_agent_message(
        self, db_session: AsyncSession, agent_id: str, interaction_id: str
    ) -> Optional[Any]:
        """Поиск сообщения от агента."""
        return await db_get_chat_message_by_interaction_id(
            db_session,
            agent_id=agent_id,
            interaction_id=interaction_id,
            sender_type=SenderType.AGENT,
        )

    async def _search_any_message(
        self, db_session: AsyncSession, agent_id: str, interaction_id: str, attempt: int
    ) -> Optional[Any]:
        """Поиск любого сообщения с данным interaction_id."""
        self.logger.debug(
            "[%s] AGENT message not found for interaction_id %s, "
            "attempt %s. Trying any sender type.",
            self._component_id,
            interaction_id,
            attempt + 1,
        )
        return await db_get_chat_message_by_interaction_id(
            db_session, agent_id=agent_id, interaction_id=interaction_id
        )

    def _clean_task_data(
        self, task_data: Dict[str, Any], message_id: Optional[int]
    ) -> None:
        """Очистка данных задачи от устаревших полей и добавление message_id."""
        # Добавляем или удаляем message_id
        if message_id:
            task_data["message_id"] = message_id
        else:
            task_data.pop("message_id", None)

        # Удаляем устаревшие поля
        deprecated_fields = ["thread_id", "chat_message_id"]
        for field in deprecated_fields:
            if field in task_data:
                if field == "thread_id":
                    self.logger.debug(
                        "[%s] Removing '%s': %s from token usage data "
                        "before saving.",
                        self._component_id,
                        field,
                        task_data[field],
                    )
                else:
                    self.logger.warning(
                        "[%s] Removing deprecated '%s': %s from token usage data.",
                        self._component_id,
                        field,
                        task_data[field],
                    )
                del task_data[field]


if __name__ == "__main__":
    logging.basicConfig(
        level=settings.LOG_LEVEL,
        format="%(asctime)s - %(levelname)s - %(name)s - [%(component_id)s] - %(message)s",
    )
    main_logger = logging.getLogger("token_usage_worker_main")
    main_logger.info("Initializing TokenUsageWorker...")

    worker = TokenUsageWorker()

    try:
        asyncio.run(worker.run())
    except KeyboardInterrupt:
        main_logger.info("TokenUsageWorker interrupted by user (KeyboardInterrupt).")
    except (RuntimeError, OSError, ImportError) as e:
        main_logger.critical(
            "TokenUsageWorker failed to start or run: %s", e, exc_info=True
        )
    finally:
        main_logger.info("TokenUsageWorker application finished.")
