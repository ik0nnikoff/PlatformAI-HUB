"""
VoiceProcessor - обработчик голосовых сообщений.

Полностью независимый компонент для обработки голосовых сообщений WhatsApp
с прямой интеграцией с voice_v2 orchestrator.
Наследует от BaseProcessor для устранения дублирования кода.
"""

from typing import TYPE_CHECKING, Any, Dict, Optional

from app.services.voice_v2.core.interfaces import AudioFormat
from app.services.voice_v2.core.schemas import STTRequest

from .base_processor import BaseProcessor

if TYPE_CHECKING:
    from ..core.redis_service import RedisService
    from ..core.user_service import UserService
    from ..whatsapp_bot import WhatsAppIntegrationBot


class VoiceProcessor(BaseProcessor):
    """Независимый процессор голосовых сообщений WhatsApp."""

    def __init__(
        self,
        bot_instance: "WhatsAppIntegrationBot",
        user_service: "UserService",
        redis_service: "RedisService",
        logger,
    ):
        """Initialize voice processor with required dependencies."""
        super().__init__(user_service, logger)
        self._initialize_common_components(bot_instance, redis_service)

    async def handle_message(
        self, response: Dict[str, Any], chat_id: str, sender_info: Dict[str, Any]
    ) -> None:
        """Implementation of abstract handle_message method."""
        await self.handle_voice_message(response, chat_id, sender_info)

    async def handle_voice_message(
        self, response: Dict[str, Any], chat_id: str, sender_info: Dict[str, Any]
    ) -> None:
        """Handle voice message processing with complete independence."""
        try:
            user_data = await self._process_message_with_validation(
                response, chat_id, sender_info
            )
            if not user_data:
                return

            # Process voice with STT using voice_v2 orchestrator
            voice_text = await self._process_voice_with_stt(response, chat_id)
            if not voice_text:
                return  # Error already handled in _process_voice_with_stt

            # Send transcribed text to agent with typing
            await self.bot.publish_to_agent(
                {
                    "chat_id": chat_id,
                    "message_text": voice_text,
                    "platform_user_id": user_data["platform_user_id"],
                    "is_voice_message": True,
                },
                user_data,
            )

        except (ConnectionError, AttributeError, ValueError) as e:
            self.logger.error("Error processing voice message: %s", e, exc_info=True)
            await self._send_error_message(
                self.bot,
                chat_id,
                "⚠️ Произошла ошибка при обработке голосового сообщения.",
            )
            # Stop typing on error
            await self.bot.stop_typing_for_chat(chat_id)

    async def _process_voice_with_stt(
        self, response: Dict[str, Any], chat_id: str
    ) -> Optional[str]:
        """Process voice message with STT using voice_v2 orchestrator."""
        try:
            # Extract voice data
            voice_data = await self._extract_voice_data(response)
            if not voice_data:
                await self._send_error_message(
                    self.bot, chat_id, "⚠️ Не удалось получить голосовое сообщение."
                )
                return None

            # Use voice_v2 orchestrator for STT
            if not self.bot.voice_orchestrator:
                self.logger.error("Voice orchestrator not initialized")
                await self._send_error_message(
                    self.bot, chat_id, "⚠️ Голосовые сообщения временно недоступны."
                )
                return None

            # Create STT request
            stt_request = STTRequest(
                audio_data=voice_data["content"],
                audio_format=AudioFormat.OGG,
                language="auto",  # Auto-detect language
            )

            # Process with voice_v2 orchestrator
            result = await self.bot.voice_orchestrator.transcribe_audio(stt_request)

            if result.text:
                self.logger.info(
                    "Voice message transcribed successfully for chat %s", chat_id
                )
                return result.text

            self.logger.warning("STT failed for chat %s: no text returned", chat_id)
            await self._send_error_message(
                self.bot,
                chat_id,
                "⚠️ Не удалось распознать голосовое сообщение. Попробуйте еще раз.",
            )
            return None

        except (ConnectionError, AttributeError, ValueError) as e:
            self.logger.error("Error in voice STT processing: %s", e, exc_info=True)
            await self._send_error_message(
                self.bot, chat_id, "⚠️ Ошибка обработки голосового сообщения."
            )
            return None

    async def _extract_voice_data(
        self, response: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        """Extract voice data from WhatsApp response."""
        message_id = self._extract_message_id(response)
        return await self._extract_media_common(self.bot, response, message_id)
