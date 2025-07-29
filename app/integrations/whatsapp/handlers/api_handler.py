"""
WhatsApp API Handler

Модуль для работы с API wppconnect-server (HTTP и Socket.IO операции)
"""
import asyncio
from typing import Optional
import httpx


class WhatsAppAPIHandler:
    """Handles WhatsApp API operations for wppconnect-server"""

    def __init__(self, bot_instance):
        """
        Initialize API handler

        Args:
            bot_instance: Reference to main WhatsAppIntegrationBot instance
        """
        self.bot = bot_instance
        self.logger = bot_instance.logger

    async def send_message(self, chat_id: str, message: str) -> bool:
        """
        Отправка сообщения в WhatsApp через wppconnect HTTP API

        Args:
            chat_id: ID чата WhatsApp
            message: Текст сообщения для отправки

        Returns:
            True если сообщение отправлено успешно
        """
        try:
            if not self.bot.http_client:
                self.logger.error("HTTP client not initialized")
                return False

            url = "/api/%s/send-message" % self.bot.session_name
            payload = {
                "phone": chat_id,
                "message": message,
                "isGroup": False
            }

            response = await self.bot.http_client.post(url, json=payload)

            # API возвращает как 200, так и 201 при успешной отправке
            if response.status_code in [200, 201]:
                self.logger.debug("Message sent successfully to %s", chat_id)
                await self.bot.update_last_active_time()
                return True
            else:
                # Упрощаем вывод ошибки - показываем только статус и первые 
                # 200 символов ответа
                response_preview = (
                    response.text[:200] + "..." 
                    if len(response.text) > 200 
                    else response.text
                )
                self.logger.error(
                    "Failed to send message. Status: %s, Response preview: %s",
                    response.status_code, response_preview
                )
                return False

        except Exception as e:
            self.logger.error("Error sending message to %s: %s", chat_id, e, exc_info=True)
            return False

    async def send_typing_action(self, chat_id: str, is_typing: bool) -> bool:
        """
        Отправка действия печати в WhatsApp через wppconnect HTTP API

        Args:
            chat_id: ID чата WhatsApp
            is_typing: True для включения печати, False для отключения

        Returns:
            True если действие отправлено успешно
        """
        try:
            if not self.bot.http_client:
                self.logger.error("HTTP client not initialized")
                return False

            url = "/api/%s/typing" % self.bot.session_name
            payload = {
                "phone": chat_id,
                "isGroup": False,
                "value": is_typing
            }

            response = await self.bot.http_client.post(url, json=payload)

            if response.status_code in [200, 201]:
                self.logger.debug(
                    "Typing action %s for %s", 
                    'started' if is_typing else 'stopped', chat_id
                )
                return True
            else:
                self.logger.warning(
                    "Failed to set typing action. Status: %s", response.status_code
                )
                return False

        except Exception as e:
            self.logger.error(
                "Error sending typing action to %s: %s", chat_id, e, exc_info=True
            )
            return False

    async def send_voice_message(self, chat_id: str, audio_url: str) -> bool:
        """
        Отправка голосового сообщения в WhatsApp через wppconnect API

        Args:
            chat_id: ID чата WhatsApp
            audio_url: URL аудиофайла

        Returns:
            True если сообщение отправлено успешно
        """
        try:
            import aiohttp
            import base64

            self.logger.debug("Downloading audio from URL: %s", audio_url)

            # Скачиваем аудиофайл по URL
            async with aiohttp.ClientSession() as session:
                async with session.get(audio_url) as resp:
                    if resp.status == 200:
                        audio_data = await resp.read()
                        self.logger.debug(
                            "Downloaded audio data: %s bytes", len(audio_data)
                        )
                    else:
                        self.logger.error(
                            "Failed to download audio from %s: HTTP %s",
                            audio_url, resp.status
                        )
                        return False

            # Кодируем в base64 для wppconnect API
            audio_base64 = base64.b64encode(audio_data).decode('utf-8')
            self.logger.debug(
                "Encoded audio to base64: %s characters", len(audio_base64)
            )

            # Отправляем через wppconnect API используя send-voice-base64 endpoint
            url = "/api/%s/send-voice-base64" % self.bot.session_name
            payload = {
                "phone": chat_id,
                "isGroup": False,
                "base64Ptt": audio_base64
            }

            self.logger.debug(f"Sending voice message to {chat_id} via {url}")
            response = await self.bot.http_client.post(url, json=payload)

            if response.status_code in [200, 201]:
                self.logger.info(f"Voice message sent successfully to {chat_id}")
                await self.bot.update_last_active_time()
                return True
            else:
                self.logger.error(f"Failed to send voice message. Status: {response.status_code}, Response: {response.text}")
                return False

        except Exception as e:
            self.logger.error(f"Error sending voice message to {chat_id}: {e}", exc_info=True)
            return False

    async def download_whatsapp_media(self, media_key: str, mimetype: str, message_id: str) -> Optional[bytes]:
        """
        Скачивание медиа файла из WhatsApp

        Args:
            media_key: Ключ медиа файла
            mimetype: MIME тип файла
            message_id: ID сообщения для скачивания медиа

        Returns:
            Данные файла или None при ошибке
        """
        try:
            url = f"{self.bot.wppconnect_base_url}/api/{self.bot.session_name}/download-media"
            payload = {
                "messageId": message_id
            }

            # Добавляем дополнительные параметры если они есть
            if media_key:
                payload["mediakey"] = media_key
            if mimetype:
                payload["mimetype"] = mimetype

            self.logger.debug(f"Downloading media with payload: {payload}")
            response = await self.bot.http_client.post(url, json=payload)

            self.logger.debug(f"Download response status: {response.status_code}")
            if response.status_code == 200:
                return await self._process_media_response(response)
            else:
                self.logger.error(f"Failed to download WhatsApp media. Status: {response.status_code}, Response: {response.text}")
                return None

        except Exception as e:
            self.logger.error(f"Error downloading WhatsApp media: {e}", exc_info=True)
            return None

    async def _process_media_response(self, response: httpx.Response) -> Optional[bytes]:
        """Process media download response"""
        try:
            import base64

            # Log raw response for debugging
            raw_response = response.text
            self.logger.debug(f"Raw response (first 200 chars): {raw_response[:200]}")

            # Response should contain base64 encoded data
            response_data = response.json()

            # Проверяем различные форматы ответа
            base64_data = None
            if "base64" in response_data:
                # Формат: {"base64": "..."}
                base64_data = response_data["base64"]
                self.logger.debug("Found base64 data in 'base64' field")
            elif "data" in response_data:
                # Формат: {"data": "..."}
                base64_data = response_data["data"]
                self.logger.debug("Found base64 data in 'data' field")
            else:
                # Попробуем интерпретировать весь ответ как base64
                if isinstance(response_data, str):
                    base64_data = response_data
                    self.logger.debug("Using entire response as base64")

            if base64_data:
                try:
                    audio_bytes = base64.b64decode(base64_data)
                    self.logger.debug(f"Successfully decoded base64 media data, size: {len(audio_bytes)} bytes")
                    return audio_bytes
                except Exception as e:
                    self.logger.error(f"Failed to decode base64 data: {e}")
                    return None
            else:
                # Some implementations return raw bytes
                self.logger.debug(f"No base64 field found, using raw response data, size: {len(response.content)} bytes")
                return response.content

        except Exception as e:
            self.logger.error(f"Error processing media response: {e}", exc_info=True)
            return None

    async def send_typing_periodically(self, chat_id: str):
        """
        Периодически отправляет индикатор печати в WhatsApp пока агент обрабатывает запрос

        Args:
            chat_id: ID чата WhatsApp
        """
        try:
            # Start typing
            await self.send_typing_action(chat_id, True)

            while True:
                await asyncio.sleep(3)  # Refresh typing indicator every 3 seconds
                await self.send_typing_action(chat_id, True)

        except asyncio.CancelledError:
            self.logger.debug(f"Typing task cancelled for chat {chat_id}")
            # Stop typing when cancelled
            await self.send_typing_action(chat_id, False)
        except Exception as e:
            self.logger.error(f"Error in typing task for chat {chat_id}: {e}", exc_info=True)
        finally:
            # Clean up typing task
            if chat_id in self.bot.typing_tasks:
                del self.bot.typing_tasks[chat_id]
            # Ensure typing is stopped
            await self.send_typing_action(chat_id, False)
