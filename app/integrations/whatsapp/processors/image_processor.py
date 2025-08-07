"""
ImageProcessor - обработчик изображений.

Полностью независимый компонент для обработки изображений WhatsApp
с прямой интеграцией с image orchestrator.
Наследует от BaseProcessor для устранения дублирования кода.
"""

from typing import TYPE_CHECKING, Any, Dict, List, Optional

from .base_processor import BaseProcessor

if TYPE_CHECKING:
    from ..core.redis_service import RedisService
    from ..core.user_service import UserService
    from ..whatsapp_bot import WhatsAppIntegrationBot


class ImageProcessor(BaseProcessor):
    """Независимый процессор изображений WhatsApp."""

    def __init__(
        self,
        bot_instance: "WhatsAppIntegrationBot",
        user_service: "UserService",
        redis_service: "RedisService",
        logger,
    ):
        """Initialize image processor with required dependencies."""
        super().__init__(user_service, logger)
        self._initialize_common_components(bot_instance, redis_service)

    async def handle_message(
        self, response: Dict[str, Any], chat_id: str, sender_info: Dict[str, Any]
    ) -> None:
        """Implementation of abstract handle_message method."""
        await self.handle_image_message(response, chat_id, sender_info)

    async def handle_image_message(
        self, response: Dict[str, Any], chat_id: str, sender_info: Dict[str, Any]
    ) -> None:
        """Handle image message processing with complete independence."""
        try:
            user_data = await self._process_message_with_validation(
                response, chat_id, sender_info
            )
            if not user_data:
                return

            # Process image using image orchestrator
            image_urls = await self._process_image_with_orchestrator(response, chat_id)
            if not image_urls:
                return  # Error already handled

            # Send to agent with image URLs
            await self.redis_service.publish_to_agent(
                response.get("body", ""),
                {
                    "chat_id": chat_id,
                    "platform_user_id": user_data["platform_user_id"],
                    "is_image_message": True,
                },
                user_data,
                image_urls,
            )

        except (ConnectionError, AttributeError, ValueError) as e:
            self.logger.error("Error processing image message: %s", e, exc_info=True)
            await self._send_error_message(
                self.bot, chat_id, "⚠️ Произошла ошибка при обработке изображения."
            )
        finally:
            # Stop typing indicator
            await self.bot._stop_typing_for_chat(chat_id)

    async def _process_image_with_orchestrator(
        self, response: Dict[str, Any], chat_id: str
    ) -> Optional[List[str]]:
        """Process image using image orchestrator."""
        try:
            # Extract image data
            image_data = await self._extract_image_data(response)
            if not image_data:
                await self._send_error_message(
                    self.bot, chat_id, "⚠️ Не удалось получить изображение."
                )
                return None

            # Use image orchestrator if available
            if not self.bot.image_orchestrator:
                self.logger.warning(
                    "Image orchestrator not initialized, using direct URL"
                )
                # Fallback to direct image URL if orchestrator not available
                return [image_data["media_url"]] if image_data["media_url"] else None

            # Process with image orchestrator
            result = await self.bot.image_orchestrator.process_images(
                [image_data], self.bot.agent_id, chat_id
            )

            if result and result.get("image_urls"):
                self.logger.info("Image processed successfully for chat %s", chat_id)
                return result["image_urls"]

            self.logger.warning("Image processing failed for chat %s", chat_id)
            await self._send_error_message(
                self.bot,
                chat_id,
                "⚠️ Не удалось обработать изображение. Попробуйте еще раз.",
            )
            return None

        except (ConnectionError, AttributeError, ValueError) as e:
            self.logger.error("Error in image processing: %s", e, exc_info=True)
            await self._send_error_message(
                self.bot, chat_id, "⚠️ Ошибка обработки изображения."
            )
            return None

    async def _extract_image_data(
        self, response: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        """Extract image data from WhatsApp response."""
        message_id = self._extract_message_id(response)
        result = await self._extract_media_common(self.bot, response, message_id)
        if result:
            # Add media_url for image processing
            media_data = self._extract_media_data(response)
            result["media_url"] = media_data["media_url"]
        return result
