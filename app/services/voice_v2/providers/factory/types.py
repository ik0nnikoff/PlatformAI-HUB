"""
Provider Types and Enums for Enhanced Voice Provider Factory
Phase 3.5.3.2 - File splitting для улучшения поддерживаемости
"""

from enum import Enum


class ProviderCategory(Enum):
    """Provider categories для type-safe categorization"""
    STT = "stt"
    TTS = "tts"


class ProviderType(Enum):
    """Конкретные типы провайдеров"""
    OPENAI = "openai"
    GOOGLE = "google"
    YANDEX = "yandex"
    CUSTOM = "custom"
    EXPERIMENTAL = "experimental"


class ProviderStatus(Enum):
    """Provider health status enumeration"""
    HEALTHY = "healthy"
    ACTIVE = "active"
    INACTIVE = "inactive"
    ERROR = "error"
    UNAVAILABLE = "unavailable"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"
    UNKNOWN = "unknown"
