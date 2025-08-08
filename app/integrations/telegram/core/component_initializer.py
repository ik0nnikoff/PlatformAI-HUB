"""
Component initialization for Telegram Integration.

Handles initialization of helper components following dependency injection patterns
to reduce complexity in main Telegram bot constructor.
"""

import logging
from typing import TYPE_CHECKING

from .message_coordinator import MessageCoordinator
from .redis_service import RedisService
from .user_service import UserService

if TYPE_CHECKING:
    from ..telegram_bot import TelegramIntegrationBot


class ComponentInitializer:
    """Initializes Telegram integration components with proper separation."""

    @staticmethod
    def initialize_core_components(
        bot_instance: "TelegramIntegrationBot",
        agent_id: str,
        db_session_factory,
        logger_adapter: logging.LoggerAdapter,
    ) -> None:
        """Initialize core business logic components."""
        # User management service with bot instance reference for Redis access
        bot_instance.user_service = UserService(
            agent_id,
            db_session_factory,
            logger_adapter,
            bot_instance=bot_instance,  # Pass bot instance for Redis client access
        )

        # Redis communication service
        bot_instance.redis_service = RedisService(bot_instance, logger_adapter)

    @staticmethod
    def initialize_infrastructure_components(
        bot_instance: "TelegramIntegrationBot",
    ) -> None:
        """Initialize infrastructure components."""
        # Lazy imports to avoid circular dependencies
        # pylint: disable=import-outside-toplevel
        from ..infrastructure.api_client import TelegramAPIClient
        from ..infrastructure.typing_manager import TypingManager

        # API client for Telegram operations
        bot_instance.api_client = TelegramAPIClient(bot_instance)

        # Typing indicator manager
        bot_instance.typing_manager = TypingManager(
            bot_instance.api_client, bot_instance.logger
        )

    @staticmethod
    def initialize_processors(bot_instance: "TelegramIntegrationBot") -> None:
        """Initialize specialized message processors."""
        # Lazy imports to avoid circular dependencies
        # pylint: disable=import-outside-toplevel
        from ..processors.contact_processor import ContactProcessor
        from ..processors.image_processor import ImageProcessor
        from ..processors.text_processor import TextProcessor
        from ..processors.voice_processor import VoiceProcessor

        # Text message processor
        bot_instance.text_processor = TextProcessor(
            bot_instance,
            bot_instance.user_service,
            bot_instance.redis_service,
            bot_instance.logger,
        )

        # Voice message processor with voice_v2 integration
        bot_instance.voice_processor = VoiceProcessor(
            bot_instance,
            bot_instance.user_service,
            bot_instance.redis_service,
            bot_instance.logger,
        )

        # Image message processor with infrastructure integration
        bot_instance.image_processor = ImageProcessor(
            bot_instance,
            bot_instance.user_service,
            bot_instance.redis_service,
            bot_instance.logger,
        )

        # Contact message processor for user authorization
        bot_instance.contact_processor = ContactProcessor(
            bot_instance,
            bot_instance.user_service,
            bot_instance.redis_service,
            bot_instance.logger,
        )

        # Message coordination service (now that all components are ready)
        bot_instance.message_coordinator = MessageCoordinator(
            bot_instance, bot_instance.user_service, bot_instance.logger
        )

    @staticmethod
    def initialize_orchestrators(bot_instance: "TelegramIntegrationBot") -> None:
        """Initialize media orchestrators."""
        # Lazy imports to avoid circular dependencies
        # pylint: disable=import-outside-toplevel
        from ..infrastructure.orchestrators.image_orchestrator import \
            ImageOrchestrator
        from ..infrastructure.orchestrators.voice_orchestrator import \
            VoiceOrchestrator

        # Voice orchestrator for STT/TTS
        bot_instance.voice_orchestrator = VoiceOrchestrator(logger=bot_instance.logger)

        # Image orchestrator
        bot_instance.image_orchestrator = ImageOrchestrator(bot_instance.logger)
