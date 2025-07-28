"""
STT Provider Factory для voice_v2 системы

Реализует паттерны:
- Factory Pattern для динамического создания STT провайдеров
- LSP compliance (Phase_1_3_1_architecture_review.md)
- SOLID principles implementation (Phase_1_2_2_solid_principles.md)
- Performance optimization через connection pooling (Phase_1_2_3_performance_optimization.md)
- Orchestrator patterns из app/services/voice (Phase_1_1_4_architecture_patterns.md)
"""

import logging
from typing import Dict, Optional, Type, List, Any
from abc import ABC, abstractmethod
from enum import Enum

from app.services.voice_v2.providers.stt.base_stt import BaseSTTProvider
from app.services.voice_v2.providers.stt.models import ProviderConfiguration, ProviderStatus
from app.core.logging_config import setup_logger


class STTProviderType(str, Enum):
    """Supported STT provider types"""
    OPENAI = "openai"
    GOOGLE = "google"
    YANDEX = "yandex"


class ProviderLoadError(Exception):
    """Provider loading error"""
    pass


class STTProviderRegistry:
    """
    Registry для STT провайдеров
    
    Implements:
    - Open/Closed Principle: легко добавлять новые провайдеры
    - Single Responsibility: только регистрация провайдеров
    """
    
    _providers: Dict[str, Type[BaseSTTProvider]] = {}
    
    @classmethod
    def register(cls, provider_type: str, provider_class: Type[BaseSTTProvider]) -> None:
        """Register STT provider"""
        if not issubclass(provider_class, BaseSTTProvider):
            raise ValueError(f"Provider {provider_class} must inherit from BaseSTTProvider")
        
        cls._providers[provider_type] = provider_class
    
    @classmethod
    def get_provider_class(cls, provider_type: str) -> Type[BaseSTTProvider]:
        """Get provider class by type"""
        if provider_type not in cls._providers:
            raise ProviderLoadError(f"Unknown STT provider type: {provider_type}")
        
        return cls._providers[provider_type]
    
    @classmethod
    def get_available_providers(cls) -> List[str]:
        """Get list of available provider types"""
        return list(cls._providers.keys())


class STTProviderFactory:
    """
    Factory для создания STT провайдеров
    
    Implements:
    - Factory Pattern для dynamic loading
    - Dependency Inversion: зависит от abstractions, не от concrete classes
    - Interface Segregation: разделенные интерфейсы для разных задач
    - Performance optimization через lazy loading
    """
    
    def __init__(self, logger: Optional[logging.Logger] = None):
        """Initialize factory"""
        self.logger = logger or setup_logger("stt_provider_factory")
        self._provider_cache: Dict[str, BaseSTTProvider] = {}
        self._registration_complete = False
        
        # Register providers при первом использовании (lazy loading)
        self._ensure_providers_registered()
    
    def _ensure_providers_registered(self) -> None:
        """Ensure all providers are registered (lazy loading pattern)"""
        if self._registration_complete:
            return
        
        try:
            # Dynamic import для избежания circular dependencies
            from app.services.voice_v2.providers.stt.openai_stt import OpenAISTTProvider
            from app.services.voice_v2.providers.stt.google_stt import GoogleSTTProvider
            from app.services.voice_v2.providers.stt.yandex_stt import YandexSTTProvider
            
            # Register providers
            STTProviderRegistry.register(STTProviderType.OPENAI, OpenAISTTProvider)
            STTProviderRegistry.register(STTProviderType.GOOGLE, GoogleSTTProvider)
            STTProviderRegistry.register(STTProviderType.YANDEX, YandexSTTProvider)
            
            self._registration_complete = True
            self.logger.info("STT providers registered successfully")
            
        except ImportError as e:
            self.logger.error(f"Failed to import STT provider: {e}")
            raise ProviderLoadError(f"Failed to register STT providers: {e}")
    
    async def create_provider(
        self,
        provider_type: str,
        config: ProviderConfiguration,
        cache_key: Optional[str] = None
    ) -> BaseSTTProvider:
        """
        Create STT provider instance
        
        Args:
            provider_type: Type of provider (openai, google, yandex)
            config: Provider configuration
            cache_key: Optional cache key for provider reuse
            
        Returns:
            Initialized STT provider instance
            
        Raises:
            ProviderLoadError: If provider creation fails
        """
        self.logger.debug(f"Creating STT provider: {provider_type}")
        
        try:
            # Check cache first (performance optimization)
            if cache_key and cache_key in self._provider_cache:
                cached_provider = self._provider_cache[cache_key]
                if cached_provider.get_status().status == ProviderStatus.READY:
                    self.logger.debug(f"Returning cached provider: {cache_key}")
                    return cached_provider
            
            # Get provider class from registry
            provider_class = STTProviderRegistry.get_provider_class(provider_type)
            
            # Create provider instance
            provider = provider_class(
                config=config,
                logger=self.logger.getChild(provider_type)
            )
            
            # Initialize provider
            await provider.initialize()
            
            # Cache provider if cache_key provided
            if cache_key:
                self._provider_cache[cache_key] = provider
            
            self.logger.info(f"Successfully created STT provider: {provider_type}")
            return provider
            
        except Exception as e:
            self.logger.error(f"Failed to create STT provider {provider_type}: {e}", exc_info=True)
            raise ProviderLoadError(f"Failed to create provider {provider_type}: {e}")
    
    async def create_providers_from_config(
        self,
        providers_config: List[Dict[str, Any]],
        agent_id: Optional[str] = None
    ) -> List[BaseSTTProvider]:
        """
        Create multiple providers from configuration
        
        Args:
            providers_config: List of provider configurations
            agent_id: Optional agent ID for caching
            
        Returns:
            List of initialized providers
        """
        providers = []
        
        for i, provider_config in enumerate(providers_config):
            try:
                # Extract provider type
                provider_type = provider_config.get("provider", "").lower()
                if not provider_type:
                    self.logger.warning(f"Provider type not specified in config {i}")
                    continue
                
                # Create configuration object
                config = ProviderConfiguration(
                    provider_type=provider_type,
                    settings=provider_config.get("settings", {}),
                    enabled=provider_config.get("enabled", True),
                    priority=provider_config.get("priority", 1)
                )
                
                # Generate cache key
                cache_key = None
                if agent_id:
                    cache_key = f"{agent_id}:{provider_type}:{i}"
                
                # Create provider
                provider = await self.create_provider(
                    provider_type=provider_type,
                    config=config,
                    cache_key=cache_key
                )
                
                providers.append(provider)
                
            except Exception as e:
                self.logger.error(
                    f"Failed to create provider from config {i}: {e}", 
                    exc_info=True
                )
                continue
        
        self.logger.info(f"Created {len(providers)} STT providers")
        return providers
    
    def get_available_provider_types(self) -> List[str]:
        """Get list of available provider types"""
        return STTProviderRegistry.get_available_providers()
    
    async def cleanup_cached_providers(self) -> None:
        """Cleanup cached providers"""
        self.logger.debug("Cleaning up cached STT providers")
        
        for cache_key, provider in self._provider_cache.items():
            try:
                await provider.cleanup()
            except Exception as e:
                self.logger.error(f"Error cleaning up provider {cache_key}: {e}")
        
        self._provider_cache.clear()
        self.logger.info("STT provider cache cleaned up")
    
    def get_cache_stats(self) -> Dict[str, Any]:
        """Get provider cache statistics"""
        stats = {
            "cached_providers": len(self._provider_cache),
            "providers": []
        }
        
        for cache_key, provider in self._provider_cache.items():
            provider_status = provider.get_status()
            stats["providers"].append({
                "cache_key": cache_key,
                "type": provider_status.provider_type,
                "status": provider_status.status.value,
                "error_count": provider_status.error_count
            })
        
        return stats


class STTProviderLoader:
    """
    Dynamic loader для STT провайдеров
    
    Implements:
    - Configuration-based initialization pattern
    - Error handling и fallback mechanisms
    - Performance monitoring
    """
    
    def __init__(self, logger: Optional[logging.Logger] = None):
        """Initialize loader"""
        self.logger = logger or setup_logger("stt_provider_loader")
        self.factory = STTProviderFactory(logger=self.logger)
    
    async def load_providers_for_agent(
        self,
        agent_id: str,
        voice_config: Dict[str, Any]
    ) -> List[BaseSTTProvider]:
        """
        Load STT providers for specific agent
        
        Args:
            agent_id: Agent identifier
            voice_config: Voice configuration from agent settings
            
        Returns:
            List of loaded and initialized providers
        """
        self.logger.debug(f"Loading STT providers for agent {agent_id}")
        
        try:
            # Extract providers configuration
            providers_config = voice_config.get("providers", [])
            if not providers_config:
                self.logger.warning(f"No STT providers configured for agent {agent_id}")
                return []
            
            # Filter enabled STT providers
            stt_configs = [
                config for config in providers_config
                if config.get("enabled", True)
            ]
            
            if not stt_configs:
                self.logger.warning(f"No enabled STT providers for agent {agent_id}")
                return []
            
            # Sort by priority
            stt_configs.sort(key=lambda x: x.get("priority", 1))
            
            # Create providers
            providers = await self.factory.create_providers_from_config(
                providers_config=stt_configs,
                agent_id=agent_id
            )
            
            if not providers:
                self.logger.error(f"Failed to create any STT providers for agent {agent_id}")
                return []
            
            self.logger.info(
                f"Successfully loaded {len(providers)} STT providers for agent {agent_id}"
            )
            return providers
            
        except Exception as e:
            self.logger.error(
                f"Failed to load STT providers for agent {agent_id}: {e}",
                exc_info=True
            )
            return []
    
    async def reload_provider(
        self,
        agent_id: str,
        provider_type: str,
        new_config: Dict[str, Any]
    ) -> Optional[BaseSTTProvider]:
        """
        Reload specific provider with new configuration
        
        Args:
            agent_id: Agent identifier
            provider_type: Type of provider to reload
            new_config: New provider configuration
            
        Returns:
            Reloaded provider or None if failed
        """
        self.logger.debug(f"Reloading STT provider {provider_type} for agent {agent_id}")
        
        try:
            # Create configuration
            config = ProviderConfiguration(
                provider_type=provider_type,
                settings=new_config.get("settings", {}),
                enabled=new_config.get("enabled", True),
                priority=new_config.get("priority", 1)
            )
            
            # Generate cache key
            cache_key = f"{agent_id}:{provider_type}:reload"
            
            # Create new provider
            provider = await self.factory.create_provider(
                provider_type=provider_type,
                config=config,
                cache_key=cache_key
            )
            
            self.logger.info(f"Successfully reloaded provider {provider_type} for agent {agent_id}")
            return provider
            
        except Exception as e:
            self.logger.error(
                f"Failed to reload provider {provider_type} for agent {agent_id}: {e}",
                exc_info=True
            )
            return None


# Export public interface
__all__ = [
    "STTProviderType",
    "STTProviderRegistry", 
    "STTProviderFactory",
    "STTProviderLoader",
    "ProviderLoadError"
]
