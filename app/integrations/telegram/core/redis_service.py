"""
Redis service for Telegram integration.

Handles message publishing and Redis communication
following single responsibility principle.
"""

import json
import logging
from typing import Any, Dict, List, Optional
from dataclasses import dataclass

from redis import exceptions as redis_exceptions


@dataclass
class MessageContent:
    """Container for message content data."""
    image_urls: Optional[List[str]] = None
    voice_data: Optional[Dict[str, Any]] = None
    document_content: Optional[str] = None
    document_metadata: Optional[Dict[str, Any]] = None


class RedisService:
    """Manages Redis operations for Telegram integration."""

    def __init__(self, bot_instance, logger: logging.LoggerAdapter):
        self.bot = bot_instance
        self.logger = logger

    def create_message_payload(
        self,
        message_text: str,
        chat_data: Dict[str, str],  # Contains chat_id and platform_user_id
        user_data: Dict[str, Any],
        content: MessageContent,
    ) -> Dict[str, Any]:
        """Create message payload for agent."""
        chat_id = chat_data["chat_id"]
        platform_user_id = chat_data["platform_user_id"]

        payload = {
            "text": message_text,
            "chat_id": chat_id,
            "platform_user_id": platform_user_id,
            "user_data": user_data,
            "channel": "telegram",
        }

        if content.image_urls:
            payload["image_urls"] = content.image_urls
            self.logger.info(f"Adding {len(content.image_urls)} image URLs to payload")

        if content.voice_data:
            payload["voice_data"] = content.voice_data
            self.logger.info("Adding voice data to payload")

        if content.document_content:
            payload["document_content"] = content.document_content
            self.logger.info("Adding document content to payload")

        if content.document_metadata:
            payload["document_metadata"] = content.document_metadata
            self.logger.info("Adding document metadata to payload")

        return payload

    async def get_redis_client(self, chat_id: str):
        """Get Redis client with error handling."""
        try:
            return await self.bot.redis_client
        except RuntimeError as e:
            self.logger.error(f"Redis client not available for chat {chat_id}: {e}")
            raise

    async def publish_message_to_agent(
        self, redis_cli, input_channel: str, payload: Dict[str, Any], chat_id: str
    ) -> None:
        """Publish message to agent with error handling."""
        try:
            await redis_cli.publish(input_channel, json.dumps(payload).encode("utf-8"))
            self.logger.info(f"Published message to {input_channel} for chat {chat_id}")
            await self.bot.update_last_active_time()

        except redis_exceptions.RedisError as e:
            self.logger.error(
                f"Redis error publishing to {input_channel}: {e}", exc_info=True
            )
            raise
        except (ConnectionError, TimeoutError, ValueError) as e:
            self.logger.error(
                f"Network or value error publishing to {input_channel}: {e}", exc_info=True
            )
            raise
        # pylint: disable=broad-exception-caught
        except Exception as e:
            # Catch-all для неожиданных системных ошибок
            self.logger.error(
                f"Unexpected error publishing to {input_channel}: {e}", exc_info=True
            )
            raise

    def decode_message(self, message_data: bytes) -> Dict[str, Any]:
        """Decode Redis message from bytes to dict."""
        try:
            if isinstance(message_data, bytes):
                data_str = message_data.decode("utf-8")
            else:
                self.logger.warning(f"Received non-bytes data: {type(message_data)}")
                return {}

            return json.loads(data_str)

        except UnicodeDecodeError as e:
            self.logger.error(f"Failed to decode UTF-8 from message: {e}")
            return {}
        except json.JSONDecodeError as e:
            self.logger.error(f"Failed to decode JSON from message: {e}")
            return {}
        except Exception as e:
            self.logger.error(f"Unexpected error decoding message: {e}", exc_info=True)
            return {}

    async def publish_to_agent(
        self,
        message_text: str,
        chat_data: Dict[str, str],
        user_data: Dict[str, Any],
        **kwargs,
    ) -> bool:
        """Publish message to agent through Redis."""
        chat_id = chat_data["chat_id"]

        try:
            redis_cli = await self.get_redis_client(chat_id)
            input_channel = f"agent:{self.bot.agent_id}:input"

            content = MessageContent(
                image_urls=kwargs.get("image_urls"),
                voice_data=kwargs.get("voice_data"),
                document_content=kwargs.get("document_content"),
                document_metadata=kwargs.get("document_metadata"),
            )

            payload = self.create_message_payload(
                message_text,
                chat_data,
                user_data,
                content,
            )

            await self.publish_message_to_agent(
                redis_cli, input_channel, payload, chat_id
            )
            return True

        except (redis_exceptions.RedisError, ConnectionError, TimeoutError, ValueError) as e:
            self.logger.error(
                f"Failed to publish message for chat {chat_id}: {e}", exc_info=True
            )
            return False
        # pylint: disable=broad-exception-caught
        except Exception as e:
            # Catch-all для неожиданных системных ошибок
            self.logger.error(
                f"Unexpected error publishing message for chat {chat_id}: {e}", exc_info=True
            )
            return False
