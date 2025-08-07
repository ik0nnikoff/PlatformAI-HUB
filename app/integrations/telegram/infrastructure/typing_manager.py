"""
TypingManager для управления индикаторами печати в Telegram интеграции.

Отвечает за запуск, остановку и управление typing indicator'ов
с автоматическим timeout и error handling.
"""

import asyncio
import logging
from typing import TYPE_CHECKING, Dict

if TYPE_CHECKING:
    from .api_client import TelegramAPIClient


class TypingManager:
    """
    Управляет typing indicator'ами для Telegram чатов.

    Предоставляет centralized управление typing tasks с:
    - Автоматическим timeout (60 секунд)
    - Graceful cancellation
    - Error handling для network issues
    - Cleanup при shutdown
    """

    def __init__(self, api_client: "TelegramAPIClient", logger: logging.LoggerAdapter):
        self.api_client = api_client
        self.logger = logger
        self.typing_tasks: Dict[int, asyncio.Task] = {}
        self.typing_timeout = 60.0  # seconds

    async def start_typing(self, chat_id: int) -> None:
        """Start typing indicator for chat."""
        # Cancel existing typing task if any
        if chat_id in self.typing_tasks:
            await self.stop_typing(chat_id)

        # Start new typing task
        self.typing_tasks[chat_id] = asyncio.create_task(
            self._send_typing_periodically(chat_id)
        )
        self.logger.debug(f"Started typing indicator for chat {chat_id}")

    async def stop_typing(self, chat_id: int) -> None:
        """Stop typing indicator for a specific chat."""
        if chat_id in self.typing_tasks:
            task = self.typing_tasks.pop(chat_id)
            if not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass  # Expected when cancelling
                except Exception as e:
                    self.logger.debug(f"Error stopping typing for chat {chat_id}: {e}")

        self.logger.debug(f"Stopped typing indicator for chat {chat_id}")

    async def start_typing_for_chat(self, chat_id: int) -> None:
        """Alias for start_typing for backward compatibility."""
        await self.start_typing(chat_id)

    async def stop_typing_for_chat(self, chat_id: int) -> None:
        """Alias for stop_typing for backward compatibility."""
        await self.stop_typing(chat_id)

    async def _send_typing_periodically(self, chat_id: int) -> None:
        """Send typing indicator periodically with timeout."""
        try:
            # Send initial typing indicator
            await self.api_client.send_typing_action(chat_id, True)

            # Keep sending typing indicator every 4 seconds
            timeout_start = asyncio.get_event_loop().time()
            while True:
                await asyncio.sleep(4.0)

                # Check timeout
                if (
                    asyncio.get_event_loop().time() - timeout_start
                    > self.typing_timeout
                ):
                    self.logger.debug(f"Typing timeout reached for chat {chat_id}")
                    break

                # Send typing indicator
                success = await self.api_client.send_typing_action(chat_id, True)
                if not success:
                    self.logger.debug(
                        f"Failed to send typing indicator to chat {chat_id}"
                    )
                    break

        except asyncio.CancelledError:
            self.logger.debug(f"Typing task cancelled for chat {chat_id}")
            raise

        except Exception as e:
            self.logger.debug(f"Error in typing task for chat {chat_id}: {e}")

        finally:
            # Clean up task reference
            if chat_id in self.typing_tasks:
                self.typing_tasks.pop(chat_id, None)

    async def cleanup_all_typing_tasks(self) -> None:
        """Stop all typing indicators and cleanup tasks."""
        if not self.typing_tasks:
            return

        # Cancel all tasks
        for task in list(self.typing_tasks.values()):
            if not task.done():
                task.cancel()

        # Wait for all tasks to complete
        if self.typing_tasks:
            await asyncio.gather(*self.typing_tasks.values(), return_exceptions=True)

        # Clear the dictionary
        self.typing_tasks.clear()
        self.logger.debug("All typing tasks cleaned up")

    def is_typing_active(self, chat_id: int) -> bool:
        """Check if typing indicator is active for chat."""
        task = self.typing_tasks.get(chat_id)
        return task is not None and not task.done()

    def get_active_typing_chats(self) -> list[int]:
        """Get list of chat IDs with active typing indicators."""
        return [
            chat_id for chat_id, task in self.typing_tasks.items() if not task.done()
        ]
