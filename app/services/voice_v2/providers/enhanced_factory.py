"""
Enhanced Voice V2 Provider Factory - Модульная архитектура

Этот файл служит backward compatibility bridge для новой модульной архитектуры.
Основная реализация теперь разделена на специализированные модули в factory/.

Модульная структура:
- factory/types.py: Enums и базовые типы (ProviderCategory, ProviderType, ProviderStatus)
- factory/models.py: Dataclass модели (ProviderInfo, ProviderHealthInfo)  
- factory/interfaces.py: Абстрактные интерфейсы (IEnhancedProviderFactory)
- factory/factory.py: Основная реализация (EnhancedVoiceProviderFactory)

Преимущества модульного подхода:
- Соблюдение Single Responsibility Principle
- Упрощение тестирования и сопровождения
- Уменьшение coupling между компонентами
- Лучшая читаемость и навигация по коду
"""

# Re-export all components from modular structure for backward compatibility
from .factory import (
    # Core factory implementation
    EnhancedVoiceProviderFactory,
    
    # Type definitions
    ProviderCategory,
    ProviderType, 
    ProviderStatus,
    
    # Data models
    ProviderInfo,
    ProviderHealthInfo,
    
    # Interfaces
    IEnhancedProviderFactory,
)

# Maintain backward compatibility - export the same interface
__all__ = [
    "EnhancedVoiceProviderFactory",
    "ProviderCategory",
    "ProviderType",
    "ProviderStatus", 
    "ProviderInfo",
    "ProviderHealthInfo",
    "IEnhancedProviderFactory",
]