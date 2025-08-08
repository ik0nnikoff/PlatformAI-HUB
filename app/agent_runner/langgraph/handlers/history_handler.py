"""
History management utilities for AgentRunner.

This module provides database history loading and caching functionality.
"""

import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import redis.exceptions as redis_exceptions
from langchain_core.messages import BaseMessage, AIMessage, HumanMessage
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.agent_runner.langgraph.contexts import HistorySaveContext
from app.core.config import settings
from app.db.alchemy_models import ChatMessageDB, SenderType
from app.db.crud.chat_crud import db_get_recent_chat_history


def convert_db_to_langchain(
    db_messages: List[ChatMessageDB], logger
) -> List[BaseMessage]:
    """Converts messages from DB format (ChatMessageDB) to LangChain BaseMessage list."""
    converted = []
    if not ChatMessageDB or not SenderType:
        logger.error(
            "ChatMessageDB model or SenderType Enum not available for history conversion."
        )
        return []

    for msg in db_messages:
        if not isinstance(msg, ChatMessageDB):
            logger.warning(
                f"Skipping message conversion due to unexpected type: {type(msg)}"
            )
            continue

        if msg.sender_type == SenderType.USER:
            converted.append(HumanMessage(content=msg.content))
        elif msg.sender_type == SenderType.AGENT:
            converted.append(AIMessage(content=msg.content))
        else:
            logger.warning(
                f"Skipping message conversion due to unhandled sender_type: "
                f"{msg.sender_type}"
            )
    return converted


class HistoryManager:
    """Manages chat history loading and caching."""

    def __init__(
        self,
        component_id: str,
        db_session_factory: Optional[async_sessionmaker[AsyncSession]],
        logger,
    ):
        self.component_id = component_id
        self.db_session_factory = db_session_factory
        self.logger = logger
        self.loaded_threads_key = f"agent_threads:{component_id}"

    def can_load_history(self) -> bool:
        """Проверяет, доступны ли необходимые компоненты для загрузки истории."""
        return (
            db_get_recent_chat_history is not None
            and self.db_session_factory is not None
            and ChatMessageDB is not None
            and SenderType is not None
        )

    async def get_redis_client_for_history(self, redis_client):
        """Получает Redis клиент для работы с историей."""
        try:
            return await redis_client
        except RuntimeError as exc:
            self.logger.error(
                "Redis client not available for handling pubsub message: %s", exc
            )
            return None

    def get_memory_config(self, agent_config: Dict[str, Any]) -> Tuple[bool, int]:
        """Получает конфигурацию памяти агента."""
        # Extract model config from agent_config
        simple_config = agent_config.get("config", {}).get("simple", {})
        model_config = simple_config.get("model_config", {})

        enable_memory = model_config.get("enable_context_memory", True)
        history_limit = model_config.get("context_memory_depth", 10)

        return enable_memory, history_limit

    async def load_history_from_db(
        self, thread_id: str, history_limit: int
    ) -> List[BaseMessage]:
        """Загружает историю из базы данных."""
        self.logger.info(
            "Thread '%s' not found in cache '%s'. Loading history from DB with depth %d.",
            thread_id, self.loaded_threads_key, history_limit
        )

        async with self.db_session_factory() as session:
            history_from_db = await db_get_recent_chat_history(
                db=session,
                agent_id=self.component_id,
                thread_id=thread_id,
                limit=history_limit,
            )
            loaded_msgs = convert_db_to_langchain(history_from_db, self.logger)

        self.logger.info(
            "Loaded %d messages from DB for thread '%s'.", len(loaded_msgs), thread_id
        )

        return loaded_msgs

    async def cache_thread(self, redis_cli, thread_id: str) -> None:
        """Добавляет thread в кэш Redis."""
        await redis_cli.sadd(self.loaded_threads_key, thread_id)
        self.logger.info(
            "Added thread '%s' to cache '%s'.", thread_id, self.loaded_threads_key
        )

    async def check_thread_cache(self, redis_cli, thread_id: str) -> bool:
        """Проверяет, есть ли thread в кэше."""
        return await redis_cli.sismember(self.loaded_threads_key, thread_id)

    async def handle_unavailable_history(
        self, redis_cli, thread_id: str, can_load: bool, history_limit: int
    ) -> List[BaseMessage]:
        """Обрабатывает случай, когда история недоступна."""
        if not can_load or history_limit <= 0:
            if not await self.check_thread_cache(redis_cli, thread_id):
                self.logger.warning(
                    "Cannot load history for thread '%s' because DB/CRUD/Models are "
                    "unavailable (but memory was enabled).", thread_id
                )
                await self.cache_thread(redis_cli, thread_id)
        return []

    async def load_or_skip_history(
        self, redis_cli, thread_id: str, history_limit: int
    ) -> List[BaseMessage]:
        """Загружает историю из БД или пропускает, если уже в кэше."""
        is_loaded = await self.check_thread_cache(redis_cli, thread_id)
        if not is_loaded:
            loaded_msgs = await self.load_history_from_db(thread_id, history_limit)
            await self.cache_thread(redis_cli, thread_id)
            return loaded_msgs

        self.logger.info(
            "Thread '%s' found in cache '%s'. Skipping DB load.",
            thread_id, self.loaded_threads_key
        )
        return []

    async def get_history(
        self, thread_id: str, redis_client, agent_config: Dict[str, Any]
    ) -> List[BaseMessage]:
        """
        Получает историю сообщений из БД для указанного thread_id.
        Возвращает список сообщений в формате LangChain BaseMessage.
        Если история не найдена, возвращает пустой список.
        """
        can_load = self.can_load_history()
        if not can_load:
            self.logger.warning(
                "Database history loading is disabled "
                "(CRUD, DB session factory, ChatMessageDB, or SenderType not available)."
            )

        redis_cli = await self.get_redis_client_for_history(redis_client)
        if redis_cli is None:
            return []

        enable_memory, history_limit = self.get_memory_config(agent_config)

        if not enable_memory:
            self.logger.info(
                "Context memory is disabled by agent config. History will not be loaded."
            )
            return []

        self.logger.info(
            "Using history limit: %d (enabled: %s, configured depth: %d)",
            history_limit, enable_memory, history_limit
        )

        # Handle unavailable history case
        if not can_load or history_limit <= 0:
            return await self.handle_unavailable_history(
                redis_cli, thread_id, can_load, history_limit
            )

        try:
            return await self.load_or_skip_history(redis_cli, thread_id, history_limit)
        except redis_exceptions.RedisError as redis_err:
            self.logger.error(
                "Redis error checking/adding thread cache for '%s': %s. "
                "Proceeding without history.", thread_id, redis_err
            )
            return []
        except Exception as db_err:
            self.logger.error(
                "Database error loading history for thread '%s': %s. "
                "Proceeding without history.", thread_id, db_err, exc_info=True
            )
            return []

    async def save_history(
        self, ctx: HistorySaveContext, redis_client
    ) -> None:
        """Сохраняет сообщение в очередь истории."""
        try:
            redis_cli = await redis_client
        except RuntimeError as e:
            self.logger.error(
                "Redis client not available for handling pubsub message: %s", e
            )
            return

        message_data = {
            "agent_id": self.component_id,
            "thread_id": ctx.thread_id,
            "sender_type": ctx.sender_type,
            "content": ctx.content,
            "channel": ctx.channel,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "interaction_id": ctx.interaction_id,
        }
        try:
            await redis_cli.lpush(settings.REDIS_HISTORY_QUEUE_NAME, json.dumps(message_data))
            self.logger.info(
                "Queued %s message for history (Thread: %s, InteractionID: %s)",
                ctx.sender_type, ctx.thread_id, ctx.interaction_id
            )
        except redis_exceptions.RedisError as e:
            self.logger.error(
                "Failed to queue message for history (Thread: %s): %s",
                ctx.thread_id, e, exc_info=True
            )
        except Exception as e:
            self.logger.error(
                "Unexpected error queuing message for history (Thread: %s): %s",
                ctx.thread_id, e, exc_info=True
            )

    async def cleanup(self, redis_client) -> None:
        """Очищает кэш истории."""
        try:
            redis_cli = await redis_client
            deleted_count = await redis_cli.delete(self.loaded_threads_key)
            self.logger.info(
                "Cleared loaded threads cache '%s' (deleted: %s) on cleanup.",
                self.loaded_threads_key, deleted_count
            )
        except redis_exceptions.RedisError as cache_clear_err:
            self.logger.error(
                "Failed to clear loaded threads cache '%s': %s",
                self.loaded_threads_key, cache_clear_err
            )
        except RuntimeError as e:
            self.logger.error("Redis client not available during cleanup: %s", e)
