"""WhatsApp media message handlers."""
from typing import Any, Dict
from app.core.config import settings

from app.services.voice_v2.core.schemas import STTRequest
from app.services.voice_v2.core.interfaces import AudioFormat
from app.services.voice_v2.core.orchestrator import VoiceServiceOrchestrator
from app.services.voice_v2.infrastructure.cache import VoiceCache
from app.services.voice_v2.infrastructure.minio_manager import MinioFileManager
from app.services.voice_v2.providers.unified_factory import VoiceProviderFactory

from .image_processor import ImageProcessor
from .voice_processor import VoiceProcessor


class MediaHandler:
    """Handles media messages for WhatsApp integration."""

    def __init__(self, bot_instance):
        """Initialize MediaHandler with bot instance and processors."""
        self.bot = bot_instance
        self.logger = bot_instance.logger

        # Initialize specialized processors
        self.image_processor = ImageProcessor(self.logger)
        self.voice_processor = VoiceProcessor(self.logger)

    async def handle_image_message(
        self,
        response: Dict[str, Any],
        chat_id: str,
        sender_info: Dict[str, Any]
    ) -> None:
        """Handle image message processing with delegated complexity."""
        try:
            await self.image_processor.process_image_message(
                response, chat_id, sender_info, self.bot
            )
        except (ValueError, KeyError, TypeError) as e:
            self.logger.error("Error handling image message: %s", e, exc_info=True)
            try:
                await self.bot.api_handler.send_message(
                    chat_id,
                    "Извините, произошла ошибка при обработке изображения."
                )
            except (ConnectionError, TimeoutError) as send_error:
                self.logger.error("Failed to send error message: %s", send_error)

    async def handle_voice_message(
        self,
        response: Dict[str, Any],
        chat_id: str,
        sender_info: Dict[str, Any]
    ) -> None:
        """Handle voice message processing with delegated complexity."""
        try:
            await self.voice_processor.process_voice_message(
                response, chat_id, sender_info, self.bot
            )
        except (ConnectionError, TimeoutError, ValueError) as e:
            self.logger.error("Error handling voice message: %s", e, exc_info=True)
            try:
                await self.bot.api_handler.send_message(
                    chat_id,
                    "Извините, произошла ошибка при обработке голосового сообщения."
                )
            except (ConnectionError, TimeoutError) as send_error:
                self.logger.error("Failed to send error message: %s", send_error)

    async def _process_voice_with_stt_orchestrator(
        self,
        voice_data: bytes,
        chat_id: str,
        user_context: Dict[str, Any],
        message_id: str  # pylint: disable=unused-argument
    ) -> None:
        """
        🎯 PHASE 4.4.2: UNIFIED VOICE PROCESSING - voice_v2 orchestrator
        Process voice message using voice_v2 orchestrator with direct STT.
        Aligned with Telegram pattern: STT → Agent → voice tools → TTS
        ConnectionManager warnings preserved as requested for legacy retry fallback.
        """
        try:
            # Use global voice orchestrator if available, otherwise create temporary one
            orchestrator = await self._get_voice_orchestrator()
            if not orchestrator:
                await self._handle_voice_processing_error(
                    chat_id, Exception("Voice orchestrator not available")
                )
                return

            # Create STT request with voice_v2 schemas
            stt_request = STTRequest(
                audio_data=voice_data,
                language="ru",  # Default to Russian for WhatsApp
                audio_format=AudioFormat.OGG  # WhatsApp voice messages are typically OGG
            )

            # Direct transcription using voice_v2 orchestrator
            stt_response = await orchestrator.transcribe_audio(stt_request)

            if stt_response.text:
                # Send recognized text to agent as a regular message (Telegram pattern)
                self.logger.info(
                    "WhatsApp voice transcription successful: '%s...'",
                    stt_response.text[:100]
                )
                await self._handle_successful_stt(
                    chat_id, user_context["platform_user_id"], stt_response.text, user_context
                )
            else:
                # Handle transcription failure
                self.logger.warning("Voice processing failed: No text recognized")
                await self._handle_failed_stt(chat_id, None)

        except (ConnectionError, TimeoutError, ValueError) as e:
            await self._handle_voice_processing_error(chat_id, e)

    async def _detect_audio_format(self, audio_data: bytes, filename: str):  # pylint: disable=unused-argument
        """Detect audio format from data and filename."""
        try:
            # Try to detect format from filename extension
            if filename.lower().endswith('.ogg'):
                return 'ogg'
            if filename.lower().endswith('.wav'):
                return 'wav'
            if filename.lower().endswith('.mp3'):
                return 'mp3'

            # Default for WhatsApp voice messages
            return 'ogg'
        except (AttributeError, ValueError) as e:
            self.logger.warning("Could not detect audio format: %s, using default", e)
            return 'ogg'

    async def _get_voice_orchestrator(self):
        """Get voice orchestrator instance."""
        if self.bot.voice_orchestrator:
            return self.bot.voice_orchestrator

        self.logger.warning(
            "Global voice orchestrator not available, creating temporary voice_v2 orchestrator"
        )

        try:
            # Initialize components with enhanced voice_v2 architecture
            voice_factory = VoiceProviderFactory()
            cache_manager = VoiceCache()
            await cache_manager.initialize()

            file_manager = MinioFileManager(
                endpoint=settings.MINIO_ENDPOINT,
                access_key=settings.MINIO_ACCESS_KEY,
                secret_key=settings.MINIO_SECRET_KEY,
                bucket_name=settings.MINIO_VOICE_BUCKET_NAME,
                secure=settings.MINIO_SECURE
            )
            await file_manager.initialize()

            orchestrator = VoiceServiceOrchestrator(
                enhanced_factory=voice_factory,
                cache_manager=cache_manager,
                file_manager=file_manager,
            )
            await orchestrator.initialize()
            return orchestrator
        except (ImportError, AttributeError, RuntimeError) as e:
            self.logger.error(f"Failed to create temporary orchestrator: {e}")
            return None

    async def _handle_successful_stt(
        self,
        chat_id: str,
        platform_user_id: str,
        text: str,
        user_data: Dict[str, Any]
    ):
        """Handle successful STT result."""
        self.logger.info("STT result for WhatsApp voice message: %s", text)

        # Stop typing indicator before sending to agent
        await self.bot.stop_typing_for_chat(chat_id)
        await self.bot.api_handler.send_typing_action(chat_id, False)

        # Publish transcribed text to agent
        await self.bot.publish_to_agent(
            chat_id, platform_user_id, text, user_data
        )

    async def _handle_failed_stt(self, chat_id: str, result=None):
        """Handle failed STT result - simplified approach aligned with Telegram."""
        error_msg = (
            "No text recognized" if not result 
            else getattr(result, 'error_message', 'STT processing failed')
        )
        self.logger.warning(f"WhatsApp STT processing failed: {error_msg}")
        await self.bot.api_handler.send_message(
            chat_id,
            "Извините, не удалось распознать голосовое сообщение."
        )

    async def _handle_voice_processing_error(self, chat_id: str, error: Exception):
        """Handle voice processing errors."""
        self.logger.error(
            "Error processing voice message with orchestrator: %s",
            error, exc_info=True
        )
        try:
            await self.bot.api_handler.send_message(
                chat_id,
                ("Извините, произошла ошибка при обработке голосового "
                 "сообщения.")
            )
        except (ConnectionError, TimeoutError):
            pass
        finally:
            # Always stop typing indicator
            await self.bot.stop_typing_for_chat(chat_id)
