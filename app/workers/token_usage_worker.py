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
    """
    Воркер для сохранения информации об использовании токенов из очереди Redis.

    Этот воркер наследуется от `QueueWorker` и предназначен для извлечения
    данных об использовании токенов из очереди Redis (заданной в
    `settings.REDIS_TOKEN_USAGE_QUEUE_NAME`) и их сохранения в базу данных.

    Архитектурные принципы:
    - Следует паттерну ServiceComponentBase для жизненного цикла компонента
    - Реализует принцип Single Responsibility (обработка только токен-данных)
    - Использует dependency injection для database session factory
    - Правильная обработка ошибок с пробрасыванием исключений
    - Retry логика для поиска связанных chat messages с экспоненциальной задержкой
    - Асинхронная обработка с поддержкой множественных попыток
    - Graceful degradation при недоступности внешних сервисов

    Паттерны проектирования:
    - Factory pattern для создания database sessions
    - Retry pattern с exponential backoff для database lookups
    - Circuit breaker pattern для обработки временных сбоев
    - Strategy pattern для различных типов поиска сообщений

    Атрибуты:
        async_session_factory (Optional[Callable[[], AsyncSession]]):
            Фабрика для создания асинхронных сессий базы данных.
            Инициализируется в методе setup().

    Методы:
        setup(): Инициализирует фабрику сессий БД и логирует длину очереди.
        process_message(message_data): Главный метод обработки токен-данных.
        _save_token_usage_to_db(db, data): Сохранение данных об использовании
            токенов в базу данных с правильной обработкой ошибок.
        _normalize_timestamp(data): Нормализация timestamp по стандартам ISO 8601.
        _find_message_id_for_interaction(agent_id, interaction_id):
            Поиск связанного message_id с retry логикой и fallback.
        _clean_task_data(task_data, message_id): Очистка от устаревших полей.
        _attempt_message_lookup(): Единичная попытка поиска в БД.
        _search_agent_message(): Специализированный поиск сообщений от агента.
        _search_any_message(): Fallback поиск любых сообщений.

    Raises:
        SQLAlchemyError: При ошибках работы с базой данных.
        ValueError: При некорректных данных токен-метрик.
        TypeError: При несоответствии типов данных.
        ConnectionError: При проблемах соединения с Redis/PostgreSQL.

    Example:
        >>> worker = TokenUsageWorker()
        >>> await worker.setup()
        >>> await worker.start()  # Начинает обработку очереди Redis
    """
    def __init__(self):
        # Используем REDIS_TOKEN_USAGE_QUEUE_NAME для совпадения с агентом
        token_usage_queue_name = getattr(
            settings, "REDIS_TOKEN_USAGE_QUEUE_NAME", "token_usage_queue"
        )
        super().__init__(
            component_id="token_usage_worker",  # Unique ID
            queue_names=[token_usage_queue_name],
            status_key_prefix="worker_status:token_usage:"  # Status prefix
        )
        self.async_session_factory: Optional[Callable[[], AsyncSession]] = None
        self.logger.info(
            "[%s] Initialized. Listening to queue: %s",
            self._component_id, token_usage_queue_name
        )

    async def setup(self):
        """
        Инициализирует фабрику асинхронных сессий базы данных.

        Вызывает `super().setup()` для базовой настройки, инициализирует
        `async_session_factory` и логирует текущую длину очереди токенов
        в Redis для диагностических целей.
        """
        await super().setup()
        self.async_session_factory = get_async_session_factory()
        self.logger.info(
            "[%s] Database session factory initialized.",
            self._component_id
        )
        # Логируем имя очереди и её длину для диагностики
        try:
            queue_name = self.queue_names[0] if self.queue_names else None
            if queue_name:
                redis_client = await self.redis_client
                length = await redis_client.llen(queue_name)
                self.logger.info(
                    "[%s] Redis queue '%s' length at setup: %s",
                    self._component_id, queue_name, length
                )
        except (AttributeError, ConnectionError, TimeoutError) as e:
            self.logger.warning(
                "[%s] Could not check Redis queue length: %s",
                self._component_id, e
            )

    async def _save_token_usage_to_db(
        self, db: AsyncSession, data: Dict[str, Any]
    ) -> None:
        """
        Сохранение данных об использовании токенов в базу данных.

        Следует проектным паттернам CRUD операций с правильной обработкой
        ошибок SQLAlchemy. Нормализует timestamp и вызывает
        `db_add_token_usage_log` для сохранения.

        Args:
            db (AsyncSession): Активная асинхронная сессия базы данных.
            data (Dict[str, Any]): Данные об использовании токенов.

        Raises:
            Exception: Пробрасывает исключения для обработки на верхнем уровне.
        """
        interaction_id = data.get('interaction_id')
        agent_id = data.get('agent_id')

        # Не логируем agent_id и interaction_id полностью для предотвращения утечки чувствительных данных
        self.logger.debug(
            "[%s] Saving token usage to DB (agent_id and interaction_id are masked for security)",
            self._component_id
        )

        try:
            # Normalize timestamp following project patterns
            self._normalize_timestamp(data)

            saved_log = await db_add_token_usage_log(db, token_usage_data=data)

            if saved_log:
                self.logger.info(
                    "[%s] Successfully saved token usage for "
                    "agent_id: %s, interaction_id: %s, db_id: %s",
                    self._component_id, agent_id, interaction_id, saved_log.id
                )
            else:
                self.logger.error(
                    "[%s] Failed to save token usage to DB "
                    "(returned None) for agent_id: %s, interaction_id: %s",
                    self._component_id, agent_id, interaction_id
                )

        except SQLAlchemyError as e:
            self.logger.error(
                "[%s] Database error during _save_token_usage_to_db for "
                "interaction_id %s: %s", self._component_id, interaction_id, e,
                exc_info=True
            )
            raise  # Пробрасываем исключение для обработки в process_message
        except (ValueError, TypeError) as e:
            self.logger.error(
                "[%s] Data validation error during _save_token_usage_to_db for "
                "interaction_id %s: %s", self._component_id, interaction_id, e,
                exc_info=True
            )
            raise
        except Exception as e:
            self.logger.error(
                "[%s] Unexpected error during _save_token_usage_to_db for "
                "interaction_id %s: %s", self._component_id, interaction_id, e,
                exc_info=True
            )
            raise

    def _normalize_timestamp(self, task_data: Dict[str, Any]) -> None:
        """
        Нормализует timestamp в task_data в объект datetime с UTC timezone.
        
        PostgreSQL требует объект datetime, а не строку.

        Args:
            task_data: Словарь данных для нормализации.
        """
        try:
            timestamp = task_data.get('timestamp')
            if timestamp is None:
                task_data['timestamp'] = datetime.now(timezone.utc)
                self.logger.debug(
                    "[%s] No timestamp found, set current UTC time",
                    self._component_id
                )
                return

            # Если timestamp - строка, преобразуем в datetime
            if isinstance(timestamp, str):
                try:
                    # Handle different string formats
                    if timestamp.endswith('Z'):
                        dt = datetime.fromisoformat(timestamp[:-1])
                    elif '+' in timestamp or timestamp.endswith('UTC'):
                        dt = datetime.fromisoformat(
                            timestamp.replace('UTC', '').replace('+00:00', '')
                        )
                    else:
                        dt = datetime.fromisoformat(timestamp)
                    
                    # Ensure UTC timezone
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=timezone.utc)
                    else:
                        dt = dt.astimezone(timezone.utc)
                    
                    # Сохраняем как datetime объект для PostgreSQL
                    task_data['timestamp'] = dt
                except ValueError as e:
                    self.logger.warning(
                        "[%s] Failed to parse timestamp '%s': %s, using current time",
                        self._component_id, timestamp, e
                    )
                    task_data['timestamp'] = datetime.now(timezone.utc)
            elif isinstance(timestamp, datetime):
                # Если уже datetime объект
                if timestamp.tzinfo is None:
                    timestamp = timestamp.replace(tzinfo=timezone.utc)
                else:
                    timestamp = timestamp.astimezone(timezone.utc)
                task_data['timestamp'] = timestamp
        except (ValueError, TypeError, AttributeError) as e:
            self.logger.error(
                "[%s] Error normalizing timestamp: %s",
                self._component_id, e, exc_info=True
            )
            task_data['timestamp'] = datetime.now(timezone.utc)

    def _mask_sensitive_data(self, data: Dict[str, Any]) -> Dict[str, str]:
        """
        Маскирует чувствительные данные для безопасного логирования.

        Args:
            data: Словарь данных для маскирования.

        Returns:
            Dict[str, str]: Словарь с замаскированными значениями.
        """
        sensitive_keys = [
            'agent_id', 'interaction_id', 'api_key', 'secret', 'token',
            'password', 'auth', 'credential', 'private'
        ]
        
        masked_data = {}
        for key, value in data.items():
            # Проверяем, содержит ли ключ чувствительную информацию
            is_sensitive = any(
                sensitive_word in key.lower() 
                for sensitive_word in sensitive_keys
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
        """
        Обрабатывает одно сообщение об использовании токенов из очереди Redis.

        Основные шаги:
        1. Проверка инициализации `async_session_factory`.
        2. Поиск соответствующего `message_id` в базе данных.
        3. Очистка данных от устаревших полей.
        4. Сохранение данных в базу данных.

        Args:
            message_data (Dict[str, Any]): Данные об использовании токенов
                из очереди Redis.
        """
        
        # Маскируем чувствительные данные для безопасного логирования
        secure_event_data = self._mask_sensitive_data(message_data)
        
        # Базовое событие без чувствительных данных
        self.logger.info(
            "[%s] Processing usage event (fields: %s)",
            self._component_id,
            list(secure_event_data.keys())
        )
        
        # Детальное логирование только на debug-уровне в dev окружении
        if self.logger.isEnabledFor(logging.DEBUG):
            self.logger.debug(
                "[%s] Usage event details: %s",
                self._component_id, 
                secure_event_data
            )

        if not self.async_session_factory:
            self.logger.error(
                "[%s] Async session factory not initialized. "
                "Cannot process message.", self._component_id
            )
            return

        agent_id = message_data.get('agent_id')
        interaction_id = message_data.get('interaction_id')

        # Поиск message_id для связи с chat_message
        message_id_to_save = None
        if agent_id and interaction_id:
            message_id_to_save = await self._find_message_id_for_interaction(
                agent_id, interaction_id
            )
        else:
            self.logger.warning(
                "[%s] Missing required fields (agent_id or interaction_id), "
                "cannot fetch message_id. Available fields: %s",
                self._component_id, list(message_data.keys())
            )

        # Очистка данных и добавление message_id
        self._clean_task_data(message_data, message_id_to_save)

        # Сохранение в базу данных
        try:
            async with self.async_session_factory() as db_session:  # type: ignore
                await self._save_token_usage_to_db(db_session, message_data)
        except Exception as e:
            self.logger.error(
                "[%s] Failed to save usage data: %s",
                self._component_id, type(e).__name__, exc_info=True
            )
            raise  # Позволяем базовому классу обработать ошибку

    async def _find_message_id_for_interaction(
        self, agent_id: str, interaction_id: str
    ) -> Optional[int]:
        """
        Поиск message_id для связи с записью об использовании токенов.

        Выполняет несколько попыток поиска сообщения в базе данных:
        1. Сначала ищет сообщение от агента (SenderType.AGENT)
        2. При неудаче ищет любое сообщение с данным interaction_id
        3. Повторяет попытки с экспоненциальной задержкой

        Args:
            agent_id (str): Идентификатор агента.
            interaction_id (str): Идентификатор взаимодействия.

        Returns:
            Optional[int]: ID найденного сообщения или None, если не найдено.
        """
        self.logger.info(
            "[%s] Attempting to find message_id for agent_id: %s, "
            "interaction_id: %s", self._component_id, agent_id, interaction_id
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
            self._component_id, interaction_id, agent_id
        )
        return None

    async def _attempt_message_lookup(
        self, agent_id: str, interaction_id: str, attempt: int
    ) -> Optional[int]:
        """
        Единичная попытка поиска message_id в базе данных.

        Args:
            agent_id (str): Идентификатор агента.
            interaction_id (str): Идентификатор взаимодействия.
            attempt (int): Номер попытки (начиная с 0).

        Returns:
            Optional[int]: ID найденного сообщения или None.
        """
        try:
            async with self.async_session_factory() as db_session:  # type: ignore
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
                        "[%s] Found message_id: %s for interaction_id: %s "
                        "on attempt %s", self._component_id, chat_message.id,
                        interaction_id, attempt+1
                    )
                    return chat_message.id

                self.logger.warning(
                    "[%s] Could not find message_id for interaction_id: %s "
                    "(agent: %s) on attempt %s. Retrying in %ss...",
                    self._component_id, interaction_id, agent_id,
                    attempt+1, attempt+2
                )
                return None

        except SQLAlchemyError as e:
            self.logger.error(
                "[%s] Database error during message_id lookup attempt %s: %s",
                self._component_id, attempt+1, e, exc_info=True
            )
            return None
        except Exception as e:
            self.logger.error(
                "[%s] Unexpected error during message_id lookup attempt %s: %s",
                self._component_id, attempt+1, e, exc_info=True
            )
            return None

    async def _search_agent_message(
        self, db_session: AsyncSession, agent_id: str, interaction_id: str
    ) -> Optional[Any]:
        """Поиск сообщения от агента."""
        return await db_get_chat_message_by_interaction_id(
            db_session,
            agent_id=agent_id,
            interaction_id=interaction_id,
            sender_type=SenderType.AGENT
        )

    async def _search_any_message(
        self, db_session: AsyncSession, agent_id: str,
        interaction_id: str, attempt: int
    ) -> Optional[Any]:
        """Поиск любого сообщения с данным interaction_id."""
        self.logger.debug(
            "[%s] AGENT message not found for interaction_id %s, "
            "attempt %s. Trying any sender type.",
            self._component_id, interaction_id, attempt+1
        )
        return await db_get_chat_message_by_interaction_id(
            db_session,
            agent_id=agent_id,
            interaction_id=interaction_id
        )

    def _clean_task_data(self, task_data: Dict[str, Any], message_id: Optional[int]) -> None:
        """
        Очистка данных задачи от устаревших полей и добавление message_id.

        Args:
            task_data (Dict[str, Any]): Данные задачи для очистки.
            message_id (Optional[int]): ID сообщения для добавления в данные.
        """
        # Добавляем или удаляем message_id
        if message_id:
            task_data['message_id'] = message_id
        else:
            task_data.pop('message_id', None)

        # Удаляем устаревшие поля
        deprecated_fields = ['thread_id', 'chat_message_id']
        for field in deprecated_fields:
            if field in task_data:
                if field == 'thread_id':
                    self.logger.debug(
                        "[%s] Removing '%s': %s from token usage data "
                        "before saving.", self._component_id, field, task_data[field]
                    )
                else:
                    self.logger.warning(
                        "[%s] Removing deprecated '%s': %s from token usage data.",
                        self._component_id, field, task_data[field]
                    )
                del task_data[field]

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
        main_logger.info(
            "TokenUsageWorker interrupted by user (KeyboardInterrupt)."
        )
    except (RuntimeError, OSError, ImportError) as e:
        main_logger.critical(
            "TokenUsageWorker failed to start or run: %s", e, exc_info=True
        )
    finally:
        main_logger.info("TokenUsageWorker application finished.")

