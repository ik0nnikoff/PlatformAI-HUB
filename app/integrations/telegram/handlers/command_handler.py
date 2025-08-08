"""
Command handler for Telegram integration.

Handles bot commands like /start, /login with proper separation of concerns.
"""

import logging
from typing import TYPE_CHECKING

from aiogram.types import Message

if TYPE_CHECKING:
    from ..telegram_bot import TelegramIntegrationBot


class CommandHandler:
    """Handles Telegram bot commands with clean separation."""

    def __init__(
        self, bot_instance: "TelegramIntegrationBot", logger: logging.LoggerAdapter
    ):
        self.bot = bot_instance
        self.logger = logger

    async def handle_start_command(self, message: Message) -> None:
        """Handle /start command."""
        platform_user_id = str(message.from_user.id)
        chat_id = message.chat.id

        self.logger.info(
            "User %s (ChatID: %s) triggered /start for agent %s",
            platform_user_id,
            chat_id,
            self.bot.agent_id,
        )

        try:
            await self.bot.api_client.send_message(
                chat_id, "Привет! Задайте мне вопрос."
            )
        except (ConnectionError, TimeoutError, ValueError) as e:
            self.logger.error("Error sending start message to %s: %s", chat_id, e)
        # pylint: disable=broad-exception-caught
        except Exception as e:
            # Catch-all для неожиданных системных ошибок
            self.logger.error(
                "Unexpected error sending start message to %s: %s", chat_id, e
            )

    async def handle_login_command(self, message: Message) -> None:
        """Handle /login command."""
        platform_user_id = str(message.from_user.id)
        chat_id = message.chat.id

        self.logger.info(
            "User %s (ChatID: %s) triggered /login for agent %s",
            platform_user_id,
            chat_id,
            self.bot.agent_id,
        )

        try:
            # Check if user is already authorized
            is_authorized = await self._check_user_authorization(platform_user_id)

            if is_authorized:
                self.logger.info(
                    "User %s is already authorized for agent %s.",
                    platform_user_id,
                    self.bot.agent_id,
                )
                await self.bot.api_client.send_message_with_markup(
                    chat_id,
                    "Вы уже авторизованы! Можете продолжать работу.",
                    self.bot.api_client.remove_keyboard(),
                )
            else:
                self.logger.info(
                    "User %s is not authorized for agent %s. "
                    "Requesting contact for login.",
                    platform_user_id,
                    self.bot.agent_id,
                )
                await self.bot.api_client.send_message_with_markup(
                    chat_id,
                    "Для авторизации, пожалуйста, поделитесь своим контактом, нажав кнопку ниже:",
                    self.bot.api_client.create_contact_request_keyboard(),
                )

        except (ConnectionError, TimeoutError, ValueError) as e:
            self.logger.error(
                "Error handling login command from %s: %s", chat_id, e, exc_info=True
            )
            await self.bot.api_client.send_message(
                chat_id, "⚠️ Ошибка при обработке команды /login."
            )
        # pylint: disable=broad-exception-caught
        except Exception as e:
            # Catch-all для неожиданных системных ошибок
            self.logger.error(
                "Unexpected error handling login command from %s: %s",
                chat_id,
                e,
                exc_info=True,
            )
            await self.bot.api_client.send_message(
                chat_id, "⚠️ Внутренняя ошибка системы."
            )

    async def handle_help_command(self, message: Message) -> None:
        """Handle /help command."""
        chat_id = message.chat.id

        help_text = """
🤖 Доступные команды:

/start - Начать работу с ботом
/login - Авторизоваться через контакт
/help - Показать это сообщение

Вы можете отправлять:
📝 Текстовые сообщения
🎤 Голосовые сообщения
🖼️ Изображения
📞 Контакт для авторизации
        """

        try:
            await self.bot.api_client.send_message(chat_id, help_text)
        except (ConnectionError, TimeoutError, ValueError) as e:
            self.logger.error("Error sending help message to %s: %s", chat_id, e)
        # pylint: disable=broad-exception-caught
        except Exception as e:
            # Catch-all для неожиданных системных ошибок
            self.logger.error(
                "Unexpected error sending help message to %s: %s", chat_id, e
            )

    async def _check_user_authorization(self, platform_user_id: str) -> bool:
        """Check if user is authorized for this agent."""
        try:
            if self.bot.user_service:
                return await self.bot.user_service.check_user_authorization(
                    platform_user_id
                )

            self.logger.warning("No authorization checking method available")
            return False
        except (ConnectionError, TimeoutError, ValueError) as e:
            self.logger.error(
                "Error checking authorization for %s: %s", platform_user_id, e
            )
            return False
        # pylint: disable=broad-exception-caught
        except Exception as e:
            # Catch-all для неожиданных системных ошибок
            self.logger.error(
                "Unexpected error checking authorization for %s: %s",
                platform_user_id,
                e,
            )
            return False
