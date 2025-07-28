"""
TTS Provider Factory для Voice_v2 - Phase 3.2.5

Применяет архитектурные принципы из Phase 1.3:
- Provider Factory Pattern (Phase_1_1_4_architecture_patterns.md)
- SOLID principles с Interface Segregation (Phase_1_2_2_solid_principles.md)
- Performance patterns с lazy loading (Phase_1_2_3_performance_optimization.md)
- LSP compliance для provider interfaces (Phase_1_3_1_architecture_review.md)

SOLID Principles Implementation:
- Single Responsibility: Только создание и управление TTS providers
- Open/Closed: Extensible для новых providers без модификации
- Liskov Substitution: Все providers взаимозаменяемы через BaseTTSProvider
- Interface Segregation: Минимальный factory interface
- Dependency Inversion: Зависит от абстракций, не конкретных реализаций

Архитектурные Patterns:
- Factory Pattern с dynamic loading
- Provider registry для efficient management
- Configuration-based initialization
- Error handling с fallback mechanisms
- Health monitoring integration
"""

import asyncio
import logging
from typing import Dict, List, Optional, Type, Any
from abc import ABC, abstractmethod

from .base_tts import BaseTTSProvider
from .openai_tts import OpenAITTSProvider
from .google_tts import GoogleTTSProvider
from .yandex_tts import YandexTTSProvider
from ...core.exceptions import VoiceServiceError, ProviderNotAvailableError
from ...core.interfaces import ProviderType

logger = logging.getLogger(__name__)


class TTSProviderFactory:
    """
    TTS Provider Factory - Phase 3.2.5
    
    Архитектурные принципы (Phase 1.3):
    - Factory Pattern: Dynamic provider creation и management
    - SOLID: Single responsibility для provider instantiation
    - Performance: Lazy initialization и provider caching
    - LSP: Все providers взаимозаменяемы через BaseTTSProvider interface
    """
    
    # Provider registry mapping
    PROVIDER_REGISTRY: Dict[str, Type[BaseTTSProvider]] = {
        "openai": OpenAITTSProvider,
        "google": GoogleTTSProvider,
        "yandex": YandexTTSProvider
    }
    
    def __init__(self):
        """Initialize TTS Provider Factory."""
        self._provider_cache: Dict[str, BaseTTSProvider] = {}
        self._provider_configs: Dict[str, Dict[str, Any]] = {}
        self._initialized = False
        
        logger.debug("TTSProviderFactory initialized")
    
    async def initialize(self, providers_config: List[Dict[str, Any]]) -> None:
        """
        Initialize factory with provider configurations.
        
        Args:
            providers_config: List of provider configurations
                [
                    {
                        "provider": "openai",
                        "config": {"api_key": "...", "voice": "alloy"},
                        "priority": 1,
                        "enabled": true
                    },
                    ...
                ]
        """
        try:
            logger.debug(f"Initializing TTSProviderFactory with {len(providers_config)} providers")
            
            # Clear existing cache
            await self._cleanup_providers()
            
            # Process and validate configurations
            for provider_config in providers_config:
                provider_name = provider_config.get("provider")
                
                if not provider_name:
                    logger.warning("Provider configuration missing 'provider' field, skipping")
                    continue
                
                if provider_name not in self.PROVIDER_REGISTRY:
                    logger.warning(f"Unknown provider '{provider_name}', skipping")
                    continue
                
                # Store configuration for lazy initialization
                cache_key = self._generate_cache_key(provider_name, provider_config)
                self._provider_configs[cache_key] = provider_config
                
                logger.debug(f"Provider '{provider_name}' configuration stored")
            
            self._initialized = True
            logger.info(f"TTSProviderFactory initialized with {len(self._provider_configs)} provider configurations")
            
        except Exception as e:
            logger.error(f"Failed to initialize TTSProviderFactory: {e}", exc_info=True)
            raise VoiceServiceError(f"TTS Factory initialization failed: {e}")
    
    async def create_provider(
        self,
        provider_name: str,
        config: Dict[str, Any],
        priority: int = 1,
        enabled: bool = True
    ) -> Optional[BaseTTSProvider]:
        """
        Create TTS provider instance with validation.
        
        Args:
            provider_name: Provider identifier ("openai", "google", "yandex")
            config: Provider-specific configuration
            priority: Provider priority (lower = higher priority)
            enabled: Whether provider is enabled
            
        Returns:
            BaseTTSProvider instance or None if creation failed
        """
        try:
            logger.debug(f"Creating TTS provider: {provider_name}")
            
            # Validate provider exists
            if provider_name not in self.PROVIDER_REGISTRY:
                raise VoiceServiceError(f"Unknown TTS provider: {provider_name}")
            
            provider_class = self.PROVIDER_REGISTRY[provider_name]
            
            # Create provider instance
            provider = provider_class(
                provider_name=provider_name,
                config=config,
                priority=priority,
                enabled=enabled
            )
            
            # Validate required configuration fields
            await self._validate_provider_config(provider, config)
            
            # Initialize provider
            await provider.initialize()
            
            logger.info(f"TTS provider '{provider_name}' created and initialized successfully")
            return provider
            
        except Exception as e:
            logger.error(f"Failed to create TTS provider '{provider_name}': {e}", exc_info=True)
            return None
    
    async def get_provider(self, provider_config: Dict[str, Any]) -> Optional[BaseTTSProvider]:
        """
        Get provider instance with caching (Performance pattern).
        
        Args:
            provider_config: Provider configuration dictionary
            
        Returns:
            BaseTTSProvider instance or None if unavailable
        """
        try:
            cache_key = self._generate_cache_key(
                provider_config.get("provider", ""),
                provider_config
            )
            
            # Check cache first (Performance optimization)
            if cache_key in self._provider_cache:
                provider = self._provider_cache[cache_key]
                
                # Validate provider is still healthy
                if await self._is_provider_healthy(provider):
                    logger.debug(f"Returning cached provider: {provider.provider_name}")
                    return provider
                else:
                    # Remove unhealthy provider from cache
                    await self._remove_from_cache(cache_key)
            
            # Create new provider instance
            provider = await self.create_provider(
                provider_name=provider_config.get("provider"),
                config=provider_config.get("config", {}),
                priority=provider_config.get("priority", 1),
                enabled=provider_config.get("enabled", True)
            )
            
            if provider:
                # Cache the provider for reuse
                self._provider_cache[cache_key] = provider
                logger.debug(f"Provider '{provider.provider_name}' cached with key: {cache_key}")
            
            return provider
            
        except Exception as e:
            logger.error(f"Failed to get provider: {e}", exc_info=True)
            return None
    
    async def get_available_providers(self, providers_config: List[Dict[str, Any]]) -> List[BaseTTSProvider]:
        """
        Get all available and healthy TTS providers ordered by priority.
        
        Args:
            providers_config: List of provider configurations
            
        Returns:
            List of available BaseTTSProvider instances ordered by priority
        """
        available_providers = []
        
        try:
            logger.debug(f"Getting available providers from {len(providers_config)} configurations")
            
            # Create providers concurrently for performance
            provider_tasks = []
            for config in providers_config:
                if config.get("enabled", True):
                    task = self.get_provider(config)
                    provider_tasks.append(task)
            
            # Wait for all provider creation attempts
            providers = await asyncio.gather(*provider_tasks, return_exceptions=True)
            
            # Filter successful providers
            for provider in providers:
                if isinstance(provider, BaseTTSProvider):
                    # Perform health check
                    if await self._is_provider_healthy(provider):
                        available_providers.append(provider)
                    else:
                        logger.warning(f"Provider {provider.provider_name} failed health check")
                elif isinstance(provider, Exception):
                    logger.warning(f"Provider creation failed: {provider}")
            
            # Sort by priority (lower number = higher priority)
            available_providers.sort(key=lambda p: p.priority)
            
            logger.info(f"Found {len(available_providers)} available TTS providers")
            return available_providers
            
        except Exception as e:
            logger.error(f"Failed to get available providers: {e}", exc_info=True)
            return []
    
    async def get_provider_capabilities(self, provider_name: str) -> Optional[Dict[str, Any]]:
        """
        Get provider capabilities without full initialization.
        
        Args:
            provider_name: Provider identifier
            
        Returns:
            Provider capabilities dictionary or None
        """
        try:
            if provider_name not in self.PROVIDER_REGISTRY:
                return None
            
            provider_class = self.PROVIDER_REGISTRY[provider_name]
            
            # Create temporary instance for capabilities query with dummy config
            dummy_config = {}
            # Add required fields with dummy values to avoid validation errors
            if provider_name == "openai":
                dummy_config = {"api_key": "dummy"}
            elif provider_name == "google":
                dummy_config = {"credentials_path": "dummy"}
            elif provider_name == "yandex":
                dummy_config = {"api_key": "dummy", "folder_id": "dummy"}
            
            temp_provider = provider_class(
                provider_name=provider_name,
                config=dummy_config,
                priority=1,
                enabled=True
            )
            
            capabilities = await temp_provider.get_capabilities()
            return capabilities.dict() if capabilities else None
            
        except Exception as e:
            logger.error(f"Failed to get capabilities for '{provider_name}': {e}")
            return None
    
    async def health_check_all_providers(self) -> Dict[str, bool]:
        """
        Perform health check on all cached providers.
        
        Returns:
            Dictionary mapping provider names to health status
        """
        health_status = {}
        
        try:
            health_tasks = []
            provider_names = []
            
            for cache_key, provider in self._provider_cache.items():
                task = self._is_provider_healthy(provider)
                health_tasks.append(task)
                provider_names.append(provider.provider_name)
            
            if health_tasks:
                results = await asyncio.gather(*health_tasks, return_exceptions=True)
                
                for provider_name, result in zip(provider_names, results):
                    if isinstance(result, bool):
                        health_status[provider_name] = result
                    else:
                        health_status[provider_name] = False
                        logger.error(f"Health check failed for {provider_name}: {result}")
            
            logger.debug(f"Health check completed for {len(health_status)} providers")
            return health_status
            
        except Exception as e:
            logger.error(f"Failed to perform health check: {e}", exc_info=True)
            return {}
    
    def get_supported_providers(self) -> List[str]:
        """
        Get list of supported provider names.
        
        Returns:
            List of supported provider identifiers
        """
        return list(self.PROVIDER_REGISTRY.keys())
    
    async def cleanup(self) -> None:
        """Clean up factory resources and cached providers."""
        try:
            logger.debug("Cleaning up TTSProviderFactory")
            
            await self._cleanup_providers()
            self._provider_configs.clear()
            self._initialized = False
            
            logger.info("TTSProviderFactory cleanup completed")
            
        except Exception as e:
            logger.error(f"Failed to cleanup TTSProviderFactory: {e}", exc_info=True)
    
    # Private helper methods
    
    def _generate_cache_key(self, provider_name: str, config: Dict[str, Any]) -> str:
        """Generate cache key for provider instance."""
        import hashlib
        import json
        
        # Create deterministic key based on provider and config
        config_str = json.dumps({
            "provider": provider_name,
            "priority": config.get("priority", 1),
            "enabled": config.get("enabled", True),
            # Include relevant config fields for caching
            "config_hash": hash(str(sorted(config.get("config", {}).items())))
        }, sort_keys=True)
        
        return hashlib.md5(config_str.encode()).hexdigest()[:16]
    
    async def _validate_provider_config(self, provider: BaseTTSProvider, config: Dict[str, Any]) -> None:
        """Validate provider configuration has required fields."""
        required_fields = provider.get_required_config_fields()
        provider_config = config  # config is already the full config dict, not nested
        
        missing_fields = [field for field in required_fields if field not in provider_config]
        if missing_fields:
            raise VoiceServiceError(f"Missing required config fields for {provider.provider_name}: {missing_fields}")
    
    async def _is_provider_healthy(self, provider: BaseTTSProvider) -> bool:
        """Check if provider is healthy and available."""
        try:
            return await provider.health_check()
        except Exception as e:
            logger.warning(f"Health check failed for {provider.provider_name}: {e}")
            return False
    
    async def _remove_from_cache(self, cache_key: str) -> None:
        """Remove provider from cache and cleanup resources."""
        if cache_key in self._provider_cache:
            provider = self._provider_cache[cache_key]
            try:
                await provider.cleanup()
            except Exception as e:
                logger.warning(f"Failed to cleanup provider during cache removal: {e}")
            
            del self._provider_cache[cache_key]
            logger.debug(f"Provider removed from cache: {cache_key}")
    
    async def _cleanup_providers(self) -> None:
        """Clean up all cached providers."""
        cleanup_tasks = []
        
        for cache_key, provider in self._provider_cache.items():
            task = provider.cleanup()
            cleanup_tasks.append(task)
        
        if cleanup_tasks:
            await asyncio.gather(*cleanup_tasks, return_exceptions=True)
        
        self._provider_cache.clear()
        logger.debug("All cached providers cleaned up")


# Factory instance for module-level access
tts_factory = TTSProviderFactory()


async def get_tts_provider(provider_config: Dict[str, Any]) -> Optional[BaseTTSProvider]:
    """
    Convenience function to get TTS provider instance.
    
    Args:
        provider_config: Provider configuration
        
    Returns:
        BaseTTSProvider instance or None
    """
    return await tts_factory.get_provider(provider_config)


async def get_available_tts_providers(providers_config: List[Dict[str, Any]]) -> List[BaseTTSProvider]:
    """
    Convenience function to get all available TTS providers.
    
    Args:
        providers_config: List of provider configurations
        
    Returns:
        List of available TTS providers ordered by priority
    """
    return await tts_factory.get_available_providers(providers_config)
