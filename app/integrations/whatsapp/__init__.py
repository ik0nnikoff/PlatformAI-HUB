"""
WhatsApp Integration для PlatformAI-HUB

Реорганизованная архитектура с четким разделением ответственности:
- core/ - основная бизнес-логика
- infrastructure/ - внешние интеграции
- processors/ - специализированные процессоры
- handlers/ - обработчики событий (legacy)

Интеграция с wppconnect-server для подключения агентов к WhatsApp мессенджеру.
Поддерживает получение сообщений в реальном времени через Socket.IO
и отправку ответов через HTTP API.
"""

from .core.message_coordinator import MessageCoordinator
from .core.redis_service import RedisService
from .core.user_service import UserService
from .infrastructure.api_client import WhatsAppAPIClient
from .infrastructure.socketio_client import SocketIOClient
from .whatsapp_bot import WhatsAppIntegrationBot

__version__ = "2.0.0"
__all__ = [
    "WhatsAppIntegrationBot",
    "UserService",
    "RedisService",
    "MessageCoordinator",
    "WhatsAppAPIClient",
    "SocketIOClient",
]
