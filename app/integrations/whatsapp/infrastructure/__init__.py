"""
Infrastructure layer for WhatsApp integration.

This module contains external service integrations:
- API clients
- Socket.IO communication
- Media orchestrators
"""

from .api_client import WhatsAppAPIClient
from .socketio_client import SocketIOClient

__all__ = [
    "WhatsAppAPIClient",
    "SocketIOClient",
]
