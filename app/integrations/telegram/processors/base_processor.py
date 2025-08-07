"""
BaseProcessor - базовый класс для всех процессоров Telegram сообщений.

Содержит общую логику для обработки пользователей и извлечения данных
из Telegram сообщений для устранения дублирования кода.
"""

import logging
import asyncio
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any, Dict, Optional

from aiogram.types import Message, User

if TYPE_CHECKING:
    from ..core.redis_service import RedisService
    from ..core.user_service import UserService
    from ..telegram_bot import TelegramIntegrationBot


class BaseProcessor(ABC):
    """Базовый абстрактный класс для всех процессоров Telegram сообщений."""

    def __init__(self, user_service: "UserService", logger: logging.LoggerAdapter):
        """Initialize base processor with shared dependencies."""
        self.user_service = user_service
        self.logger = logger
        # Initialize attributes that will be set by _initialize_common_components
        self.bot: Optional["TelegramIntegrationBot"] = None
        self.redis_service: Optional["RedisService"] = None

    def _initialize_common_components(
        self,
        bot_instance: "TelegramIntegrationBot",
        redis_service: "RedisService",
    ) -> None:
        """Initialize common components used by all processors."""
        self.bot = bot_instance
        self.redis_service = redis_service

    def _extract_chat_data(self, message: Message) -> Dict[str, str]:
        """Extract chat data from message."""
        return {
            "chat_id": str(message.chat.id),
            "platform_user_id": str(message.from_user.id),
        }

    def _extract_user_info(self, telegram_user: User) -> Dict[str, Any]:
        """Extract user information from Telegram user object."""
        return {
            "platform_user_id": str(telegram_user.id),
            "first_name": telegram_user.first_name or "Unknown",
            "last_name": telegram_user.last_name,
            "username": telegram_user.username,
            "language_code": telegram_user.language_code,
        }

    async def _get_user_authorization_status(self, platform_user_id: str) -> bool:
        """Check user authorization status through bot instance."""
        try:
            if self.bot and hasattr(self.bot, "check_user_authorization"):
                return await self.bot.check_user_authorization(platform_user_id)

            # Fallback to user service if available
            return await self.user_service.check_user_authorization(
                platform_user_id
            )
        except (ConnectionError, TimeoutError, ValueError) as e:
            self.logger.error("Error checking user authorization: %s", e)
            return False
        # pylint: disable=broad-exception-caught
        except Exception as e:
            # Catch-all для неожиданных системных ошибок
            self.logger.error("Unexpected error checking user authorization: %s", e)
            return False

    async def _process_user_common(self, message: Message) -> Dict[str, Any]:
        """Common user processing logic shared across processors."""
        telegram_user = message.from_user
        platform_user_id = str(telegram_user.id)

        # Check authorization
        is_authorized = await self._get_user_authorization_status(platform_user_id)

        # Get or create user
        user_data = await self.user_service.get_or_create_user(
            telegram_user, is_authorized
        )

        if not user_data:
            user_data = self.user_service.create_fallback_data(telegram_user)

        return user_data

    async def _send_error_message(self, chat_id: int, error_text: str) -> None:
        """Send error message to user."""
        try:
            if self.bot and self.bot.api_client:
                await self.bot.api_client.send_message(chat_id, error_text)
            elif self.bot and self.bot.bot:
                await self.bot.bot.send_message(chat_id, error_text)
        except (ConnectionError, TimeoutError, ValueError) as e:
            self.logger.error("Failed to send error message to %s: %s", chat_id, e)
        # pylint: disable=broad-exception-caught
        except Exception as e:
            # Catch-all для неожиданных системных ошибок
            self.logger.error(
                "Unexpected error sending error message to %s: %s", chat_id, e
            )

    async def _start_typing_indicator(self, chat_id: int) -> None:
        """Start typing indicator for chat."""
        try:
            if self.bot and self.bot.typing_manager:
                await self.bot.typing_manager.start_typing(chat_id)
            elif self.bot and hasattr(self.bot, "typing_tasks"):
                # Fallback to direct typing management
                if chat_id in self.bot.typing_tasks:
                    self.bot.typing_tasks[chat_id].cancel()

                self.bot.typing_tasks[chat_id] = asyncio.create_task(
                    self._send_typing_periodically(chat_id)
                )
        except (ConnectionError, TimeoutError, ValueError) as e:
            self.logger.error("Error starting typing indicator for %s: %s", chat_id, e)
        # pylint: disable=broad-exception-caught
        except Exception as e:
            # Catch-all для неожиданных системных ошибок
            self.logger.error(
                "Unexpected error starting typing indicator for %s: %s", chat_id, e
            )

    async def _stop_typing_indicator(self, chat_id: int) -> None:
        """Stop typing indicator for chat."""
        try:
            if self.bot and self.bot.typing_manager:
                await self.bot.typing_manager.stop_typing(chat_id)
            elif self.bot and hasattr(self.bot, "typing_tasks"):
                # Fallback to direct typing management
                if chat_id in self.bot.typing_tasks:
                    self.bot.typing_tasks[chat_id].cancel()
                    del self.bot.typing_tasks[chat_id]
        except (ConnectionError, TimeoutError, ValueError) as e:
            self.logger.error("Error stopping typing indicator for %s: %s", chat_id, e)
        # pylint: disable=broad-exception-caught
        except Exception as e:
            # Catch-all для неожиданных системных ошибок
            self.logger.error(
                "Unexpected error stopping typing indicator for %s: %s", chat_id, e
            )

    async def _send_typing_periodically(self, chat_id: int):
        """Send typing indicator periodically - fallback method."""
        if not self.bot or not self.bot.bot:
            return

        try:
            # pylint: disable=import-outside-toplevel
            from aiogram.enums import ChatAction

            while True:
                await self.bot.bot.send_chat_action(chat_id, ChatAction.TYPING)
                await asyncio.sleep(3)  # Send typing status every 3 seconds
        except asyncio.CancelledError:
            self.logger.debug("Typing task cancelled for chat %s.", chat_id)
        except (ConnectionError, TimeoutError, ValueError) as e:
            self.logger.error("Error in typing task for chat %s: %s", chat_id, e)
        # pylint: disable=broad-exception-caught
        except Exception as e:
            # Catch-all для неожиданных системных ошибок
            self.logger.error(
                "Unexpected error in typing task for chat %s: %s", chat_id, e
            )

    def _validate_message_data(self, message: Message) -> bool:
        """Validate basic message data."""
        if not message:
            self.logger.error("Received None message")
            return False

        if not message.from_user:
            self.logger.warning("Message without user information")
            return False

        if not message.chat:
            self.logger.warning("Message without chat information")
            return False

        return True

    def is_initialized(self) -> bool:
        """Check if processor is properly initialized."""
        return self.bot is not None and self.redis_service is not None

    def get_processor_type(self) -> str:
        """Get processor type name."""
        return self.__class__.__name__

    @abstractmethod
    async def handle_message(self, message: Message) -> None:
        """Handle message processing - must be implemented by subclasses."""
