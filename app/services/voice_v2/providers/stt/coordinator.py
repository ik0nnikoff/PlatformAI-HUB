"""
STT System Coordinator - Центральная интеграция всех STT компонентов

Реализует:
- Координацию между factory, dynamic loader и config manager
- Unified interface для всей STT системы
- Performance monitoring и optimization
- Comprehensive error handling и recovery
"""

import asyncio
import time
from typing import Dict, List, Optional, Any
from dataclasses import dataclass
import logging

from app.services.voice_v2.providers.stt.base_stt import BaseSTTProvider
from app.services.voice_v2.providers.stt.dynamic_loader import (
    STTProviderManager, LoadingStrategy, ProviderLoadingConfig
)
from app.services.voice_v2.providers.stt.config_manager import (
    STTConfigManager, ConfigSource, AgentSTTConfig
)
from app.services.voice_v2.providers.stt.models import TranscriptionResult
from app.core.logging_config import setup_logger


@dataclass
class STTSystemMetrics:
    """System-wide STT metrics"""
    total_transcriptions: int = 0
    successful_transcriptions: int = 0
    failed_transcriptions: int = 0
    average_response_time: float = 0.0
    provider_usage: Dict[str, int] = None
    fallback_usage: int = 0
    cache_hits: int = 0
    cache_misses: int = 0

    def __post_init__(self):
        if self.provider_usage is None:
            self.provider_usage = {}


class STTSystemCoordinator:
    """
    Центральный координатор для всей STT системы

    Implements:
    - Unified interface для STT operations
    - Integration между всеми STT компонентами
    - Performance monitoring и optimization
    - Fallback chain coordination
    - Comprehensive error handling
    """

    def __init__(
        self,
        config_manager: Optional[STTConfigManager] = None,
        loading_config: Optional[ProviderLoadingConfig] = None,
        logger: Optional[logging.Logger] = None
    ):
        """Initialize STT system coordinator"""
        self.logger = logger or setup_logger("stt_system_coordinator")

        # Core components
        self.config_manager = config_manager or STTConfigManager(logger=self.logger)
        self.provider_manager = STTProviderManager(
            loading_config=loading_config,
            logger=self.logger
        )

        # Agent providers cache
        self._agent_providers: Dict[str, List[BaseSTTProvider]] = {}
        self._agent_configs: Dict[str, AgentSTTConfig] = {}

        # Performance monitoring
        self._metrics = STTSystemMetrics()
        self._performance_history: List[Dict[str, Any]] = []

        # System state
        self._initialized = False
        self._shutdown_event = asyncio.Event()

    async def initialize(self) -> None:
        """Initialize STT system coordinator"""
        if self._initialized:
            return

        self.logger.debug("Initializing STT system coordinator")

        try:
            # Initialize provider manager
            await self.provider_manager.initialize()

            # Load system configuration
            system_config = self.config_manager.load_system_config()
            self.logger.info(f"Loaded system config: enabled={system_config.enabled}")

            self._initialized = True
            self.logger.info("STT system coordinator initialized successfully")

        except Exception as e:
            self.logger.error(f"Failed to initialize STT system coordinator: {e}")
            raise

    async def setup_agent_stt(
        self,
        agent_id: str,
        voice_config: Optional[Dict[str, Any]] = None,
        config_source: ConfigSource = ConfigSource.FILE,
        loading_strategy: Optional[LoadingStrategy] = None
    ) -> List[BaseSTTProvider]:
        """
        Setup STT providers for specific agent

        Args:
            agent_id: Agent identifier
            voice_config: Voice configuration (for non-file sources)
            config_source: Configuration source
            loading_strategy: Provider loading strategy

        Returns:
            List of initialized STT providers
        """
        self.logger.debug(f"Setting up STT for agent {agent_id}")

        try:
            await self._ensure_initialized()

            # Load agent configuration
            agent_config = self.config_manager.load_agent_config(
                agent_id=agent_id,
                config_source=config_source,
                config_data=voice_config
            )

            if not agent_config.enabled:
                self.logger.info(f"STT disabled for agent {agent_id}")
                return []

            # Store configuration
            self._agent_configs[agent_id] = agent_config

            # Prepare voice config for provider manager
            providers_voice_config = {
                "providers": agent_config.providers,
                "enabled": agent_config.enabled,
                **agent_config.custom_settings
            }

            # Load providers using provider manager
            providers = await self.provider_manager.load_providers_for_agent(
                agent_id=agent_id,
                voice_config=providers_voice_config,
                strategy=loading_strategy
            )

            # Cache providers
            self._agent_providers[agent_id] = providers

            self.logger.info(f"Successfully set up {len(providers)} STT providers for agent {agent_id}")
            return providers

        except Exception as e:
            self.logger.error(f"Failed to setup STT for agent {agent_id}: {e}")
            return []

    async def transcribe(
        self,
        agent_id: str,
        audio_file: str,
        language: str = "auto",
        provider_hint: Optional[str] = None
    ) -> TranscriptionResult:
        """
        Transcribe audio using agent's STT providers with fallback chain

        Args:
            agent_id: Agent identifier
            audio_file: Path to audio file
            language: Language code or "auto"
            provider_hint: Preferred provider type

        Returns:
            Transcription result
        """
        start_time = time.time()
        self.logger.debug(f"Transcribing audio for agent {agent_id}, language={language}")

        try:
            await self._ensure_initialized()

            # Get providers for agent
            providers = await self._get_agent_providers(agent_id)
            if not providers:
                raise RuntimeError(f"No STT providers available for agent {agent_id}")

            # Sort providers by priority, with hint provider first
            sorted_providers = self._sort_providers_by_priority(providers, provider_hint)

            # Attempt transcription with fallback chain
            last_error = None
            for i, provider in enumerate(sorted_providers):
                try:
                    self.logger.debug(f"Attempting transcription with provider {i+1}/{len(sorted_providers)}")

                    # Check provider health
                    if not await provider.health_check():
                        self.logger.warning(f"Provider {provider.get_status().provider_type} is unhealthy, skipping")
                        continue

                    # Perform transcription
                    text = await provider.transcribe(audio_file, language)

                    # Record success metrics
                    response_time = time.time() - start_time
                    await self._record_success_metrics(
                        provider.get_status().provider_type,
                        response_time,
                        fallback_used=(i > 0)
                    )

                    return TranscriptionResult(
                        text=text,
                        language=language,
                        provider_type=provider.get_status().provider_type,
                        confidence=1.0,  # Providers don't return confidence yet
                        processing_time=response_time,
                        success=True
                    )

                except Exception as e:
                    last_error = e
                    self.logger.warning(
                        f"Transcription failed with provider {provider.get_status().provider_type}: {e}"
                    )
                    continue

            # All providers failed
            response_time = time.time() - start_time
            await self._record_failure_metrics(response_time)

            return TranscriptionResult(
                text="",
                language=language,
                provider_type="none",
                confidence=0.0,
                processing_time=response_time,
                success=False,
                error=str(last_error) if last_error else "All providers failed"
            )

        except Exception as e:
            response_time = time.time() - start_time
            await self._record_failure_metrics(response_time)

            self.logger.error(f"Critical error in transcription: {e}")
            return TranscriptionResult(
                text="",
                language=language,
                provider_type="none",
                confidence=0.0,
                processing_time=response_time,
                success=False,
                error=str(e)
            )

    async def health_check_agent_providers(self, agent_id: str) -> Dict[str, bool]:
        """Check health of all providers for specific agent"""
        self.logger.debug(f"Checking provider health for agent {agent_id}")

        try:
            providers = await self._get_agent_providers(agent_id)
            health_status = {}

            for provider in providers:
                provider_type = provider.get_status().provider_type
                try:
                    is_healthy = await provider.health_check()
                    health_status[provider_type] = is_healthy
                except Exception as e:
                    self.logger.error(f"Health check failed for {provider_type}: {e}")
                    health_status[provider_type] = False

            return health_status

        except Exception as e:
            self.logger.error(f"Failed to check provider health for agent {agent_id}: {e}")
            return {}

    async def reload_agent_providers(
        self,
        agent_id: str,
        provider_type: Optional[str] = None
    ) -> bool:
        """
        Reload providers for specific agent

        Args:
            agent_id: Agent identifier
            provider_type: Specific provider to reload (None for all)

        Returns:
            True if reload successful
        """
        self.logger.debug(f"Reloading providers for agent {agent_id}, provider={provider_type}")

        try:
            if provider_type:
                # Reload specific provider
                success = await self.provider_manager.reload_provider(agent_id, provider_type)
                if success:
                    # Update cached providers
                    await self._refresh_agent_providers_cache(agent_id)
                return success
            else:
                # Reload all providers
                agent_config = self._agent_configs.get(agent_id)
                if not agent_config:
                    self.logger.error(f"No configuration found for agent {agent_id}")
                    return False

                # Re-setup all providers
                providers = await self.setup_agent_stt(
                    agent_id=agent_id,
                    voice_config={"providers": agent_config.providers},
                    config_source=ConfigSource.DATABASE
                )

                return len(providers) > 0

        except Exception as e:
            self.logger.error(f"Failed to reload providers for agent {agent_id}: {e}")
            return False

    async def _get_agent_providers(self, agent_id: str) -> List[BaseSTTProvider]:
        """Get providers for agent, loading if necessary"""
        # Check cache first
        if agent_id in self._agent_providers:
            return self._agent_providers[agent_id]

        # Auto-setup if not found
        self.logger.info(f"Auto-setting up STT providers for agent {agent_id}")
        return await self.setup_agent_stt(agent_id)

    def _sort_providers_by_priority(
        self,
        providers: List[BaseSTTProvider],
        provider_hint: Optional[str] = None
    ) -> List[BaseSTTProvider]:
        """Sort providers by priority with optional hint"""
        # If hint provided, put that provider first
        if provider_hint:
            hinted_providers = [
                p for p in providers
                if p.get_status().provider_type == provider_hint
            ]
            other_providers = [
                p for p in providers
                if p.get_status().provider_type != provider_hint
            ]
            return hinted_providers + other_providers

        # Sort by priority (assuming providers are already ordered by priority)
        return providers

    async def _record_success_metrics(
        self,
        provider_type: str,
        response_time: float,
        fallback_used: bool
    ) -> None:
        """Record successful transcription metrics"""
        self._metrics.total_transcriptions += 1
        self._metrics.successful_transcriptions += 1

        if provider_type not in self._metrics.provider_usage:
            self._metrics.provider_usage[provider_type] = 0
        self._metrics.provider_usage[provider_type] += 1

        if fallback_used:
            self._metrics.fallback_usage += 1

        # Update average response time
        if self._metrics.successful_transcriptions == 1:
            self._metrics.average_response_time = response_time
        else:
            # Running average
            total_time = self._metrics.average_response_time * (self._metrics.successful_transcriptions - 1)
            self._metrics.average_response_time = (total_time + response_time) / self._metrics.successful_transcriptions

        # Store performance history
        self._performance_history.append({
            "timestamp": time.time(),
            "provider_type": provider_type,
            "response_time": response_time,
            "fallback_used": fallback_used,
            "success": True
        })

        # Limit history size
        if len(self._performance_history) > 1000:
            self._performance_history = self._performance_history[-500:]

    async def _record_failure_metrics(self, response_time: float) -> None:
        """Record failed transcription metrics"""
        self._metrics.total_transcriptions += 1
        self._metrics.failed_transcriptions += 1

        # Store performance history
        self._performance_history.append({
            "timestamp": time.time(),
            "provider_type": "none",
            "response_time": response_time,
            "fallback_used": False,
            "success": False
        })

    async def _refresh_agent_providers_cache(self, agent_id: str) -> None:
        """Refresh cached providers for agent"""
        # This would need to get updated providers from provider manager
        # For now, just clear the cache to force reload
        self._agent_providers.pop(agent_id, None)

    async def _ensure_initialized(self) -> None:
        """Ensure coordinator is initialized"""
        if not self._initialized:
            await self.initialize()

    def get_system_metrics(self) -> STTSystemMetrics:
        """Get system-wide STT metrics"""
        return self._metrics

    def get_agent_stats(self, agent_id: str) -> Dict[str, Any]:
        """Get statistics for specific agent"""
        providers = self._agent_providers.get(agent_id, [])
        config = self._agent_configs.get(agent_id)

        stats = {
            "agent_id": agent_id,
            "providers_count": len(providers),
            "providers": [],
            "enabled": config.enabled if config else False
        }

        for provider in providers:
            provider_status = provider.get_status()
            stats["providers"].append({
                "type": provider_status.provider_type,
                "status": provider_status.status.value,
                "error_count": provider_status.error_count
            })

        return stats

    def get_performance_history(self, limit: int = 100) -> List[Dict[str, Any]]:
        """Get recent performance history"""
        return self._performance_history[-limit:] if self._performance_history else []

    async def shutdown(self) -> None:
        """Shutdown STT system coordinator"""
        self.logger.debug("Shutting down STT system coordinator")

        # Signal shutdown
        self._shutdown_event.set()

        # Shutdown provider manager
        await self.provider_manager.shutdown()

        # Clear caches
        self._agent_providers.clear()
        self._agent_configs.clear()

        self._initialized = False
        self.logger.info("STT system coordinator shut down")


# Export public interface
__all__ = [
    "STTSystemCoordinator",
    "STTSystemMetrics"
]
