"""
Voice message processor for Telegram integration.

Handles voice message processing with voice_v2 orchestrator integration.
"""

from typing import TYPE_CHECKING, Optional

from aiogram.types import Message

from app.integrations.telegram.processors.base_processor import BaseProcessor
from app.services.voice_v2.core.interfaces import AudioFormat
from app.services.voice_v2.core.schemas import STTRequest

if TYPE_CHECKING:
    from ..core.redis_service import RedisService
    from ..core.user_service import UserService
    from ..telegram_bot import TelegramIntegrationBot


class VoiceProcessor(BaseProcessor):
    """Независимый процессор голосовых сообщений Telegram."""

    def __init__(
        self,
        bot_instance: "TelegramIntegrationBot",
        user_service: "UserService",
        redis_service: "RedisService",
        logger,
    ):
        """Initialize voice processor with required dependencies."""
        super().__init__(user_service, logger)
        self._initialize_common_components(bot_instance, redis_service)

    async def handle_message(self, message: Message) -> None:
        """Implementation of abstract handle_message method."""
        await self.handle_voice_message(message)

    async def handle_voice_message(self, message: Message) -> None:
        """Handle voice message processing with complete independence."""
        if not self._validate_message_data(message):
            return

        chat_id = message.chat.id
        platform_user_id = str(message.from_user.id)

        if not self._validate_voice_content(message, platform_user_id):
            return

        self.logger.info("Voice message from %s in chat %s", platform_user_id, chat_id)

        # Start typing indicator
        await self._start_typing_indicator(chat_id)

        try:
            if not await self._validate_voice_orchestrator(chat_id):
                return

            user_data = await self._process_user_common(message)

            success = await self._process_and_publish_voice(message, user_data, chat_id)

            if not success:
                await self._send_error_message(chat_id, "🔇 Не удалось распознать речь")

        except (ConnectionError, TimeoutError, ValueError) as e:
            self.logger.error(
                "Error processing voice message from chat %s: %s",
                chat_id,
                e,
                exc_info=True,
            )
            await self._send_error_message(
                chat_id, "⚠️ Ошибка при обработке голосового сообщения."
            )
        # pylint: disable=broad-exception-caught
        except Exception as e:
            # Catch-all для неожиданных системных ошибок
            self.logger.error(
                "Unexpected error processing voice message from chat %s: %s",
                chat_id,
                e,
                exc_info=True,
            )
            await self._send_error_message(chat_id, "⚠️ Внутренняя ошибка системы.")

        finally:
            # Stop typing indicator
            await self._stop_typing_indicator(chat_id)

    def _validate_voice_content(self, message: Message, platform_user_id: str) -> bool:
        """Validate voice message content."""
        if not (message.voice or message.audio):
            self.logger.warning(
                "Voice message without audio content from %s", platform_user_id
            )
            return False
        return True

    async def _validate_voice_orchestrator(self, chat_id: int) -> bool:
        """Validate if voice orchestrator is available."""
        if (
            not self.bot.voice_orchestrator
            or not self.bot.voice_orchestrator.get_orchestrator()
        ):
            await self._send_error_message(
                chat_id,
                "🔇 Функции обработки голосовых сообщений временно недоступны.",
            )
            return False
        return True

    async def _process_and_publish_voice(
        self, message: Message, user_data: dict, chat_id: int
    ) -> bool:
        """Process voice with STT and publish to agent."""
        transcribed_text = await self._process_voice_with_stt(message)

        if not transcribed_text:
            return False

        self.logger.info(
            "Voice transcription successful: '%s...'", transcribed_text[:100]
        )

        chat_data = self._extract_chat_data(message)
        success = await self.redis_service.publish_to_agent(
            transcribed_text, chat_data, user_data
        )

        if not success:
            await self._send_error_message(
                chat_id, "Ошибка отправки распознанного текста агенту."
            )

        return success

    async def _process_voice_with_stt(self, message: Message) -> Optional[str]:
        """Process voice message with STT (Speech-to-Text)."""
        try:
            voice_orchestrator = self.bot.voice_orchestrator.get_orchestrator()
            if not voice_orchestrator:
                return None

            audio_file = self._get_audio_file(message)
            if not audio_file:
                return None

            audio_data = await self._download_audio_file(audio_file)
            if not audio_data:
                return None

            return await self._perform_stt_conversion(
                audio_data, audio_file, message.from_user.id
            )

        except (ConnectionError, TimeoutError, ValueError) as e:
            self.logger.error("Error in voice STT processing: %s", e, exc_info=True)
            return None
        # pylint: disable=broad-exception-caught
        except Exception as e:
            # Catch-all для неожиданных системных ошибок
            self.logger.error(
                "Unexpected error in voice STT processing: %s", e, exc_info=True
            )
            return None

    def _get_audio_file(self, message: Message):
        """Get audio file from message."""
        return message.voice or message.audio

    async def _download_audio_file(self, audio_file) -> Optional[bytes]:
        """Download audio file from Telegram."""
        file_info = await self.bot.api_client.get_file_info(audio_file.file_id)
        if not file_info:
            return None

        return await self.bot.api_client.download_file(file_info["file_path"])

    async def _perform_stt_conversion(
        self, audio_data: bytes, audio_file, user_id: int
    ) -> Optional[str]:
        """Perform STT conversion using voice orchestrator."""
        voice_orchestrator = self.bot.voice_orchestrator.get_orchestrator()

        audio_format = self._determine_audio_format(audio_file)

        stt_request = STTRequest(
            audio_data=audio_data,
            audio_format=audio_format,
            language="ru-RU",  # Default to Russian
        )

        result = await voice_orchestrator.transcribe_audio(stt_request)

        if result.text:
            return result.text

        self.logger.warning("STT failed: no text returned")
        return None

    def _determine_audio_format(self, audio_file) -> AudioFormat:
        """Determine audio format from file information."""
        # Telegram voice messages are typically OGG/OPUS
        if hasattr(audio_file, "mime_type"):
            mime_type = audio_file.mime_type
            if "ogg" in mime_type.lower():
                return AudioFormat.OGG
            if "mp3" in mime_type.lower():
                return AudioFormat.MP3
            if "wav" in mime_type.lower():
                return AudioFormat.WAV

        # Default fallback for Telegram voice messages
        return AudioFormat.OGG
