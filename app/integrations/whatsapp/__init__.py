"""
WhatsApp Integration для PlatformAI-HUB

Интеграция с wppconnect-server для подключения агентов к WhatsApp мессенджеру.
Поддерживает получение сообщений в реальном времени через Socket.IO
и отправку ответов через HTTP API.
"""

from .whatsapp_bot import WhatsAppIntegrationBot

__version__ = "1.0.0"
__all__ = ["WhatsAppIntegrationBot"]
