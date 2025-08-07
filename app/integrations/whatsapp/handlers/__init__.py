"""
WhatsApp integration handlers package

Содержит активные компоненты для обработки Socket.IO операций WhatsApp:
- SocketIOEventHandlers: обработка событий Socket.IO
- VoiceMessageSender: отправка голосовых сообщений

Примечание: MediaHandler мигрирован в processors/ в рамках clean architecture
"""

from .socketio_handler import SocketIOEventHandlers

__all__ = ["SocketIOEventHandlers"]
