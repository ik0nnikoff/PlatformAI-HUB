"""
Message coordinator for WhatsApp integration.

Coordinates message processing flow with focused responsibility.
Delegates to specialized processors for different message types.
"""

import logging
from typing import Any, Dict, Optional

from ..processors.image_processor import ImageProcessor
from ..processors.text_processor import TextProcessor
from ..processors.voice_processor import VoiceProcessor
from .user_service import UserService


class MessageCoordinator:
    """Coordinates WhatsApp message processing with clear delegation."""

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

    def validate_message(self, response: Dict[str, Any]) -> Dict[str, Any]:
        """Validate incoming message and extract basic info."""
        from_me = response.get("fromMe", False)
        message_type = response.get("type", "")
        message_id = response.get("id", response.get("messageId", ""))
        session = response.get("session", "")

        self.logger.debug(
            "Message details: fromMe=%s, type=%s, id=%s",
            from_me,
            message_type,
            message_id,
        )

        # Validation checks
        if from_me:
            self.logger.debug("Ignoring outgoing message")
            return {"valid": False}

        if session != self.bot.session_name:
            self.logger.debug("Message from different session %s, ignoring", session)
            return {"valid": False}

        return {
            "valid": True,
            "type": message_type,
            "chat_id": response.get("chatId") or response.get("from", ""),
            "sender_info": response.get("sender", {}),
            "message_text": response.get("content") or response.get("body", ""),
        }

    async def route_message_by_type(
        self, response: Dict[str, Any], validation_result: Dict[str, Any]
    ) -> None:
        """Route message processing to specialized processors."""
        message_type = validation_result["type"]
        chat_id = validation_result["chat_id"]
        sender_info = validation_result["sender_info"]

        # Route to appropriate processor
        if message_type in ["ptt", "audio"]:
            self.logger.debug("Routing voice message to VoiceProcessor")
            await self.voice_processor.handle_voice_message(
                response, chat_id, sender_info
            )
        elif message_type == "image":
            self.logger.debug("Routing image message to ImageProcessor")
            await self.image_processor.handle_image_message(
                response, chat_id, sender_info
            )
        else:
            self.logger.debug("Routing text message to TextProcessor")
            await self.text_processor.handle_text_message(
                response, chat_id, sender_info
            )

    async def handle_received_message(self, data: Dict[str, Any]) -> None:
        """
        Handle received message from WhatsApp through Socket.IO.

        Coordinates the entire message processing flow.
        """
        try:
            response = data.get("response", {})
            if not response:
                self.logger.warning("Received message without response data: %s", data)
                return

            # Validate and extract basic info
            validation_result = self.validate_message(response)
            if not validation_result["valid"]:
                return

            # Route message to appropriate processor
            await self.route_message_by_type(response, validation_result)

        except (ValueError, KeyError, TypeError) as e:
            self.logger.error("Error handling received message: %s", e, exc_info=True)
            # Stop typing indicator if error occurred
            chat_id = data.get("response", {}).get("chatId") or data.get(
                "response", {}
            ).get("from", "")
            if chat_id:
                await self.bot.stop_typing_for_chat(chat_id)

    def is_valid_agent_response(self, data: Dict[str, Any]) -> bool:
        """Validate agent response data."""
        channel = data.get("channel")
        chat_id = data.get("chat_id")
        response_text = data.get("response")

        if channel != "whatsapp":
            return False

        if not chat_id or not response_text:
            self.logger.warning("Invalid agent response data: %s", data)
            return False

        return True

    async def send_voice_response(self, chat_id: str, audio_url: Optional[str]) -> bool:
        """
        Send voice response to WhatsApp chat.
        Uses bot instance to send voice message.
        """
        if not audio_url:
            return False
        try:
            return await self.bot.api_client.send_voice_message(chat_id, audio_url)
        except (AttributeError, ConnectionError) as e:
            self.logger.error("Error sending voice response: %s", e)
            return False
