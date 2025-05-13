from enum import Enum
from typing import Optional # Добавлено
from pydantic import BaseModel # Добавлено

class IntegrationType(str, Enum):
    TELEGRAM = "telegram"
    # Add other integration types here

class SenderType(str, Enum):
    USER = "user"
    AGENT = "agent"
    SYSTEM = "system" # Для возможных ошибок/уведомлений
