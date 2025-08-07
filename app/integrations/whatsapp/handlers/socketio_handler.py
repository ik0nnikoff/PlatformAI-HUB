"""
WhatsApp Socket.IO Event Handlers

Модуль для обработки событий Socket.IO wppconnect-server
"""


class SocketIOEventHandlers:
    """Handles Socket.IO events for WhatsApp integration"""

    def __init__(self, bot_instance):
        """
        Initialize Socket.IO event handlers

        Args:
            bot_instance: Reference to main WhatsAppIntegrationBot instance
        """
        self.bot = bot_instance
        self.logger = bot_instance.logger

    def setup_handlers(self):
        """Setup all Socket.IO event handlers"""
        if not self.bot.sio:
            self.logger.error("Socket.IO client not initialized")
            return

        self._setup_connection_handlers()
        self._setup_message_handlers()
        self._setup_status_handlers()
        self._setup_diagnostic_handlers()

    def cleanup_handlers(self):
        """Cleanup and remove Socket.IO event handlers"""
        if self.bot.sio:
            self.logger.debug("Cleaning up Socket.IO event handlers")
            # Socket.IO client will handle cleanup automatically
            # when connection is closed

    def _setup_connection_handlers(self):
        """Setup connection-related event handlers"""

        @self.bot.sio.event
        async def connect():
            self.logger.info("Connected to wppconnect-server Socket.IO")
            self.bot.reconnect_attempts = 0

        @self.bot.sio.event
        async def disconnect():
            self.logger.warning("Disconnected from wppconnect-server Socket.IO")

        @self.bot.sio.event
        async def connect_error(data):
            self.logger.error(f"Socket.IO connection error: {data}")

    def _setup_message_handlers(self):
        """Setup message-related event handlers"""

        async def received_message_hyphen(data):
            """Обработка полученных сообщений от WhatsApp"""
            response = data.get("response", {})
            body = response.get("content") or response.get("body", "")
            from_me = response.get("fromMe", False)
            chat_id = response.get("chatId") or response.get("from", "")
            body_preview = f"{body[:50]}..." if len(body) > 50 else body
            self.logger.info(
                "Received message: chat=%s, fromMe=%s, body='%s'",
                chat_id,
                from_me,
                body_preview,
            )
            await self.bot._handle_received_message(data)

        # Регистрируем обработчик для события с дефисом
        self.bot.sio.on("received-message", received_message_hyphen)

    def _setup_status_handlers(self):
        """Setup status-related event handlers"""

        @self.bot.sio.event
        async def whatsapp_status(data):
            """Обработка изменения статуса WhatsApp"""
            self.logger.info(f"WhatsApp status changed: {data}")

        @self.bot.sio.event
        async def session_logged(data):
            """Обработка успешного подключения сессии"""
            self.logger.info(f"Session logged in: {data}")
            await self.bot.mark_as_running()

    def _setup_diagnostic_handlers(self):
        """Setup diagnostic event handlers"""

        async def catch_all_events(event, data):
            """Ловит все события для диагностики"""
            if event in ["onack", "mensagem-enviada"]:
                await self._log_status_event(event, data)
            elif event == "received-message":
                await self._handle_message_event(data)
            else:
                self.logger.debug(f"[DIAGNOSTIC] Event '{event}' received")

        # Регистрируем универсальный обработчик
        self.bot.sio.on("*", catch_all_events)

    async def _log_status_event(self, event: str, data):
        """Log status events with minimal info"""
        msg_data = self._extract_message_data(data)
        ack = msg_data.get("ack", "unknown")
        body = msg_data.get("body", "")
        body_preview = f"{body[:30]}..." if len(body) > 30 else body
        self.logger.info("[%s] ack=%s, body='%s'", event, ack, body_preview)

    async def _handle_message_event(self, data):
        """Handle received message events"""
        if isinstance(data, dict):
            await self.bot._handle_received_message(data)
        else:
            self.logger.warning(
                "Received message event with non-dict data: %s", type(data)
            )

    def _extract_message_data(self, data) -> dict:
        """Extract message data from various formats"""
        if isinstance(data, list) and len(data) > 0:
            return data[0] if isinstance(data[0], dict) else {}
        return data if isinstance(data, dict) else {}
