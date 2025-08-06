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
            # Safely extract message_id handling both string and dict formats
            id_field = response.get("id", {})
            if isinstance(id_field, dict):
                message_id = id_field.get("id", "")
            else:
                message_id = str(id_field)
            
            # Safely extract media_data
            body_field = response.get("body", {})
            if isinstance(body_field, dict):
                media_data = body_field
            else:
                media_data = {}
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
        """Handle voice message processing."""
        try:
            # Extract message_id - it can be either a string or nested object
            id_field = response.get("id", "")
            if isinstance(id_field, dict):
                message_id = id_field.get("id", "")
            else:
                message_id = str(id_field) if id_field else ""
            
            # DEBUG: Log full response structure for voice message analysis
            self.logger.debug(f"Voice message full response structure: {response}")
            
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
                
            self.logger.debug(f"Extracted media_key: '{media_key}', mimetype: '{mimetype}'")

            if not media_key:
                # Try alternative locations for media key
                alt_locations = [
                    response.get("quotedMsg", {}).get("mediaKey", ""),
                    response.get("message", {}).get("mediaKey", ""), 
                    response.get("mediaData", {}).get("mediaKey", ""),
                    response.get("media", {}).get("mediaKey", "")
                ]
                for alt_key in alt_locations:
                    if alt_key:
                        media_key = alt_key
                        self.logger.debug(f"Found media_key in alternative location: {media_key}")
                        break
                        
            if not media_key:
                self.logger.warning(
                    "No media key found for voice message: %s. Available keys: %s", 
                    message_id, list(response.keys())
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

            # 🎯 PHASE 4.4.2: UNIFIED VOICE PROCESSING - Use real STT orchestrator
            # Remove simple voice processing path, use only advanced STT processing
            await self._process_voice_with_stt_orchestrator(
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

    async def _process_voice_with_stt_orchestrator(
        self,
        voice_data: bytes,
        chat_id: str,
        user_context: Dict[str, Any],
        message_id: str
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
                await self._handle_voice_processing_error(chat_id, Exception("Voice orchestrator not available"))
                return

            # Process STT using the voice_v2 orchestrator (simplified approach like Telegram)
            from app.services.voice_v2.core.schemas import STTRequest
            from app.services.voice_v2.core.interfaces import AudioFormat
            
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
                self.logger.info(f"WhatsApp voice transcription successful: '{stt_response.text[:100]}...'")
                await self._handle_successful_stt(
                    chat_id, user_context["platform_user_id"], stt_response.text, user_context
                )
            else:
                # Handle transcription failure
                self.logger.warning("Voice processing failed: No text recognized")
                await self._handle_failed_stt(chat_id, None)

        except Exception as e:
            await self._handle_voice_processing_error(chat_id, e)

    async def _detect_audio_format(self, audio_data: bytes, filename: str):
        """Detect audio format from data and filename."""
        try:
            # Try to detect format from filename extension
            if filename.lower().endswith('.ogg'):
                return 'ogg'
            elif filename.lower().endswith('.wav'):
                return 'wav'
            elif filename.lower().endswith('.mp3'):
                return 'mp3'
            else:
                # Default for WhatsApp voice messages
                return 'ogg'
        except Exception as e:
            self.logger.warning("Could not detect audio format: %s, using default", e)
            return 'ogg'

    async def _get_voice_orchestrator(self):
        """Get voice orchestrator instance."""
        if self.bot.voice_orchestrator:
            return self.bot.voice_orchestrator

        self.logger.warning(
            "Global voice orchestrator not available, creating temporary voice_v2 orchestrator"
        )
        
        # Use the voice_v2 orchestrator 
        from app.services.voice_v2.core.orchestrator import VoiceServiceOrchestrator
        from app.services.voice_v2.infrastructure.cache import VoiceCache
        from app.services.voice_v2.infrastructure.minio_manager import MinioFileManager
        from app.services.voice_v2.providers.unified_factory import VoiceProviderFactory
        from app.core.config import settings

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
        except Exception as e:
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
        error_msg = "No text recognized" if not result else getattr(result, 'error_message', 'STT processing failed')
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
        except Exception:
            pass
        finally:
            # Always stop typing indicator
            await self.bot.stop_typing_for_chat(chat_id)
