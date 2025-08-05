"""
Token usage tracking utilities for AgentRunner.

This module provides token usage data processing and queuing functionality.
"""

import json
from typing import Any, Dict, List, Optional

import redis.exceptions as redis_exceptions

from app.agent_runner.models.contexts import TokenContext
from app.agent_runner.langgraph.models import TokenUsageData
from app.core.config import settings


class TokenManager:
    """Manages token usage tracking and queuing."""

    def __init__(self, component_id: str, logger):
        self.component_id = component_id
        self.logger = logger

    async def get_final_graph_state(self, agent_app, config) -> Optional[Dict[str, Any]]:
        """Получает финальное состояние графа LangGraph."""
        try:
            graph_state = agent_app.get_state(config)
            if graph_state:
                self.logger.debug("Retrieved final graph state snapshot")
                return graph_state.values

            self.logger.warning("agent_app.get_state(config) returned None")
            return None
        except Exception as exc:
            self.logger.error(
                "Error calling agent_app.get_state(config): %s", exc, exc_info=True
            )
            return None

    def create_token_payload(
        self, token_data: TokenUsageData, interaction_id: str, thread_id: str
    ) -> Dict[str, Any]:
        """Создает payload для токен события."""
        return {
            "interaction_id": interaction_id,
            "agent_id": self.component_id,
            "thread_id": thread_id,
            "call_type": token_data.call_type,
            "model_id": token_data.model_id,
            "prompt_tokens": token_data.prompt_tokens,
            "completion_tokens": token_data.completion_tokens,
            "total_tokens": token_data.total_tokens,
            "timestamp": token_data.timestamp,
        }

    async def queue_token_data(
        self, redis_cli, token_payload: Dict[str, Any], interaction_id: str
    ) -> None:
        """Отправляет данные о токенах в очередь Redis."""
        try:
            await redis_cli.lpush(
                settings.REDIS_TOKEN_USAGE_QUEUE_NAME, json.dumps(token_payload)
            )
            self.logger.debug(
                "Queued usage data to '%s': %s",
                settings.REDIS_TOKEN_USAGE_QUEUE_NAME, token_payload
            )
        except redis_exceptions.RedisError as exc:
            self.logger.error(
                "Failed to queue usage data for InteractionID %s: %s",
                interaction_id, exc
            )
        except Exception as exc:
            self.logger.error(
                "Unexpected error queuing usage data for InteractionID %s: %s",
                interaction_id, exc, exc_info=True
            )

    async def process_token_events(
        self, redis_cli, token_events: List, interaction_id: str, thread_id: str
    ) -> None:
        """Обрабатывает список событий использования токенов."""
        self.logger.info(
            "Found %d usage events for InteractionID: %s.",
            len(token_events), interaction_id
        )

        for token_data in token_events:
            token_payload = self.create_token_payload(token_data, interaction_id, thread_id)
            await self.queue_token_data(redis_cli, token_payload, interaction_id)

    async def save_tokens(self, ctx: TokenContext, redis_client) -> None:
        """
        Сохраняет данные об использовании токенов в Redis для дальнейшей обработки.
        Отправляет данные в очередь Redis для обработки `TokenUsageWorker`.
        """
        try:
            redis_cli = await redis_client
        except RuntimeError as exc:
            self.logger.error(
                "Redis client not available for handling pubsub message: %s", exc
            )
            return

        # Получение финального состояния графа
        final_state = await self.get_final_graph_state(ctx.agent_app, ctx.config)

        # Обработка токен событий
        if final_state and "token_usage_events" in final_state:
            token_events: List[TokenUsageData] = final_state["token_usage_events"]
            if token_events:
                await self.process_token_events(
                    redis_cli, token_events, ctx.interaction_id, ctx.thread_id
                )
            else:
                self.logger.info(
                    "No token usage events recorded in retrieved final state "
                    "for InteractionID: %s.", ctx.interaction_id
                )
        else:
            self.logger.warning(
                "Could not retrieve token_usage_events from final graph state "
                "for InteractionID: %s. State: %s",
                ctx.interaction_id, final_state is not None
            )
