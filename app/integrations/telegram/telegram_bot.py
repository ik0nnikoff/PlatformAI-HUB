"""
Telegram Bot Integration for PlatformAI Hub - Clean Architecture Version.

Refactored Telegram integration following WhatsApp architecture patterns with:
- Clean separation of concerns
- Component-based initialization
- Proper dependency injection
- Specialized processors for different message types
"""

import asyncio
import json
import logging
import os
from typing import TYPE_CHECKING, Any, Dict, Optional

from aiogram import Bot, Dispatcher, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart
from aiogram.types import Message

from app.api.schemas.common_schemas import IntegrationType
from app.core.base.service_component import ServiceComponentBase
from app.core.config import settings

from .core.component_initializer import ComponentInitializer

if TYPE_CHECKING:
    from .core.message_coordinator import MessageCoordinator
    from .core.redis_service import RedisService
    from .core.user_service import UserService
    from .handlers.command_handler import CommandHandler
    from .infrastructure.api_client import TelegramAPIClient
    from .infrastructure.orchestrators.image_orchestrator import \
        ImageOrchestrator
    from .infrastructure.orchestrators.voice_orchestrator import \
        VoiceOrchestrator
    from .infrastructure.typing_manager import TypingManager
    from .processors.contact_processor import ContactProcessor
    from .processors.image_processor import ImageProcessor
    from .processors.text_processor import TextProcessor
    from .processors.voice_processor import VoiceProcessor

# Constants
REDIS_USER_CACHE_TTL = getattr(
    settings, "REDIS_USER_CACHE_TTL", int(os.getenv("REDIS_USER_CACHE_TTL", "3600"))
)
USER_CACHE_PREFIX = "user_cache:"
AUTH_TRIGGER = "AUTH_REQUIRED"


class TelegramIntegrationBot(ServiceComponentBase):
    """
    Manages the lifecycle and execution of a Telegram Bot integration.
    Refactored to follow clean architecture patterns with proper separation of concerns.
    """

    # Type hints for core services
    user_service: "UserService"
    redis_service: "RedisService"
    message_coordinator: "MessageCoordinator"

    # Type hints for infrastructure
    api_client: "TelegramAPIClient"
    typing_manager: "TypingManager"

    # Type hints for processors
    text_processor: "TextProcessor"
    voice_processor: "VoiceProcessor"
    image_processor: "ImageProcessor"
    contact_processor: "ContactProcessor"

    # Type hints for handlers
    command_handler: "CommandHandler"

    # Type hints for orchestrators
    voice_orchestrator: "VoiceOrchestrator"
    image_orchestrator: "ImageOrchestrator"

    def __init__(
        self,
        config: Dict[str, Any],
        logger_adapter: logging.LoggerAdapter,
    ):
        """Initialize Telegram bot with consolidated config."""
        agent_id = config["agent_id"]
        bot_token = config["bot_token"]
        db_session_factory = config["db_session_factory"]

        # Initialize ServiceComponentBase
        super().__init__(
            component_id=f"{agent_id}:{IntegrationType.TELEGRAM.value}",
            status_key_prefix="integration_status:",
            logger_adapter=logger_adapter,
        )

        # Core attributes
        self.agent_id = agent_id
        self.bot_token = bot_token
        self.db_session_factory = db_session_factory
        self._pubsub_channel = f"agent:{self.agent_id}:output"

        # Aiogram specific attributes
        self.bot: Optional[Bot] = None
        self.dp: Optional[Dispatcher] = None

        # Legacy typing support (for backward compatibility)
        self.typing_tasks: Dict[int, asyncio.Task] = {}

        # Agent configuration
        self.agent_config: Optional[Dict[str, Any]] = None

        # Photo grouping (migrated to ImageProcessor but kept for compatibility)
        self.photo_groups: Dict[str, list[Message]] = {}
        self.photo_buffers: Dict[int, list[Message]] = {}
        self.photo_timers: Dict[int, asyncio.Task] = {}
        self.photo_group_timeout = 2.0

        # Initialize components using ComponentInitializer
        ComponentInitializer.initialize_core_components(
            self, agent_id, db_session_factory, logger_adapter
        )
        ComponentInitializer.initialize_infrastructure_components(self)
        ComponentInitializer.initialize_processors(self)
        ComponentInitializer.initialize_orchestrators(self)

        # Initialize command handler
        # pylint: disable=import-outside-toplevel
        from .handlers.command_handler import CommandHandler

        self.command_handler = CommandHandler(self, logger_adapter)

        self.logger.info(
            "TelegramIntegrationBot initialized with clean architecture. PID: %s",
            os.getpid(),
        )

    async def setup(self) -> None:
        """Initialize bot and all components."""
        try:
            await super().setup()
            await self._setup_bot()
            await self._setup_orchestrators()
            await self._load_agent_config()
            await self._register_handlers()

            self.logger.info("Telegram integration setup completed")

        except Exception as e:
            self.logger.error("Setup failed: %s", e, exc_info=True)
            await self.mark_as_error(f"Setup failed: {str(e)}")
            raise

    async def _setup_bot(self) -> None:
        """Setup Telegram bot and dispatcher."""
        self.bot = Bot(
            token=self.bot_token,
            default=DefaultBotProperties(parse_mode=ParseMode.HTML),
        )

        self.dp = Dispatcher()
        self.logger.info("Telegram bot initialized")

    async def _setup_orchestrators(self) -> None:
        """Initialize voice and image orchestrators."""
        await self._initialize_voice_orchestrator()
        await self._initialize_image_orchestrator()

    async def _initialize_voice_orchestrator(self) -> None:
        """Initialize voice orchestrator."""
        if await self.voice_orchestrator.initialize():
            self.logger.info("Voice orchestrator initialized")
        else:
            self.logger.warning("Voice orchestrator initialization failed")

    async def _initialize_image_orchestrator(self) -> None:
        """Initialize image orchestrator."""
        if await self.image_orchestrator.initialize():
            self.logger.info("Image orchestrator initialized")
        else:
            self.logger.warning("Image orchestrator initialization failed")

    async def _load_agent_config(self) -> None:
        """Load agent configuration dynamically."""
        try:
            config = await self._fetch_dynamic_config()
            self.agent_config = config if config else self._get_fallback_agent_config()

            if config:
                self.logger.info("Dynamic agent config loaded")
            else:
                self.logger.warning("Using fallback agent config")

        except Exception as e:
            self.logger.warning("Error loading agent config: %s, using fallback", e)
            self.agent_config = self._get_fallback_agent_config()

    async def _fetch_dynamic_config(self) -> Optional[Dict[str, Any]]:
        """Fetch agent configuration from API."""
        try:
            # pylint: disable=import-outside-toplevel
            import httpx

            api_url = f"{settings.SOURCES_API_BASE_URL}/agents/{self.agent_id}/config"
            async with httpx.AsyncClient(timeout=5.0) as client:
                response = await client.get(api_url)
                if response.status_code == 200:
                    return response.json()
                return None
        except Exception:
            return None

    def _get_fallback_agent_config(self) -> Dict[str, Any]:
        """Get fallback agent configuration."""
        return {
            "voice_enabled": getattr(settings, "VOICE_ENABLED", True),
            "image_enabled": getattr(settings, "IMAGE_ENABLED", True),
            "max_message_length": 4096,
            "typing_timeout": 60.0,
        }

    async def _register_handlers(self) -> None:
        """Register Aiogram handlers with clean delegation."""
        if not self.dp:
            raise RuntimeError("Dispatcher not initialized")

        # Command handlers
        self.dp.message.register(
            self.command_handler.handle_start_command, CommandStart()
        )
        self.dp.message.register(
            self.command_handler.handle_login_command, Command("login")
        )
        self.dp.message.register(
            self.command_handler.handle_help_command, Command("help")
        )

        # Message type handlers - delegate to MessageCoordinator
        self.dp.message.register(
            self.message_coordinator.route_message_by_type,
            F.text | F.voice | F.audio | F.photo | F.contact | F.document,
        )

        self.logger.info("Telegram handlers registered")

    async def run(self) -> None:
        """Main execution loop for the Telegram Bot."""
        await self.setup()
        await self.run_loop()

    async def run_loop(self) -> None:
        """Implementation of abstract run_loop method."""
        await self.mark_as_initializing()

        try:
            if not self.bot or not self.dp:
                raise RuntimeError("Bot or dispatcher not initialized")

            # Register main tasks
            self._register_main_task(
                self._listen_agent_responses(),
                name=f"telegram_redis_listener_{self.agent_id}",
            )

            # Start the service component run loop
            await self.mark_as_running()

            # Start Telegram polling
            self.logger.info("Starting Telegram bot polling...")
            await self.dp.start_polling(self.bot)

        except Exception as e:
            self.logger.error("Error in run loop: %s", e, exc_info=True)
            await self.mark_as_error("Run loop error: %s", str(e))
            raise

    async def _listen_agent_responses(self) -> None:
        """Listen for agent responses through Redis Pub/Sub."""
        try:
            redis_cli = await self.redis_client
            pubsub = redis_cli.pubsub()
            await pubsub.subscribe(self._pubsub_channel)

            self.logger.info(
                "Listening for agent responses on %s", self._pubsub_channel
            )

            async for message in pubsub.listen():
                if message["type"] == "message":
                    try:
                        await self._handle_agent_response(message["data"])
                    except Exception as e:
                        self.logger.error(
                            "Error handling agent response: %s", e, exc_info=True
                        )

        except Exception as e:
            self.logger.error("Error in agent response listener: %s", e, exc_info=True)
            raise

    async def _handle_agent_response(self, message_data: bytes) -> None:
        """Handle agent response message."""
        try:
            data = self.redis_service.decode_message(message_data)
            if not self.message_coordinator.is_valid_agent_response(data):
                return

            chat_id = int(data["chat_id"])
            response_text = data["response"]
            audio_url = data.get("audio_url")
            platform_user_id = str(
                chat_id
            )  # For Telegram, chat_id is the same as user_id in private chats

            # Stop typing indicator
            await self.typing_manager.stop_typing(chat_id)

            # Check for authorization trigger (following original backup logic)
            auth_required = AUTH_TRIGGER in response_text
            if auth_required:
                # Remove AUTH_TRIGGER from response first
                response_text = response_text.replace(AUTH_TRIGGER, "").strip()

                # Check if user is actually authorized
                is_user_authorized = await self._check_user_authorization(
                    platform_user_id
                )

                if not is_user_authorized:
                    # User is not authorized, send response with authorization request
                    await self.api_client.send_message_with_markup(
                        chat_id,
                        f"{response_text}\n\nДля продолжения требуется авторизация. Используйте /login или кнопку ниже:",
                        self._request_contact_markup(),
                    )
                    self.logger.info(
                        f"Authorization required message sent to user {platform_user_id}"
                    )
                    return

            # Send voice response if available
            if audio_url:
                voice_sent = await self.message_coordinator.send_voice_response(
                    str(chat_id), audio_url
                )
                if voice_sent:
                    return

            # Send text response (only if not empty after AUTH_TRIGGER removal)
            if response_text:
                await self.api_client.send_message(chat_id, response_text)
                self.logger.info("Response sent to chat %s", chat_id)

        except Exception as e:
            self.logger.error("Error handling agent response: %s", e, exc_info=True)

    async def cleanup(self) -> None:
        """Cleanup bot resources."""
        self.logger.info("Cleaning up Telegram integration...")

        try:
            await self._cleanup_typing_tasks()
            await self._cleanup_orchestrators()
            await self._cleanup_bot_session()
            await super().cleanup()

            self.logger.info("Telegram integration cleanup completed")

        except Exception as e:
            self.logger.error("Error during cleanup: %s", e, exc_info=True)

    def _request_contact_markup(self):
        """Create keyboard for contact sharing request."""
        return self.api_client.create_contact_request_keyboard()

    async def _check_user_authorization(self, platform_user_id: str) -> bool:
        """Check if user is authorized for this agent."""
        return await self.user_service.check_user_authorization(platform_user_id)

    async def _cleanup_typing_tasks(self) -> None:
        """Cleanup typing tasks and timers."""
        # Cleanup typing tasks
        if self.typing_manager:
            await self.typing_manager.cleanup_all_typing_tasks()

        # Cleanup legacy typing tasks
        for task in self.typing_tasks.values():
            if not task.done():
                task.cancel()
        self.typing_tasks.clear()

        # Cleanup photo timers
        for task in self.photo_timers.values():
            if not task.done():
                task.cancel()
        self.photo_timers.clear()

    async def _cleanup_orchestrators(self) -> None:
        """Cleanup orchestrators."""
        if self.voice_orchestrator:
            await self.voice_orchestrator.cleanup()

        if self.image_orchestrator:
            await self.image_orchestrator.cleanup()

    async def _cleanup_bot_session(self) -> None:
        """Close bot session."""
        if self.bot:
            await self.bot.session.close()

    # Legacy methods for backward compatibility
    async def _send_voice_response(self, chat_id: int, audio_url: str) -> bool:
        """Legacy method - delegates to API client."""
        return await self.api_client.send_voice_from_url(chat_id, audio_url)

    async def _handle_pubsub_message(self, message_data: bytes) -> None:
        """Handle incoming pub/sub messages from agent."""
        try:
            message_str = message_data.decode("utf-8")
            message_json = json.loads(message_str)
            await self._handle_agent_response(message_json)
        except Exception as e:
            self.logger.error("Error handling pub/sub message: %s", e, exc_info=True)
