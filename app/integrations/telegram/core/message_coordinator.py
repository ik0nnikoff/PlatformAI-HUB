"""
Message coordinator for Telegram integration.

Handles message routing and coordination between different processors.
Follows the coordinator pattern for clean message processing.
"""

import logging
from typing import Dict, Any, Optional

from aiogram.types import Message

from ..processors.image_processor import ImageProcessor
from ..processors.text_processor import TextProcessor
from ..processors.voice_processor import VoiceProcessor
from ..processors.contact_processor import ContactProcessor
from .user_service import UserService


class MessageCoordinator:
    """Coordinates Telegram message processing with clear delegation."""

    def __init__(
        self, bot_instance, user_service: UserService, logger: logging.LoggerAdapter
    ):
        self.bot = bot_instance
        self.user_service = user_service
        self.logger = logger

        # Initialize specialized processors
        self.text_processor = TextProcessor(
            bot_instance, user_service, bot_instance.redis_service, logger
        )
        self.voice_processor = VoiceProcessor(
            bot_instance, user_service, bot_instance.redis_service, logger
        )
        self.image_processor = ImageProcessor(
            bot_instance, user_service, bot_instance.redis_service, logger
        )
        self.contact_processor = ContactProcessor(
            bot_instance, user_service, bot_instance.redis_service, logger
        )

    async def handle_text_message(self, message: Message) -> None:
        """Handle text message through specialized processor."""
        if not message.text:
            self.logger.warning("Received text message without content")
            return

        await self.text_processor.handle_message(message)

    async def handle_voice_message(self, message: Message) -> None:
        """Handle voice/audio message through specialized processor."""
        if not (message.voice or message.audio):
            self.logger.warning("Received voice message without audio content")
            return

        await self.voice_processor.handle_message(message)

    async def handle_photo_message(self, message: Message) -> None:
        """Handle photo message through specialized processor."""
        if not message.photo:
            self.logger.warning("Received photo message without image content")
            return

        await self.image_processor.handle_message(message)

    async def handle_contact_message(self, message: Message) -> None:
        """Handle contact message through specialized processor."""
        if not message.contact:
            self.logger.warning("Received contact message without contact data")
            return

        await self.contact_processor.handle_message(message)

    async def handle_media_group_message(self, messages: list[Message]) -> None:
        """Handle media group (album) through image processor."""
        if not messages:
            self.logger.warning("Received empty media group")
            return

        await self.image_processor.handle_media_group(messages)

    def validate_message(self, message: Message) -> bool:
        """Validate incoming message basic requirements."""
        if not message:
            self.logger.error("Received None message")
            return False

        if not message.from_user:
            self.logger.warning("Message without user information")
            return False

        if not message.chat:
            self.logger.warning("Message without chat information")
            return False

        return True

    async def route_message_by_type(self, message: Message) -> None:
        """Route message to appropriate processor based on content type."""
        if not self.validate_message(message):
            return

        try:
            await self._route_to_processor(message)
        except Exception as e:
            await self._handle_routing_error(message, e)

    async def _route_to_processor(self, message: Message) -> None:
        """Route message to the appropriate processor."""
        if message.text:
            await self.handle_text_message(message)
        elif message.voice or message.audio:
            await self.handle_voice_message(message)
        elif message.photo:
            await self.handle_photo_message(message)
        elif message.contact:
            await self.handle_contact_message(message)
        elif message.document:
            await self.text_processor.handle_document_message(message)
        else:
            await self._handle_unsupported_message(message)

    async def _handle_unsupported_message(self, message: Message) -> None:
        """Handle unsupported message types."""
        self.logger.info("Unsupported message type from %s", message.from_user.id)
        if self.bot.bot:
            await self.bot.bot.send_message(
                message.chat.id,
                "Извините, данный тип сообщений пока не поддерживается.",
            )

    async def _handle_routing_error(self, message: Message, error: Exception) -> None:
        """Handle errors during message routing."""
        self.logger.error(
            "Error routing message from %s: %s",
            message.from_user.id,
            error,
            exc_info=True,
        )
        if self.bot.bot:
            try:
                await self.bot.bot.send_message(
                    message.chat.id, "⚠️ Произошла ошибка при обработке сообщения."
                )
            except Exception as reply_error:
                self.logger.error("Failed to send error reply: %s", reply_error)

    def is_valid_agent_response(self, data: Dict[str, Any]) -> bool:
        """Validate agent response data."""
        channel = data.get("channel")
        chat_id = data.get("chat_id")
        response_text = data.get("response")

        if channel != "telegram":
            return False

        if not chat_id or not response_text:
            self.logger.warning("Invalid agent response data: %s", data)
            return False

        return True

    async def send_voice_response(self, chat_id: str, audio_url: Optional[str]) -> bool:
        """
        Send voice response to Telegram chat.
        Uses bot instance to send voice message.
        """
        if not audio_url:
            return False
        try:
            return await self.bot.api_client.send_voice_message(chat_id, audio_url)
        except (AttributeError, ConnectionError) as e:
            self.logger.error("Error sending voice response: %s", e)
            return False

    def extract_chat_data(self, message: Message) -> Dict[str, str]:
        """Extract chat data from message."""
        return {
            "chat_id": str(message.chat.id),
            "platform_user_id": str(message.from_user.id),
        }
