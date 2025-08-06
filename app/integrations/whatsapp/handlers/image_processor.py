"""
Image processing utilities for WhatsApp media handler.

Handles image message parsing and processing
to reduce complexity in main MediaHandler.
"""

import base64
import logging
from typing import Any, Dict


class ImageProcessor:
    """Processes WhatsApp image messages with reduced complexity."""

    def __init__(self, logger: logging.LoggerAdapter):
        self.logger = logger

    def extract_message_id(self, response: Dict[str, Any]) -> str:
        """Extract message ID from response with fallback logic."""
        id_field = response.get("id", {})
        if isinstance(id_field, dict):
            return id_field.get("id", "")
        return str(id_field) if id_field else ""

    def extract_media_data(self, response: Dict[str, Any]) -> Dict[str, str]:
        """Extract media data from response body."""
        body_field = response.get("body", {})
        if isinstance(body_field, dict):
            media_data = body_field
        else:
            media_data = {}

        return {
            "media_key": media_data.get("mediaKey", ""),
            "mimetype": media_data.get("mimetype", "")
        }

    def validate_media_data(self, media_data: Dict[str, str], message_id: str) -> bool:
        """Validate that required media data is present."""
        if not media_data.get("media_key"):
            self.logger.warning(
                "No media key found for image message: %s", message_id
            )
            return False
        return True

    def create_image_filename(self, message_id: str, agent_id: str) -> str:
        """Create standardized image filename."""
        return f"whatsapp_image_{message_id}_{agent_id}.jpg"

    def create_image_info(
        self, image_data: bytes, filename: str, mimetype: str
    ) -> Dict[str, str]:
        """Create image info structure for agent processing."""
        return {
            "data": base64.b64encode(image_data).decode('utf-8'),
            "filename": filename,
            "content_type": mimetype
        }

    def create_agent_payload(self, image_info: Dict[str, str]) -> list:
        """Create image payload for agent."""
        return [{
            "data": image_info["data"],
            "filename": image_info["filename"]
        }]

    async def process_image_message(
        self,
        response: Dict[str, Any],
        chat_id: str,
        sender_info: Dict[str, Any],
        bot_instance
    ) -> None:
        """Process complete image message with reduced complexity."""
        # Extract basic info
        message_id = self.extract_message_id(response)
        media_data = self.extract_media_data(response)

        # Validate media data
        if not self.validate_media_data(media_data, message_id):
            return

        # Download image
        image_data = await bot_instance.api_handler.download_whatsapp_media(
            media_data["media_key"], media_data["mimetype"], message_id
        )
        if not image_data:
            self.logger.error(
                "Failed to download image data for message: %s", message_id
            )
            return

        # Get user context
        user_context = await bot_instance._extract_user_context(  # pylint: disable=protected-access
            response, chat_id, sender_info
        )
        if not user_context:
            await bot_instance.api_handler.send_message(
                chat_id,
                "Извините, не удалось получить информацию о пользователе."
            )
            return

        # Process and send to agent
        image_info = {
            "data": image_data,
            "media_data": media_data,
            "message_id": message_id,
            "chat_id": chat_id
        }
        await self._send_image_to_agent(image_info, user_context, bot_instance)

    async def _send_image_to_agent(
        self,
        image_info: Dict[str, Any],  # Contains data, media_data, message_id, chat_id
        user_context: Dict[str, Any],
        bot_instance
    ) -> None:
        """Send processed image to agent."""
        image_data = image_info["data"]
        media_data = image_info["media_data"]
        message_id = image_info["message_id"]
        chat_id = image_info["chat_id"]
        # Create image info
        image_filename = self.create_image_filename(message_id, bot_instance.agent_id)
        image_info = self.create_image_info(
            image_data, image_filename, media_data["mimetype"]
        )

        # Create agent payload
        image_payload = self.create_agent_payload(image_info)

        # Send to agent
        await bot_instance._publish_to_agent(  # pylint: disable=protected-access
            chat_id=chat_id,
            platform_user_id=user_context["platform_user_id"],
            message_text="Пользователь отправил изображение",
            user_data=user_context["user_data"],
            image_urls=image_payload
        )
