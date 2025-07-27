"""
Voice_v2 Dependency Injection Factory

Following SOLID principles:
- Dependency Inversion: High-level modules depend on abstractions
- Single Responsibility: Factory for creating and wiring dependencies
- Open/Closed: Extensible for new providers without modification

Design patterns:
- Factory pattern for provider instantiation
- Dependency injection container
- Configuration-driven creation
- Type-safe provider registration
"""

from typing import Dict, List, Optional, Type, Any, cast
import logging

from .interfaces import (
    ProviderType, FullSTTProvider, FullTTSProvider, 
    CacheInterface, FileManagerInterface, MetricsCollector
)
from .config import VoiceConfig, get_config
from .orchestrator import VoiceServiceOrchestrator, initialize_orchestrator
from .exceptions import VoiceServiceError, VoiceConfigurationError


logger = logging.getLogger(__name__)


class ProviderRegistry:
    """
    Registry for provider implementations
    
    Single Responsibility: Provider registration and discovery
    Supports dynamic loading of provider implementations
    """
    
    def __init__(self):
        """Initialize empty registry"""
        self._stt_providers: Dict[ProviderType, Type[FullSTTProvider]] = {}
        self._tts_providers: Dict[ProviderType, Type[FullTTSProvider]] = {}
        self._cache_backends: Dict[str, Type[CacheInterface]] = {}
        self._file_backends: Dict[str, Type[FileManagerInterface]] = {}
        self._metrics_backends: Dict[str, Type[MetricsCollector]] = {}
    
    def register_stt_provider(
        self, 
        provider_type: ProviderType, 
        provider_class: Type[FullSTTProvider]
    ) -> None:
        """Register STT provider implementation"""
        self._stt_providers[provider_type] = provider_class
        logger.debug(f"Registered STT provider: {provider_type}")
    
    def register_tts_provider(
        self, 
        provider_type: ProviderType, 
        provider_class: Type[FullTTSProvider]
    ) -> None:
        """Register TTS provider implementation"""
        self._tts_providers[provider_type] = provider_class
        logger.debug(f"Registered TTS provider: {provider_type}")
    
    def register_cache_backend(
        self, 
        backend_name: str, 
        backend_class: Type[CacheInterface]
    ) -> None:
        """Register cache backend implementation"""
        self._cache_backends[backend_name] = backend_class
        logger.debug(f"Registered cache backend: {backend_name}")
    
    def register_file_backend(
        self, 
        backend_name: str, 
        backend_class: Type[FileManagerInterface]
    ) -> None:
        """Register file storage backend implementation"""
        self._file_backends[backend_name] = backend_class
        logger.debug(f"Registered file backend: {backend_name}")
    
    def register_metrics_backend(
        self, 
        backend_name: str, 
        backend_class: Type[MetricsCollector]
    ) -> None:
        """Register metrics collector implementation"""
        self._metrics_backends[backend_name] = backend_class
        logger.debug(f"Registered metrics backend: {backend_name}")
    
    def get_stt_provider_class(self, provider_type: ProviderType) -> Type[FullSTTProvider]:
        """Get STT provider class by type"""
        if provider_type not in self._stt_providers:
            raise VoiceServiceError(f"STT provider not registered: {provider_type}")
        return self._stt_providers[provider_type]
    
    def get_tts_provider_class(self, provider_type: ProviderType) -> Type[FullTTSProvider]:
        """Get TTS provider class by type"""
        if provider_type not in self._tts_providers:
            raise VoiceServiceError(f"TTS provider not registered: {provider_type}")
        return self._tts_providers[provider_type]
    
    def get_cache_backend_class(self, backend_name: str) -> Type[CacheInterface]:
        """Get cache backend class by name"""
        if backend_name not in self._cache_backends:
            raise VoiceServiceError(f"Cache backend not registered: {backend_name}")
        return self._cache_backends[backend_name]
    
    def get_file_backend_class(self, backend_name: str) -> Type[FileManagerInterface]:
        """Get file backend class by name"""
        if backend_name not in self._file_backends:
            raise VoiceServiceError(f"File backend not registered: {backend_name}")
        return self._file_backends[backend_name]
    
    def get_metrics_backend_class(self, backend_name: str) -> Type[MetricsCollector]:
        """Get metrics backend class by name"""
        if backend_name not in self._metrics_backends:
            raise VoiceServiceError(f"Metrics backend not registered: {backend_name}")
        return self._metrics_backends[backend_name]
    
    def list_registered_providers(self) -> Dict[str, List[str]]:
        """List all registered providers for debugging"""
        return {
            "stt_providers": [str(p.value) for p in self._stt_providers.keys()],
            "tts_providers": [str(p.value) for p in self._tts_providers.keys()],
            "cache_backends": list(self._cache_backends.keys()),
            "file_backends": list(self._file_backends.keys()),
            "metrics_backends": list(self._metrics_backends.keys())
        }


class VoiceServiceFactory:
    """
    Main factory for creating voice service components
    
    Responsibilities:
    - Configuration-driven dependency creation
    - Provider instantiation with proper configuration
    - Dependency injection orchestration
    - Resource lifecycle management
    
    Dependency Inversion: Creates abstractions, not concrete classes
    """
    
    def __init__(self, config: Optional[VoiceConfig] = None, registry: Optional[ProviderRegistry] = None):
        """
        Initialize factory
        
        Args:
            config: Voice configuration (defaults to global config)
            registry: Provider registry (defaults to global registry)
        """
        self._config = config or get_config()
        self._registry = registry or get_global_registry()
        self._logger = logging.getLogger("voice_v2.factory")
        
    async def create_orchestrator(self) -> VoiceServiceOrchestrator:
        """
        Create fully configured orchestrator with all dependencies
        
        Returns:
            Initialized VoiceServiceOrchestrator instance
            
        Raises:
            VoiceServiceError: If required providers not available
            VoiceConfigurationError: If configuration is invalid
        """
        self._logger.info("Creating voice service orchestrator...")
        
        try:
            # Create all dependencies
            stt_providers = await self._create_stt_providers()
            tts_providers = await self._create_tts_providers()
            cache_manager = await self._create_cache_manager()
            file_manager = await self._create_file_manager()
            
            # Initialize orchestrator with dependencies
            orchestrator = await initialize_orchestrator(
                stt_providers=stt_providers,
                tts_providers=tts_providers,
                cache_manager=cache_manager,
                file_manager=file_manager,
                config=self._config
            )
            
            self._logger.info("Voice service orchestrator created successfully")
            return orchestrator
            
        except Exception as e:
            self._logger.error(f"Failed to create orchestrator: {e}")
            raise VoiceServiceError(f"Orchestrator creation failed: {str(e)}")
    
    async def _create_stt_providers(self) -> Dict[ProviderType, FullSTTProvider]:
        """Create and configure STT providers"""
        providers = {}
        
        for provider_type, config in self._config.stt_providers.items():
            if not config.enabled:
                self._logger.debug(f"Skipping disabled STT provider: {provider_type}")
                continue
            
            try:
                provider_class = self._registry.get_stt_provider_class(provider_type)
                provider_instance = await self._instantiate_provider(
                    provider_class, 
                    config.dict()
                )
                providers[provider_type] = cast(FullSTTProvider, provider_instance)
                self._logger.debug(f"Created STT provider: {provider_type}")
                
            except Exception as e:
                self._logger.error(f"Failed to create STT provider {provider_type}: {e}")
                if config.get('required', True):
                    raise VoiceServiceError(f"Required STT provider {provider_type} failed to initialize")
        
        if not providers:
            raise VoiceConfigurationError(
                config_field="stt_providers",
                invalid_value="no_enabled_providers",
                reason="At least one STT provider must be enabled"
            )
        
        return providers
    
    async def _create_tts_providers(self) -> Dict[ProviderType, FullTTSProvider]:
        """Create and configure TTS providers"""
        providers = {}
        
        for provider_type, config in self._config.tts_providers.items():
            if not config.enabled:
                self._logger.debug(f"Skipping disabled TTS provider: {provider_type}")
                continue
            
            try:
                provider_class = self._registry.get_tts_provider_class(provider_type)
                provider_instance = await self._instantiate_provider(
                    provider_class, 
                    config.dict()
                )
                providers[provider_type] = cast(FullTTSProvider, provider_instance)
                self._logger.debug(f"Created TTS provider: {provider_type}")
                
            except Exception as e:
                self._logger.error(f"Failed to create TTS provider {provider_type}: {e}")
                if config.get('required', True):
                    raise VoiceServiceError(f"Required TTS provider {provider_type} failed to initialize")
        
        if not providers:
            raise VoiceConfigurationError(
                config_field="tts_providers",
                invalid_value="no_enabled_providers",
                reason="At least one TTS provider must be enabled"
            )
        
        return providers
    
    async def _create_cache_manager(self) -> CacheInterface:
        """Create cache manager based on configuration"""
        backend_name = self._config.cache.backend.value
        
        try:
            cache_class = self._registry.get_cache_backend_class(backend_name)
            cache_instance = await self._instantiate_infrastructure(
                cache_class,
                self._config.cache.dict()
            )
            
            self._logger.debug(f"Created cache manager: {backend_name}")
            return cast(CacheInterface, cache_instance)
            
        except Exception as e:
            self._logger.error(f"Failed to create cache manager {backend_name}: {e}")
            raise VoiceServiceError(f"Cache manager creation failed: {str(e)}")
    
    async def _create_file_manager(self) -> FileManagerInterface:
        """Create file manager based on configuration"""
        backend_name = self._config.file_storage.backend.value
        
        try:
            file_class = self._registry.get_file_backend_class(backend_name)
            file_instance = await self._instantiate_infrastructure(
                file_class,
                self._config.file_storage.dict()
            )
            
            self._logger.debug(f"Created file manager: {backend_name}")
            return cast(FileManagerInterface, file_instance)
            
        except Exception as e:
            self._logger.error(f"Failed to create file manager {backend_name}: {e}")
            raise VoiceServiceError(f"File manager creation failed: {str(e)}")
    
    async def _instantiate_provider(self, provider_class: Type, config: Dict[str, Any]) -> Any:
        """
        Instantiate and initialize a provider
        
        Args:
            provider_class: Provider class to instantiate
            config: Provider configuration
            
        Returns:
            Initialized provider instance
        """
        try:
            # Create instance
            instance = provider_class(config)
            
            # Initialize if it's a voice service
            if hasattr(instance, 'initialize'):
                await instance.initialize()
            
            return instance
            
        except Exception as e:
            self._logger.error(f"Failed to instantiate {provider_class.__name__}: {e}")
            raise
    
    async def _instantiate_infrastructure(self, infra_class: Type, config: Dict[str, Any]) -> Any:
        """
        Instantiate and initialize infrastructure service
        
        Args:
            infra_class: Infrastructure class to instantiate
            config: Service configuration
            
        Returns:
            Initialized service instance
        """
        try:
            # Create instance
            instance = infra_class(config)
            
            # Initialize if needed
            if hasattr(instance, 'initialize'):
                await instance.initialize()
            
            return instance
            
        except Exception as e:
            self._logger.error(f"Failed to instantiate {infra_class.__name__}: {e}")
            raise
    
    def validate_configuration(self) -> bool:
        """
        Validate that all required providers are registered
        
        Returns:
            True if configuration is valid
            
        Raises:
            VoiceConfigurationError: If configuration is invalid
        """
        errors = []
        
        # Check STT providers
        for provider_type in self._config.stt_providers.keys():
            try:
                self._registry.get_stt_provider_class(provider_type)
            except VoiceServiceError:
                errors.append(f"STT provider not registered: {provider_type}")
        
        # Check TTS providers
        for provider_type in self._config.tts_providers.keys():
            try:
                self._registry.get_tts_provider_class(provider_type)
            except VoiceServiceError:
                errors.append(f"TTS provider not registered: {provider_type}")
        
        # Check cache backend
        cache_backend = self._config.cache.backend.value
        try:
            self._registry.get_cache_backend_class(cache_backend)
        except VoiceServiceError:
            errors.append(f"Cache backend not registered: {cache_backend}")
        
        # Check file storage backend
        file_backend = self._config.file_storage.backend.value
        try:
            self._registry.get_file_backend_class(file_backend)
        except VoiceServiceError:
            errors.append(f"File backend not registered: {file_backend}")
        
        if errors:
            raise VoiceConfigurationError(
                config_field="provider_registration",
                invalid_value=errors,
                reason="Required providers not registered in factory"
            )
        
        return True


# Global registry instance
_global_registry: Optional[ProviderRegistry] = None


def get_global_registry() -> ProviderRegistry:
    """Get or create global provider registry"""
    global _global_registry
    
    if _global_registry is None:
        _global_registry = ProviderRegistry()
        # Register default providers will be done in provider modules
    
    return _global_registry


def register_stt_provider(provider_type: ProviderType, provider_class: Type[FullSTTProvider]) -> None:
    """Register STT provider in global registry"""
    registry = get_global_registry()
    registry.register_stt_provider(provider_type, provider_class)


def register_tts_provider(provider_type: ProviderType, provider_class: Type[FullTTSProvider]) -> None:
    """Register TTS provider in global registry"""
    registry = get_global_registry()
    registry.register_tts_provider(provider_type, provider_class)


def register_cache_backend(backend_name: str, backend_class: Type[CacheInterface]) -> None:
    """Register cache backend in global registry"""
    registry = get_global_registry()
    registry.register_cache_backend(backend_name, backend_class)


def register_file_backend(backend_name: str, backend_class: Type[FileManagerInterface]) -> None:
    """Register file storage backend in global registry"""
    registry = get_global_registry()
    registry.register_file_backend(backend_name, backend_class)


def register_metrics_backend(backend_name: str, backend_class: Type[MetricsCollector]) -> None:
    """Register metrics backend in global registry"""
    registry = get_global_registry()
    registry.register_metrics_backend(backend_name, backend_class)


async def create_voice_service(config: Optional[VoiceConfig] = None) -> VoiceServiceOrchestrator:
    """
    Convenience function to create fully configured voice service
    
    Args:
        config: Voice configuration (defaults to global config)
        
    Returns:
        Initialized VoiceServiceOrchestrator
        
    Raises:
        VoiceServiceError: If service creation fails
        VoiceConfigurationError: If configuration is invalid
    """
    factory = VoiceServiceFactory(config)
    
    # Validate configuration first
    factory.validate_configuration()
    
    # Create and return orchestrator
    return await factory.create_orchestrator()


def list_available_providers() -> Dict[str, List[str]]:
    """List all available providers for debugging"""
    registry = get_global_registry()
    return registry.list_registered_providers()
