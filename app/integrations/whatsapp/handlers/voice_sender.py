"""
Voice message API utilities for WhatsApp handler.

Handles voice message sending operations
to reduce complexity in main APIHandler.
"""

import base64
import logging
from typing import Any, Dict

import aiohttp


class VoiceMessageSender:
    """Handles WhatsApp voice message sending with reduced complexity."""

    def __init__(self, logger: logging.LoggerAdapter):
        self.logger = logger

    async def download_audio_data(self, audio_url: str) -> bytes:
        """Download audio data from URL."""

        self.logger.debug("Downloading audio from URL: %s", audio_url)

        async with aiohttp.ClientSession() as session:
            async with session.get(audio_url) as resp:
                if resp.status == 200:
                    audio_data = await resp.read()
                    self.logger.debug(
                        "Downloaded audio data: %s bytes", len(audio_data)
                    )
                    return audio_data

                self.logger.error(
                    "Failed to download audio from %s: HTTP %s", audio_url, resp.status
                )
                raise ConnectionError(f"HTTP {resp.status} downloading audio")

    def encode_audio_to_base64(self, audio_data: bytes) -> str:
        """Encode audio data to base64."""
        audio_base64 = base64.b64encode(audio_data).decode("utf-8")
        self.logger.debug("Encoded audio to base64: %s characters", len(audio_base64))
        return audio_base64

    def create_voice_payload(self, chat_id: str, audio_base64: str) -> Dict[str, Any]:
        """Create voice message payload for wppconnect API."""
        return {"phone": chat_id, "isGroup": False, "base64Ptt": audio_base64}

    def create_api_url(self, session_name: str) -> str:
        """Create API URL for voice message sending."""
        return f"/api/{session_name}/send-voice-base64"

    async def send_to_api(
        self, http_client, url: str, payload: Dict[str, Any], chat_id: str
    ) -> bool:
        """Send voice message through wppconnect API."""
        self.logger.debug(f"Sending voice message to {chat_id} via {url}")
        response = await http_client.post(url, json=payload)

        if response.status_code in [200, 201]:
            self.logger.info(f"Voice message sent successfully to {chat_id}")
            return True

        self.logger.error(
            f"Failed to send voice message. Status: {response.status_code}, "
            f"Response: {response.text}"
        )
        return False

    async def send_voice_message(
        self, chat_id: str, audio_url: str, bot_instance
    ) -> bool:
        """
        Send voice message to WhatsApp through wppconnect API with reduced complexity.

        Args:
            chat_id: WhatsApp chat ID
            audio_url: Audio file URL
            bot_instance: Bot instance for API access

        Returns:
            True if message sent successfully
        """
        try:
            # Download audio data
            audio_data = await self.download_audio_data(audio_url)

            # Encode to base64
            audio_base64 = self.encode_audio_to_base64(audio_data)

            # Create payload and URL
            payload = self.create_voice_payload(chat_id, audio_base64)
            url = self.create_api_url(bot_instance.session_name)

            # Send to API
            success = await self.send_to_api(
                bot_instance.http_client, url, payload, chat_id
            )

            if success:
                await bot_instance.update_last_active_time()

            return success

        except (ConnectionError, TimeoutError, ValueError) as e:
            self.logger.error(
                f"Error sending voice message to {chat_id}: {e}", exc_info=True
            )
            return False
