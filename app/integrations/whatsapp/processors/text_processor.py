"""
TextProcessor - обработчик текстовых сообщений.

Полностью независимый компонент для обработки текстовых сообщений WhatsApp
с прямой интеграцией с сервисами без промежуточных зависимостей.
Наследует от BaseProcessor для устранения дублирования кода.
"""

from typing import TYPE_CHECKING, Any, Dict

from .base_processor import BaseProcessor

if TYPE_CHECKING:
    from ..core.redis_service import RedisService
    from ..core.user_service import UserService
    from ..whatsapp_bot import WhatsAppIntegrationBot


class TextProcessor(BaseProcessor):
    """Независимый процессор текстовых сообщений WhatsApp."""

    def __init__(
        self,
        bot_instance: "WhatsAppIntegrationBot",
        user_service: "UserService",
        redis_service: "RedisService",
        logger,
    ):
        """Initialize text processor with required dependencies."""
        super().__init__(user_service, logger)
        self._initialize_common_components(bot_instance, redis_service)

    async def handle_message(
        self, response: Dict[str, Any], chat_id: str, sender_info: Dict[str, Any]
    ) -> None:
        """Implementation of abstract handle_message method."""
        await self.handle_text_message(response, chat_id, sender_info)

    async def handle_text_message(
        self, response: Dict[str, Any], chat_id: str, sender_info: Dict[str, Any]
    ) -> None:
        """Handle text message processing with complete independence."""
        try:
            user_data = await self._process_message_with_validation(
                response, chat_id, sender_info
            )
            if not user_data:
                return

            # Validate message text
            text_content = response.get("body", "").strip()
            if not self._is_valid_text_message(text_content, chat_id):
                return

            # Send to agent via Redis with typing
            await self.bot.publish_to_agent(
                {
                    "chat_id": chat_id,
                    "message_text": text_content,
                    "platform_user_id": user_data["platform_user_id"],
                },
                user_data,
            )

        except (ConnectionError, AttributeError, ValueError) as e:
            self.logger.error("Error processing text message: %s", e, exc_info=True)
            await self._send_error_message(
                self.bot, chat_id, "⚠️ Произошла ошибка при обработке сообщения."
            )
            # Stop typing only on error
            await self.bot.stop_typing_for_chat(chat_id)

    def _is_valid_text_message(self, message_text: str, chat_id: str) -> bool:
        """Check if text message is valid."""
        if not message_text or not chat_id:
            self.logger.warning(
                "Message missing required fields: text='%s', chat_id='%s'",
                message_text,
                chat_id,
            )
            return False
        return True
