"""
🏭 Provider Factory for voice_v2 System

Implements Factory Pattern with SOLID principles for STT/TTS provider creation.
Based on Phase_1_2_2_solid_principles.md - Interface Segregation & Dependency Inversion.

This module provides type-safe, configuration-driven provider instantiation 
with proper dependency injection and error handling.
"""

import logging
from typing import Dict, Type, Optional, List, Any
from abc import ABC, abstractmethod

from app.services.voice_v2.core.exceptions import (
    VoiceServiceError,
    ProviderInitializationError,
    ProviderNotFoundError,
    ConfigurationError
)
from app.services.voice_v2.core.interfaces import (
    ISTTProvider,
    ITTSProvider,
    ProviderType,
    VoiceProviderConfig
)
from app.services.voice_v2.core.config import VoiceV2Settings

# STT Providers
from app.services.voice_v2.providers.stt.base_stt import BaseSTTProvider
from app.services.voice_v2.providers.stt.openai_stt import OpenAISTTProvider
from app.services.voice_v2.providers.stt.google_stt import GoogleSTTProvider
from app.services.voice_v2.providers.stt.yandex_stt import YandexSTTProvider

# TTS Providers (будут добавлены позже)
# from app.services.voice_v2.providers.tts.base_tts import BaseTTSProvider


logger = logging.getLogger(__name__)


class IProviderFactory(ABC):
    """
    Interface for provider factories - Interface Segregation Principle
    
    Based on Phase_1_2_2_solid_principles.md - ISP implementation.
    """
    
    @abstractmethod
    async def create_stt_provider(
        self, 
        provider_type: str, 
        config: VoiceProviderConfig
    ) -> ISTTProvider:
        """Create STT provider instance"""
        pass
    
    @abstractmethod
    async def create_tts_provider(
        self, 
        provider_type: str, 
        config: VoiceProviderConfig
    ) -> ITTSProvider:
        """Create TTS provider instance"""
        pass
    
    @abstractmethod
    def get_available_stt_providers(self) -> List[str]:
        """Get list of available STT providers"""
        pass
    
    @abstractmethod
    def get_available_tts_providers(self) -> List[str]:
        """Get list of available TTS providers"""
        pass


class ProviderRegistry:
    """
    Provider registry for type-safe provider registration
    
    Based on Phase_1_2_2_solid_principles.md - Single Responsibility Principle.
    This class ONLY manages provider registration/discovery.
    """
    
    def __init__(self):
        self._stt_providers: Dict[str, Type[ISTTProvider]] = {}
        self._tts_providers: Dict[str, Type[ITTSProvider]] = {}
        self._register_default_providers()
    
    def _register_default_providers(self) -> None:
        """Register default STT/TTS providers"""
        try:
            # STT Providers
            self._stt_providers.update({
                "openai": OpenAISTTProvider,
                "google": GoogleSTTProvider,
                "yandex": YandexSTTProvider,
            })
            
            # TTS Providers (будут добавлены в Phase 3.2)
            # self._tts_providers.update({
            #     "openai": OpenAITTSProvider,
            #     "google": GoogleTTSProvider, 
            #     "yandex": YandexTTSProvider,
            # })
            
            logger.debug(f"Registered {len(self._stt_providers)} STT providers")
            logger.debug(f"Registered {len(self._tts_providers)} TTS providers")
            
        except Exception as e:
            logger.error(f"Failed to register default providers: {e}")
            raise ProviderInitializationError(f"Provider registration failed: {e}")
    
    def register_stt_provider(self, name: str, provider_class: Type[ISTTProvider]) -> None:
        """Register custom STT provider - Open/Closed Principle"""
        if not issubclass(provider_class, BaseSTTProvider):
            raise ValueError(f"Provider {name} must inherit from BaseSTTProvider")
        
        self._stt_providers[name] = provider_class
        logger.info(f"Registered custom STT provider: {name}")
    
    def register_tts_provider(self, name: str, provider_class: Type[ITTSProvider]) -> None:
        """Register custom TTS provider - Open/Closed Principle"""
        # Will be implemented in Phase 3.2
        pass
    
    def get_stt_provider(self, name: str) -> Type[ISTTProvider]:
        """Get STT provider class by name"""
        if name not in self._stt_providers:
            available = list(self._stt_providers.keys())
            raise ProviderNotFoundError(
                f"STT provider '{name}' not found. Available: {available}"
            )
        return self._stt_providers[name]
    
    def get_tts_provider(self, name: str) -> Type[ITTSProvider]:
        """Get TTS provider class by name"""
        if name not in self._tts_providers:
            available = list(self._tts_providers.keys())
            raise ProviderNotFoundError(
                f"TTS provider '{name}' not found. Available: {available}"
            )
        return self._tts_providers[name]
    
    def get_available_stt_providers(self) -> List[str]:
        """Get list of available STT providers"""
        return list(self._stt_providers.keys())
    
    def get_available_tts_providers(self) -> List[str]:
        """Get list of available TTS providers"""
        return list(self._tts_providers.keys())


class VoiceProviderFactory(IProviderFactory):
    """
    Main provider factory implementation
    
    Based on Phase_1_2_2_solid_principles.md - Dependency Inversion Principle.
    High-level factory depends on abstractions (IProviderFactory), not concrete classes.
    """
    
    def __init__(self, settings: VoiceV2Settings):
        """
        Initialize factory with settings dependency injection
        
        Args:
            settings: Voice v2 system settings
        """
        self.settings = settings
        self.registry = ProviderRegistry()
        logger.info("VoiceProviderFactory initialized")
    
    async def create_stt_provider(
        self, 
        provider_type: str, 
        config: VoiceProviderConfig
    ) -> ISTTProvider:
        """
        Create STT provider instance with proper configuration
        
        Based on Phase_1_1_4_architecture_patterns.md - Provider instantiation patterns.
        
        Args:
            provider_type: Type of STT provider ("openai", "google", "yandex")
            config: Provider configuration
            
        Returns:
            Configured STT provider instance
            
        Raises:
            ProviderNotFoundError: If provider type not supported
            ProviderInitializationError: If provider initialization fails
            ConfigurationError: If configuration is invalid
        """
        logger.debug(f"Creating STT provider: {provider_type}")
        
        try:
            # Get provider class from registry
            provider_class = self.registry.get_stt_provider(provider_type)
            
            # Validate configuration
            self._validate_stt_config(provider_type, config)
            
            # Create provider instance with dependency injection
            provider_instance = provider_class(
                config=config,
                settings=self.settings,
                logger=logger.getChild(f"stt.{provider_type}")
            )
            
            # Initialize provider
            await provider_instance.initialize()
            
            logger.info(f"Successfully created STT provider: {provider_type}")
            return provider_instance
            
        except ProviderNotFoundError:
            raise
        except Exception as e:
            logger.error(f"Failed to create STT provider {provider_type}: {e}")
            raise ProviderInitializationError(
                f"STT provider {provider_type} initialization failed: {e}"
            )
    
    async def create_tts_provider(
        self, 
        provider_type: str, 
        config: VoiceProviderConfig
    ) -> ITTSProvider:
        """
        Create TTS provider instance with proper configuration
        
        Will be implemented in Phase 3.2 - TTS providers
        """
        logger.warning("TTS provider creation not yet implemented")
        raise NotImplementedError("TTS providers will be implemented in Phase 3.2")
    
    def get_available_stt_providers(self) -> List[str]:
        """Get list of available STT providers"""
        return self.registry.get_available_stt_providers()
    
    def get_available_tts_providers(self) -> List[str]:
        """Get list of available TTS providers"""
        return self.registry.get_available_tts_providers()
    
    def _validate_stt_config(self, provider_type: str, config: VoiceProviderConfig) -> None:
        """
        Validate STT provider configuration
        
        Based on Phase_1_2_2_solid_principles.md - Input validation patterns.
        """
        if not config:
            raise ConfigurationError(f"Configuration required for {provider_type} STT provider")
        
        # Provider-specific validation
        if provider_type == "openai":
            required_fields = ["api_key"]
        elif provider_type == "google":
            required_fields = ["project_id", "credentials_path"]
        elif provider_type == "yandex":
            required_fields = ["api_key", "folder_id"]
        else:
            raise ProviderNotFoundError(f"Unknown STT provider: {provider_type}")
        
        # Check required fields
        for field in required_fields:
            if not config.get(field):
                raise ConfigurationError(
                    f"Missing required field '{field}' for {provider_type} STT provider"
                )
        
        logger.debug(f"Configuration validated for {provider_type} STT provider")


class ProviderManager:
    """
    High-level provider manager for orchestrator integration
    
    Based on Phase_1_2_2_solid_principles.md - Single Responsibility Principle.
    This class ONLY manages provider lifecycle and fallback logic.
    """
    
    def __init__(self, factory: IProviderFactory, settings: VoiceV2Settings):
        """
        Initialize provider manager with factory dependency injection
        
        Args:
            factory: Provider factory for creating instances
            settings: Voice v2 system settings
        """
        self.factory = factory
        self.settings = settings
        self._active_stt_providers: List[ISTTProvider] = []
        self._active_tts_providers: List[ITTSProvider] = []
        logger.info("ProviderManager initialized")
    
    async def initialize_stt_providers(
        self, 
        provider_configs: List[Dict[str, Any]]
    ) -> None:
        """
        Initialize STT providers based on configuration
        
        Based on Phase_1_1_4_architecture_patterns.md - Fallback chain patterns.
        
        Args:
            provider_configs: List of provider configurations with priority
        """
        logger.info(f"Initializing {len(provider_configs)} STT providers")
        
        for config_dict in provider_configs:
            try:
                provider_type = config_dict.get("provider")
                provider_config = VoiceProviderConfig(config_dict.get("config", {}))
                
                provider = await self.factory.create_stt_provider(
                    provider_type, 
                    provider_config
                )
                
                self._active_stt_providers.append(provider)
                logger.info(f"Initialized STT provider: {provider_type}")
                
            except Exception as e:
                logger.error(f"Failed to initialize STT provider {config_dict}: {e}")
                # Continue with other providers - fallback logic
                continue
        
        if not self._active_stt_providers:
            raise ProviderInitializationError("No STT providers could be initialized")
        
        logger.info(f"Successfully initialized {len(self._active_stt_providers)} STT providers")
    
    async def initialize_tts_providers(
        self, 
        provider_configs: List[Dict[str, Any]]
    ) -> None:
        """
        Initialize TTS providers based on configuration
        
        Will be implemented in Phase 3.2
        """
        logger.warning("TTS provider initialization not yet implemented")
    
    def get_stt_providers(self) -> List[ISTTProvider]:
        """Get active STT providers in priority order"""
        return self._active_stt_providers.copy()
    
    def get_tts_providers(self) -> List[ITTSProvider]:
        """Get active TTS providers in priority order"""
        return self._active_tts_providers.copy()
    
    async def cleanup(self) -> None:
        """Cleanup all provider resources"""
        logger.info("Cleaning up provider resources")
        
        # Cleanup STT providers
        for provider in self._active_stt_providers:
            try:
                await provider.cleanup()
            except Exception as e:
                logger.error(f"Error cleaning up STT provider: {e}")
        
        # Cleanup TTS providers  
        for provider in self._active_tts_providers:
            try:
                await provider.cleanup()
            except Exception as e:
                logger.error(f"Error cleaning up TTS provider: {e}")
        
        self._active_stt_providers.clear()
        self._active_tts_providers.clear()
        logger.info("Provider cleanup completed")


# Factory instance creation helper
def create_provider_factory(settings: VoiceV2Settings) -> VoiceProviderFactory:
    """
    Create provider factory instance
    
    Helper function for dependency injection in orchestrator.
    Based on Phase_1_2_2_solid_principles.md - Dependency Inversion patterns.
    """
    return VoiceProviderFactory(settings)


def create_provider_manager(
    settings: VoiceV2Settings, 
    factory: Optional[IProviderFactory] = None
) -> ProviderManager:
    """
    Create provider manager instance
    
    Helper function for orchestrator integration.
    """
    if factory is None:
        factory = create_provider_factory(settings)
    
    return ProviderManager(factory, settings)
