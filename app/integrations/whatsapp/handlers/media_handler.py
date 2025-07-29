"""WhatsApp media message handlers."""
import base64
import logging
from typing import Dict, Any


class MediaHandler:
    """Handles media messages for WhatsApp integration."""

    def __init__(self, bot_instance):
        """Initialize media handler."""
        self.bot = bot_instance
        self.logger = logging.getLogger(__name__)

    async def handle_image_message(
        self,
        response: Dict[str, Any],
        chat_id: str,
        sender_info: Dict[str, Any]
    ) -> None:
        """Handle image message processing."""
        try:
            message_id = response.get("id", {}).get("id", "")
            media_data = response.get("body", {})
            media_key = media_data.get("mediaKey", "")
            mimetype = media_data.get("mimetype", "")

            if not media_key:
                self.logger.warning(
                    "No media key found for image message: %s", message_id
                )
                return

            # Download the image
            image_data = await self.bot.api_handler.download_whatsapp_media(
                media_key, mimetype, message_id
            )
            if not image_data:
                self.logger.error(
                    "Failed to download image data for message: %s", message_id
                )
                return

            user_context = await self.bot._extract_user_context(
                response, chat_id, sender_info
            )
            if not user_context:
                await self.bot.api_handler.send_message(
                    chat_id,
                    "Извините, не удалось получить информацию о пользователе."
                )
                return

            # Create image URL or process image
            image_filename = (
                "whatsapp_image_%s_%s.jpg" % (message_id, self.bot.agent_id)
            )

            # Create image data structure
            image_info = {
                "data": base64.b64encode(image_data).decode('utf-8'),
                "filename": image_filename,
                "content_type": mimetype
            }

            # Send to agent with image data
            await self.bot._publish_to_agent(
                chat_id=chat_id,
                platform_user_id=user_context["platform_user_id"],
                message_text="Пользователь отправил изображение",
                user_data=user_context["user_data"],
                image_urls=[{
                    "data": image_info["data"],
                    "filename": image_info["filename"]
                }]
            )

        except Exception as e:
            self.logger.error("Error handling image message: %s", e, exc_info=True)
            try:
                await self.bot.api_handler.send_message(
                    chat_id,
                    "Извините, произошла ошибка при обработке изображения."
                )
            except Exception as send_error:
                self.logger.error("Failed to send error message: %s", send_error)

    async def handle_voice_message(
        self,
        response: Dict[str, Any],
        chat_id: str,
        sender_info: Dict[str, Any]
    ) -> None:
        """Handle voice message processing."""
        try:
            message_id = response.get("id", {}).get("id", "")
            media_data = response.get("body", {})
            media_key = media_data.get("mediaKey", "")
            mimetype = media_data.get("mimetype", "")

            if not media_key:
                self.logger.warning(
                    "No media key found for voice message: %s", message_id
                )
                return

            # Download the voice message
            voice_data = await self.bot.api_handler.download_whatsapp_media(
                media_key, mimetype, message_id
            )
            if not voice_data:
                self.logger.error(
                    "Failed to download voice data for message: %s", message_id
                )
                return

            user_context = await self.bot._extract_user_context(
                response, chat_id, sender_info
            )
            if not user_context:
                await self.bot.api_handler.send_message(
                    chat_id,
                    "Извините, не удалось получить информацию о пользователе."
                )
                return

            # Process voice message with new voice orchestrator
            await self._process_voice_with_orchestrator(
                voice_data, chat_id, user_context, message_id
            )

        except Exception as e:
            self.logger.error("Error handling voice message: %s", e, exc_info=True)
            try:
                await self.bot.api_handler.send_message(
                    chat_id,
                    "Извините, произошла ошибка при обработке голосового сообщения."
                )
            except Exception as send_error:
                self.logger.error("Failed to send error message: %s", send_error)

    async def _process_voice_with_orchestrator(
        self,
        voice_data: bytes,
        chat_id: str,
        user_context: Dict[str, Any],
        message_id: str
    ) -> None:
        """Process voice message using voice orchestrator."""
        try:
            # Create voice file info
            # For now, send voice data directly to agent
            # Voice processing will be handled by the agent's voice tools

            # Send voice data to agent for processing
            await self.bot._publish_to_agent(
                chat_id=chat_id,
                platform_user_id=user_context["platform_user_id"],
                message_text="Пользователь отправил голосовое сообщение",
                user_data=user_context["user_data"]
            )

        except Exception as e:
            self.logger.error(
                "Error processing voice with orchestrator: %s", e, exc_info=True
            )
            try:
                await self.bot.api_handler.send_message(
                    chat_id,
                    "Извините, произошла ошибка при обработке голосового сообщения."
                )
            except Exception as send_error:
                self.logger.error("Failed to send error message: %s", send_error)

    async def process_voice_message_with_orchestrator(
        self,
        audio_data: bytes,
        filename: str,
        chat_id: str,
        platform_user_id: str,
        user_data: Dict[str, Any]
    ) -> None:
        """
        Обработка голосового сообщения через voice orchestrator

        Args:
            audio_data: Данные аудиофайла
            filename: Имя файла
            chat_id: ID чата
            platform_user_id: ID пользователя
            user_data: Данные пользователя
        """
        try:
            # Get agent config
            agent_config = (
                self.bot.agent_config or self.bot._get_fallback_agent_config()
            )

            # Use global voice orchestrator if available, otherwise create temporary one
            orchestrator = await self._get_voice_orchestrator()

            # Initialize voice services for this agent if needed
            await orchestrator.initialize_voice_services_for_agent(
                agent_config=agent_config
            )

            # Process STT
            result = await orchestrator.process_voice_message(
                agent_id=self.bot.agent_id,
                user_id=platform_user_id,
                audio_data=audio_data,
                original_filename=filename,
                agent_config=agent_config
            )

            if result.success and result.text:
                await self._handle_successful_stt(
                    chat_id, platform_user_id, result.text, user_data
                )
            else:
                await self._handle_failed_stt(chat_id, result)

        except Exception as e:
            await self._handle_voice_processing_error(chat_id, e)

    async def _detect_audio_format(self, audio_data: bytes, filename: str):
        """Detect audio format from data and filename."""
        try:
            from app.services.voice_v2.utils.audio import AudioUtils
            detected_format = AudioUtils.detect_format(audio_data, filename)
            mime_type = AudioUtils.get_mime_type(detected_format)
            self.logger.info(
                "Detected audio format: %s -> %s", 
                detected_format.value, mime_type
            )
            return detected_format
        except Exception as e:
            self.logger.warning("Could not detect audio format: %s, using default", e)
            from app.services.voice_v2.core.interfaces import AudioFormat
            return AudioFormat.OGG

    async def _get_voice_orchestrator(self):
        """Get voice orchestrator instance."""
        if self.bot.voice_orchestrator:
            return self.bot.voice_orchestrator

        self.logger.warning(
            ("Global voice orchestrator not available, creating temporary "
             "voice_v2 orchestrator")
        )
        from app.services.voice_v2.providers.enhanced_factory import (
            EnhancedVoiceProviderFactory
        )
        from app.services.voice_v2.infrastructure.cache import VoiceCache
        from app.services.voice_v2.core.orchestrator import (
            VoiceServiceOrchestrator
        )

        enhanced_factory = EnhancedVoiceProviderFactory()
        cache_manager = VoiceCache()
        await cache_manager.initialize()

        orchestrator = VoiceServiceOrchestrator(
            enhanced_factory=enhanced_factory,
            cache_manager=cache_manager,
            file_manager=None  # Use basic setup
        )
        await orchestrator.initialize()
        return orchestrator

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

    async def _handle_failed_stt(self, chat_id: str, result):
        """Handle failed STT result."""
        self.logger.warning(
            "STT processing failed: %s", 
            result.error_message if result else 'No result'
        )
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
        except Exception:
            pass
        finally:
            # Always stop typing indicator
            await self.bot.stop_typing_for_chat(chat_id)
