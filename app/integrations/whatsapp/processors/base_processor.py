"""
BaseProcessor - базовый класс для всех процессоров WhatsApp сообщений.

Содержит общую логику для обработки пользователей и извлечения данных
из WhatsApp ответов для устранения дублирования кода (R0801).
"""

import logging
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any, Dict, Optional

if TYPE_CHECKING:
    from ..core.redis_service import RedisService
    from ..core.user_service import UserService
    from ..whatsapp_bot import WhatsAppIntegrationBot


class BaseProcessor(ABC):
    """Базовый абстрактный класс для всех процессоров WhatsApp сообщений."""

    def __init__(self, user_service: "UserService", logger: logging.LoggerAdapter):
        """Initialize base processor with shared dependencies."""
        self.user_service = user_service
        self.logger = logger
        # Initialize attributes that will be set by _initialize_common_components
        self.bot: Optional["WhatsAppIntegrationBot"] = None
        self.redis_service: Optional["RedisService"] = None

    def _initialize_common_components(
        self,
        bot_instance: "WhatsAppIntegrationBot",
        redis_service: "RedisService"
    ) -> None:
        """Initialize common components for all processors."""
        self.bot = bot_instance
        self.redis_service = redis_service

    def _extract_user_info(
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
            "Extracted user info: %s, %s, %s", user_name, chat_id, phone_number
        )

        return {
            "platform_user_id": chat_id,
            "first_name": first_name,
            "last_name": last_name,
            "phone_number": phone_number,
        }

    def _extract_phone_from_sender_id(self, response: Dict[str, Any]) -> str:
        """Extract phone number from sender ID."""
        sender_id = response.get("sender", {}).get("id", "")
        if sender_id and "@c.us" in sender_id:
            return sender_id.split("@c.us")[0]
        return ""

    def _parse_user_name(self, user_name: str) -> tuple[str, str]:
        """Parse user name into first and last name."""
        name_parts = (
            user_name.strip().split(" ", 1)
            if user_name and user_name != "Unknown"
            else ["Unknown"]
        )
        first_name = name_parts[0]
        last_name = name_parts[1] if len(name_parts) > 1 else ""
        return first_name, last_name

    async def _process_user_common(
        self, response: Dict[str, Any], sender_info: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Common user processing logic."""
        # Extract user info
        user_info = self._extract_user_info(response, sender_info)

        # Process user
        user_data = await self.user_service.get_or_create_user(
            user_info["platform_user_id"],
            user_info["first_name"],
            user_info["last_name"],
            user_info["phone_number"],
        )

        if not user_data:
            self.logger.warning(
                "Failed to get/create user for %s", user_info["platform_user_id"]
            )
            return None

        return user_data

    def _extract_media_data(self, response: Dict[str, Any]) -> Dict[str, Any]:
        """
        Extract media data from WhatsApp response.

        WhatsApp структура содержит mediaKey в body или других вложенных объектах,
        а не прямой URL. Используется логика из бэкапа интеграции.
        """
        # Извлекаем message_id - может быть строкой или вложенным объектом
        id_field = response.get("id", {})
        if isinstance(id_field, dict):
            message_id = id_field.get("id", "")
        else:
            message_id = str(id_field) if id_field else ""

        # Извлекаем media_data из body
        body_field = response.get("body", {})
        if isinstance(body_field, dict):
            media_key = body_field.get("mediaKey", "")
            mimetype = body_field.get("mimetype", "")
        else:
            # Для случаев когда body - строка, ищем в других местах
            media_key = response.get("mediaKey", "")
            mimetype = response.get("mimetype", "")

        # Если media_key не найден, пробуем альтернативные локации
        if not media_key:
            alt_locations = [
                response.get("quotedMsg", {}).get("mediaKey", ""),
                response.get("message", {}).get("mediaKey", ""),
                response.get("mediaData", {}).get("mediaKey", ""),
                response.get("media", {}).get("mediaKey", "")
            ]
            for alt_key in alt_locations:
                if alt_key:
                    media_key = alt_key
                    break

        return {
            "media_key": media_key,
            "mimetype": mimetype,
            "message_id": message_id,
            "filename": f"whatsapp_media_{message_id}",
        }

    def _extract_message_id(self, response: Dict[str, Any]) -> str:
        """Extract message ID from response."""
        return response.get("id", "")

    def _validate_media_data(self, media_data: Dict[str, Any], message_id: str) -> bool:
        """Validate extracted media data for WhatsApp format."""
        if not media_data["media_key"]:
            self.logger.warning("No media key found for message %s", message_id)
            return False

        if not media_data["mimetype"]:
            self.logger.warning("No mimetype found for message %s", message_id)
            return False

        return True

    async def _handle_message_common(
        self,
        bot_instance: "WhatsAppIntegrationBot",
        response: Dict[str, Any],
        chat_id: str,
        sender_info: Dict[str, Any],
    ) -> Optional[Dict[str, Any]]:
        """Common message handling logic for all processors."""
        try:
            # Process user with common logic from BaseProcessor
            user_data = await self._process_user_common(response, sender_info)
            if not user_data:
                await self._send_error_message(
                    bot_instance, chat_id, "⚠️ Ошибка обработки пользователя."
                )
                return None

            return user_data

        except (ConnectionError, AttributeError, ValueError) as e:
            self.logger.error("Error in common message handling: %s", e, exc_info=True)

    async def _extract_media_common(
        self, bot_instance, response: Dict[str, Any], message_id: str
    ) -> Optional[Dict[str, Any]]:
        """Common media extraction logic for processors."""
        try:
            # Extract media information
            media_data = self._extract_media_data(response)

            if not self._validate_media_data(media_data, message_id):
                return None

            # Download content using WhatsApp API download method
            content = await bot_instance.api_client.download_whatsapp_media(
                media_data["media_key"], media_data["message_id"]
            )

            if not content:
                self.logger.error(
                    "Failed to download content for message %s", message_id
                )
                return None

            return {
                "content": content,
                "mimetype": media_data["mimetype"],
                "message_id": message_id,
            }

        except (ConnectionError, AttributeError, ValueError) as e:
            self.logger.error("Error extracting media: %s", e, exc_info=True)
            return None

    async def _send_error_message(
        self, bot_instance, chat_id: str, error_text: str
    ) -> None:
        """Send error message to user."""
        try:
            await bot_instance.api_client.send_message(chat_id, error_text)
        except (ConnectionError, AttributeError, ValueError) as e:
            self.logger.error("Failed to send error message to %s: %s", chat_id, e)

    def is_initialized(self) -> bool:
        """Check if processor is properly initialized."""
        return self.bot is not None and self.redis_service is not None

    def get_processor_type(self) -> str:
        """Get processor type name."""
        return self.__class__.__name__

    @abstractmethod
    async def handle_message(
        self, response: Dict[str, Any], chat_id: str, sender_info: Dict[str, Any]
    ) -> None:
        """Abstract method for handling messages - must be implemented by subclasses."""

    async def _handle_message_with_common_logic(
        self, response: Dict[str, Any], chat_id: str, sender_info: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        """Common message handling logic for all processors."""
        try:
            # Use common handling logic
            user_data = await self._handle_message_common(
                self.bot, response, chat_id, sender_info
            )
            if not user_data:
                return None  # Error already handled in common method
            return user_data
        except (ConnectionError, AttributeError, ValueError) as e:
            self.logger.error("Error in common message handling: %s", e, exc_info=True)
            return None

    async def _process_message_with_validation(
        self, response: Dict[str, Any], chat_id: str, sender_info: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        """Process message with validation - common pattern for all processors."""
        user_data = await self._handle_message_with_common_logic(
            response, chat_id, sender_info
        )
        if not user_data:
            return None
        return user_data
