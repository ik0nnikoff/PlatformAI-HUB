"""
Text message processor for Telegram integration.

Handles text message processing and user management.
"""

from typing import TYPE_CHECKING

from aiogram.types import Message

from app.integrations.telegram.processors.base_processor import BaseProcessor

if TYPE_CHECKING:
    from ..core.redis_service import RedisService
    from ..core.user_service import UserService
    from ..telegram_bot import TelegramIntegrationBot


class TextProcessor(BaseProcessor):
    """Независимый процессор текстовых сообщений Telegram."""

    def __init__(
        self,
        bot_instance: "TelegramIntegrationBot",
        user_service: "UserService",
        redis_service: "RedisService",
        logger,
    ):
        """Initialize text processor with required dependencies."""
        super().__init__(user_service, logger)
        self._initialize_common_components(bot_instance, redis_service)

    async def handle_message(self, message: Message) -> None:
        """Implementation of abstract handle_message method."""
        await self.handle_text_message(message)

    async def handle_text_message(self, message: Message) -> None:
        """Handle text message processing with complete independence."""
        if not self._validate_message_data(message):
            return

        chat_id = message.chat.id
        platform_user_id = str(message.from_user.id)
        message_text = message.text

        if not self._is_valid_text_message(message_text, chat_id):
            return

        self.logger.info(
            "Text from %s in chat %s for agent %s: '%s...'",
            platform_user_id,
            chat_id,
            self.bot.agent_id,
            message_text[:50],
        )

        # Start typing indicator
        await self._start_typing_indicator(chat_id)

        try:
            # Process user and get data
            user_data = await self._process_user_common(message)

            # Extract chat data
            chat_data = self._extract_chat_data(message)

            # Publish message to agent
            success = await self.redis_service.publish_to_agent(
                message_text, chat_data, user_data
            )

            if success:
                self.logger.info(
                    "Message from %s published to agent %s. User data: %s",
                    platform_user_id,
                    self.bot.agent_id,
                    user_data,
                )
            else:
                await self._send_error_message(
                    chat_id, "Ошибка: Не удалось отправить сообщение агенту."
                )

        except (ConnectionError, TimeoutError, ValueError) as e:
            self.logger.error(
                "Error handling text message from chat %s for agent %s: %s",
                chat_id,
                self.bot.agent_id,
                e,
                exc_info=True,
            )
            await self._send_error_message(chat_id, "⚠️ Ошибка при обработке запроса.")
        # pylint: disable=broad-exception-caught
        except Exception as e:
            # Catch-all для неожиданных системных ошибок
            self.logger.error(
                "Unexpected error handling text message from chat %s for agent %s: %s",
                chat_id,
                self.bot.agent_id,
                e,
                exc_info=True,
            )
            await self._send_error_message(chat_id, "⚠️ Внутренняя ошибка системы.")

        finally:
            # Stop typing indicator after processing
            await self._stop_typing_indicator(chat_id)

    async def handle_document_message(self, message: Message) -> None:
        """Handle document message by treating as text with document info."""
        if not message.document:
            self.logger.warning("Document message without document content")
            return

        # For now, handle as text message indicating document received
        document_name = message.document.file_name or "document"

        # Create a modified message for text processing
        # Note: This is a simplified approach - in future iterations,
        # document processing can be enhanced with actual content extraction

        self.logger.info(
            "Document received: %s from %s", document_name, message.from_user.id
        )

        # Create a modified message text for processing
        document_text = f"Пользователь отправил документ: {document_name}"

        # Temporarily modify message text
        original_text = message.text
        message.text = document_text

        try:
            await self.handle_text_message(message)
        finally:
            # Restore original text
            message.text = original_text

    def _is_valid_text_message(self, message_text: str, chat_id: int) -> bool:
        """Check if text message is valid."""
        if not message_text:
            self.logger.warning("Empty text message from chat %s", chat_id)
            return False

        return True
