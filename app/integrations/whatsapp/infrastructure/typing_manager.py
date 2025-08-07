"""
TypingManager для управления индикаторами печати в WhatsApp интеграции.

Отвечает за запуск, остановку и управление typing indicator'ов
с автоматическим timeout и error handling.
"""

import asyncio
import logging
from typing import TYPE_CHECKING, Dict

import httpx

if TYPE_CHECKING:
    from ..infrastructure.api_client import WhatsAppAPIClient


class TypingManager:
    """
    Управляет typing indicator'ами для WhatsApp чатов.
    
    Предоставляет centralized управление typing tasks с:
    - Автоматическим timeout (60 секунд)
    - Graceful cancellation
    - Error handling для network issues
    - Cleanup при shutdown
    """

    def __init__(self, api_client: "WhatsAppAPIClient", logger: logging.LoggerAdapter):
        self.api_client = api_client
        self.logger = logger
        self.typing_tasks: Dict[str, asyncio.Task] = {}

    async def start_typing_for_chat(self, chat_id: str) -> None:
        """Start typing indicator for chat"""
        # Cancel existing typing task if any
        if chat_id in self.typing_tasks:
            self.typing_tasks[chat_id].cancel()
        
        # Start new typing task
        self.typing_tasks[chat_id] = asyncio.create_task(
            self._send_typing_periodically(chat_id)
        )

    async def stop_typing_for_chat(self, chat_id: str) -> None:
        """Stop typing indicator for a specific chat"""
        if chat_id in self.typing_tasks:
            self.typing_tasks[chat_id].cancel()
            del self.typing_tasks[chat_id]

        # Send stop typing action to WhatsApp
        try:
            await self.api_client.send_typing_action(chat_id, False)
        except (httpx.RequestError, AttributeError) as e:
            self.logger.error(
                "Failed to stop typing action for chat %s: %s", chat_id, e
            )

    async def _send_typing_periodically(self, chat_id: str) -> None:
        """
        Периодически отправляет индикатор печати в WhatsApp.

        Показывает пользователю, что агент обрабатывает запрос.
        Автоматически останавливается через 60 секунд для предотвращения зависания.
        """
        try:
            # Start typing
            await self.api_client.send_typing_action(chat_id, True)
            
            # Максимальное время typing - 60 секунд (20 итераций по 3 сек)
            max_iterations = 20
            iterations = 0

            while iterations < max_iterations:
                await asyncio.sleep(3)  # Refresh typing indicator every 3 seconds
                await self.api_client.send_typing_action(chat_id, True)
                iterations += 1

            # Typing timeout reached
            self.logger.warning(
                "Typing timeout (60s) reached for chat %s, stopping indicator", 
                chat_id
            )
            await self.api_client.send_typing_action(chat_id, False)

        except asyncio.CancelledError:
            self.logger.debug("Typing task cancelled for chat %s", chat_id)
            # Stop typing when cancelled
            try:
                await self.api_client.send_typing_action(chat_id, False)
            except (httpx.RequestError, AttributeError) as e:
                self.logger.error(
                    "Failed to stop typing action on cancel for chat %s: %s", chat_id, e
                )
        except (httpx.RequestError, asyncio.TimeoutError, ConnectionError) as e:
            self.logger.error(
                "Error in typing task for chat %s: %s", chat_id, e, exc_info=True
            )
        finally:
            # Clean up typing task
            if chat_id in self.typing_tasks:
                del self.typing_tasks[chat_id]

    async def cleanup_all_typing_tasks(self) -> None:
        """Cancel all typing tasks and send stop typing for all chats"""
        for chat_id, task in self.typing_tasks.items():
            task.cancel()
            # Optionally send stop typing for each chat
            try:
                await self.api_client.send_typing_action(chat_id, False)
            except (httpx.RequestError, AttributeError) as e_typing:
                self.logger.debug(
                    "Failed to stop typing for %s during cleanup: %s", chat_id, e_typing
                )
        self.typing_tasks.clear()

    def is_typing_active(self, chat_id: str) -> bool:
        """Check if typing is currently active for a chat"""
        return chat_id in self.typing_tasks and not self.typing_tasks[chat_id].done()

    def get_active_typing_chats(self) -> list[str]:
        """Get list of chat_ids with active typing indicators"""
        return [
            chat_id for chat_id, task in self.typing_tasks.items() 
            if not task.done()
        ]
