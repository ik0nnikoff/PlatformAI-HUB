"""
Voice processing utilities for WhatsApp media handler.

Handles voice message parsing and processing
to reduce complexity in main MediaHandler.
"""

import logging
from typing import Any, Dict, Optional


class VoiceProcessor:
    """Processes WhatsApp voice messages with reduced complexity."""

    def __init__(self, logger: logging.LoggerAdapter):
        self.logger = logger

    def extract_message_id(self, response: Dict[str, Any]) -> str:
        """Extract message ID from response with fallback logic."""
        id_field = response.get("id", "")
        if isinstance(id_field, dict):
            return id_field.get("id", "")
        return str(id_field) if id_field else ""

    def extract_media_data(self, response: Dict[str, Any]) -> Dict[str, str]:
        """Extract media data from response with multiple fallback strategies."""
        media_data = response.get("body", {})
        self.logger.debug(f"Voice message body data: {media_data}, type: {type(media_data)}")

        # Handle both dict and direct string cases for media_key extraction
        if isinstance(media_data, dict):
            media_key = media_data.get("mediaKey", "")
            mimetype = media_data.get("mimetype", "")
        else:
            # For string body, look for mediaKey elsewhere in response
            media_key = response.get("mediaKey", "")
            mimetype = response.get("mimetype", "")

        return {"media_key": media_key, "mimetype": mimetype}

    def find_alternative_media_key(self, response: Dict[str, Any]) -> Optional[str]:
        """Try alternative locations for media key."""
        alt_locations = [
            response.get("quotedMsg", {}).get("mediaKey", ""),
            response.get("message", {}).get("mediaKey", ""),
            response.get("mediaData", {}).get("mediaKey", ""),
            response.get("media", {}).get("mediaKey", "")
        ]

        for alt_key in alt_locations:
            if alt_key:
                self.logger.debug(f"Found media_key in alternative location: {alt_key}")
                return alt_key
        return None

    def validate_media_key(
        self, media_key: str, response: Dict[str, Any], message_id: str
    ) -> bool:
        """Validate media key or try to find alternative."""
        if media_key:
            return True

        # Try alternative locations
        alt_key = self.find_alternative_media_key(response)
        if alt_key:
            return True

        self.logger.warning(
            "No media key found for voice message: %s. Available keys: %s",
            message_id, list(response.keys())
        )
        return False

    def get_final_media_key(self, media_data: Dict[str, str], response: Dict[str, Any]) -> str:
        """Get final media key with fallback logic."""
        media_key = media_data["media_key"]
        if not media_key:
            media_key = self.find_alternative_media_key(response) or ""
        return media_key

    async def process_voice_message(
        self,
        response: Dict[str, Any],
        chat_id: str,
        sender_info: Dict[str, Any],
        bot_instance
    ) -> None:
        """Process complete voice message with reduced complexity."""
        # DEBUG: Log full response structure for voice message analysis
        self.logger.debug(f"Voice message full response structure: {response}")

        # Extract basic info
        message_id = self.extract_message_id(response)
        media_data = self.extract_media_data(response)

        self.logger.debug(
            "Extracted media_key: '%s', mimetype: '%s'",
            media_data['media_key'], media_data['mimetype']
        )

        # Validate and get final media key
        if not self.validate_media_key(media_data["media_key"], response, message_id):
            return

        final_media_key = self.get_final_media_key(media_data, response)

        # Download voice message
        voice_data = await bot_instance.api_handler.download_whatsapp_media(
            final_media_key, media_data["mimetype"], message_id
        )
        if not voice_data:
            self.logger.error(
                "Failed to download voice data for message: %s", message_id
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

        # Process voice with STT orchestrator
        voice_context = {
            "voice_data": voice_data,
            "chat_id": chat_id,
            "message_id": message_id
        }
        await self._process_voice_with_orchestrator(voice_context, user_context, bot_instance)

    async def _process_voice_with_orchestrator(
        self,
        voice_context: Dict[str, Any],  # Contains voice_data, chat_id, message_id
        user_context: Dict[str, Any],
        bot_instance
    ) -> None:
        """Process voice using STT orchestrator."""
        # Extract data from context
        voice_data = voice_context["voice_data"]
        chat_id = voice_context["chat_id"]
        message_id = voice_context["message_id"]

        # 🎯 PHASE 4.4.2: UNIFIED VOICE PROCESSING - Use real STT orchestrator
        # This delegates to the existing method in MediaHandler
        # We maintain the same interface for compatibility
        await bot_instance.media_handler._process_voice_with_stt_orchestrator(  # pylint: disable=protected-access
            voice_data, chat_id, user_context, message_id
        )
