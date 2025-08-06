"""
Message processing for WhatsApp Integration.

Handles message validation, routing and processing
to reduce complexity in main WhatsApp bot.
"""

import logging
from typing import Any, Dict, Optional

from .user_manager import WhatsAppUserManager


class WhatsAppMessageProcessor:
    """Processes WhatsApp messages with reduced complexity."""

    def __init__(
        self,
        bot_instance,
        user_manager: WhatsAppUserManager,
        logger: logging.LoggerAdapter
    ):
        self.bot = bot_instance
        self.user_manager = user_manager
        self.logger = logger

    def validate_message(self, response: Dict[str, Any]) -> Dict[str, Any]:
        """Validate incoming message and extract basic info."""
        from_me = response.get("fromMe", False)
        message_type = response.get("type", "")
        message_id = response.get("id", response.get("messageId", ""))
        session = response.get("session", "")

        self.logger.debug(
            "Message details: fromMe=%s, type=%s, id=%s", from_me, message_type, message_id
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
        """Route message processing based on message type."""
        message_type = validation_result["type"]
        chat_id = validation_result["chat_id"]
        sender_info = validation_result["sender_info"]

        # Handle different message types
        if message_type in ["ptt", "audio"]:
            self.logger.debug(f"Voice message detected. Full response structure: {response}")
            await self.bot._handle_voice_message(response, chat_id, sender_info)  # pylint: disable=protected-access
        elif message_type == "image":
            await self.bot._handle_image_message(response, chat_id, sender_info)  # pylint: disable=protected-access
        else:
            await self.handle_text_message(response, validation_result)

    async def handle_text_message(
        self, response: Dict[str, Any], validation_result: Dict[str, Any]
    ) -> None:
        """Handle text messages with reduced complexity."""
        message_text = validation_result["message_text"]
        chat_id = validation_result["chat_id"]
        sender_info = validation_result["sender_info"]

        if not self._is_valid_text_message(message_text, chat_id):
            return

        # Extract user info and start typing
        user_info = self.extract_user_info(response, sender_info)
        await self.bot._start_typing_for_chat(chat_id)  # pylint: disable=protected-access

        # Process user and send to agent
        await self._process_user_and_send_to_agent(
            chat_id, user_info, message_text
        )

    def _is_valid_text_message(self, message_text: str, chat_id: str) -> bool:
        """Check if text message is valid."""
        if not message_text or not chat_id:
            self.logger.warning(
                "Message missing required fields: text='%s', chat_id='%s'", message_text, chat_id
            )
            return False
        return True

    async def _process_user_and_send_to_agent(
        self, chat_id: str, user_info: Dict[str, Any], message_text: str
    ) -> None:
        """Process user and send message to agent."""
        user_data = await self.user_manager.get_or_create_user(
            user_info["platform_user_id"],
            user_info["first_name"],
            user_info["last_name"],
            user_info["phone_number"],
        )

        if user_data:
            await self.bot.redis_manager.publish_to_agent(
                message_text,
                {"chat_id": chat_id, "platform_user_id": user_info["platform_user_id"]},
                user_data
            )
        else:
            self.logger.warning("Failed to get/create user for %s", user_info["platform_user_id"])
            await self.bot._stop_typing_for_chat(chat_id)  # pylint: disable=protected-access

    def extract_user_info(
        self, response: Dict[str, Any], sender_info: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Extract user information from message."""
        user_name = sender_info.get("pushname", "Unknown")
        chat_id = response.get("chatId") or response.get("from", "")

        # Extract phone number from sender.id (format: 79222088435@c.us)
        phone_number = self._extract_phone_from_sender_id(response)

        # Parse user name
        first_name, last_name = self._parse_user_name(user_name)

        self.logger.info(
            "Received WhatsApp message from %s (%s), phone: %s", user_name, chat_id, phone_number
        )

        return {
            "platform_user_id": chat_id,
            "first_name": first_name,
            "last_name": last_name,
            "phone_number": phone_number,
        }

    def _extract_phone_from_sender_id(self, response: Dict[str, Any]) -> Optional[str]:
        """Extract phone number from sender ID."""
        sender_id = response.get("sender", {}).get("id", "")
        if sender_id and "@c.us" in sender_id:
            return sender_id.split("@c.us")[0]
        return None

    def _parse_user_name(self, user_name: str) -> tuple[str, Optional[str]]:
        """Parse user name into first and last name."""
        name_parts = (
            user_name.strip().split(" ", 1) if user_name and user_name != "Unknown" else ["Unknown"]
        )
        first_name = name_parts[0]
        last_name = name_parts[1] if len(name_parts) > 1 else None
        return first_name, last_name

    async def handle_received_message(self, data: Dict[str, Any]) -> None:
        """
        Handle received message from WhatsApp through Socket.IO with reduced complexity.

        Args:
            data: Message data from wppconnect-server
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

            # Route message by type
            await self.route_message_by_type(response, validation_result)

        except (ValueError, KeyError, TypeError) as e:
            self.logger.error("Error handling received message: %s", e, exc_info=True)
            # Stop typing indicator if error occurred
            chat_id = data.get("response", {}).get("chatId") or data.get("response", {}).get(
                "from", ""
            )
            if chat_id and chat_id in self.bot.typing_tasks:
                self.bot.typing_tasks[chat_id].cancel()

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
        Send voice response with standardized error handling.

        Args:
            chat_id: WhatsApp chat ID
            audio_url: URL to audio file from LangGraph voice tools

        Returns:
            bool: True if voice message sent successfully, False otherwise
        """
        if not audio_url:
            return False

        try:
            self.logger.info(
                f"Sending voice response from LangGraph agent to chat {chat_id}: {audio_url}"
            )
            success = await self.bot.api_handler.send_voice_message(chat_id, audio_url)

            if success:
                self.logger.info(f"Voice message sent successfully to WhatsApp chat {chat_id}")
                return True

            self.logger.warning(f"Failed to send voice message to WhatsApp chat {chat_id}")
            return False

        except (ConnectionError, ValueError, TypeError) as e:
            self.logger.error(
                f"Error sending voice response to WhatsApp chat {chat_id}: {e}", exc_info=True
            )
            return False
