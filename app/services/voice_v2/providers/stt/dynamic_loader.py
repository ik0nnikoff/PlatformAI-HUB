"""
Dynamic Loading Mechanism для STT провайдеров

Реализует:
- Hot-reload провайдеров без перезапуска системы
- Configuration-based provider selection
- Health monitoring и automatic failover
- Performance optimization patterns из Phase 1.2.3
"""

import asyncio
import time
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, field
from enum import Enum
import logging

from app.services.voice_v2.providers.stt.base_stt import BaseSTTProvider
from app.services.voice_v2.providers.stt.factory import STTProviderFactory, STTProviderLoader
from app.core.logging_config import setup_logger


class LoadingStrategy(str, Enum):
    """Provider loading strategies"""
    LAZY = "lazy"          # Load на первом использовании
    EAGER = "eager"        # Load при инициализации
    ON_DEMAND = "on_demand"  # Load по требованию


@dataclass
class ProviderLoadingConfig:
    """Configuration for provider loading"""
    strategy: LoadingStrategy = LoadingStrategy.LAZY
    max_concurrent_loads: int = 3
    load_timeout: int = 30
    health_check_interval: int = 60
    auto_reload_on_failure: bool = True
    fallback_enabled: bool = True


@dataclass
class LoadedProvider:
    """Wrapper для loaded provider с metadata"""
    provider: BaseSTTProvider
    load_time: float
    last_health_check: float = field(default_factory=time.time)
    error_count: int = 0
    is_healthy: bool = True
    config_hash: str = ""


class STTProviderManager:
    """
    Dynamic manager для STT провайдеров
    
    Implements:
    - Dynamic loading/unloading providers
    - Health monitoring и automatic recovery
    - Configuration-based provider selection
    - Performance optimization через caching и pooling
    """
    
    def __init__(
        self,
        loading_config: Optional[ProviderLoadingConfig] = None,
        logger: Optional[logging.Logger] = None
    ):
        """Initialize provider manager"""
        self.logger = logger or setup_logger("stt_provider_manager")
        self.loading_config = loading_config or ProviderLoadingConfig()
        
        # Provider management
        self._loaded_providers: Dict[str, LoadedProvider] = {}
        self._provider_configs: Dict[str, Dict[str, Any]] = {}
        self._loading_semaphore = asyncio.Semaphore(self.loading_config.max_concurrent_loads)
        
        # Factory и loader
        self.factory = STTProviderFactory(logger=self.logger)
        self.loader = STTProviderLoader(logger=self.logger)
        
        # Health monitoring
        self._health_monitor_task: Optional[asyncio.Task] = None
        self._shutdown_event = asyncio.Event()
        
        # Performance metrics
        self._load_metrics: Dict[str, Dict[str, Any]] = {}
    
    async def initialize(self) -> None:
        """Initialize provider manager"""
        self.logger.debug("Initializing STT provider manager")
        
        # Start health monitoring
        if self.loading_config.health_check_interval > 0:
            self._health_monitor_task = asyncio.create_task(self._health_monitor_loop())
        
        self.logger.info("STT provider manager initialized")
    
    async def load_providers_for_agent(
        self,
        agent_id: str,
        voice_config: Dict[str, Any],
        strategy: Optional[LoadingStrategy] = None
    ) -> List[BaseSTTProvider]:
        """
        Load providers for specific agent with specified strategy
        
        Args:
            agent_id: Agent identifier
            voice_config: Voice configuration
            strategy: Loading strategy override
            
        Returns:
            List of loaded providers
        """
        strategy = strategy or self.loading_config.strategy
        self.logger.debug(f"Loading providers for agent {agent_id} with strategy {strategy}")
        
        try:
            # Store configuration for potential reloads
            self._provider_configs[agent_id] = voice_config
            
            if strategy == LoadingStrategy.EAGER:
                return await self._load_providers_eager(agent_id, voice_config)
            elif strategy == LoadingStrategy.LAZY:
                return await self._load_providers_lazy(agent_id, voice_config)
            elif strategy == LoadingStrategy.ON_DEMAND:
                return await self._setup_on_demand_loading(agent_id, voice_config)
            else:
                raise ValueError(f"Unknown loading strategy: {strategy}")
                
        except Exception as e:
            self.logger.error(f"Failed to load providers for agent {agent_id}: {e}", exc_info=True)
            return []
    
    async def _load_providers_eager(
        self,
        agent_id: str,
        voice_config: Dict[str, Any]
    ) -> List[BaseSTTProvider]:
        """Eager loading: load all providers immediately"""
        self.logger.debug(f"Eager loading providers for agent {agent_id}")
        
        async with self._loading_semaphore:
            start_time = time.time()
            
            try:
                providers = await self.loader.load_providers_for_agent(agent_id, voice_config)
                
                # Wrap providers
                for provider in providers:
                    provider_key = f"{agent_id}:{provider.get_status().provider_type}"
                    loaded_provider = LoadedProvider(
                        provider=provider,
                        load_time=time.time() - start_time,
                        config_hash=self._get_config_hash(voice_config)
                    )
                    self._loaded_providers[provider_key] = loaded_provider
                
                # Record metrics
                self._record_load_metrics(agent_id, "eager", time.time() - start_time, len(providers))
                
                self.logger.info(f"Eager loaded {len(providers)} providers for agent {agent_id}")
                return providers
                
            except Exception as e:
                self.logger.error(f"Eager loading failed for agent {agent_id}: {e}")
                return []
    
    async def _load_providers_lazy(
        self,
        agent_id: str,
        voice_config: Dict[str, Any]
    ) -> List[BaseSTTProvider]:
        """Lazy loading: load providers on first use"""
        self.logger.debug(f"Setting up lazy loading for agent {agent_id}")
        
        # Return lazy proxies that will load providers on first use
        provider_configs = voice_config.get("providers", [])
        lazy_providers = []
        
        for config in provider_configs:
            if config.get("enabled", True):
                provider_type = config.get("provider", "").lower()
                proxy = LazySTTProviderProxy(
                    agent_id=agent_id,
                    provider_type=provider_type,
                    config=config,
                    manager=self
                )
                lazy_providers.append(proxy)
        
        self.logger.info(f"Set up lazy loading for {len(lazy_providers)} providers for agent {agent_id}")
        return lazy_providers
    
    async def _setup_on_demand_loading(
        self,
        agent_id: str,
        voice_config: Dict[str, Any]
    ) -> List[BaseSTTProvider]:
        """On-demand loading: load only when explicitly requested"""
        self.logger.debug(f"Setting up on-demand loading for agent {agent_id}")
        
        # Store configuration for on-demand loading
        self._provider_configs[agent_id] = voice_config
        
        # Return empty list - providers будут загружены через load_provider_on_demand
        return []
    
    async def load_provider_on_demand(
        self,
        agent_id: str,
        provider_type: str
    ) -> Optional[BaseSTTProvider]:
        """Load specific provider on demand"""
        self.logger.debug(f"Loading provider {provider_type} on demand for agent {agent_id}")
        
        try:
            provider_key = f"{agent_id}:{provider_type}"
            
            # Check if already loaded
            if provider_key in self._loaded_providers:
                loaded_provider = self._loaded_providers[provider_key]
                if loaded_provider.is_healthy:
                    return loaded_provider.provider
            
            # Get configuration
            voice_config = self._provider_configs.get(agent_id)
            if not voice_config:
                self.logger.error(f"No configuration found for agent {agent_id}")
                return None
            
            # Find provider config
            provider_config = None
            for config in voice_config.get("providers", []):
                if config.get("provider", "").lower() == provider_type:
                    provider_config = config
                    break
            
            if not provider_config:
                self.logger.error(f"No configuration found for provider {provider_type}")
                return None
            
            # Load provider
            async with self._loading_semaphore:
                start_time = time.time()
                
                provider = await self.loader.reload_provider(
                    agent_id=agent_id,
                    provider_type=provider_type,
                    new_config=provider_config
                )
                
                if provider:
                    loaded_provider = LoadedProvider(
                        provider=provider,
                        load_time=time.time() - start_time,
                        config_hash=self._get_config_hash(provider_config)
                    )
                    self._loaded_providers[provider_key] = loaded_provider
                    
                    self.logger.info(f"Successfully loaded provider {provider_type} on demand")
                    return provider
                
                return None
                
        except Exception as e:
            self.logger.error(f"Failed to load provider {provider_type} on demand: {e}")
            return None
    
    async def reload_provider(
        self,
        agent_id: str,
        provider_type: str,
        new_config: Optional[Dict[str, Any]] = None
    ) -> bool:
        """Reload specific provider with new configuration"""
        self.logger.debug(f"Reloading provider {provider_type} for agent {agent_id}")
        
        try:
            provider_key = f"{agent_id}:{provider_type}"
            
            # Cleanup old provider
            if provider_key in self._loaded_providers:
                old_provider = self._loaded_providers[provider_key]
                try:
                    await old_provider.provider.cleanup()
                except Exception as e:
                    self.logger.warning(f"Error cleaning up old provider: {e}")
                
                del self._loaded_providers[provider_key]
            
            # Load new provider
            if new_config:
                # Update stored configuration
                if agent_id in self._provider_configs:
                    voice_config = self._provider_configs[agent_id]
                    for i, config in enumerate(voice_config.get("providers", [])):
                        if config.get("provider", "").lower() == provider_type:
                            voice_config["providers"][i] = new_config
                            break
            
            # Load provider on demand
            provider = await self.load_provider_on_demand(agent_id, provider_type)
            
            if provider:
                self.logger.info(f"Successfully reloaded provider {provider_type}")
                return True
            else:
                self.logger.error(f"Failed to reload provider {provider_type}")
                return False
                
        except Exception as e:
            self.logger.error(f"Error reloading provider {provider_type}: {e}")
            return False
    
    async def _health_monitor_loop(self) -> None:
        """Health monitoring loop"""
        self.logger.debug("Starting health monitor loop")
        
        while not self._shutdown_event.is_set():
            try:
                await self._check_all_providers_health()
                await asyncio.sleep(self.loading_config.health_check_interval)
            except asyncio.CancelledError:
                break
            except Exception as e:
                self.logger.error(f"Error in health monitor loop: {e}")
                await asyncio.sleep(5)  # Short sleep before retry
    
    async def _check_all_providers_health(self) -> None:
        """Check health of all loaded providers"""
        current_time = time.time()
        
        for provider_key, loaded_provider in list(self._loaded_providers.items()):
            try:
                # Check if health check is due
                if (current_time - loaded_provider.last_health_check) < self.loading_config.health_check_interval:
                    continue
                
                # Perform health check
                is_healthy = await loaded_provider.provider.health_check()
                loaded_provider.last_health_check = current_time
                
                if is_healthy:
                    loaded_provider.is_healthy = True
                    loaded_provider.error_count = 0
                else:
                    loaded_provider.is_healthy = False
                    loaded_provider.error_count += 1
                    
                    self.logger.warning(f"Provider {provider_key} health check failed")
                    
                    # Auto-reload if configured
                    if (self.loading_config.auto_reload_on_failure and 
                        loaded_provider.error_count >= 3):
                        self.logger.info(f"Auto-reloading unhealthy provider {provider_key}")
                        agent_id, provider_type = provider_key.split(":", 1)
                        asyncio.create_task(self.reload_provider(agent_id, provider_type))
                
            except Exception as e:
                self.logger.error(f"Error checking health of provider {provider_key}: {e}")
                loaded_provider.error_count += 1
    
    def _get_config_hash(self, config: Dict[str, Any]) -> str:
        """Generate hash for configuration"""
        import hashlib
        import json
        config_str = json.dumps(config, sort_keys=True)
        return hashlib.sha256(config_str.encode()).hexdigest()
    
    def _record_load_metrics(
        self,
        agent_id: str,
        strategy: str,
        load_time: float,
        provider_count: int
    ) -> None:
        """Record loading metrics"""
        if agent_id not in self._load_metrics:
            self._load_metrics[agent_id] = {}
        
        self._load_metrics[agent_id][strategy] = {
            "load_time": load_time,
            "provider_count": provider_count,
            "timestamp": time.time()
        }
    
    def get_provider_stats(self) -> Dict[str, Any]:
        """Get provider statistics"""
        stats = {
            "loaded_providers": len(self._loaded_providers),
            "providers": {},
            "load_metrics": self._load_metrics
        }
        
        for provider_key, loaded_provider in self._loaded_providers.items():
            stats["providers"][provider_key] = {
                "load_time": loaded_provider.load_time,
                "last_health_check": loaded_provider.last_health_check,
                "error_count": loaded_provider.error_count,
                "is_healthy": loaded_provider.is_healthy,
                "status": loaded_provider.provider.get_status().status.value
            }
        
        return stats
    
    async def shutdown(self) -> None:
        """Shutdown provider manager"""
        self.logger.debug("Shutting down STT provider manager")
        
        # Signal shutdown
        self._shutdown_event.set()
        
        # Cancel health monitor
        if self._health_monitor_task:
            self._health_monitor_task.cancel()
            try:
                await self._health_monitor_task
            except asyncio.CancelledError:
                pass
        
        # Cleanup all providers
        for loaded_provider in self._loaded_providers.values():
            try:
                await loaded_provider.provider.cleanup()
            except Exception as e:
                self.logger.error(f"Error cleaning up provider: {e}")
        
        # Cleanup factory
        await self.factory.cleanup_cached_providers()
        
        self._loaded_providers.clear()
        self._provider_configs.clear()
        
        self.logger.info("STT provider manager shut down")


class LazySTTProviderProxy(BaseSTTProvider):
    """
    Lazy proxy для STT provider
    
    Загружает actual provider только при первом использовании
    """
    
    def __init__(
        self,
        agent_id: str,
        provider_type: str,
        config: Dict[str, Any],
        manager: STTProviderManager
    ):
        """Initialize lazy proxy"""
        self.agent_id = agent_id
        self.provider_type = provider_type
        self.config = config
        self.manager = manager
        self._actual_provider: Optional[BaseSTTProvider] = None
        self._loading_lock = asyncio.Lock()
    
    async def _ensure_loaded(self) -> BaseSTTProvider:
        """Ensure actual provider is loaded"""
        if self._actual_provider is not None:
            return self._actual_provider
        
        async with self._loading_lock:
            if self._actual_provider is not None:
                return self._actual_provider
            
            # Load actual provider
            self._actual_provider = await self.manager.load_provider_on_demand(
                self.agent_id,
                self.provider_type
            )
            
            if self._actual_provider is None:
                raise RuntimeError(f"Failed to load provider {self.provider_type}")
            
            return self._actual_provider
    
    async def initialize(self) -> None:
        """Initialize provider (lazy)"""
        # Initialization happens in _ensure_loaded
        pass
    
    async def transcribe(self, audio_file: str, language: str = "auto") -> str:
        """Transcribe audio (lazy loading)"""
        provider = await self._ensure_loaded()
        return await provider.transcribe(audio_file, language)
    
    async def health_check(self) -> bool:
        """Health check (lazy loading)"""
        if self._actual_provider is None:
            return True  # Assume healthy until loaded
        
        return await self._actual_provider.health_check()
    
    async def cleanup(self) -> None:
        """Cleanup provider"""
        if self._actual_provider is not None:
            await self._actual_provider.cleanup()


# Export public interface
__all__ = [
    "LoadingStrategy",
    "ProviderLoadingConfig",
    "STTProviderManager",
    "LazySTTProviderProxy"
]
