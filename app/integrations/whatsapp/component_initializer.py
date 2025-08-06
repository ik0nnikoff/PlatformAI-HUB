"""
Component initialization for WhatsApp Integration.

Handles initialization of helper components
to reduce complexity in main WhatsApp bot constructor.
"""

import logging

from .message_processor import WhatsAppMessageProcessor
from .redis_manager import WhatsAppRedisManager
from .user_manager import WhatsAppUserManager

from .whatsapp_bot import WhatsAppIntegrationBot
from .handlers.api_handler import WhatsAppAPIHandler
from .handlers.media_handler import MediaHandler
from .handlers.socketio_handler import SocketIOEventHandlers

class ComponentInitializer:
    """Initializes WhatsApp integration components."""

    @staticmethod
    def initialize_helper_components(
        bot_instance: "WhatsAppIntegrationBot",
        agent_id: str,
        db_session_factory,
        logger_adapter: logging.LoggerAdapter,
    ) -> None:
        """Initialize all helper components for WhatsApp bot."""
        # User management
        bot_instance.user_manager = WhatsAppUserManager(
            agent_id, db_session_factory, logger_adapter
        )

        # Redis operations
        bot_instance.redis_manager = WhatsAppRedisManager(bot_instance, logger_adapter)

        # Message processing
        bot_instance.message_processor = WhatsAppMessageProcessor(
            bot_instance, bot_instance.user_manager, logger_adapter
        )

    @staticmethod
    def initialize_handlers(bot_instance: "WhatsAppIntegrationBot") -> None:
        """Initialize API and event handlers."""
        bot_instance.media_handler = MediaHandler(bot_instance)
        bot_instance.api_handler = WhatsAppAPIHandler(bot_instance)
        bot_instance.socketio_handler = SocketIOEventHandlers(bot_instance)
