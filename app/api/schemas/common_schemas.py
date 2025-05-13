from enum import Enum

class IntegrationType(str, Enum):
    TELEGRAM = "telegram"
    # Add other integration types here

class SenderType(str, Enum):
    USER = "user"
    AGENT = "agent"
    SYSTEM = "system" # Для возможных ошибок/уведомлений
