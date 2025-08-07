"""
Telegram API client for infrastructure layer.

Handles bot API operations with external Telegram service
following clean architecture principles.
"""

from typing import Optional, Union

from aiogram import Bot
from aiogram.enums import ChatAction
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import (
    ReplyKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardRemove,
    InputFile,
    InlineKeyboardMarkup,
)


class TelegramAPIClient:
    """Handles Telegram API operations for bot integration."""

    def __init__(self, bot_instance):
        """
        Initialize API client.

        Args:
            bot_instance: Reference to main TelegramIntegrationBot instance
        """
        self.bot_instance = bot_instance
        self.logger = bot_instance.logger

    @property
    def bot(self) -> Optional[Bot]:
        """Get the Telegram bot instance."""
        return self.bot_instance.bot

    async def send_message(self, chat_id: int, text: str, reply_markup=None) -> bool:
        """Send text message via Telegram Bot API."""
        if not self.bot:
            self.logger.error("Bot not initialized, cannot send message")
            return False

        try:
            await self.bot.send_message(
                chat_id=chat_id, text=text, reply_markup=reply_markup
            )
            self.logger.debug(f"Message sent to chat {chat_id}")
            return True

        except TelegramBadRequest as e:
            self.logger.error(f"Telegram API error sending message to {chat_id}: {e}")
            return False
        except (ConnectionError, TimeoutError, ValueError) as e:
            self.logger.error(
                f"Network or value error sending message to {chat_id}: {e}",
                exc_info=True
            )
            return False
        # pylint: disable=broad-exception-caught
        except Exception as e:
            # Catch-all для неожиданных системных ошибок
            self.logger.error(
                f"Unexpected error sending message to {chat_id}: {e}", exc_info=True
            )
            return False

    async def send_message_with_markup(
        self,
        chat_id: int,
        text: str,
        reply_markup: Union[
            ReplyKeyboardMarkup, ReplyKeyboardRemove, InlineKeyboardMarkup
        ],
    ) -> bool:
        """Send message with specific reply markup."""
        return await self.send_message(chat_id, text, reply_markup)

    async def send_typing_action(self, chat_id: int, is_typing: bool = True) -> bool:
        """Send typing indicator to Telegram chat."""
        if not self.bot:
            return False

        try:
            if is_typing:
                await self.bot.send_chat_action(
                    chat_id=chat_id, action=ChatAction.TYPING
                )
            # Note: Telegram doesn't have explicit "stop typing" action
            return True
        except (ConnectionError, TimeoutError, ValueError) as e:
            self.logger.debug(f"Network or value error sending typing action to {chat_id}: {e}")
            return False
        # pylint: disable=broad-exception-caught
        except Exception as e:
            # Catch-all для неожиданных системных ошибок
            self.logger.debug(f"Unexpected error sending typing action to {chat_id}: {e}")
            return False

    async def send_voice_message(self, chat_id: int, audio_url: str) -> bool:
        """Send voice message to Telegram chat from audio URL."""
        if not self.bot:
            return False

        try:
            # Download audio file from URL and send as InputFile
            import aiohttp
            from aiogram.types import BufferedInputFile
            
            async with aiohttp.ClientSession() as session:
                async with session.get(audio_url) as response:
                    if response.status == 200:
                        audio_data = await response.read()
                        voice_file = BufferedInputFile(
                            audio_data, 
                            filename="voice_response.mp3"
                        )
                        await self.bot.send_voice(chat_id=chat_id, voice=voice_file)
                        self.logger.debug(f"Voice message sent to chat {chat_id}")
                        return True
                    else:
                        self.logger.error(f"Failed to download audio from URL: {response.status}")
                        return False
                        
        except (ConnectionError, TimeoutError, ValueError) as e:
            self.logger.error(
                f"Network or value error sending voice message to {chat_id}: {e}",
                exc_info=True
            )
            return False
        # pylint: disable=broad-exception-caught
        except Exception as e:
            # Catch-all для неожиданных системных ошибок
            self.logger.error(
                f"Unexpected error sending voice message to {chat_id}: {e}", exc_info=True
            )
            return False

    async def send_voice_from_url(self, chat_id: int, audio_url: str) -> bool:
        """Send voice message from URL."""
        if not self.bot:
            return False

        try:
            # pylint: disable=import-outside-toplevel
            import aiohttp
            from aiogram.types import BufferedInputFile

            async with aiohttp.ClientSession() as session:
                async with session.get(audio_url) as resp:
                    if resp.status == 200:
                        audio_data = await resp.read()
                        voice_file = BufferedInputFile(audio_data, filename="voice.ogg")
                        return await self.send_voice_message(chat_id, voice_file)

                    self.logger.error(
                        f"Failed to download audio from {audio_url}: {resp.status}"
                    )
                    return False
        except (ConnectionError, TimeoutError, ValueError) as e:
            self.logger.error(
                f"Network or value error sending voice from URL to {chat_id}: {e}",
                exc_info=True
            )
            return False
        # pylint: disable=broad-exception-caught
        except Exception as e:
            # Catch-all для неожиданных системных ошибок
            self.logger.error(
                f"Unexpected error sending voice from URL to {chat_id}: {e}", exc_info=True
            )
            return False

    async def send_photo(
        self, chat_id: int, photo: InputFile, caption: str = None
    ) -> bool:
        """Send photo to Telegram chat."""
        if not self.bot:
            return False

        try:
            await self.bot.send_photo(chat_id=chat_id, photo=photo, caption=caption)
            self.logger.debug(f"Photo sent to chat {chat_id}")
            return True
        except (ConnectionError, TimeoutError, ValueError) as e:
            self.logger.error(
                f"Network or value error sending photo to {chat_id}: {e}", exc_info=True
            )
            return False
        # pylint: disable=broad-exception-caught
        except Exception as e:
            # Catch-all для неожиданных системных ошибок
            self.logger.error(
                f"Unexpected error sending photo to {chat_id}: {e}", exc_info=True
            )
            return False

    async def download_file(self, file_path: str) -> Optional[bytes]:
        """Download file from Telegram servers."""
        if not self.bot:
            return None

        try:
            file_data = await self.bot.download_file(file_path)
            return file_data.read() if hasattr(file_data, "read") else file_data
        except (ConnectionError, TimeoutError, ValueError) as e:
            self.logger.error(
                f"Network or value error downloading file {file_path}: {e}", exc_info=True
            )
            return None
        # pylint: disable=broad-exception-caught
        except Exception as e:
            # Catch-all для неожиданных системных ошибок
            self.logger.error(
                f"Unexpected error downloading file {file_path}: {e}", exc_info=True
            )
            return None

    def create_contact_request_keyboard(self) -> ReplyKeyboardMarkup:
        """Create keyboard for contact sharing request."""
        button = KeyboardButton(text="Поделиться контактом", request_contact=True)
        return ReplyKeyboardMarkup(
            keyboard=[[button]], resize_keyboard=True, one_time_keyboard=True
        )

    def remove_keyboard(self) -> ReplyKeyboardRemove:
        """Create reply markup to remove keyboard."""
        return ReplyKeyboardRemove()

    async def get_file_info(self, file_id: str) -> Optional[dict]:
        """Get file information from Telegram."""
        if not self.bot:
            return None

        try:
            file_info = await self.bot.get_file(file_id)
            return {
                "file_id": file_info.file_id,
                "file_unique_id": file_info.file_unique_id,
                "file_size": file_info.file_size,
                "file_path": file_info.file_path,
            }
        except (ConnectionError, TimeoutError, ValueError) as e:
            self.logger.error(
                f"Network or value error getting file info for {file_id}: {e}",
                exc_info=True
            )
            return None
        # pylint: disable=broad-exception-caught
        except Exception as e:
            # Catch-all для неожиданных системных ошибок
            self.logger.error(
                f"Unexpected error getting file info for {file_id}: {e}", exc_info=True
            )
            return None

    async def get_me(self) -> Optional[dict]:
        """Get bot information."""
        if not self.bot:
            return None

        try:
            bot_info = await self.bot.get_me()
            return {
                "id": bot_info.id,
                "is_bot": bot_info.is_bot,
                "first_name": bot_info.first_name,
                "username": bot_info.username,
            }
        except (ConnectionError, TimeoutError, ValueError) as e:
            self.logger.error(f"Network or value error getting bot info: {e}", exc_info=True)
            return None
        # pylint: disable=broad-exception-caught
        except Exception as e:
            # Catch-all для неожиданных системных ошибок
            self.logger.error(f"Unexpected error getting bot info: {e}", exc_info=True)
            return None
