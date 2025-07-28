"""
Enhanced Voice V2 Provider Factory - Phase 3.3.1 Implementation

Реализует comprehensive provider factory с полным соблюдением Phase 1.3 архитектурных требований:
- Phase_1_3_1_architecture_review.md → LSP compliance для provider interfaces
- Phase_1_1_4_architecture_patterns.md → успешные patterns из app/services/voice
- Phase_1_2_3_performance_optimization.md → async patterns и connection pooling
- Phase_1_2_2_solid_principles.md → Interface Segregation в provider design

Enhanced Features (Phase 3.3.1):
- Universal provider factory для STT и TTS
- Dynamic provider loading через module paths
- Configuration-based initialization с comprehensive validation
- Provider registry management с metadata
- Health monitoring и provider status tracking
- Performance optimization через instance caching
- Circuit breaker patterns для provider failures
"""

import logging
import asyncio
import importlib
from abc import ABC, abstractmethod
from typing import Dict, Any, Optional, List, Union
from enum import Enum
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from app.services.voice_v2.providers.stt.base_stt import BaseSTTProvider
from app.services.voice_v2.providers.tts.base_tts import BaseTTSProvider
from app.services.voice_v2.core.exceptions import (
    VoiceServiceError,
    ConfigurationError
)

logger = logging.getLogger(__name__)


class ProviderCategory(Enum):
    """Provider categories для type-safe categorization"""
    STT = "stt"
    TTS = "tts"


class ProviderType(Enum):
    """Provider types для enhanced classification"""
    BUILTIN = "builtin"
    CUSTOM = "custom"
    THIRD_PARTY = "third_party"
    EXPERIMENTAL = "experimental"


class ProviderStatus(Enum):
    """Provider health status enumeration"""
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"
    UNKNOWN = "unknown"


@dataclass
class ProviderHealthInfo:
    """Provider health information tracking"""
    status: ProviderStatus = ProviderStatus.UNKNOWN
    last_check: Optional[datetime] = None
    failure_count: int = 0
    consecutive_failures: int = 0
    last_error: Optional[str] = None
    response_time_avg: float = 0.0
    
    def record_success(self, response_time: float) -> None:
        """Record successful operation"""
        self.status = ProviderStatus.HEALTHY
        self.last_check = datetime.now()
        self.consecutive_failures = 0
        self.response_time_avg = (self.response_time_avg + response_time) / 2
    
    def record_failure(self, error: str) -> None:
        """Record failed operation"""
        self.failure_count += 1
        self.consecutive_failures += 1
        self.last_error = error
        self.last_check = datetime.now()
        
        # Update status based on consecutive failures
        if self.consecutive_failures >= 5:
            self.status = ProviderStatus.UNHEALTHY
        elif self.consecutive_failures >= 2:
            self.status = ProviderStatus.DEGRADED


@dataclass
class ProviderInfo:
    """
    Enhanced provider metadata structure.
    Implements Interface Segregation - contains all necessary metadata для comprehensive management.
    """
    name: str
    category: ProviderCategory
    provider_type: ProviderType
    module_path: str
    class_name: str
    description: str = ""
    version: str = "1.0.0"
    dependencies: List[str] = field(default_factory=list)
    config_schema: Dict[str, Any] = field(default_factory=dict)
    priority: int = 100  # Lower number = higher priority
    enabled: bool = True
    health_info: ProviderHealthInfo = field(default_factory=ProviderHealthInfo)
    
    def __post_init__(self):
        """Validate provider info after initialization"""
        if not self.name:
            raise ConfigurationError("Provider name cannot be empty")
        if not self.module_path:
            raise ConfigurationError("Module path cannot be empty")
        if not self.class_name:
            raise ConfigurationError("Class name cannot be empty")


class IEnhancedProviderFactory(ABC):
    """
    Enhanced abstract provider factory interface.
    Implements Interface Segregation Principle - comprehensive interface для advanced factory operations.
    Follows LSP - all implementations должны быть fully substitutable.
    """
    
    @abstractmethod
    async def create_provider(
        self, 
        provider_name: str, 
        config: Dict[str, Any]
    ) -> Union[BaseSTTProvider, BaseTTSProvider]:
        """Create provider instance with enhanced error handling"""
        raise NotImplementedError
    
    @abstractmethod
    def register_provider(self, provider_info: ProviderInfo) -> None:
        """Register new provider in registry"""
        raise NotImplementedError
    
    @abstractmethod
    def get_available_providers(
        self, 
        category: Optional[ProviderCategory] = None,
        enabled_only: bool = True
    ) -> List[ProviderInfo]:
        """Get list of available providers with filtering"""
        raise NotImplementedError
    
    @abstractmethod
    async def health_check_provider(self, provider_name: str) -> ProviderStatus:
        """Enhanced provider health check"""
        raise NotImplementedError
    
    @abstractmethod
    async def health_check_all_providers(self) -> Dict[str, ProviderStatus]:
        """Check health of all registered providers"""
        raise NotImplementedError
    
    @abstractmethod
    def get_provider_priority_list(self, category: ProviderCategory) -> List[str]:
        """Get providers ordered by priority"""
        raise NotImplementedError


class EnhancedVoiceProviderFactory(IEnhancedProviderFactory):
    """
    Enhanced Voice Provider Factory - Phase 3.3.1 Implementation
    
    Follows SOLID principles:
    - Single Responsibility: Provider creation, registration, и health management
    - Open/Closed: Open for extension (new providers), closed for modification
    - Liskov Substitution: All providers implement BaseSTTProvider/BaseTTSProvider
    - Interface Segregation: Focused interface для enhanced factory operations
    - Dependency Inversion: Depends on abstractions, not concrete classes
    
    Enhanced features:
    - Provider health monitoring с circuit breaker patterns
    - Priority-based provider selection
    - Enhanced caching mechanisms
    - Comprehensive error handling и recovery
    - Performance metrics collection
    """
    
    def __init__(self):
        self._registry: Dict[str, ProviderInfo] = {}
        self._instance_cache: Dict[str, Union[BaseSTTProvider, BaseTTSProvider]] = {}
        self._circuit_breakers: Dict[str, bool] = {}  # Provider name -> circuit open status
        self._circuit_breaker_timeout: Dict[str, datetime] = {}
        self._initialized = False
        self._lock = asyncio.Lock()
        self._circuit_breaker_threshold = 5  # Consecutive failures to open circuit
        self._circuit_breaker_timeout_minutes = 5
        logger.info("EnhancedVoiceProviderFactory initialized")
    
    async def initialize(self) -> None:
        """
        Initialize factory with built-in providers.
        Implements async initialization pattern from Phase_1_2_3_performance_optimization.md
        """
        if self._initialized:
            return
            
        async with self._lock:
            if self._initialized:
                return
                
            try:
                # Register built-in providers
                self._register_all_builtin_providers()
                
                # Initialize health monitoring
                await self._initialize_health_monitoring()
                
                self._initialized = True
                logger.info(f"Enhanced factory initialized with {len(self._registry)} providers")
                
            except Exception as e:
                logger.error(f"Failed to initialize enhanced factory: {e}", exc_info=True)
                raise ConfigurationError(f"Enhanced factory initialization failed: {e}")
    
    def _register_all_builtin_providers(self) -> None:
        """Register all built-in STT и TTS providers"""
        # Built-in STT providers
        stt_providers = [
            ProviderInfo(
                name="openai_stt",
                category=ProviderCategory.STT,
                provider_type=ProviderType.BUILTIN,
                module_path="app.services.voice_v2.providers.stt.openai_stt",
                class_name="OpenAISTTProvider",
                description="OpenAI Whisper STT provider with высокая accuracy",
                priority=10,  # Highest priority
                dependencies=["openai"],
                config_schema={
                    "api_key": {"type": "string", "required": True},
                    "model": {"type": "string", "default": "whisper-1"},
                    "language": {"type": "string", "default": "auto"}
                }
            ),
            ProviderInfo(
                name="google_stt",
                category=ProviderCategory.STT,
                provider_type=ProviderType.BUILTIN,
                module_path="app.services.voice_v2.providers.stt.google_stt",
                class_name="GoogleSTTProvider",
                description="Google Cloud Speech-to-Text provider",
                priority=20,  # Second priority
                dependencies=["google-cloud-speech"],
                config_schema={
                    "credentials_path": {"type": "string", "required": True},
                    "language_code": {"type": "string", "default": "ru-RU"}
                }
            ),
            ProviderInfo(
                name="yandex_stt",
                category=ProviderCategory.STT,
                provider_type=ProviderType.BUILTIN,
                module_path="app.services.voice_v2.providers.stt.yandex_stt",
                class_name="YandexSTTProvider",
                description="Yandex SpeechKit STT provider",
                priority=30,  # Third priority
                dependencies=["requests"],
                config_schema={
                    "api_key": {"type": "string", "required": True},
                    "folder_id": {"type": "string", "required": True}
                }
            )
        ]
        
        # Built-in TTS providers
        tts_providers = [
            ProviderInfo(
                name="openai_tts",
                category=ProviderCategory.TTS,
                provider_type=ProviderType.BUILTIN,
                module_path="app.services.voice_v2.providers.tts.openai_tts",
                class_name="OpenAITTSProvider",
                description="OpenAI TTS provider with natural voices",
                priority=10,  # Highest priority
                dependencies=["openai"],
                config_schema={
                    "api_key": {"type": "string", "required": True},
                    "model": {"type": "string", "default": "tts-1"},
                    "voice": {"type": "string", "default": "alloy"}
                }
            ),
            ProviderInfo(
                name="google_tts",
                category=ProviderCategory.TTS,
                provider_type=ProviderType.BUILTIN,
                module_path="app.services.voice_v2.providers.tts.google_tts",
                class_name="GoogleTTSProvider",
                description="Google Cloud Text-to-Speech provider",
                priority=20,  # Second priority
                dependencies=["google-cloud-texttospeech"],
                config_schema={
                    "credentials_path": {"type": "string", "required": True},
                    "language_code": {"type": "string", "default": "ru-RU"},
                    "voice_name": {"type": "string", "default": "ru-RU-Wavenet-A"}
                }
            ),
            ProviderInfo(
                name="yandex_tts",
                category=ProviderCategory.TTS,
                provider_type=ProviderType.BUILTIN,
                module_path="app.services.voice_v2.providers.tts.yandex_tts",
                class_name="YandexTTSProvider",
                description="Yandex SpeechKit TTS provider",
                priority=30,  # Third priority
                dependencies=["requests"],
                config_schema={
                    "api_key": {"type": "string", "required": True},
                    "folder_id": {"type": "string", "required": True},
                    "voice": {"type": "string", "default": "alena"}
                }
            )
        ]
        
        # Register all providers
        all_providers = stt_providers + tts_providers
        for provider in all_providers:
            self._registry[provider.name] = provider
            self._circuit_breakers[provider.name] = False  # Circuit closed initially
            logger.debug(f"Registered provider: {provider.name} ({provider.category.value})")
    
    async def _initialize_health_monitoring(self) -> None:
        """Initialize health monitoring для all providers"""
        for provider_name in self._registry.keys():
            try:
                status = await self.health_check_provider(provider_name)
                logger.debug(f"Initial health check for {provider_name}: {status.value}")
            except Exception as e:
                logger.warning(f"Initial health check failed for {provider_name}: {e}")
    
    def register_provider(self, provider_info: ProviderInfo) -> None:
        """
        Register new provider в registry.
        Implements Open/Closed principle - extensible без modification.
        """
        try:
            if provider_info.name in self._registry:
                logger.warning(f"Provider {provider_info.name} already registered, overwriting")
            
            self._registry[provider_info.name] = provider_info
            self._circuit_breakers[provider_info.name] = False
            
            logger.info(
                f"Registered provider: {provider_info.name} "
                f"({provider_info.category.value}, priority: {provider_info.priority})"
            )
            
        except Exception as e:
            logger.error(f"Failed to register provider {provider_info.name}: {e}")
            raise ConfigurationError(f"Provider registration failed: {e}")
    
    async def create_provider(
        self, 
        provider_name: str, 
        config: Dict[str, Any]
    ) -> Union[BaseSTTProvider, BaseTTSProvider]:
        """
        Create provider instance with enhanced error handling и circuit breaker.
        
        Implements performance optimization через caching и LSP compliance.
        Following patterns from Phase_1_1_4_architecture_patterns.md
        """
        if not self._initialized:
            await self.initialize()
        
        # Check circuit breaker
        if self._is_circuit_breaker_open(provider_name):
            raise VoiceServiceError(
                f"Provider {provider_name} circuit breaker is open "
                f"(too many consecutive failures)"
            )
        
        # Check cache first (performance optimization)
        cache_key = self._generate_cache_key(provider_name, config)
        if cache_key in self._instance_cache:
            logger.debug(f"Returning cached provider: {provider_name}")
            return self._instance_cache[cache_key]
        
        # Get provider info
        provider_info = self._registry.get(provider_name)
        if not provider_info:
            raise VoiceServiceError(f"Provider not found: {provider_name}")
        
        if not provider_info.enabled:
            raise VoiceServiceError(f"Provider {provider_name} is disabled")
        
        try:
            start_time = datetime.now()
            
            # Dynamic module loading
            module = importlib.import_module(provider_info.module_path)
            provider_class = getattr(module, provider_info.class_name)
            
            # Validate interface compliance (LSP)
            expected_base = (
                BaseSTTProvider if provider_info.category == ProviderCategory.STT 
                else BaseTTSProvider
            )
            
            if not issubclass(provider_class, expected_base):
                raise ConfigurationError(
                    f"Provider {provider_name} does not implement {expected_base.__name__}"
                )
            
            # Validate configuration
            self._validate_provider_config(provider_info, config)
            
            # Create instance with provider_name as first argument
            provider_instance = provider_class(provider_name, config)
            
            # Initialize if needed
            if hasattr(provider_instance, 'initialize'):
                await provider_instance.initialize()
            
            # Cache instance
            self._instance_cache[cache_key] = provider_instance
            
            # Record success
            response_time = (datetime.now() - start_time).total_seconds()
            provider_info.health_info.record_success(response_time)
            
            logger.info(f"Created provider: {provider_name} (response_time: {response_time:.3f}s)")
            return provider_instance
            
        except ImportError as e:
            error_msg = f"Failed to import provider {provider_name}: {e}"
            logger.error(error_msg)
            self._record_provider_failure(provider_name, error_msg)
            raise ConfigurationError(error_msg)
        except Exception as e:
            error_msg = f"Failed to create provider {provider_name}: {e}"
            logger.error(error_msg, exc_info=True)
            self._record_provider_failure(provider_name, error_msg)
            raise VoiceServiceError(error_msg)
    
    def _validate_provider_config(self, provider_info: ProviderInfo, config: Dict[str, Any]) -> None:
        """Validate provider configuration against schema"""
        schema = provider_info.config_schema
        if not schema:
            return
        
        for field_name, field_config in schema.items():
            if field_config.get("required", False) and field_name not in config:
                raise ConfigurationError(
                    f"Required field '{field_name}' missing for provider {provider_info.name}"
                )
    
    def _record_provider_failure(self, provider_name: str, error: str) -> None:
        """Record provider failure и update circuit breaker"""
        provider_info = self._registry.get(provider_name)
        if provider_info:
            provider_info.health_info.record_failure(error)
            
            # Check if circuit breaker should be opened
            if provider_info.health_info.consecutive_failures >= self._circuit_breaker_threshold:
                self._circuit_breakers[provider_name] = True
                self._circuit_breaker_timeout[provider_name] = (
                    datetime.now() + timedelta(minutes=self._circuit_breaker_timeout_minutes)
                )
                logger.warning(
                    f"Circuit breaker opened for provider {provider_name} "
                    f"({provider_info.health_info.consecutive_failures} consecutive failures)"
                )
    
    def _is_circuit_breaker_open(self, provider_name: str) -> bool:
        """Check if circuit breaker is open для provider"""
        if not self._circuit_breakers.get(provider_name, False):
            return False
        
        # Check if timeout has passed
        timeout = self._circuit_breaker_timeout.get(provider_name)
        if timeout and datetime.now() > timeout:
            # Reset circuit breaker
            self._circuit_breakers[provider_name] = False
            del self._circuit_breaker_timeout[provider_name]
            logger.info(f"Circuit breaker reset for provider {provider_name}")
            return False
        
        return True
    
    def get_available_providers(
        self, 
        category: Optional[ProviderCategory] = None,
        enabled_only: bool = True
    ) -> List[ProviderInfo]:
        """
        Get list of available providers with enhanced filtering.
        """
        providers = list(self._registry.values())
        
        if category:
            providers = [p for p in providers if p.category == category]
        
        if enabled_only:
            providers = [p for p in providers if p.enabled]
        
        # Filter out providers with open circuit breakers
        providers = [
            p for p in providers 
            if not self._is_circuit_breaker_open(p.name)
        ]
        
        logger.debug(
            f"Retrieved {len(providers)} providers "
            f"(category: {category}, enabled_only: {enabled_only})"
        )
        return providers
    
    def get_provider_priority_list(self, category: ProviderCategory) -> List[str]:
        """Get providers ordered by priority (lower number = higher priority)"""
        providers = self.get_available_providers(category=category, enabled_only=True)
        sorted_providers = sorted(providers, key=lambda p: p.priority)
        return [p.name for p in sorted_providers]
    
    async def health_check_provider(self, provider_name: str) -> ProviderStatus:
        """
        Enhanced provider health check.
        """
        try:
            provider_info = self._registry.get(provider_name)
            if not provider_info:
                return ProviderStatus.UNKNOWN
            
            # Check circuit breaker status
            if self._is_circuit_breaker_open(provider_name):
                return ProviderStatus.UNHEALTHY
            
            # Try to import module и class
            try:
                module = importlib.import_module(provider_info.module_path)
                getattr(module, provider_info.class_name)  # Check if class exists
                
                # Check if class exists и is importable
                provider_info.health_info.status = ProviderStatus.HEALTHY
                provider_info.health_info.last_check = datetime.now()
                return ProviderStatus.HEALTHY
                
            except (ImportError, AttributeError) as e:
                error_msg = f"Import failed: {e}"
                provider_info.health_info.record_failure(error_msg)
                return provider_info.health_info.status
                
        except Exception as e:
            logger.error(f"Health check failed for {provider_name}: {e}")
            return ProviderStatus.UNKNOWN
    
    async def health_check_all_providers(self) -> Dict[str, ProviderStatus]:
        """Check health of all registered providers"""
        results = {}
        
        # Run health checks concurrently
        tasks = [
            self.health_check_provider(provider_name)
            for provider_name in self._registry.keys()
        ]
        
        provider_names = list(self._registry.keys())
        statuses = await asyncio.gather(*tasks, return_exceptions=True)
        
        for provider_name, status in zip(provider_names, statuses):
            if isinstance(status, Exception):
                results[provider_name] = ProviderStatus.UNKNOWN
                logger.error(f"Health check failed for {provider_name}: {status}")
            else:
                results[provider_name] = status
        
        logger.info(f"Health check completed for {len(results)} providers")
        return results
    
    def _generate_cache_key(self, provider_name: str, config: Dict[str, Any]) -> str:
        """Generate cache key для provider instance"""
        # Create deterministic hash from provider name и config using SHA256
        import hashlib
        config_str = str(sorted(config.items()))
        combined = f"{provider_name}:{config_str}"
        return hashlib.sha256(combined.encode()).hexdigest()[:16]  # First 16 chars for readability
    
    def get_provider_info(self, provider_name: str) -> Optional[ProviderInfo]:
        """Get provider metadata"""
        return self._registry.get(provider_name)
    
    def get_provider_health_info(self, provider_name: str) -> Optional[ProviderHealthInfo]:
        """Get provider health information"""
        provider_info = self._registry.get(provider_name)
        return provider_info.health_info if provider_info else None
    
    def clear_cache(self) -> None:
        """Clear provider instance cache"""
        self._instance_cache.clear()
        logger.info("Provider cache cleared")
    
    def reset_circuit_breaker(self, provider_name: str) -> bool:
        """Manually reset circuit breaker для provider"""
        if provider_name in self._circuit_breakers:
            self._circuit_breakers[provider_name] = False
            if provider_name in self._circuit_breaker_timeout:
                del self._circuit_breaker_timeout[provider_name]
            
            # Reset health info consecutive failures
            provider_info = self._registry.get(provider_name)
            if provider_info:
                provider_info.health_info.consecutive_failures = 0
                provider_info.health_info.status = ProviderStatus.UNKNOWN
            
            logger.info(f"Circuit breaker manually reset for provider {provider_name}")
            return True
        return False
    
    async def shutdown(self) -> None:
        """Graceful shutdown with comprehensive resource cleanup"""
        try:
            # Shutdown all cached providers
            for provider_name, provider in self._instance_cache.items():
                try:
                    if hasattr(provider, 'shutdown'):
                        await provider.shutdown()
                    logger.debug(f"Shut down provider: {provider_name}")
                except Exception as e:
                    logger.error(f"Error shutting down provider {provider_name}: {e}")
            
            self.clear_cache()
            self._circuit_breakers.clear()
            self._circuit_breaker_timeout.clear()
            self._initialized = False
            
            logger.info("EnhancedVoiceProviderFactory shut down successfully")
            
        except Exception as e:
            logger.error(f"Error during enhanced factory shutdown: {e}", exc_info=True)


# Global enhanced factory instance
_enhanced_factory_instance: Optional[EnhancedVoiceProviderFactory] = None
_enhanced_factory_lock = asyncio.Lock()


async def get_enhanced_voice_provider_factory() -> EnhancedVoiceProviderFactory:
    """
    Get singleton enhanced factory instance.
    Implements singleton pattern с thread safety для performance.
    """
    global _enhanced_factory_instance
    
    if _enhanced_factory_instance is None:
        async with _enhanced_factory_lock:
            if _enhanced_factory_instance is None:
                _enhanced_factory_instance = EnhancedVoiceProviderFactory()
                await _enhanced_factory_instance.initialize()
    
    return _enhanced_factory_instance


async def shutdown_enhanced_factory() -> None:
    """Shutdown enhanced factory instance"""
    global _enhanced_factory_instance
    
    if _enhanced_factory_instance:
        await _enhanced_factory_instance.shutdown()
        _enhanced_factory_instance = None
