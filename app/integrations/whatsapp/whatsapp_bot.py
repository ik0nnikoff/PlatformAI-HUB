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
from socketio.async_client import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api.schemas.common_schemas import IntegrationType
from app.core.base.service_component import ServiceComponentBase
from app.core.config import settings
from app.services.voice_v2.core.orchestrator import VoiceServiceOrchestrator
from app.services.voice_v2.infrastructure.cache import VoiceCache
from app.services.voice_v2.infrastructure.minio_manager import MinioFileManager
from app.services.voice_v2.providers.unified_factory import VoiceProviderFactory
from app.services.media.image_orchestrator import ImageOrchestrator

from .component_initializer import ComponentInitializer

from .handlers.api_handler import WhatsAppAPIHandler
from .handlers.media_handler import MediaHandler
from .handlers.socketio_handler import SocketIOEventHandlers
from .user_manager import WhatsAppUserManager
from .redis_manager import WhatsAppRedisManager
from .message_processor import WhatsAppMessageProcessor

class WhatsAppIntegrationBot(ServiceComponentBase):
    """
    Manages the lifecycle and execution of a WhatsApp Bot integration for a specific agent.
    Inherits from ServiceComponentBase for unified state and lifecycle management.

    Использует Socket.IO для получения сообщений от wppconnect-server в реальном времени
    и HTTP API для отправки сообщений обратно.
    """

    # Type hints for attributes initialized by ComponentInitializer
    api_handler: 'WhatsAppAPIHandler'
    media_handler: 'MediaHandler'
    socketio_handler: 'SocketIOEventHandlers'
    user_manager: 'WhatsAppUserManager'
    redis_manager: 'WhatsAppRedisManager'
    message_processor: 'WhatsAppMessageProcessor'

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

        # Initialize helper components using ComponentInitializer
        ComponentInitializer.initialize_helper_components(
            self, agent_id, db_session_factory, logger_adapter
        )
        ComponentInitializer.initialize_handlers(self)

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

            self.logger.info(
                "WhatsApp integration setup completed for session %s", self.session_name
            )

        except Exception as e:
            self.logger.error(
                "Setup failed for session %s: %s", self.session_name, e, exc_info=True
            )
            await self.mark_as_error(f"Setup failed: {str(e)}")
            raise

    async def _setup_http_client(self) -> None:
        """Setup HTTP client with authentication headers."""
        headers = {"Content-Type": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"

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
        except (ImportError, AttributeError, ConnectionError) as e:
            self.logger.warning("Failed to initialize voice orchestrator v2: %s", e)
            self.voice_orchestrator = None

    async def _setup_image_orchestrator(self) -> None:
        """Initialize image orchestrator with error handling."""
        try:
            self.image_orchestrator = ImageOrchestrator()
            await self.image_orchestrator.initialize()
            self.logger.info("Image orchestrator initialized for WhatsApp bot")
        except (ImportError, AttributeError, ConnectionError) as e:
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
            socketio_url = f"{self.wppconnect_base_url}"
            self.logger.info("Connecting to wppconnect-server at %s", socketio_url)

            if not self.sio:
                raise RuntimeError("Socket.IO client not initialized")

            await self.sio.connect(socketio_url, socketio_path=settings.WPPCONNECT_SOCKETIO_PATH)

            # Register main tasks
            self._register_main_task(
                self._listen_agent_responses(), name=f"whatsapp_redis_listener_{self.agent_id}"
            )

            # Start the service component run loop
            await super().run_loop()

        except (ConnectionError, RuntimeError, OSError) as e:
            self.logger.error("Error in WhatsApp integration main loop: %s", e, exc_info=True)
            await self.mark_as_error(f"Main loop failed: {str(e)}")
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

        except (AttributeError, ConnectionError, OSError) as e:
            self.logger.error("Error during WhatsApp integration cleanup: %s", e, exc_info=True)

    async def _cleanup_typing_tasks(self) -> None:
        """Cancel all typing tasks"""
        for chat_id, task in self.typing_tasks.items():
            task.cancel()
            # Optionally send stop typing for each chat
            try:
                await self.api_handler.send_typing_action(chat_id, False)
            except (httpx.RequestError, AttributeError) as e_typing:
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
            except (AttributeError, ConnectionError) as e:
                self.logger.error("Error cleaning up voice orchestrator: %s", e)

    async def _cleanup_image_orchestrator(self) -> None:
        """Cleanup image orchestrator"""
        if hasattr(self, "image_orchestrator") and self.image_orchestrator:
            try:
                self.logger.info("Image orchestrator cleaned up")
            except AttributeError as e:
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
        return await self.user_manager.extract_user_context(response, chat_id, sender_info)

    async def _handle_received_message(self, data: Dict[str, Any]) -> None:
        """
        Handle received message from WhatsApp through Socket.IO with delegated complexity.
        """
        await self.message_processor.handle_received_message(data)

    def _validate_message(self, response: Dict[str, Any]) -> Dict[str, Any]:
        """Validate incoming message and extract basic info."""
        return self.message_processor.validate_message(response)

    async def _route_message_by_type(
        self, response: Dict[str, Any], validation_result: Dict[str, Any]
    ) -> None:
        """Route message processing based on message type."""
        await self.message_processor.route_message_by_type(response, validation_result)

    async def _handle_text_message(
        self, response: Dict[str, Any], validation_result: Dict[str, Any]
    ) -> None:
        """Handle text messages with delegated complexity."""
        await self.message_processor.handle_text_message(response, validation_result)

    def _extract_user_info(
        self, response: Dict[str, Any], sender_info: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Extract user information from message with delegated complexity."""
        return self.message_processor.extract_user_info(response, sender_info)

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
        except (httpx.RequestError, AttributeError) as e:
            self.logger.error("Failed to stop typing action for chat %s: %s", chat_id, e)

    async def stop_typing_for_chat(self, chat_id: str) -> None:
        """Stop typing indicator for chat - public interface for helpers."""
        await self._stop_typing_for_chat(chat_id)

    async def _send_typing_periodically(self, chat_id: str) -> None:
        """Периодически отправляет индикатор печати в WhatsApp пока агент обрабатывает запрос"""
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
            except (httpx.RequestError, AttributeError) as e:
                self.logger.error(
                    "Failed to stop typing action on cancel for chat %s: %s", chat_id, e
                )
        except (httpx.RequestError, asyncio.TimeoutError, ConnectionError) as e:
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
        """Get or create user with delegated complexity to UserManager."""
        return await self.user_manager.get_or_create_user(
            platform_user_id, first_name, last_name, phone_number
        )

    async def _publish_to_agent(
        self,
        chat_id: str,
        platform_user_id: str,
        message_text: str,
        user_data: Dict[str, Any],
        image_urls: Optional[List[str]] = None,
    ) -> None:
        """Publish message to agent Redis channel with delegated complexity."""
        await self.redis_manager.publish_to_agent(
            message_text,
            {"chat_id": chat_id, "platform_user_id": platform_user_id},
            user_data,
            image_urls
        )

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
        """Обработка ответа агента и отправка в WhatsApp"""
        try:
            data = json.loads(message_data.decode("utf-8"))

            if not self._is_valid_agent_response(data):
                return

            chat_id = data["chat_id"]
            response_text = data["response"]
            audio_url = data.get("audio_url")

            await self._stop_typing_for_chat(chat_id)
            await asyncio.sleep(0.5)  # Natural typing simulation

            voice_sent_successfully = await self._send_voice_response(chat_id, audio_url)

            # Send text response only if voice was not sent successfully
            if not voice_sent_successfully:
                await self.api_handler.send_message(chat_id, response_text)
            else:
                self.logger.info(
                    "Voice response sent successfully to chat %s, skipping text message", chat_id
                )

        except json.JSONDecodeError as e:
            self.logger.error("Failed to decode agent response: %s", e)
        except Exception as e:
            self.logger.error("Error handling agent response: %s", e, exc_info=True)

    def _is_valid_agent_response(self, data: Dict[str, Any]) -> bool:
        """Validate agent response data with delegated complexity."""
        return self.message_processor.is_valid_agent_response(data)

    async def _send_voice_response(self, chat_id: str, audio_url: Optional[str]) -> bool:
        """Send voice response with delegated complexity to MessageProcessor."""
        return await self.message_processor.send_voice_response(chat_id, audio_url)

    async def _try_send_voice_message(self, chat_id: str, audio_url: Optional[str]) -> bool:
        """Try to send voice message, return True if successful. """
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

            self.logger.warning("Voice message failed for %s, falling back to text", chat_id)
            return False

        except (httpx.RequestError, httpx.TimeoutException, httpx.HTTPStatusError) as e:
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
            socketio_url = f"{self.wppconnect_base_url}"
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
        """Get agent configuration dynamically"""
        try:
            self.logger.debug("Fetching dynamic agent config for %s", self.agent_id)

            async with httpx.AsyncClient(timeout=5.0) as client:
                config_url = (
                    f"http://{settings.MANAGER_HOST}:{settings.MANAGER_PORT}"
                    f"/api/v1/agents/{self.agent_id}/config"
                )
                response = await client.get(config_url)
                if response.status_code == 200:
                    config = response.json()
                    self.logger.debug(
                        "Successfully fetched dynamic agent config for %s", self.agent_id
                    )
                    return config

                self.logger.warning(
                    "Failed to fetch agent config: HTTP %s, using fallback", response.status_code
                )
                return self._get_fallback_agent_config()

        except Exception as e:
            self.logger.warning("Error fetching dynamic agent config: %s, using fallback", e)
            return self._get_fallback_agent_config()

    def _get_fallback_agent_config(self) -> Dict[str, Any]:
        """Returns Dict containing fallback agent configuration"""
        return {"config": {"simple": {"settings": {"voice_settings": {"enabled": False}}}}}
