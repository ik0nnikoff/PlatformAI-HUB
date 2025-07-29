"""
Enhanced Voice Provider Factory Module

Разделенная архитектура factory для улучшения поддерживаемости:
- types.py: Enums и базовые типы
- models.py: Dataclass модели (ProviderInfo, ProviderHealthInfo)
- interfaces.py: Абстрактные интерфейсы
- factory.py: Основная реализация EnhancedVoiceProviderFactory
"""

from .types import ProviderCategory, ProviderType, ProviderStatus
from .models import ProviderHealthInfo, ProviderInfo
from .interfaces import IEnhancedProviderFactory
from .factory import EnhancedVoiceProviderFactory

__all__ = [
    "ProviderCategory",
    "ProviderType",
    "ProviderStatus",
    "ProviderHealthInfo",
    "ProviderInfo",
    "IEnhancedProviderFactory",
    "EnhancedVoiceProviderFactory",
]
