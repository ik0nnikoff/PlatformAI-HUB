"""
WhatsApp Integration Bot для PlatformAI-HUB

Интеграция с wppconnect-server через Socket.IO WebSocket соединение
для получения сообщений в реальном времени и HTTP API для отправки.
"""

import asyncio
import json
import logging
import os
from typing import Optional, Dict, Any
import httpx
from socketio.async_client import AsyncClient

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from redis import exceptions as redis_exceptions

from app.core.config import settings
from app.core.base.service_component import ServiceComponentBase
from app.db.crud import user_crud
from app.api.schemas.common_schemas import IntegrationType


# Constants
REDIS_USER_CACHE_TTL = getattr(settings, "REDIS_USER_CACHE_TTL", 3600)
USER_CACHE_PREFIX = "user_cache:"


class WhatsAppIntegrationBot(ServiceComponentBase):
    """
    Manages the lifecycle and execution of a WhatsApp Bot integration for a specific agent.
    Inherits from ServiceComponentBase for unified state and lifecycle management.
    
    Использует Socket.IO для получения сообщений от wppconnect-server в реальном времени
    и HTTP API для отправки сообщений обратно.
    """

    def __init__(self,
                 agent_id: str,
                 session_name: str,
                 token: str,
                 db_session_factory: Optional[async_sessionmaker[AsyncSession]],
                 logger_adapter: logging.LoggerAdapter,
                 ):

        # Initialize ServiceComponentBase
        super().__init__(component_id=f"{agent_id}:{IntegrationType.WHATSAPP.value}",
                         status_key_prefix="integration_status:",
                         logger_adapter=logger_adapter)

        self.agent_id = agent_id
        self.session_name = session_name
        self.token = token
        self.db_session_factory = db_session_factory
        self._pubsub_channel = f"agent:{self.agent_id}:output"

        # --- Socket.IO and HTTP specific attributes ---
        self.sio: Optional[AsyncClient] = None
        self.http_client: Optional[httpx.AsyncClient] = None
        self.wppconnect_base_url = settings.WPPCONNECT_URL
        
        # Typing indicator tracking
        self.typing_tasks: Dict[str, asyncio.Task] = {}
        
        # Reconnection tracking
        self.reconnect_attempts = 0
        self.max_reconnect_attempts = settings.WPPCONNECT_RECONNECT_ATTEMPTS
        self.reconnect_delay = settings.WPPCONNECT_RECONNECT_DELAY
        
        self.logger.info(f"WhatsAppIntegrationBot initialized for session {session_name}. PID: {os.getpid()}")
        self.logger.info(f"WPPConnect URL: {self.wppconnect_base_url}")
        self.logger.info(f"Socket.IO Path: {settings.WPPCONNECT_SOCKETIO_PATH}")

    async def setup(self) -> None:
        """
        Инициализация WhatsApp интеграции.
        
        Настраивает Socket.IO клиент, HTTP клиент и Redis соединение.
        """
        try:
            await super().setup()
            
            # Setup HTTP client
            headers = {"Content-Type": "application/json"}
            if self.token:
                headers["Authorization"] = f"Bearer {self.token}"
            
            self.http_client = httpx.AsyncClient(
                base_url=self.wppconnect_base_url,
                timeout=httpx.Timeout(30.0),
                headers=headers
            )
            
            # Setup Socket.IO client
            self.sio = AsyncClient(
                reconnection=True,
                reconnection_attempts=self.max_reconnect_attempts,
                reconnection_delay=self.reconnect_delay,
                logger=False,  # Disable socketio internal logging
                engineio_logger=False
            )
            
            # Register Socket.IO event handlers
            self._setup_socketio_handlers()
            
            self.logger.info(f"WhatsApp integration setup completed for session {self.session_name}")
            
        except Exception as e:
            self.logger.error(f"Error setting up WhatsApp integration: {e}", exc_info=True)
            await self.mark_as_error(f"Setup failed: {str(e)}")
            raise

    def _setup_socketio_handlers(self):
        """Настройка обработчиков событий Socket.IO"""
        
        if not self.sio:
            self.logger.error("Socket.IO client not initialized")
            return
        
        @self.sio.event
        async def connect():
            self.logger.info(f"Connected to wppconnect-server Socket.IO")
            self.reconnect_attempts = 0
            
        @self.sio.event
        async def disconnect():
            self.logger.warning(f"Disconnected from wppconnect-server Socket.IO")
            
        @self.sio.event
        async def connect_error(data):
            self.logger.error(f"Socket.IO connection error: {data}")
            
        # Обработчик для события received-message (основное событие wppconnect-server)
        async def received_message_hyphen(data):
            """Обработка полученных сообщений от WhatsApp"""
            # Упрощенное логирование - только ключевые поля
            response = data.get("response", {})
            body = response.get("content") or response.get("body", "")
            from_me = response.get("fromMe", False)
            chat_id = response.get("chatId") or response.get("from", "")
            self.logger.info(f"Received message: chat={chat_id}, fromMe={from_me}, body='{body[:50]}{'...' if len(body) > 50 else ''}'")
            await self._handle_received_message(data)
        
        # Регистрируем обработчик для события с дефисом
        self.sio.on('received-message', received_message_hyphen)
            
        @self.sio.event
        async def whatsapp_status(data):
            """Обработка изменения статуса WhatsApp"""
            self.logger.info(f"WhatsApp status changed: {data}")
            
        @self.sio.event
        async def session_logged(data):
            """Обработка успешного подключения сессии"""
            self.logger.info(f"Session logged in: {data}")
            await self.mark_as_running()
            
        # Универсальный обработчик для диагностики всех событий
        async def catch_all_events(event, data):
            """Ловит все события для диагностики"""
            # Упрощенное логирование только для важных событий
            if event in ['onack', 'mensagem-enviada']:
                # Минимальная информация для статусных событий
                if isinstance(data, list) and len(data) > 0:
                    msg_data = data[0] if isinstance(data[0], dict) else {}
                else:
                    msg_data = data if isinstance(data, dict) else {}
                    
                ack = msg_data.get('ack', 'unknown')
                body = msg_data.get('body', '')
                self.logger.info(f"[{event}] ack={ack}, body='{body[:30]}{'...' if len(body) > 30 else ''}'")
            elif event not in ['received-message']:  # Не логируем received-message здесь, уже логируется выше
                self.logger.debug(f"[DIAGNOSTIC] Event '{event}' received")
            
            # Если это событие сообщения, обрабатываем его
            if event in ['received-message']:
                if isinstance(data, dict):
                    await self._handle_received_message(data)
                else:
                    self.logger.warning(f"Received message event '{event}' with non-dict data: {type(data)}")
        
        # Регистрируем универсальный обработчик
        self.sio.on('*', catch_all_events)

    async def run_loop(self) -> None:
        """
        Основной цикл выполнения WhatsApp интеграции.
        
        Подключается к wppconnect-server через Socket.IO и слушает сообщения,
        а также подписывается на ответы агента через Redis.
        """
        await self.mark_as_initializing()
        
        try:
            # Connect to wppconnect-server Socket.IO
            socketio_url = f"{self.wppconnect_base_url}"
            self.logger.info(f"Connecting to wppconnect-server at {socketio_url}")
            
            if not self.sio:
                raise RuntimeError("Socket.IO client not initialized")
                
            await self.sio.connect(socketio_url, socketio_path=settings.WPPCONNECT_SOCKETIO_PATH)
            
            # Register main tasks
            redis_task = self._register_main_task(
                self._listen_agent_responses(),
                name=f"whatsapp_redis_listener_{self.agent_id}"
            )
            
            # Start the service component run loop
            await super().run_loop()
                    
        except Exception as e:
            self.logger.error(f"Error in WhatsApp integration main loop: {e}", exc_info=True)
            await self.mark_as_error(f"Main loop failed: {str(e)}")
        finally:
            await self.cleanup()

    async def cleanup(self) -> None:
        """Очистка ресурсов при завершении работы"""
        self.logger.info("Cleaning up WhatsApp integration...")
        
        try:
            # Cancel all typing tasks
            for chat_id, task in self.typing_tasks.items():
                task.cancel()
                # Optionally send stop typing for each chat
                try:
                    await self._send_typing_action(chat_id, False)
                except Exception as e_typing:
                    self.logger.debug(f"Failed to stop typing for {chat_id} during cleanup: {e_typing}")
            self.typing_tasks.clear()
            
            if self.sio and self.sio.connected:
                await self.sio.disconnect()
                
            if self.http_client:
                await self.http_client.aclose()
                
            await super().cleanup()
            
        except Exception as e:
            self.logger.error(f"Error during WhatsApp integration cleanup: {e}", exc_info=True)

    async def _handle_pubsub_message(self, message_data: bytes) -> None:
        """
        Обработка сообщения от агента через Redis Pub/Sub.
        Реализует абстрактный метод ServiceComponentBase.
        """
        await self._handle_agent_response(message_data)

    async def _handle_received_message(self, data: Dict[str, Any]) -> None:
        """
        Обработка полученного сообщения от WhatsApp через Socket.IO
        
        Args:
            data: Данные сообщения от wppconnect-server
        """
        try:
            response = data.get("response", {})
            if not response:
                self.logger.warning(f"Received message without response data: {data}")
                return
            
            # Добавим дополнительное логирование для отладки
            from_me = response.get("fromMe", False)
            self.logger.debug(f"Message fromMe={from_me}")
            
            # Игнорировать исходящие сообщения (отправленные ботом) чтобы избежать зацикливания
            if from_me:
                self.logger.debug(f"Ignoring outgoing message")
                return
            
            # Extract message data
            message_text = response.get("content") or response.get("body", "")
            chat_id = response.get("chatId") or response.get("from", "")
            sender_info = response.get("sender", {})
            session = response.get("session", "")
            message_type = response.get("type", "")
            
            # Validate session matches
            if session != self.session_name:
                self.logger.debug(f"Message from different session {session}, ignoring")
                return
            
            # Handle voice messages
            if message_type in ["ptt", "audio"]:  # ptt = push-to-talk (voice message)
                await self._handle_voice_message(response, chat_id, sender_info)
                return
                
            if not message_text or not chat_id:
                self.logger.warning(f"Message missing required fields: text='{message_text}', chat_id='{chat_id}'")
                return
                
            # Extract user information
            user_name = sender_info.get("pushname", "Unknown")
            platform_user_id = chat_id  # Use chat_id as user identifier
            
            # Extract phone number from sender.id (format: 79222088435@c.us)
            sender_id = response.get("sender", {}).get("id", "")
            phone_number = None
            if sender_id and "@c.us" in sender_id:
                phone_number = sender_id.split("@c.us")[0]
            
            # Parse user name - try to split into first_name and last_name
            name_parts = user_name.strip().split(' ', 1) if user_name and user_name != "Unknown" else ["Unknown"]
            first_name = name_parts[0]
            last_name = name_parts[1] if len(name_parts) > 1 else None
            
            self.logger.info(f"Received WhatsApp message from {user_name} ({platform_user_id}), phone: {phone_number}: {message_text[:100]}")
            
            # Start typing indicator
            if chat_id in self.typing_tasks:  # Cancel previous if any
                self.typing_tasks[chat_id].cancel()
            self.typing_tasks[chat_id] = asyncio.create_task(self._send_typing_periodically(chat_id))
            
            # Check user authorization and create/update user
            user_data = await self._get_or_create_user(platform_user_id, first_name, last_name, phone_number)
            if not user_data:
                self.logger.warning(f"Failed to get/create user for {platform_user_id}")
                # Stop typing if user creation failed
                if chat_id in self.typing_tasks:
                    self.typing_tasks[chat_id].cancel()
                return
                
            # Publish message to agent input
            await self._publish_to_agent(chat_id, platform_user_id, message_text, user_data)
            
        except Exception as e:
            self.logger.error(f"Error handling received message: {e}", exc_info=True)
            # Stop typing indicator if error occurred
            if 'chat_id' in locals() and chat_id in self.typing_tasks:
                self.typing_tasks[chat_id].cancel()

    async def _get_or_create_user(self, platform_user_id: str, first_name: str, last_name: Optional[str] = None, phone_number: Optional[str] = None) -> Optional[Dict[str, Any]]:
        """
        Получение или создание пользователя в системе с авторизацией
        
        Args:
            platform_user_id: ID пользователя в WhatsApp
            first_name: Имя пользователя
            last_name: Фамилия пользователя (опционально)
            phone_number: Номер телефона пользователя (извлекается из sender.id)
            
        Returns:
            Данные пользователя с флагом авторизации
        """
        try:
            if not self.db_session_factory:
                self.logger.warning("Database session factory not available")
                fallback_data = {
                    "first_name": first_name,
                    "platform_user_id": platform_user_id,
                    "is_authenticated": False
                }
                # Add last_name only if it exists
                if last_name:
                    fallback_data["last_name"] = last_name
                return fallback_data
                
            async with self.db_session_factory() as session:
                # Try to get existing user
                user_db = await user_crud.get_user_by_platform_id(
                    session, "whatsapp", platform_user_id
                )
                
                is_new_user = False
                if not user_db:
                    # Create new user
                    user_details = {
                        "first_name": first_name,
                        "username": first_name  # Use first_name as username fallback
                    }
                    # Add last_name if available  
                    if last_name:
                        user_details["last_name"] = last_name
                    # Add phone number if available
                    if phone_number:
                        user_details["phone_number"] = phone_number
                        
                    user_db = await user_crud.create_or_update_user(
                        session, "whatsapp", platform_user_id, user_details
                    )
                    is_new_user = True
                    self.logger.info(f"Created new WhatsApp user: {platform_user_id}, first_name: {first_name}, last_name: {last_name}, phone: {phone_number}")
                
                if not user_db:
                    fallback_data = {
                        "first_name": first_name,
                        "username": first_name,
                        "platform_user_id": platform_user_id,
                        "is_authenticated": False
                    }
                    # Add last_name only if it exists
                    if last_name:
                        fallback_data["last_name"] = last_name
                    return fallback_data
                
                # Check if user is authorized for this agent
                auth_record = await user_crud.get_agent_user_authorization(
                    session, self.agent_id, user_db.id  # type: ignore
                )
                
                is_authorized = bool(auth_record and auth_record.is_authorized) if auth_record else False
                
                # If new user and has phone number, automatically authorize
                if is_new_user and phone_number and not is_authorized:
                    auth_record = await user_crud.update_agent_user_authorization(
                        session, agent_id=self.agent_id, user_id=user_db.id, is_authorized=True  # type: ignore
                    )
                    is_authorized = bool(auth_record and auth_record.is_authorized) if auth_record else False
                    if is_authorized:
                        self.logger.info(f"Auto-authorized WhatsApp user {platform_user_id} with phone {phone_number}")
                
                user_data_result = {
                    "user_id": user_db.id,  # type: ignore
                    "first_name": user_db.first_name,
                    "username": user_db.username,
                    "platform_user_id": platform_user_id,
                    "platform": "whatsapp",
                    "is_authenticated": is_authorized
                }
                
                # Add last_name only if it's not None or empty
                if user_db.last_name is not None and user_db.last_name.strip():
                    user_data_result["last_name"] = user_db.last_name
                    
                # Add phone_number only if it's not None or empty  
                if user_db.phone_number is not None and user_db.phone_number.strip():
                    user_data_result["phone_number"] = user_db.phone_number
                
                return user_data_result
                
        except Exception as e:
            self.logger.error(f"Error getting/creating user {platform_user_id}: {e}", exc_info=True)
            error_fallback_data = {
                "first_name": first_name,
                "platform_user_id": platform_user_id,
                "is_authenticated": False
            }
            # Add last_name only if it exists
            if last_name:
                error_fallback_data["last_name"] = last_name
            return error_fallback_data

    async def _publish_to_agent(self, chat_id: str, platform_user_id: str, message_text: str, user_data: Dict[str, Any]) -> None:
        """
        Публикация сообщения в Redis канал агента
        
        Args:
            chat_id: ID чата в WhatsApp
            platform_user_id: ID пользователя
            message_text: Текст сообщения
            user_data: Данные пользователя
        """
        try:
            redis_cli = await self.redis_client
        except RuntimeError as e:
            self.logger.error(f"Redis client not available for publishing to agent: {e}")
            await self._send_error_message(chat_id, "Ошибка: Не удалось связаться с агентом (сервис недоступен).")
            return

        input_channel = f"agent:{self.agent_id}:input"

        payload = {
            "text": message_text,
            "chat_id": chat_id,
            "platform_user_id": platform_user_id,
            "user_data": user_data,
            "channel": "whatsapp"
        }
        
        try:
            await redis_cli.publish(input_channel, json.dumps(payload).encode('utf-8'))
            self.logger.debug(f"Published message to {input_channel}: {payload}")
            await self.update_last_active_time()
            
        except redis_exceptions.RedisError as e:
            self.logger.error(f"Redis error publishing message: {e}")
            await self._send_error_message(chat_id, "Ошибка отправки сообщения. Попробуйте позже.")
        except Exception as e:
            self.logger.error(f"Unexpected error publishing message: {e}", exc_info=True)

    async def _listen_agent_responses(self) -> None:
        """Слушатель ответов агента через Redis Pub/Sub"""
        try:
            redis_cli = await self.redis_client
            pubsub = redis_cli.pubsub()
            await pubsub.subscribe(self._pubsub_channel)
            
            self.logger.info(f"Subscribed to agent responses on {self._pubsub_channel}")
            
            while self._running and not self.needs_restart:
                try:
                    message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
                    if message and message["type"] == "message":
                        await self._handle_agent_response(message["data"])
                except Exception as e:
                    self.logger.error(f"Error processing agent response: {e}", exc_info=True)
                    await asyncio.sleep(1)
                    
        except Exception as e:
            self.logger.error(f"Error listening to agent responses: {e}", exc_info=True)

    async def _handle_agent_response(self, message_data: bytes) -> None:
        """
        Обработка ответа агента и отправка в WhatsApp
        
        Args:
            message_data: Данные сообщения от агента
        """
        try:
            data = json.loads(message_data.decode('utf-8'))
            
            chat_id = data.get("chat_id")
            response_text = data.get("response")
            channel = data.get("channel")
            
            if channel != "whatsapp":
                return
                
            if not chat_id or not response_text:
                self.logger.warning(f"Invalid agent response data: {data}")
                return
                
            # Stop typing indicator before sending response
            if chat_id in self.typing_tasks:
                self.typing_tasks[chat_id].cancel()
                # Small delay to make the typing simulation look more natural
                await asyncio.sleep(0.5)
                
            await self._send_message(chat_id, response_text)
            
        except json.JSONDecodeError as e:
            self.logger.error(f"Failed to decode agent response: {e}")
        except Exception as e:
            self.logger.error(f"Error handling agent response: {e}", exc_info=True)

    async def _send_message(self, chat_id: str, message: str) -> bool:
        """
        Отправка сообщения в WhatsApp через wppconnect HTTP API
        
        Args:
            chat_id: ID чата WhatsApp
            message: Текст сообщения для отправки
            
        Returns:
            True если сообщение отправлено успешно
        """
        try:
            if not self.http_client:
                self.logger.error("HTTP client not initialized")
                return False
                
            url = f"/api/{self.session_name}/send-message"
            payload = {
                "phone": chat_id,
                "message": message,
                "isGroup": False
            }
            
            response = await self.http_client.post(url, json=payload)
            
            # API возвращает как 200, так и 201 при успешной отправке
            if response.status_code in [200, 201]:
                self.logger.debug(f"Message sent successfully to {chat_id}")
                await self.update_last_active_time()
                return True
            else:
                # Упрощаем вывод ошибки - показываем только статус и первые 200 символов ответа
                response_preview = response.text[:200] + "..." if len(response.text) > 200 else response.text
                self.logger.error(f"Failed to send message. Status: {response.status_code}, Response preview: {response_preview}")
                return False
                
        except Exception as e:
            self.logger.error(f"Error sending message to {chat_id}: {e}", exc_info=True)
            return False

    async def _send_error_message(self, chat_id: str, error_text: str) -> None:
        """Отправка сообщения об ошибке пользователю"""
        await self._send_message(chat_id, error_text)

    async def _send_typing_action(self, chat_id: str, is_typing: bool) -> bool:
        """
        Отправка действия печати в WhatsApp через wppconnect HTTP API
        
        Args:
            chat_id: ID чата WhatsApp
            is_typing: True для включения печати, False для отключения
            
        Returns:
            True если действие отправлено успешно
        """
        try:
            if not self.http_client:
                self.logger.error("HTTP client not initialized")
                return False
                
            url = f"/api/{self.session_name}/typing"
            payload = {
                "phone": chat_id,
                "isGroup": False,
                "value": is_typing
            }
            
            response = await self.http_client.post(url, json=payload)
            
            if response.status_code in [200, 201]:
                self.logger.debug(f"Typing action {'started' if is_typing else 'stopped'} for {chat_id}")
                return True
            else:
                self.logger.warning(f"Failed to set typing action. Status: {response.status_code}")
                return False
                
        except Exception as e:
            self.logger.error(f"Error sending typing action to {chat_id}: {e}", exc_info=True)
            return False

    async def _send_typing_periodically(self, chat_id: str):
        """
        Периодически отправляет индикатор печати в WhatsApp пока агент обрабатывает запрос
        
        Args:
            chat_id: ID чата WhatsApp
        """
        try:
            # Start typing
            await self._send_typing_action(chat_id, True)
            
            while True:
                await asyncio.sleep(3)  # Refresh typing indicator every 3 seconds
                await self._send_typing_action(chat_id, True)
                
        except asyncio.CancelledError:
            self.logger.debug(f"Typing task cancelled for chat {chat_id}")
            # Stop typing when cancelled
            await self._send_typing_action(chat_id, False)
        except Exception as e:
            self.logger.error(f"Error in typing task for chat {chat_id}: {e}", exc_info=True)
        finally:
            # Clean up typing task
            if chat_id in self.typing_tasks:
                del self.typing_tasks[chat_id]
            # Ensure typing is stopped
            await self._send_typing_action(chat_id, False)

    async def _handle_reconnection(self) -> None:
        """Обработка переподключения к wppconnect-server"""
        if self.reconnect_attempts >= self.max_reconnect_attempts:
            self.logger.error(f"Max reconnection attempts ({self.max_reconnect_attempts}) reached")
            await self.mark_as_error("Max reconnection attempts reached")
            return
            
        self.reconnect_attempts += 1
        self.logger.info(f"Attempting reconnection {self.reconnect_attempts}/{self.max_reconnect_attempts}")
        
        try:
            await asyncio.sleep(self.reconnect_delay)
            socketio_url = f"{self.wppconnect_base_url}"
            if not self.sio:
                self.logger.error("Socket.IO client not initialized for reconnection")
                return
            await self.sio.connect(socketio_url, socketio_path=settings.WPPCONNECT_SOCKETIO_PATH)
            
        except Exception as e:
            self.logger.error(f"Reconnection attempt {self.reconnect_attempts} failed: {e}")
    
    async def _handle_voice_message(self, response: Dict[str, Any], chat_id: str, sender_info: Dict[str, Any]) -> None:
        """
        Обработка голосового сообщения из WhatsApp
        
        Args:
            response: Данные сообщения от wppconnect-server
            chat_id: ID чата
            sender_info: Информация об отправителе
        """
        try:
            # Extract user information
            user_name = sender_info.get("pushname", "Unknown")
            platform_user_id = chat_id
            
            # Extract phone number from sender.id (format: 79222088435@c.us)
            sender_id = response.get("sender", {}).get("id", "")
            phone_number = None
            if sender_id and "@c.us" in sender_id:
                phone_number = sender_id.split("@c.us")[0]
            
            # Parse user name
            name_parts = user_name.strip().split(' ', 1) if user_name and user_name != "Unknown" else ["Unknown"]
            first_name = name_parts[0]
            last_name = name_parts[1] if len(name_parts) > 1 else None
            
            self.logger.info(f"Received WhatsApp voice message from {user_name} ({platform_user_id})")
            
            # Start typing indicator
            if chat_id in self.typing_tasks:
                self.typing_tasks[chat_id].cancel()
            self.typing_tasks[chat_id] = asyncio.create_task(self._send_typing_periodically(chat_id))
            
            # Check user authorization and create/update user
            user_data = await self._get_or_create_user(platform_user_id, first_name, last_name, phone_number)
            if not user_data:
                self.logger.warning(f"Failed to get/create user for voice message {platform_user_id}")
                if chat_id in self.typing_tasks:
                    self.typing_tasks[chat_id].cancel()
                return
            
            # Get audio file information
            media_key = response.get("mediaKey", "")
            mimetype = response.get("mimetype", "")
            filename = response.get("filename", "voice.ogg")
            
            if not media_key:
                self.logger.warning("Voice message without media key")
                if chat_id in self.typing_tasks:
                    self.typing_tasks[chat_id].cancel()
                return
            
            # Download audio file
            audio_data = await self._download_whatsapp_media(media_key, mimetype)
            if not audio_data:
                self.logger.error("Failed to download voice message audio")
                if chat_id in self.typing_tasks:
                    self.typing_tasks[chat_id].cancel()
                return
            
            # Process voice message with orchestrator
            await self._process_voice_message_with_orchestrator(
                audio_data, filename, chat_id, platform_user_id, user_data
            )
            
        except Exception as e:
            self.logger.error(f"Error handling WhatsApp voice message: {e}", exc_info=True)
            if 'chat_id' in locals() and chat_id in self.typing_tasks:
                self.typing_tasks[chat_id].cancel()

    async def _download_whatsapp_media(self, media_key: str, mimetype: str) -> Optional[bytes]:
        """
        Скачивание медиа файла из WhatsApp
        
        Args:
            media_key: Ключ медиа файла
            mimetype: MIME тип файла
            
        Returns:
            Данные файла или None при ошибке
        """
        try:
            url = f"/api/{self.session_name}/download-media"
            payload = {
                "mediakey": media_key,
                "mimetype": mimetype
            }
            
            response = await self.http_client.post(url, json=payload)
            
            if response.status_code == 200:
                # Response should contain base64 encoded data
                response_data = response.json()
                if "data" in response_data:
                    import base64
                    return base64.b64decode(response_data["data"])
                else:
                    # Some implementations return raw bytes
                    return response.content
            else:
                self.logger.error(f"Failed to download WhatsApp media. Status: {response.status_code}")
                return None
                
        except Exception as e:
            self.logger.error(f"Error downloading WhatsApp media: {e}", exc_info=True)
            return None

    async def _process_voice_message_with_orchestrator(self, 
                                                      audio_data: bytes, 
                                                      filename: str,
                                                      chat_id: str, 
                                                      platform_user_id: str, 
                                                      user_data: Dict[str, Any]) -> None:
        """
        Обработка голосового сообщения через voice orchestrator
        
        Args:
            audio_data: Данные аудиофайла
            filename: Имя файла
            chat_id: ID чата
            platform_user_id: ID пользователя
            user_data: Данные пользователя
        """
        try:
            # Import voice orchestrator here to avoid circular imports
            from app.services.voice.voice_orchestrator import VoiceServiceOrchestrator
            from app.services.voice import VoiceFileInfo
            from app.services.redis_wrapper import RedisService
            
            # Create file info
            file_info = VoiceFileInfo(
                original_filename=filename,
                size_bytes=len(audio_data),
                format=None,  # Will be auto-detected
                duration_seconds=None
            )
            
            # Example agent config - in real implementation, load from database/API
            agent_config = {
                "voice_settings": {
                    "enabled": True,
                    "intent_detection_mode": "keywords",
                    "intent_keywords": ["голос", "скажи", "произнеси", "озвучь", "отвечай голосом"],
                    "auto_stt": True,
                    "auto_tts_on_keywords": False,
                    "max_file_size_mb": 25,
                    "cache_enabled": True,
                    "cache_ttl_hours": 24,
                    "rate_limit_per_minute": 10,
                    "providers": [
                        {
                            "provider": "openai",
                            "priority": 1,
                            "fallback_enabled": True,
                            "stt_config": {
                                "enabled": True,
                                "model": "whisper-1",
                                "language": "ru",
                                "max_duration": 120
                            }
                        }
                    ]
                }
            }
            
            # Initialize orchestrator (you might want to cache this)
            redis_service = RedisService()
            await redis_service.initialize()
            
            orchestrator = VoiceServiceOrchestrator(redis_service, self.logger)
            await orchestrator.initialize()
            await orchestrator.initialize_voice_services_for_agent(self.agent_id, agent_config)
            
            # Process STT
            result = await orchestrator.process_stt(
                agent_id=self.agent_id,
                user_id=platform_user_id,
                audio_data=audio_data,
                file_info=file_info,
                agent_config=agent_config
            )
            
            if result and result.text:
                self.logger.info(f"STT result for WhatsApp voice message: {result.text}")
                
                # Publish transcribed text to agent
                await self._publish_to_agent(chat_id, platform_user_id, result.text, user_data)
            else:
                self.logger.warning("STT processing failed or returned empty text")
                # Send error message to user
                await self._send_whatsapp_message(chat_id, "Извините, не удалось распознать голосовое сообщение.")
            
            # Cleanup
            await orchestrator.cleanup()
            await redis_service.cleanup()
            
        except Exception as e:
            self.logger.error(f"Error processing voice message with orchestrator: {e}", exc_info=True)
            try:
                await self._send_whatsapp_message(chat_id, "Извините, произошла ошибка при обработке голосового сообщения.")
            except:
                pass
