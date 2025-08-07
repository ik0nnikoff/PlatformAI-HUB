"""
Socket.IO client for infrastructure layer.

Handles Socket.IO WebSocket communication with external service
following clean architecture principles.
"""

from socketio.async_client import AsyncClient

from ..handlers.socketio_handler import SocketIOEventHandlers


class SocketIOClient:
    """Handles Socket.IO client operations for WhatsApp integration."""

    def __init__(self, bot_instance):
        """
        Initialize Socket.IO client.

        Args:
            bot_instance: Reference to main WhatsAppIntegrationBot instance
        """
        self.bot = bot_instance
        self.logger = bot_instance.logger
        self.event_handlers = SocketIOEventHandlers(bot_instance)

    async def setup(self) -> None:
        """Setup Socket.IO client with event handlers."""
        if not self.bot.sio:
            self.bot.sio = AsyncClient(
                reconnection=True,
                reconnection_attempts=self.bot.max_reconnect_attempts,
                reconnection_delay=self.bot.reconnect_delay,
                logger=False,  # Disable socketio internal logging
                engineio_logger=False,
            )

        # Setup event handlers
        self.event_handlers.setup_handlers()
        self.logger.debug("Socket.IO client setup completed")

    async def connect(self, url: str, path: str) -> bool:
        """Connect to Socket.IO server."""
        try:
            if not self.bot.sio:
                raise RuntimeError("Socket.IO client not initialized")

            await self.bot.sio.connect(url, socketio_path=path)
            self.logger.info("Connected to Socket.IO server at %s", url)
            return True

        except (ConnectionError, OSError, ValueError) as e:
            self.logger.error("Failed to connect to Socket.IO server: %s", e)
            return False

    async def disconnect(self) -> None:
        """Disconnect from Socket.IO server."""
        try:
            if self.bot.sio and self.bot.sio.connected:
                await self.bot.sio.disconnect()
                self.logger.info("Disconnected from Socket.IO server")
        except (ConnectionError, OSError, AttributeError) as e:
            self.logger.error("Error during Socket.IO disconnect: %s", e)

    async def cleanup(self) -> None:
        """Cleanup Socket.IO client."""
        try:
            self.event_handlers.cleanup_handlers()
            await self.disconnect()
        except (AttributeError, ConnectionError) as e:
            self.logger.error("Error during cleanup: %s", e)
