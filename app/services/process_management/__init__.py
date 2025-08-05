"""
Process Management Package.

Модульная система управления процессами агентов и интеграций.
Разделена на специализированные компоненты для лучшей архитектуры.
"""

# Основные классы для внешнего использования
from .coordinator import ProcessManager, ProcessLifecycleCoordinator
from .agent_manager import AgentProcessManager
from .integration_manager import IntegrationProcessManager
from .status_manager import ProcessStatusManager
from .lifecycle_manager import ProcessLifecycleManager

# Конфигурация и базовые классы
from .config import ProcessConfiguration, PathResolver, ProcessDefaults
from .base import (
    ProcessManagerBase, ProcessInfo, AgentInfo, IntegrationInfo,
    AgentStatusInfo, IntegrationStatusInfo, IntegrationTypeStr
)

# Исключения
from .exceptions import (
    ProcessManagerError,
    ProcessStartupError,
    ProcessTerminationError,
    ProcessNotFoundError,
    AgentProcessError,
    IntegrationProcessError
)

# Публичный API - экспортируем основные компоненты
__all__ = [
    # Main ProcessManager class
    'ProcessManager',

    # Configuration
    'ProcessConfiguration',

    # Exceptions
    'ProcessManagerError',
    'ProcessStartupError',
    'ProcessTerminationError',
    'ProcessNotFoundError',
    'AgentProcessError',
    'IntegrationProcessError',

    # Base classes for advanced usage
    'ProcessManagerBase',
    'ProcessStatusManager',
    'ProcessLifecycleManager',
    'AgentProcessManager',
    'IntegrationProcessManager',

    # Data classes
    'ProcessInfo',
    'AgentInfo',
    'IntegrationInfo'
]
