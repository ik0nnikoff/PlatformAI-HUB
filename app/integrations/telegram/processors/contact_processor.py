"""
Contact processor for Telegram integration.

Handles contact sharing and user authorization through contact validation.
Implements secure contact-based authentication following SOLID principles.
"""

import asyncio
from typing import TYPE_CHECKING
from aiogram.types import Message, ReplyKeyboardRemove
from app.integrations.telegram.processors.base_processor import BaseProcessor

if TYPE_CHECKING:
    from ..core.redis_service import RedisService
    from ..core.user_service import UserService
    from ..telegram_bot import TelegramIntegrationBot


class ContactProcessor(BaseProcessor):
    """Процессор для обработки контактов в Telegram интеграции."""

    def __init__(
        self,
        bot_instance: "TelegramIntegrationBot",
        user_service: "UserService",
        redis_service: "RedisService",
        logger,
    ):
        """Initialize contact processor with required dependencies."""
        super().__init__(user_service, logger)
        self._initialize_common_components(bot_instance, redis_service)

    async def handle_message(self, message: Message) -> None:
        """Implementation of abstract handle_message method."""
        await self.handle_contact_message(message)

    async def handle_contact_message(self, message: Message) -> None:
        """Handle contact message for user authorization."""
        if not self._validate_message_data(message):
            return

        if not message.contact:
            self.logger.warning("Contact message without contact data")
            return

        chat_id = message.chat.id
        user_id = message.from_user.id
        contact_platform_user_id = (
            str(message.contact.user_id) if message.contact.user_id else None
        )
        phone_number = message.contact.phone_number
        first_name = message.contact.first_name
        last_name = message.contact.last_name
        telegram_user_id_from_message = str(message.from_user.id)

        self.logger.info(
            f"Received contact from Telegram UserID {telegram_user_id_from_message} "
            f"(ChatID: {chat_id}). Contact details: Phone {phone_number}, "
            f"ContactPlatformUserID {contact_platform_user_id}. For agent {self.bot.agent_id}"
        )

        # Validate contact belongs to sender
        if not self._validate_contact_ownership(
            contact_platform_user_id, telegram_user_id_from_message, chat_id
        ):
            return

        # Process contact authorization
        contact_data = {
            "platform_user_id": contact_platform_user_id,
            "phone_number": phone_number,
            "first_name": first_name,
            "last_name": last_name,
            "username": message.from_user.username,
            "chat_id": chat_id,
            "user_id": user_id,
        }
        await self._process_contact_authorization(contact_data)

    def _validate_contact_ownership(
        self, contact_platform_user_id: str, telegram_user_id: str, chat_id: int
    ) -> bool:
        """Validate that the contact belongs to the sender."""
        if not contact_platform_user_id or contact_platform_user_id != telegram_user_id:
            self.logger.warning(
                f"Contact's platform_user_id ({contact_platform_user_id}) does not match "
                f"sender's Telegram ID ({telegram_user_id}). Ignoring contact."
            )

            # Send error message asynchronously
            asyncio.create_task(self._send_contact_ownership_error(chat_id))
            return False
        return True

    async def _send_contact_ownership_error(self, chat_id: int) -> None:
        """Send error message for contact ownership validation."""
        try:
            if self.bot.api_client:
                await self.bot.api_client.send_message_with_markup(
                    chat_id,
                    "Похоже, вы пытаетесь поделиться чужим контактом. "
                    "Пожалуйста, поделитесь своим собственным контактом.",
                    ReplyKeyboardRemove(),
                )
        except (ConnectionError, TimeoutError, ValueError) as e:
            self.logger.error("Failed to send contact ownership error: %s", e)
        # pylint: disable=broad-exception-caught
        except Exception as e:
            # Catch-all для неожиданных системных ошибок
            self.logger.error("Unexpected error sending contact ownership error: %s", e)

    async def _process_contact_authorization(self, contact_data: dict) -> None:
        """Process contact authorization and update user data."""
        chat_id = contact_data["chat_id"]
        platform_user_id = contact_data["platform_user_id"]

        try:
            # Prepare user details for update
            user_details = {
                "phone_number": contact_data["phone_number"],
                "first_name": contact_data["first_name"],
                "last_name": contact_data["last_name"],
                "username": contact_data["username"],
            }
            # Remove None values
            user_details = {k: v for k, v in user_details.items() if v is not None}

            # Create or update user
            result = await self.user_service.create_or_update_user_with_authorization(
                platform_user_id=platform_user_id,
                user_details=user_details,
                is_authorized=True,
            )

            if result["success"]:
                await self._handle_successful_authorization(contact_data)
            else:
                await self._handle_authorization_failure(chat_id, result.get("error"))

        except (ConnectionError, TimeoutError, ValueError) as e:
            self.logger.error(
                "Error processing contact for user %s, agent %s: %s",
                platform_user_id,
                self.bot.agent_id,
                e,
                exc_info=True,
            )
            await self._send_error_message(
                chat_id, "Внутренняя ошибка при обработке контакта."
            )
        # pylint: disable=broad-exception-caught
        except Exception as e:
            # Catch-all для неожиданных системных ошибок
            self.logger.error(
                "Unexpected error processing contact for user %s, agent %s: %s",
                platform_user_id,
                self.bot.agent_id,
                e,
                exc_info=True,
            )
            await self._send_error_message(
                chat_id, "Внутренняя ошибка при обработке контакта."
            )

    async def _handle_successful_authorization(self, contact_data: dict) -> None:
        """Handle successful user authorization."""
        chat_id = contact_data["chat_id"]
        platform_user_id = contact_data["platform_user_id"]

        self.logger.info(
            "User %s authorized for agent %s",
            platform_user_id,
            self.bot.agent_id
        )

        # Clear auth cache
        await self._clear_auth_cache(platform_user_id)

        # Send success message
        try:
            if self.bot.api_client:
                await self.bot.api_client.send_message_with_markup(
                    chat_id, "Спасибо! Вы успешно авторизованы.", ReplyKeyboardRemove()
                )
        except (ConnectionError, TimeoutError, ValueError) as e:
            self.logger.error("Failed to send success message: %s", e)
        # pylint: disable=broad-exception-caught
        except Exception as e:
            # Catch-all для неожиданных системных ошибок
            self.logger.error("Unexpected error sending success message: %s", e)

        # Notify agent about authorization
        await self._notify_agent_about_authorization(contact_data)

    async def _handle_authorization_failure(self, chat_id: int, error: str) -> None:
        """Handle authorization failure."""
        self.logger.error(f"Authorization failed: {error}")
        await self._send_error_message(
            chat_id, "Ошибка при обновлении статуса авторизации."
        )

    async def _clear_auth_cache(self, platform_user_id: str) -> None:
        """Clear authentication cache for user."""
        try:
            await self.user_service.clear_auth_cache(platform_user_id)
            self.logger.info("Auth cache cleared for user %s", platform_user_id)
        except (ConnectionError, TimeoutError, ValueError) as e:
            self.logger.error("Failed to clear auth cache: %s", e)
        # pylint: disable=broad-exception-caught
        except Exception as e:
            # Catch-all для неожиданных системных ошибок
            self.logger.error("Unexpected error clearing auth cache: %s", e)

    async def _notify_agent_about_authorization(self, contact_data: dict) -> None:
        """Notify agent about successful user authorization."""
        chat_id = contact_data["chat_id"]
        user_id = contact_data["user_id"]

        try:
            agent_user_data = {
                "is_authenticated": True,
                "user_id": str(user_id),
                "phone_number": contact_data["phone_number"],
                "first_name": contact_data["first_name"],
                "last_name": contact_data["last_name"],
            }

            chat_data = {"chat_id": str(chat_id), "platform_user_id": str(user_id)}

            await self.redis_service.publish_to_agent(
                "Пользователь успешно авторизовался.", chat_data, agent_user_data
            )
        except (ConnectionError, TimeoutError, ValueError) as e:
            self.logger.error("Failed to notify agent about authorization: %s", e)
        # pylint: disable=broad-exception-caught
        except Exception as e:
            # Catch-all для неожиданных системных ошибок
            self.logger.error(
                "Unexpected error notifying agent about authorization: %s", e
            )

    async def _send_error_message(self, chat_id: int, error_text: str) -> None:
        """Send error message to user."""
        try:
            if self.bot.api_client:
                await self.bot.api_client.send_message_with_markup(
                    chat_id, error_text, ReplyKeyboardRemove()
                )
        except (ConnectionError, TimeoutError, ValueError) as e:
            self.logger.error("Failed to send error message: %s", e)
        # pylint: disable=broad-exception-caught
        except Exception as e:
            # Catch-all для неожиданных системных ошибок
            self.logger.error("Unexpected error sending error message: %s", e)
