"""
Redis operations for WhatsApp Integration.

Handles message publishing and Redis communication
to reduce complexity in main WhatsApp bot.
"""

import json
import logging
from typing import Any, Dict, List, Optional

from redis import exceptions as redis_exceptions


class WhatsAppRedisManager:
    """Manages Redis operations for WhatsApp integration."""

    def __init__(self, bot_instance, logger: logging.LoggerAdapter):
        self.bot = bot_instance
        self.logger = logger

    def create_message_payload(
        self,
        message_text: str,
        chat_data: Dict[str, str],  # Contains chat_id and platform_user_id
        user_data: Dict[str, Any],
        image_urls: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """Create message payload for agent."""
        chat_id = chat_data["chat_id"]
        platform_user_id = chat_data["platform_user_id"]
        payload = {
            "text": message_text,
            "chat_id": chat_id,
            "platform_user_id": platform_user_id,
            "user_data": user_data,
            "channel": "whatsapp",
        }

        if image_urls:
            payload["image_urls"] = image_urls
            self.logger.info("Adding %s image URLs to WhatsApp message payload", len(image_urls))

        return payload

    async def get_redis_client(self, chat_id: str):
        """Get Redis client with error handling."""
        try:
            return await self.bot.redis_client
        except RuntimeError as e:
            self.logger.error("Redis client not available for publishing to agent: %s", e)
            await self.bot.api_handler.send_message(
                chat_id, "Ошибка: Не удалось связаться с агентом (сервис недоступен)."
            )
            raise

    async def publish_message_to_agent(
        self, redis_cli, input_channel: str, payload: Dict[str, Any], chat_id: str
    ) -> None:
        """Publish message to agent with error handling."""
        try:
            await redis_cli.publish(input_channel, json.dumps(payload).encode("utf-8"))
            self.logger.debug("Published message to %s: %s", input_channel, payload)
            await self.bot.update_last_active_time()

        except redis_exceptions.RedisError as e:
            self.logger.error("Redis error publishing message: %s", e)
            await self.bot.api_handler.send_message(
                chat_id, "Ошибка отправки сообщения. Попробуйте позже."
            )
            raise
        except Exception as e:
            self.logger.error("Unexpected error publishing message: %s", e, exc_info=True)
            raise

    async def publish_to_agent(
        self,
        message_text: str,
        chat_data: Dict[str, str],  # Contains chat_id and platform_user_id
        user_data: Dict[str, Any],
        image_urls: Optional[List[str]] = None,
    ) -> bool:
        """Publish message to agent via Redis."""
        try:
            payload = self.create_message_payload(
                message_text=message_text,
                chat_data=chat_data,
                user_data=user_data,
                image_urls=image_urls,
            )

            channel = f"agent:{self.agent_id}:input"  # pylint: disable=no-member
            await self.redis_client.publish(channel, json.dumps(payload))  # pylint: disable=no-member
            self.logger.debug(f"Published message to agent {self.agent_id}")  # pylint: disable=no-member
            return True

        except ConnectionError as e:
            self.logger.error("Redis connection error: %s", e, exc_info=True)
            raise
        except (TypeError, ValueError) as e:
            self.logger.error("Data serialization error: %s", e, exc_info=True)
            raise
        except Exception as e:
            self.logger.error("Unexpected error publishing message: %s", e, exc_info=True)
            raise
