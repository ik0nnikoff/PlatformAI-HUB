"""
Component initialization for WhatsApp Integration.

Handles initialization of helper components following dependency injection patterns
to reduce complexity in main WhatsApp bot constructor.
"""

import logging
from typing import TYPE_CHECKING

from .message_coordinator import MessageCoordinator
from .redis_service import RedisService
from .user_service import UserService

if TYPE_CHECKING:
    from ..whatsapp_bot import WhatsAppIntegrationBot


class ComponentInitializer:
    """Initializes WhatsApp integration components with proper separation."""

    @staticmethod
    def initialize_core_components(
        bot_instance: "WhatsAppIntegrationBot",
        agent_id: str,
        db_session_factory,
        logger_adapter: logging.LoggerAdapter,
    ) -> None:
        """Initialize core business logic components."""
        # User management service
        bot_instance.user_service = UserService(
            agent_id, db_session_factory, logger_adapter
        )

        # Redis communication service
        bot_instance.redis_service = RedisService(bot_instance, logger_adapter)

        # Message coordination service
        bot_instance.message_coordinator = MessageCoordinator(
            bot_instance, bot_instance.user_service, logger_adapter
        )

    @staticmethod
    def initialize_infrastructure_components(
        bot_instance: "WhatsAppIntegrationBot",
    ) -> None:
        """Initialize infrastructure components."""
        # Lazy imports to avoid circular dependencies
        from ..infrastructure.api_client import \
            WhatsAppAPIClient  # pylint: disable=import-outside-toplevel
        from ..infrastructure.socketio_client import \
            SocketIOClient  # pylint: disable=import-outside-toplevel
        from ..infrastructure.typing_manager import \
            TypingManager  # pylint: disable=import-outside-toplevel

        # API client for HTTP operations
        bot_instance.api_client = WhatsAppAPIClient(bot_instance)

        # Socket.IO client for real-time communication
        bot_instance.socketio_client = SocketIOClient(bot_instance)

        # Typing manager for handling typing indicators
        bot_instance.typing_manager = TypingManager(
            bot_instance.api_client, bot_instance.logger
        )

    @staticmethod
    def initialize_processors(bot_instance: "WhatsAppIntegrationBot") -> None:
        """Initialize specialized message processors."""
        # Lazy imports to avoid circular dependencies
        from ..processors.image_processor import \
            ImageProcessor  # pylint: disable=import-outside-toplevel
        from ..processors.text_processor import \
            TextProcessor  # pylint: disable=import-outside-toplevel
        from ..processors.voice_processor import \
            VoiceProcessor  # pylint: disable=import-outside-toplevel

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
