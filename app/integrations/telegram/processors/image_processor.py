"""
Image processor for Telegram integration.

Handles image and media group processing with orchestrator integration.
Processes photos, media groups, and coordinates with image orchestrator.
"""

from typing import TYPE_CHECKING, Any, Dict, List, Optional

from aiogram.types import Message

from app.integrations.telegram.processors.base_processor import BaseProcessor

if TYPE_CHECKING:
    from ..core.redis_service import RedisService
    from ..core.user_service import UserService
    from ..telegram_bot import TelegramIntegrationBot


class ImageProcessor(BaseProcessor):
    """Независимый процессор изображений Telegram."""

    def __init__(
        self,
        bot_instance: "TelegramIntegrationBot",
        user_service: "UserService",
        redis_service: "RedisService",
        logger,
    ):
        """Initialize image processor with required dependencies."""
        super().__init__(user_service, logger)
        self._initialize_common_components(bot_instance, redis_service)

    async def handle_message(self, message: Message) -> None:
        """Implementation of abstract handle_message method."""
        await self.handle_image_message(message)

    async def handle_image_message(self, message: Message) -> None:
        """Handle single image message processing."""
        if not self._validate_message_data(message):
            return

        platform_user_id = str(message.from_user.id)

        if not message.photo:
            self.logger.warning(
                f"Image message without photo content from {platform_user_id}"
            )
            return

        # Handle as single-photo group
        await self.handle_media_group([message])

    async def handle_media_group(self, messages: List[Message]) -> None:
        """Handle media group (album) processing."""
        if not messages:
            return

        first_message = messages[0]
        chat_id = first_message.chat.id
        platform_user_id = str(first_message.from_user.id)

        self.logger.info(
            "Processing %s images from %s in chat %s",
            len(messages),
            platform_user_id,
            chat_id,
        )

        # Start typing indicator
        await self._start_typing_indicator(chat_id)

        try:
            if not await self._validate_image_orchestrator(chat_id):
                return

            user_data = await self._process_user_common(first_message)

            success = await self._process_and_publish_images(
                messages, first_message, user_data, platform_user_id
            )

            if not success:
                await self._send_error_message(
                    chat_id, "🖼️ Не удалось обработать изображения"
                )

        except (ConnectionError, TimeoutError, ValueError) as e:
            self.logger.error(
                "Error processing images from chat %s: %s", chat_id, e, exc_info=True
            )
            await self._send_error_message(
                chat_id, "⚠️ Ошибка при обработке изображений."
            )
        # pylint: disable=broad-exception-caught
        except Exception as e:
            # Catch-all для неожиданных системных ошибок
            self.logger.error(
                "Unexpected error processing images from chat %s: %s",
                chat_id,
                e,
                exc_info=True,
            )
            await self._send_error_message(chat_id, "⚠️ Внутренняя ошибка системы.")

        finally:
            # Stop typing indicator
            await self._stop_typing_indicator(chat_id)

    async def _validate_image_orchestrator(self, chat_id: int) -> bool:
        """Validate if image orchestrator is available."""
        if (
            not self.bot.image_orchestrator
            or not self.bot.image_orchestrator.get_orchestrator()
        ):
            await self._send_error_message(
                chat_id, "🖼️ Функции обработки изображений временно недоступны."
            )
            return False
        return True

    async def _process_and_publish_images(
        self,
        messages: List[Message],
        first_message: Message,
        user_data: dict,
        platform_user_id: str,
    ) -> bool:
        """Process images and publish to agent."""
        image_urls = await self._process_images_with_orchestrator(messages)

        if not image_urls:
            return False

        message_text = self._create_image_message_text(image_urls, first_message)
        chat_data = self._extract_chat_data(first_message)

        success = await self.redis_service.publish_to_agent(
            message_text, chat_data, user_data, image_urls=image_urls
        )

        if success:
            self.logger.info(
                "Images from %s published to agent %s",
                platform_user_id,
                self.bot.agent_id,
            )
        else:
            await self._send_error_message(
                first_message.chat.id, "Ошибка отправки изображений агенту."
            )

        return success

    def _create_image_message_text(
        self, image_urls: List[str], first_message: Message
    ) -> str:
        """Create message text for images."""
        image_count = len(image_urls)
        if image_count == 1:
            plural_form = "е"
        elif image_count < 5:
            plural_form = "й"
        else:
            plural_form = "й"

        message_text = f"Пользователь отправил {image_count} изображени{plural_form}"

        # Add caption if available
        caption = first_message.caption
        if caption:
            message_text += f" с подписью: {caption}"

        return message_text

    async def _process_images_with_orchestrator(
        self, messages: List[Message]
    ) -> Optional[List[str]]:
        """Process images using image orchestrator."""
        try:
            image_orchestrator = self.bot.image_orchestrator.get_orchestrator()
            if not image_orchestrator:
                return None

            # Collect image data from all messages
            images_data = []
            original_filenames = []
            for message in messages:
                if message.photo:
                    image_data = await self._extract_image_data(message)
                    if image_data:
                        # ImageOrchestrator ожидает список bytes, не dict
                        images_data.append(image_data["data"])
                        # Создаем имя файла на основе file_id
                        filename = f"telegram_photo_{image_data['file_id']}.jpg"
                        original_filenames.append(filename)

            if not images_data:
                return None

            # Process images through orchestrator
            first_message = messages[0]
            agent_id = self.bot.agent_id
            user_id = str(first_message.from_user.id)

            image_urls = await image_orchestrator.process_images(
                images_data=images_data,
                agent_id=agent_id,
                user_id=user_id,
                original_filenames=original_filenames,
            )

            return image_urls

        except (ConnectionError, TimeoutError, ValueError) as e:
            self.logger.error(
                "Error processing images with orchestrator: %s", e, exc_info=True
            )
            return None
        # pylint: disable=broad-exception-caught
        except Exception as e:
            # Catch-all для неожиданных системных ошибок
            self.logger.error(
                "Unexpected error processing images with orchestrator: %s",
                e,
                exc_info=True,
            )
            return None

    async def _extract_image_data(self, message: Message) -> Optional[Dict[str, Any]]:
        """Extract image data from Telegram message."""
        try:
            if not message.photo:
                return None

            # Get the largest photo size
            largest_photo = max(message.photo, key=lambda p: p.file_size or 0)

            # Get file info
            file_info = await self.bot.api_client.get_file_info(largest_photo.file_id)
            if not file_info:
                return None

            # Download image data
            image_data = await self.bot.api_client.download_file(file_info["file_path"])
            if not image_data:
                return None

            return {
                "data": image_data,
                "file_id": largest_photo.file_id,
                "width": largest_photo.width,
                "height": largest_photo.height,
                "file_size": largest_photo.file_size,
                "caption": message.caption,
            }

        except (ConnectionError, TimeoutError, ValueError) as e:
            self.logger.error("Error extracting image data: %s", e, exc_info=True)
            return None
        # pylint: disable=broad-exception-caught
        except Exception as e:
            # Catch-all для неожиданных системных ошибок
            self.logger.error(
                "Unexpected error extracting image data: %s", e, exc_info=True
            )
            return None
