"""
WhatsApp API client for infrastructur            if response.status_code == 200:
                self.logger.deb            if response.status_code == 200:
                return await self._process_media_response(response)

            self.logger.error(
                "Failed to download media %s: HTTP %s", message_id, response.status_code
            )
            return Nonesage sent successfully to %s", chat_id)
                return True

            self.logger.error(
                "Failed to send message to %s: %s", chat_id, response.status_code
            )
            return False

Handles HTTP API operations with external WhatsApp service
following clean architecture principles.
"""

import asyncio
import base64
from typing import Optional

import httpx

from ..handlers.voice_sender import VoiceMessageSender


class WhatsAppAPIClient:
    """Handles WhatsApp API operations for wppconnect-server."""

    def __init__(self, bot_instance):
        """
        Initialize API client.

        Args:
            bot_instance: Reference to main WhatsAppIntegrationBot instance
        """
        self.bot = bot_instance
        self.logger = bot_instance.logger
        self.voice_sender = VoiceMessageSender(self.logger)

    async def send_message(self, chat_id: str, message: str) -> bool:
        """Send text message to WhatsApp chat."""
        try:
            if not self.bot.http_client:
                self.logger.error("HTTP client not initialized")
                return False

            payload = {
                "chatId": chat_id,
                "message": message,
                "session": self.bot.session_name,
            }

            response = await self.bot.http_client.post("/api/sendMessage", json=payload)

            if response.status_code == 200:
                self.logger.debug("Message sent successfully to %s", chat_id)
                return True

            self.logger.error(
                "Failed to send message to %s: HTTP %s",
                chat_id,
                response.status_code,
            )
            return False

        except (httpx.RequestError, httpx.TimeoutException) as e:
            self.logger.error("Error sending message to %s: %s", chat_id, e)
            return False

    async def send_typing_action(self, chat_id: str, is_typing: bool) -> bool:
        """Send typing indicator to WhatsApp chat."""
        try:
            if not self.bot.http_client:
                self.logger.error("HTTP client not initialized")
                return False

            action = "start" if is_typing else "stop"
            payload = {
                "chatId": chat_id,
                "action": action,
                "session": self.bot.session_name,
            }

            response = await self.bot.http_client.post("/api/sendTyping", json=payload)

            if response.status_code == 200:
                self.logger.debug("Typing action %s sent to %s", action, chat_id)
                return True

            self.logger.warning(
                "Failed to send typing action to %s: HTTP %s",
                chat_id,
                response.status_code,
            )
            return False

        except (httpx.RequestError, httpx.TimeoutException) as e:
            self.logger.debug("Error sending typing action to %s: %s", chat_id, e)
            return False

    async def send_voice_message(self, chat_id: str, audio_url: str) -> bool:
        """Send voice message to WhatsApp chat."""
        try:
            if not self.bot.http_client:
                self.logger.error("HTTP client not initialized")
                return False

            # Use voice sender for actual sending
            return await self.voice_sender.send_voice_message(
                chat_id, audio_url, self.bot
            )

        except (httpx.RequestError, httpx.TimeoutException, AttributeError) as e:
            self.logger.error("Error sending voice message to %s: %s", chat_id, e)
            return False

    async def download_whatsapp_media(
        self, media_key: str, message_id: str
    ) -> Optional[bytes]:
        """Download media from WhatsApp."""
        try:
            if not self.bot.http_client:
                self.logger.error("HTTP client not initialized")
                return None

            payload = {
                "messageId": message_id,
                "session": self.bot.session_name,
            }

            response = await self.bot.http_client.post(
                "/api/downloadMedia", json=payload
            )

            if response.status_code == 200:
                return await self._process_media_response(response)

            self.logger.error(
                "Failed to download media %s: HTTP %s",
                media_key,
                response.status_code,
            )
            return None

        except (httpx.RequestError, httpx.TimeoutException, ValueError) as e:
            self.logger.error("Error downloading media %s: %s", media_key, e)
            return None

    async def _process_media_response(
        self, response: httpx.Response
    ) -> Optional[bytes]:
        """Process media download response."""
        try:
            # Log raw response for debugging
            raw_response = response.text
            self.logger.debug("Raw response (first 200 chars): %s", raw_response[:200])

            # Response should contain base64 encoded data
            response_data = response.json()

            # Check various response formats
            base64_data = None
            if "base64" in response_data:
                # Format: {"base64": "..."}
                base64_data = response_data["base64"]
                self.logger.debug("Found base64 data in 'base64' field")
            elif "data" in response_data:
                # Format: {"data": "..."}
                base64_data = response_data["data"]
                self.logger.debug("Found base64 data in 'data' field")
            else:
                self.logger.error(
                    "No base64 data found in response: %s", list(response_data.keys())
                )
                return None

            if not base64_data:
                self.logger.error("Empty base64 data received")
                return None

            # Remove data URL prefix if present
            if base64_data.startswith("data:"):
                base64_data = base64_data.split(",", 1)[1]

            # Decode base64 data
            media_bytes = base64.b64decode(base64_data)
            self.logger.debug(
                "Successfully decoded %s bytes of media data", len(media_bytes)
            )
            return media_bytes

        except (ValueError, TypeError, AttributeError) as e:
            self.logger.error("Error processing media response: %s", e, exc_info=True)
            return None

    async def send_typing_periodically(self, chat_id: str):
        """Start periodic typing indicator."""
        try:
            # Start typing
            await self.send_typing_action(chat_id, True)

            while True:
                await asyncio.sleep(3)  # Refresh typing indicator every 3 seconds
                await self.send_typing_action(chat_id, True)

        except asyncio.CancelledError:
            self.logger.debug("Typing task cancelled for chat %s", chat_id)
        except (httpx.RequestError, AttributeError, ConnectionError) as e:
            self.logger.error("Error in periodic typing for %s: %s", chat_id, e)
        finally:
            # Clean up typing task
            if chat_id in self.bot.typing_tasks:
                del self.bot.typing_tasks[chat_id]
            # Ensure typing is stopped
            await self.send_typing_action(chat_id, False)
