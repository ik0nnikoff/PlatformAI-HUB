"""
WhatsApp Integration Bot для PlatformAI-HUB

Интеграция с wppconnect-server через Socket.IO WebSocket соединение
для получения сообщений в реальном времени и HTTP API для отправки.
"""

import asyncio
import json
import logging
import os
from typing import Any, Dict, List, Optional

import httpx
from redis import exceptions as redis_exceptions
from socketio.async_client import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api.schemas.common_schemas import IntegrationType
from app.core.base.service_component import ServiceComponentBase
from app.core.config import settings
from app.db.crud import user_crud

from .handlers.api_handler import WhatsAppAPIHandler
from .handlers.media_handler import MediaHandler
from .handlers.socketio_handler import SocketIOEventHandlers

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

    def __init__(
        self,
        agent_id: str,
        session_name: str,
        token: str,
        db_session_factory: Optional[async_sessionmaker[AsyncSession]],
        logger_adapter: logging.LoggerAdapter,
    ):

        # Initialize ServiceComponentBase
        super().__init__(
            component_id=f"{agent_id}:{IntegrationType.WHATSAPP.value}",
            status_key_prefix="integration_status:",
            logger_adapter=logger_adapter,
        )

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

        # Voice orchestrator (will be initialized in setup())
        self.voice_orchestrator = None

        # Image orchestrator (will be initialized in setup())
        self.image_orchestrator = None

        # � PHASE 4.4.2: DYNAMIC CONFIG - Remove static agent_config caching
        # Agent configuration now fetched dynamically when needed
        # This allows runtime configuration updates without restart

        # Initialize helper components
        self.media_handler = MediaHandler(self)
        self.api_handler = WhatsAppAPIHandler(self)
        self.socketio_handler = SocketIOEventHandlers(self)

        # Reconnection tracking
        self.reconnect_attempts = 0
        self.max_reconnect_attempts = settings.WPPCONNECT_RECONNECT_ATTEMPTS
        self.reconnect_delay = settings.WPPCONNECT_RECONNECT_DELAY

        self.logger.info(
            "WhatsAppIntegrationBot initialized for session %s. PID: %s", session_name, os.getpid()
        )
        self.logger.info("WPPConnect URL: %s", self.wppconnect_base_url)
        self.logger.info("Socket.IO Path: %s", settings.WPPCONNECT_SOCKETIO_PATH)

    async def setup(self) -> None:
        """
        Инициализация WhatsApp интеграции.

        Настраивает Socket.IO клиент, HTTP клиент и Redis соединение.
        """
        try:
            await super().setup()
            await self._setup_http_client()
            await self._setup_socketio_client()
            await self._setup_orchestrators()

            # 🎯 PHASE 4.4.2: DYNAMIC CONFIG - Remove static agent config loading
            # Agent configuration will be fetched dynamically when needed

            self.logger.info(
                "WhatsApp integration setup completed for session %s", self.session_name
            )

        except Exception as e:
            self.logger.error(
                "Setup failed for session %s: %s", self.session_name, e, exc_info=True
            )
            await self.mark_as_error("Setup failed: %s" % str(e))
            raise

    async def _setup_http_client(self) -> None:
        """Setup HTTP client with authentication headers."""
        headers = {"Content-Type": "application/json"}
        if self.token:
            headers["Authorization"] = "Bearer %s" % self.token

        self.http_client = httpx.AsyncClient(
            base_url=self.wppconnect_base_url, timeout=httpx.Timeout(30.0), headers=headers
        )

    async def _setup_socketio_client(self) -> None:
        """Setup Socket.IO client with event handlers."""
        self.sio = AsyncClient(
            reconnection=True,
            reconnection_attempts=self.max_reconnect_attempts,
            reconnection_delay=self.reconnect_delay,
            logger=False,  # Disable socketio internal logging
            engineio_logger=False,
        )
        self._setup_socketio_handlers()

    async def _setup_orchestrators(self) -> None:
        """Initialize voice and image orchestrators."""
        await self._setup_voice_orchestrator()
        await self._setup_image_orchestrator()

    async def _setup_voice_orchestrator(self) -> None:
        """Initialize voice orchestrator with error handling."""
        try:
            # Import voice_v2 dependencies
            from app.services.voice_v2.core.orchestrator import VoiceServiceOrchestrator
            from app.services.voice_v2.infrastructure.cache import VoiceCache
            from app.services.voice_v2.infrastructure.minio_manager import MinioFileManager
            from app.services.voice_v2.providers.unified_factory import VoiceProviderFactory

            # Initialize components with enhanced voice_v2 architecture
            voice_factory = VoiceProviderFactory()
            cache_manager = VoiceCache()
            await cache_manager.initialize()

            file_manager = MinioFileManager(
                endpoint=settings.MINIO_ENDPOINT,
                access_key=settings.MINIO_ACCESS_KEY,
                secret_key=settings.MINIO_SECRET_KEY,
                bucket_name=settings.MINIO_VOICE_BUCKET_NAME,
                secure=settings.MINIO_SECURE
            )
            await file_manager.initialize()

            self.voice_orchestrator = VoiceServiceOrchestrator(
                enhanced_factory=voice_factory,
                cache_manager=cache_manager,
                file_manager=file_manager,
            )
            await self.voice_orchestrator.initialize()
            self.logger.info("Voice orchestrator v2 initialized for WhatsApp bot")
        except Exception as e:
            self.logger.warning("Failed to initialize voice orchestrator v2: %s", e)
            self.voice_orchestrator = None

    async def _setup_image_orchestrator(self) -> None:
        """Initialize image orchestrator with error handling."""
        try:
            from app.services.media.image_orchestrator import ImageOrchestrator

            self.image_orchestrator = ImageOrchestrator()
            await self.image_orchestrator.initialize()
            self.logger.info("Image orchestrator initialized for WhatsApp bot")
        except Exception as e:
            self.logger.warning("Failed to initialize image orchestrator: %s", e)
            self.image_orchestrator = None

    def _setup_socketio_handlers(self):
        """Настройка обработчиков событий Socket.IO"""
        self.socketio_handler.setup_handlers()

    async def run_loop(self) -> None:
        """
        Основной цикл выполнения WhatsApp интеграции.

        Подключается к wppconnect-server через Socket.IO и слушает сообщения,
        а также подписывается на ответы агента через Redis.
        """
        await self.mark_as_initializing()

        try:
            # Connect to wppconnect-server Socket.IO
            socketio_url = "%s" % self.wppconnect_base_url
            self.logger.info("Connecting to wppconnect-server at %s", socketio_url)

            if not self.sio:
                raise RuntimeError("Socket.IO client not initialized")

            await self.sio.connect(socketio_url, socketio_path=settings.WPPCONNECT_SOCKETIO_PATH)

            # Register main tasks
            self._register_main_task(
                self._listen_agent_responses(), name="whatsapp_redis_listener_%s" % self.agent_id
            )

            # Start the service component run loop
            await super().run_loop()

        except Exception as e:
            self.logger.error("Error in WhatsApp integration main loop: %s", e, exc_info=True)
            await self.mark_as_error("Main loop failed: %s" % str(e))
        finally:
            await self.cleanup()

    async def cleanup(self) -> None:
        """Очистка ресурсов при завершении работы"""
        self.logger.info("Cleaning up WhatsApp integration...")

        try:
            await self._cleanup_typing_tasks()
            await self._cleanup_socket_connection()
            await self._cleanup_http_client()
            await self._cleanup_orchestrators()
            await super().cleanup()

        except Exception as e:
            self.logger.error("Error during WhatsApp integration cleanup: %s", e, exc_info=True)

    async def _cleanup_typing_tasks(self) -> None:
        """Cancel all typing tasks"""
        for chat_id, task in self.typing_tasks.items():
            task.cancel()
            # Optionally send stop typing for each chat
            try:
                await self.api_handler.send_typing_action(chat_id, False)
            except Exception as e_typing:
                self.logger.debug(
                    "Failed to stop typing for %s during cleanup: %s", chat_id, e_typing
                )
        self.typing_tasks.clear()

    async def _cleanup_socket_connection(self) -> None:
        """Cleanup Socket.IO connection"""
        if self.sio and self.sio.connected:
            await self.sio.disconnect()

    async def _cleanup_http_client(self) -> None:
        """Cleanup HTTP client"""
        if self.http_client:
            await self.http_client.aclose()

    async def _cleanup_orchestrators(self) -> None:
        """Cleanup voice and image orchestrators"""
        await self._cleanup_voice_orchestrator()
        await self._cleanup_image_orchestrator()

    async def _cleanup_voice_orchestrator(self) -> None:
        """Cleanup voice orchestrator"""
        if hasattr(self, "voice_orchestrator") and self.voice_orchestrator:
            try:
                await self.voice_orchestrator.cleanup()
                self.logger.info("Voice orchestrator cleaned up")
            except Exception as e:
                self.logger.error("Error cleaning up voice orchestrator: %s", e)

    async def _cleanup_image_orchestrator(self) -> None:
        """Cleanup image orchestrator"""
        if hasattr(self, "image_orchestrator") and self.image_orchestrator:
            try:
                await self.image_orchestrator.cleanup()
                self.logger.info("Image orchestrator cleaned up")
            except Exception as e:
                self.logger.error("Error cleaning up image orchestrator: %s", e)
            finally:
                self.image_orchestrator = None

    async def _handle_pubsub_message(self, message_data: bytes) -> None:
        """
        Обработка сообщения от агента через Redis Pub/Sub.
        Реализует абстрактный метод ServiceComponentBase.
        """
        await self._handle_agent_response(message_data)

    async def _extract_user_context(
        self, response: Dict[str, Any], chat_id: str, sender_info: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        """Extract user context from WhatsApp response."""
        try:
            # Extract platform user ID
            platform_user_id = sender_info.get("id", "")
            if not platform_user_id:
                self.logger.warning("No platform_user_id found in sender_info")
                return None

            # Extract contact information
            contact_info = response.get("sender", {})
            if not contact_info:
                # Fallback to sender_info
                contact_info = sender_info

            # Get user names
            first_name = contact_info.get("pushname") or contact_info.get("name") or ""
            profile_name = contact_info.get("shortName", "")

            # Use profile name if first_name is empty
            if not first_name and profile_name:
                first_name = profile_name

            # Default name if still empty
            if not first_name:
                first_name = "WhatsApp User"

            # Get phone number if available
            phone_number = contact_info.get("formattedName") or ""

            # Check if it looks like a phone number
            if phone_number and not phone_number.startswith("+"):
                phone_number = ""  # Reset if invalid format

            # Create user context
            user_context = {
                "platform_user_id": platform_user_id,
                "first_name": first_name,
                "phone_number": phone_number if phone_number else None,
                "platform": "whatsapp",
                "chat_id": chat_id
            }

            # Get or create user in database
            user_data = await self._get_or_create_user(
                platform_user_id=platform_user_id,
                first_name=first_name,
                phone_number=phone_number if phone_number else None,
            )

            if user_data:
                user_context.update(user_data)

            return user_context

        except Exception as e:
            self.logger.error("Error in _extract_user_context: %s", e, exc_info=True)
            return None

    async def _handle_received_message(self, data: Dict[str, Any]) -> None:
        """
        Обработка полученного сообщения от WhatsApp через Socket.IO

        Args:
            data: Данные сообщения от wppconnect-server
        """
        try:
            response = data.get("response", {})
            if not response:
                self.logger.warning("Received message without response data: %s", data)
                return

            # Validate and extract basic info
            validation_result = self._validate_message(response)
            if not validation_result["valid"]:
                return

            # Route message by type
            await self._route_message_by_type(response, validation_result)

        except Exception as e:
            self.logger.error("Error handling received message: %s", e, exc_info=True)
            # Stop typing indicator if error occurred
            chat_id = data.get("response", {}).get("chatId") or data.get("response", {}).get(
                "from", ""
            )
            if chat_id and chat_id in self.typing_tasks:
                self.typing_tasks[chat_id].cancel()

    def _validate_message(self, response: Dict[str, Any]) -> Dict[str, Any]:
        """Validate incoming message and extract basic info"""
        from_me = response.get("fromMe", False)
        message_type = response.get("type", "")
        message_id = response.get("id", response.get("messageId", ""))
        session = response.get("session", "")

        self.logger.debug(
            "Message details: fromMe=%s, type=%s, id=%s", from_me, message_type, message_id
        )

        # Validation checks
        if from_me:
            self.logger.debug("Ignoring outgoing message")
            return {"valid": False}

        if session != self.session_name:
            self.logger.debug("Message from different session %s, ignoring", session)
            return {"valid": False}

        return {
            "valid": True,
            "type": message_type,
            "chat_id": response.get("chatId") or response.get("from", ""),
            "sender_info": response.get("sender", {}),
            "message_text": response.get("content") or response.get("body", ""),
        }

    async def _route_message_by_type(
        self, response: Dict[str, Any], validation_result: Dict[str, Any]
    ) -> None:
        """Route message processing based on message type"""
        message_type = validation_result["type"]
        chat_id = validation_result["chat_id"]
        sender_info = validation_result["sender_info"]

        # Handle different message types
        if message_type in ["ptt", "audio"]:
            self.logger.debug(f"Voice message detected. Full response structure: {response}")
            await self._handle_voice_message(response, chat_id, sender_info)
        elif message_type == "image":
            await self._handle_image_message(response, chat_id, sender_info)
        else:
            await self._handle_text_message(response, validation_result)

    async def _handle_text_message(
        self, response: Dict[str, Any], validation_result: Dict[str, Any]
    ) -> None:
        """Handle text messages"""
        message_text = validation_result["message_text"]
        chat_id = validation_result["chat_id"]
        sender_info = validation_result["sender_info"]

        if not message_text or not chat_id:
            self.logger.warning(
                "Message missing required fields: text='%s', chat_id='%s'", message_text, chat_id
            )
            return

        # Extract user info and start typing
        user_info = self._extract_user_info(response, sender_info)
        await self._start_typing_for_chat(chat_id)

        # Process user and send to agent
        user_data = await self._get_or_create_user(
            user_info["platform_user_id"],
            user_info["first_name"],
            user_info["last_name"],
            user_info["phone_number"],
        )

        if user_data:
            await self._publish_to_agent(
                chat_id, user_info["platform_user_id"], message_text, user_data
            )
        else:
            self.logger.warning("Failed to get/create user for %s", user_info["platform_user_id"])
            await self._stop_typing_for_chat(chat_id)

    def _extract_user_info(
        self, response: Dict[str, Any], sender_info: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Extract user information from message"""
        user_name = sender_info.get("pushname", "Unknown")
        chat_id = response.get("chatId") or response.get("from", "")

        # Extract phone number from sender.id (format: 79222088435@c.us)
        sender_id = response.get("sender", {}).get("id", "")
        phone_number = None
        if sender_id and "@c.us" in sender_id:
            phone_number = sender_id.split("@c.us")[0]

        # Parse user name
        name_parts = (
            user_name.strip().split(" ", 1) if user_name and user_name != "Unknown" else ["Unknown"]
        )
        first_name = name_parts[0]
        last_name = name_parts[1] if len(name_parts) > 1 else None

        self.logger.info(
            "Received WhatsApp message from %s (%s), phone: %s", user_name, chat_id, phone_number
        )

        return {
            "platform_user_id": chat_id,
            "first_name": first_name,
            "last_name": last_name,
            "phone_number": phone_number,
        }

    async def _start_typing_for_chat(self, chat_id: str) -> None:
        """Start typing indicator for chat"""
        if chat_id in self.typing_tasks:
            self.typing_tasks[chat_id].cancel()
        self.typing_tasks[chat_id] = asyncio.create_task(self._send_typing_periodically(chat_id))

    async def _stop_typing_for_chat(self, chat_id: str) -> None:
        """Stop typing indicator for a specific chat"""
        if chat_id in self.typing_tasks:
            self.typing_tasks[chat_id].cancel()
            del self.typing_tasks[chat_id]
        
        # Send stop typing action to WhatsApp
        try:
            await self.api_handler.send_typing_action(chat_id, False)
        except Exception as e:
            self.logger.error("Failed to stop typing action for chat %s: %s", chat_id, e)

    async def stop_typing_for_chat(self, chat_id: str) -> None:
        """Stop typing indicator for chat - public interface for helpers."""
        await self._stop_typing_for_chat(chat_id)

    async def _send_typing_periodically(self, chat_id: str) -> None:
        """
        Периодически отправляет индикатор печати в WhatsApp пока агент обрабатывает запрос
        
        Args:
            chat_id: ID чата WhatsApp
        """
        try:
            # Start typing
            await self.api_handler.send_typing_action(chat_id, True)
            
            while True:
                await asyncio.sleep(3)  # Refresh typing indicator every 3 seconds
                await self.api_handler.send_typing_action(chat_id, True)
                
        except asyncio.CancelledError:
            self.logger.debug("Typing task cancelled for chat %s", chat_id)
            # Stop typing when cancelled
            try:
                await self.api_handler.send_typing_action(chat_id, False)
            except Exception as e:
                self.logger.error("Failed to stop typing action on cancel for chat %s: %s", chat_id, e)
        except Exception as e:
            self.logger.error("Error in typing task for chat %s: %s", chat_id, e, exc_info=True)
        finally:
            # Clean up typing task
            if chat_id in self.typing_tasks:
                del self.typing_tasks[chat_id]

    async def publish_to_agent(
        self,
        chat_id: str,
        platform_user_id: str,
        message_text: str,
        user_data: Dict[str, Any],
        image_urls: Optional[List[str]] = None,
    ) -> None:
        """Publish message to agent - public interface for helpers."""
        await self._publish_to_agent(chat_id, platform_user_id, message_text, user_data, image_urls)

        if chat_id in self.typing_tasks:
            self.typing_tasks[chat_id].cancel()
            del self.typing_tasks[chat_id]

    async def _get_or_create_user(
        self,
        platform_user_id: str,
        first_name: str,
        last_name: Optional[str] = None,
        phone_number: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
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

    async def _publish_to_agent(
        self,
        chat_id: str,
        platform_user_id: str,
        message_text: str,
        user_data: Dict[str, Any],
        image_urls: Optional[List[str]] = None,
    ) -> None:
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
            self.logger.error("Redis client not available for publishing to agent: %s", e)
            await self._send_error_message(
                chat_id, "Ошибка: Не удалось связаться с агентом (сервис недоступен)."
            )
            return

        input_channel = "agent:%s:input" % self.agent_id

        payload = {
            "text": message_text,
            "chat_id": chat_id,
            "platform_user_id": platform_user_id,
            "user_data": user_data,
            "channel": "whatsapp",
        }

        # Add image URLs if provided
        if image_urls:
            payload["image_urls"] = image_urls
            self.logger.info("Adding %s image URLs to WhatsApp message payload", len(image_urls))

        try:
            await redis_cli.publish(input_channel, json.dumps(payload).encode("utf-8"))
            self.logger.debug("Published message to %s: %s", input_channel, payload)
            await self.update_last_active_time()

        except redis_exceptions.RedisError as e:
            self.logger.error("Redis error publishing message: %s", e)
            await self._send_error_message(chat_id, "Ошибка отправки сообщения. Попробуйте позже.")
        except Exception as e:
            self.logger.error("Unexpected error publishing message: %s", e, exc_info=True)

    async def _listen_agent_responses(self) -> None:
        """Слушатель ответов агента через Redis Pub/Sub"""
        try:
            redis_cli = await self.redis_client
            pubsub = redis_cli.pubsub()
            await pubsub.subscribe(self._pubsub_channel)

            self.logger.info("Subscribed to agent responses on %s", self._pubsub_channel)

            while self._running and not self.needs_restart:
                try:
                    message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
                    if message and message["type"] == "message":
                        await self._handle_agent_response(message["data"])
                except Exception as e:
                    self.logger.error("Error processing agent response: %s", e, exc_info=True)
                    await asyncio.sleep(1)

        except Exception as e:
            self.logger.error("Error listening to agent responses: %s", e, exc_info=True)

    async def _handle_agent_response(self, message_data: bytes) -> None:
        """
        Обработка ответа агента и отправка в WhatsApp

        Args:
            message_data: Данные сообщения от агента
        """
        try:
            data = json.loads(message_data.decode("utf-8"))

            if not self._is_valid_agent_response(data):
                return

            chat_id = data["chat_id"]
            response_text = data["response"]
            audio_url = data.get("audio_url")

            await self._stop_typing_for_chat(chat_id)
            await asyncio.sleep(0.5)  # Natural typing simulation

            # 🎯 PHASE 4.4.2: UNIFIED TTS RESPONSE - Consistent with Telegram pattern
            # Voice responses from LangGraph agent through voice tools
            voice_sent_successfully = await self._send_voice_response(chat_id, audio_url)

            # Send text response only if voice was not sent successfully
            if not voice_sent_successfully:
                await self.api_handler.send_message(chat_id, response_text)
            else:
                self.logger.info(f"Voice response sent successfully to chat {chat_id}, skipping text message")

        except json.JSONDecodeError as e:
            self.logger.error(f"Failed to decode agent response: {e}")
        except Exception as e:
            self.logger.error(f"Error handling agent response: {e}", exc_info=True)

    def _is_valid_agent_response(self, data: Dict[str, Any]) -> bool:
        """Validate agent response data."""
        channel = data.get("channel")
        chat_id = data.get("chat_id")
        response_text = data.get("response")

        if channel != "whatsapp":
            return False

        if not chat_id or not response_text:
            self.logger.warning("Invalid agent response data: %s", data)
            return False

        return True

    async def _send_voice_response(self, chat_id: str, audio_url: Optional[str]) -> bool:
        """
        🎯 PHASE 4.4.2: Unified voice response sending
        Handles voice message delivery with standardized error handling.
        Consistent with Telegram pattern for unified voice processing.

        Args:
            chat_id: WhatsApp chat ID
            audio_url: URL to audio file from LangGraph voice tools

        Returns:
            bool: True if voice message sent successfully, False otherwise
        """
        if not audio_url:
            return False

        try:
            self.logger.info(
                f"Sending voice response from LangGraph agent to chat {chat_id}: {audio_url}"
            )
            success = await self.api_handler.send_voice_message(chat_id, audio_url)

            if success:
                self.logger.info(f"Voice message sent successfully to WhatsApp chat {chat_id}")
                return True
            else:
                self.logger.warning(f"Failed to send voice message to WhatsApp chat {chat_id}")
                return False

        except Exception as e:
            self.logger.error(
                f"Error sending voice response to WhatsApp chat {chat_id}: {e}", exc_info=True
            )
            return False

    async def _try_send_voice_message(self, chat_id: str, audio_url: Optional[str]) -> bool:
        """
        Try to send voice message, return True if successful.

        Args:
            chat_id: Chat ID
            audio_url: Audio URL if available

        Returns:
            True if voice message was sent successfully
        """
        if not audio_url:
            return False

        try:
            self.logger.debug(
                "Attempting to send voice message to %s with audio_url: %s", chat_id, audio_url
            )
            success = await self.api_handler.send_voice_message(chat_id, audio_url)

            if success:
                self.logger.info("Voice message sent successfully to WhatsApp chat %s", chat_id)
                return True
            else:
                self.logger.warning("Voice message failed for %s, falling back to text", chat_id)
                return False

        except Exception as e:
            self.logger.error("Error sending voice message to WhatsApp chat %s: %s", chat_id, e)
            return False

    async def _send_error_message(self, chat_id: str, error_text: str) -> None:
        """Отправка сообщения об ошибке пользователю"""
        await self.api_handler.send_message(chat_id, error_text)

    async def _handle_reconnection(self) -> None:
        """Обработка переподключения к wppconnect-server"""
        if self.reconnect_attempts >= self.max_reconnect_attempts:
            self.logger.error("Max reconnection attempts (%s) reached", self.max_reconnect_attempts)
            await self.mark_as_error("Max reconnection attempts reached")
            return

        self.reconnect_attempts += 1
        self.logger.info(
            "Attempting reconnection %s/%s", self.reconnect_attempts, self.max_reconnect_attempts
        )

        try:
            await asyncio.sleep(self.reconnect_delay)
            socketio_url = "%s" % self.wppconnect_base_url
            if not self.sio:
                self.logger.error("Socket.IO client not initialized for reconnection")
                return
            await self.sio.connect(socketio_url, socketio_path=settings.WPPCONNECT_SOCKETIO_PATH)

        except Exception as e:
            self.logger.error("Reconnection attempt %s failed: %s", self.reconnect_attempts, e)

    async def _handle_image_message(
        self, response: Dict[str, Any], chat_id: str, sender_info: Dict[str, Any]
    ) -> None:
        """Handle image message using MediaHandler."""
        await self.media_handler.handle_image_message(response, chat_id, sender_info)

    async def _handle_voice_message(
        self, response: Dict[str, Any], chat_id: str, sender_info: Dict[str, Any]
    ) -> None:
        """Handle voice message using MediaHandler."""
        try:
            # Delegate to media handler
            await self.media_handler.handle_voice_message(response, chat_id, sender_info)
        except Exception as e:
            self.logger.error("Error in _handle_voice_message: %s", e, exc_info=True)
            await self._send_error_message(
                chat_id, "⚠️ Произошла ошибка при обработке голосового сообщения."
            )
        finally:
            # Stop typing indicator
            if chat_id in self.typing_tasks:
                self.typing_tasks[chat_id].cancel()
                del self.typing_tasks[chat_id]

    async def _get_agent_config(self) -> Dict[str, Any]:
        """
        🎯 PHASE 4.4.2: DYNAMIC CONFIG - Get agent configuration dynamically
        Fetches current agent configuration at runtime, enabling real-time updates.

        Returns:
            Dict containing current agent configuration
        """
        try:
            self.logger.debug(f"Fetching dynamic agent config for {self.agent_id}")

            async with httpx.AsyncClient(timeout=5.0) as client:
                response = await client.get(
                    f"http://{settings.MANAGER_HOST}:{settings.MANAGER_PORT}/api/v1/agents/{self.agent_id}/config"
                )
                if response.status_code == 200:
                    config = response.json()
                    self.logger.debug(
                        f"Successfully fetched dynamic agent config for {self.agent_id}"
                    )
                    return config
                else:
                    self.logger.warning(
                        f"Failed to fetch agent config: HTTP {response.status_code}, using fallback"
                    )
                    return self._get_fallback_agent_config()

        except Exception as e:
            self.logger.warning(f"Error fetching dynamic agent config: {e}, using fallback")
            return self._get_fallback_agent_config()

    def _get_fallback_agent_config(self) -> Dict[str, Any]:
        """
        🎯 PHASE 4.4.2: Fallback agent configuration for error cases
        Provides minimal configuration when dynamic config loading fails.

        Returns:
            Dict containing fallback agent configuration
        """
        return {"config": {"simple": {"settings": {"voice_settings": {"enabled": False}}}}}
