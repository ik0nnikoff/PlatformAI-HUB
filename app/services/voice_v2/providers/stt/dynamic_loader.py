"""
Simplified STT Provider Loading - Phase 3.1.3 Implementation

Basic factory pattern for STT provider creation.
Removes enterprise patterns: hot-reload, health monitoring, lazy proxies.
Preserves essential functionality: provider registration, basic error handling.
"""

import logging
from typing import Dict, Optional, Any, Type
from enum import Enum

from .base_stt import BaseSTTProvider

logger = logging.getLogger(__name__)


class LoadingStrategy(str, Enum):
    """Simplified loading strategies"""
    LAZY = "lazy"
    EAGER = "eager"


class ProviderLoadingConfig:
    """Simple loading configuration"""
    def __init__(
        self,
        strategy: LoadingStrategy = LoadingStrategy.LAZY,
        load_timeout: int = 30
    ):
        self.strategy = strategy
        self.load_timeout = load_timeout


class SimplifiedSTTProviderFactory:
    """
    Simplified STT provider factory.

    Reduces 518 → ~100 lines by removing:
    - Health monitoring loops
    - Hot-reload mechanisms
    - Lazy proxy patterns
    - Complex management layers

    Preserves:
    - Basic provider instantiation
    - Simple error handling
    - Provider registration
    """

    def __init__(self):
        self._provider_classes: Dict[str, Type[BaseSTTProvider]] = {}
        self._provider_configs: Dict[str, Dict[str, Any]] = {}
        self._logger = logger

    def register_provider(
        self,
        name: str,
        provider_class: Type[BaseSTTProvider],
        config: Optional[Dict[str, Any]] = None
    ) -> None:
        """Register STT provider class."""
        self._provider_classes[name] = provider_class
        self._provider_configs[name] = config or {}
        self._logger.info(f"Registered STT provider: {name}")

    def create_provider(self, name: str, config: Optional[Dict[str, Any]] = None) -> BaseSTTProvider:
        """Create STT provider instance."""
        if name not in self._provider_classes:
            raise ValueError(f"Unknown STT provider: {name}")

        provider_class = self._provider_classes[name]
        provider_config = {**self._provider_configs[name]}

        if config:
            provider_config.update(config)

        try:
            provider = provider_class(
                provider_name=name,
                config=provider_config
            )
            self._logger.info(f"Created STT provider: {name}")
            return provider

        except Exception as e:
            self._logger.error(f"Failed to create STT provider {name}: {e}")
            raise

    def get_available_providers(self) -> list[str]:
        """Get list of available provider names."""
        return list(self._provider_classes.keys())

    def cleanup(self) -> None:
        """Cleanup factory resources."""
        self._provider_classes.clear()
        self._provider_configs.clear()
        self._logger.info("STT provider factory cleanup completed")


# Backward compatibility aliases
STTProviderManager = SimplifiedSTTProviderFactory
LazySTTProviderProxy = None  # Removed enterprise pattern


__all__ = [
    'LoadingStrategy',
    'ProviderLoadingConfig',
    'SimplifiedSTTProviderFactory',
    'STTProviderManager'  # Backward compatibility
]
