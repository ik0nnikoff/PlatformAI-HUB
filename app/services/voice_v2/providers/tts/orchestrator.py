"""
TTS Orchestrator для Voice_v2 - Phase 3.2.5

Применяет архитектурные принципы из Phase 1.3:
- Multi-provider fallback pattern (Phase_1_1_4_architecture_patterns.md)
- SOLID principles с dependency inversion (Phase_1_2_2_solid_principles.md)
- Performance optimization с caching (Phase_1_2_3_performance_optimization.md)
- LSP compliance для provider abstraction (Phase_1_3_1_architecture_review.md)

SOLID Principles Implementation:
- Single Responsibility: Координация TTS providers и fallback logic
- Open/Closed: Extensible для новых fallback strategies
- Liskov Substitution: Работает с любыми BaseTTSProvider implementations
- Interface Segregation: Четкий orchestrator interface
- Dependency Inversion: Зависит от BaseTTSProvider abstraction

Архитектурные Patterns:
- Multi-provider fallback с priority ordering
- Circuit breaker для failed providers
- Retry logic с exponential backoff
- Health monitoring и automatic recovery
- Comprehensive error handling и logging
"""

import asyncio
import logging
import time
from typing import List, Optional, Dict, Any
from dataclasses import dataclass

from .base_tts import BaseTTSProvider
from .factory import TTSProviderFactory
from .models import TTSRequest, TTSResult, TTSCapabilities
from ...core.exceptions import VoiceServiceError, AudioProcessingError, ProviderNotAvailableError

logger = logging.getLogger(__name__)


@dataclass
class ProviderHealthStatus:
    """Provider health tracking for circuit breaker pattern."""
    provider_name: str
    is_healthy: bool
    last_check_time: float
    consecutive_failures: int
    last_failure_time: Optional[float] = None
    recovery_time: Optional[float] = None


class TTSOrchestrator:
    """
    TTS Orchestrator - Phase 3.2.5

    Архитектурные принципы (Phase 1.3):
    - Multi-provider coordination с fallback mechanisms
    - SOLID: Single responsibility для TTS orchestration
    - Performance: Circuit breaker и caching для optimal performance
    - LSP: Работает с любыми BaseTTSProvider implementations
    """

    # Circuit breaker configuration
    MAX_CONSECUTIVE_FAILURES = 3
    CIRCUIT_BREAKER_TIMEOUT = 300  # 5 minutes
    HEALTH_CHECK_INTERVAL = 60  # 1 minute

    # Retry configuration
    MAX_RETRIES = 2
    RETRY_DELAYS = [1, 2]  # Exponential backoff

    def __init__(self, factory: Optional[TTSProviderFactory] = None):
        """
        Initialize TTS Orchestrator.

        Args:
            factory: TTS provider factory instance
        """
        self._factory = factory or TTSProviderFactory()
        self._provider_health: Dict[str, ProviderHealthStatus] = {}
        self._fallback_enabled = True
        self._circuit_breaker_enabled = True
        self._last_health_check = 0

        logger.debug("TTSOrchestrator initialized")

    async def initialize(self, providers_config: List[Dict[str, Any]]) -> None:
        """
        Initialize orchestrator with provider configurations.

        Args:
            providers_config: List of provider configurations
        """
        try:
            logger.debug("Initializing TTSOrchestrator with %s providers", len(providers_config))

            # Initialize factory
            await self._factory.initialize(providers_config)

            # Initialize health tracking for each provider
            for config in providers_config:
                provider_name = config.get("provider")
                if provider_name:
                    self._provider_health[provider_name] = ProviderHealthStatus(
                        provider_name=provider_name,
                        is_healthy=True,
                        last_check_time=time.time(),
                        consecutive_failures=0
                    )

            logger.info("TTSOrchestrator initialized with %s providers", len(self._provider_health))

        except Exception as e:
            logger.error("Failed to initialize TTSOrchestrator: %s", e, exc_info=True)
            raise VoiceServiceError(f"TTS Orchestrator initialization failed: {e}")

    async def synthesize_speech(
        self,
        request: TTSRequest,
        providers_config: List[Dict[str, Any]]
    ) -> TTSResult:
        """
        Synthesize speech with multi-provider fallback.

        Args:
            request: TTS synthesis request
            providers_config: List of provider configurations ordered by priority

        Returns:
            TTSResult with synthesized audio

        Raises:
            AudioProcessingError: If all providers fail
        """
        start_time = time.time()

        try:
            logger.debug("Starting TTS synthesis with %s providers", len(providers_config))

            # Prepare providers for synthesis
            available_providers = await self._prepare_providers_for_synthesis(providers_config)

            # Attempt synthesis with fallback
            result = await self._attempt_synthesis_with_fallback(
                request, available_providers, start_time
            )

            return result

        except Exception as e:
            logger.error("TTS synthesis failed: %s", e, exc_info=True)
            if isinstance(e, (AudioProcessingError, ProviderNotAvailableError)):
                raise
            raise AudioProcessingError(f"TTS synthesis failed: {e}")

    async def _prepare_providers_for_synthesis(
        self, providers_config: List[Dict[str, Any]]
    ) -> List[BaseTTSProvider]:
        """Prepare and validate providers for synthesis."""
        # Update provider health status
        await self._update_health_status()

        # Get available providers
        available_providers = await self._get_healthy_providers(providers_config)

        if not available_providers:
            raise ProviderNotAvailableError("No healthy TTS providers available")

        return available_providers

    async def _attempt_synthesis_with_fallback(
        self,
        request: TTSRequest,
        available_providers: List[BaseTTSProvider],
        start_time: float
    ) -> TTSResult:
        """Attempt synthesis with each provider until success or all fail."""
        last_error = None
        attempted_providers = []

        for provider in available_providers:
            try:
                logger.debug("Attempting TTS synthesis with provider: %s", provider.provider_name)
                attempted_providers.append(provider.provider_name)

                # Check circuit breaker
                if not self._is_provider_allowed(provider.provider_name):
                    logger.warning("Provider %s blocked by circuit breaker", provider.provider_name)
                    continue

                # Attempt synthesis with retry logic
                result = await self._synthesize_with_retry(provider, request)

                # Mark provider as healthy on success
                await self._mark_provider_healthy(provider.provider_name)

                # Add orchestrator metadata
                self._add_orchestrator_metadata(
                    result, attempted_providers, provider.provider_name, start_time
                )

                logger.info("TTS synthesis successful with provider: %s", provider.provider_name)
                return result

            except Exception as e:
                last_error = e
                await self._mark_provider_unhealthy(provider.provider_name, str(e))
                logger.warning("Provider %s failed: %s", provider.provider_name, e)

                # Continue to next provider if fallback is enabled
                if not self._fallback_enabled:
                    break

        # All providers failed
        error_msg = f"All TTS providers failed. Attempted: {attempted_providers}. Last error: {last_error}"
        logger.error(error_msg)
        raise AudioProcessingError(error_msg)

    def _add_orchestrator_metadata(
        self,
        result: TTSResult,
        attempted_providers: List[str],
        successful_provider: str,
        start_time: float
    ) -> None:
        """Add orchestrator metadata to the result."""
        result.provider_metadata.update({
            "orchestrator_info": {
                "attempted_providers": attempted_providers,
                "successful_provider": successful_provider,
                "total_orchestration_time": time.time() - start_time,
                "fallback_used": len(attempted_providers) > 1
            }
        })

    async def get_available_providers(
            self, providers_config: List[Dict[str, Any]]) -> List[BaseTTSProvider]:
        """
        Get list of available and healthy TTS providers.

        Args:
            providers_config: List of provider configurations

        Returns:
            List of healthy providers ordered by priority
        """
        try:
            await self._update_health_status()
            return await self._get_healthy_providers(providers_config)

        except Exception as e:
            logger.error("Failed to get available providers: %s", e, exc_info=True)
            return []

    async def get_provider_capabilities(self, provider_name: str) -> Optional[TTSCapabilities]:
        """
        Get capabilities for specific provider.

        Args:
            provider_name: Provider identifier

        Returns:
            Provider capabilities or None if unavailable
        """
        try:
            capabilities_dict = await self._factory.get_provider_capabilities(provider_name)
            if capabilities_dict:
                return TTSCapabilities(**capabilities_dict)
            return None

        except Exception as e:
            logger.error("Failed to get capabilities for %s: %s", provider_name, e)
            return None

    async def health_check_all_providers(self) -> Dict[str, bool]:
        """
        Perform health check on all providers.

        Returns:
            Dictionary mapping provider names to health status
        """
        try:
            # Force health status update
            await self._update_health_status(force=True)

            return {
                name: status.is_healthy
                for name, status in self._provider_health.items()
            }

        except Exception as e:
            logger.error("Failed to perform health check: %s", e, exc_info=True)
            return {}

    def get_provider_health_status(self) -> Dict[str, Dict[str, Any]]:
        """
        Get detailed health status for all providers.

        Returns:
            Detailed health status information
        """
        return {
            name: {
                "is_healthy": status.is_healthy,
                "last_check_time": status.last_check_time,
                "consecutive_failures": status.consecutive_failures,
                "last_failure_time": status.last_failure_time,
                "recovery_time": status.recovery_time,
                "circuit_breaker_active": not self._is_provider_allowed(name)
            }
            for name, status in self._provider_health.items()
        }

    def configure_fallback(self, enabled: bool) -> None:
        """Enable or disable fallback mechanism."""
        self._fallback_enabled = enabled
        logger.info("Fallback mechanism %s", 'enabled' if enabled else 'disabled')

    def configure_circuit_breaker(self, enabled: bool) -> None:
        """Enable or disable circuit breaker pattern."""
        self._circuit_breaker_enabled = enabled
        logger.info("Circuit breaker %s", 'enabled' if enabled else 'disabled')

    async def cleanup(self) -> None:
        """Clean up orchestrator resources."""
        try:
            logger.debug("Cleaning up TTSOrchestrator")

            await self._factory.cleanup()
            self._provider_health.clear()

            logger.info("TTSOrchestrator cleanup completed")

        except Exception as e:
            logger.error("Failed to cleanup TTSOrchestrator: %s", e, exc_info=True)

    # Private helper methods

    async def _get_healthy_providers(
            self, providers_config: List[Dict[str, Any]]) -> List[BaseTTSProvider]:
        """Get list of healthy providers ordered by priority."""
        available_providers = await self._factory.get_available_providers(providers_config)

        # Filter by health status if circuit breaker is enabled
        if self._circuit_breaker_enabled:
            healthy_providers = []
            for provider in available_providers:
                if self._is_provider_allowed(provider.provider_name):
                    healthy_providers.append(provider)
                else:
                    logger.debug("Provider %s blocked by circuit breaker", provider.provider_name)
            return healthy_providers

        return available_providers

    async def _synthesize_with_retry(
            self,
            provider: BaseTTSProvider,
            request: TTSRequest) -> TTSResult:
        """Synthesize speech with retry logic."""
        last_error = None

        for attempt in range(self.MAX_RETRIES + 1):
            try:
                if attempt > 0:
                    delay = self.RETRY_DELAYS[min(attempt - 1, len(self.RETRY_DELAYS) - 1)]
                    logger.debug(
                        "Retrying provider %s after %ss delay",
                        provider.provider_name,
                        delay)
                    await asyncio.sleep(delay)

                result = await provider.synthesize_speech(request)

                if attempt > 0:
                    logger.info(
                        "Provider %s succeeded on retry %s",
                        provider.provider_name,
                        attempt)

                return result

            except Exception as e:
                last_error = e
                logger.warning("Provider %s attempt %s failed: %s",
                               provider.provider_name, attempt + 1, e)

                # Don't retry for certain error types
                if isinstance(e, (ProviderNotAvailableError,)):
                    break

        raise last_error or AudioProcessingError(
            f"Provider {provider.provider_name} failed after {self.MAX_RETRIES + 1} attempts"
        )

    async def _update_health_status(self, force: bool = False) -> None:
        """Update health status for all providers."""
        current_time = time.time()

        # Check if health check is needed
        if not force and (current_time - self._last_health_check) < self.HEALTH_CHECK_INTERVAL:
            return

        logger.debug("Updating provider health status")

        # Perform health checks
        health_results = await self._factory.health_check_all_providers()

        for provider_name, is_healthy in health_results.items():
            if provider_name in self._provider_health:
                status = self._provider_health[provider_name]
                status.last_check_time = current_time

                if is_healthy:
                    if not status.is_healthy:
                        logger.info("Provider %s recovered", provider_name)
                        status.recovery_time = current_time
                    status.is_healthy = True
                    status.consecutive_failures = 0
                else:
                    if status.is_healthy:
                        logger.warning("Provider %s became unhealthy", provider_name)
                    status.is_healthy = False
                    status.consecutive_failures += 1
                    status.last_failure_time = current_time

        self._last_health_check = current_time

    async def _mark_provider_healthy(self, provider_name: str) -> None:
        """Mark provider as healthy after successful operation."""
        if provider_name in self._provider_health:
            status = self._provider_health[provider_name]

            if not status.is_healthy:
                logger.info("Provider %s marked as healthy", provider_name)
                status.recovery_time = time.time()

            status.is_healthy = True
            status.consecutive_failures = 0
            status.last_check_time = time.time()

    async def _mark_provider_unhealthy(self, provider_name: str, error_message: str) -> None:
        """Mark provider as unhealthy after failure."""
        if provider_name in self._provider_health:
            status = self._provider_health[provider_name]
            status.is_healthy = False
            status.consecutive_failures += 1
            status.last_failure_time = time.time()
            status.last_check_time = time.time()

            logger.warning("Provider %s marked unhealthy (failures: %s): %s",
                           provider_name, status.consecutive_failures, error_message)

    def _is_provider_allowed(self, provider_name: str) -> bool:
        """Check if provider is allowed by circuit breaker."""
        if not self._circuit_breaker_enabled:
            return True

        if provider_name not in self._provider_health:
            return True

        status = self._provider_health[provider_name]

        # Allow if provider is healthy
        if status.is_healthy:
            return True

        # Block if too many consecutive failures
        if status.consecutive_failures >= self.MAX_CONSECUTIVE_FAILURES:
            # Check if enough time has passed for recovery attempt
            if status.last_failure_time:
                time_since_failure = time.time() - status.last_failure_time
                if time_since_failure >= self.CIRCUIT_BREAKER_TIMEOUT:
                    logger.info(
                        "Circuit breaker timeout expired for %s, allowing recovery attempt",
                        provider_name)
                    return True
            return False

        return True


# Global orchestrator instance
tts_orchestrator = TTSOrchestrator()


async def synthesize_speech_with_fallback(
    request: TTSRequest,
    providers_config: List[Dict[str, Any]]
) -> TTSResult:
    """
    Convenience function for TTS synthesis with fallback.

    Args:
        request: TTS synthesis request
        providers_config: List of provider configurations

    Returns:
        TTSResult with synthesized audio
    """
    return await tts_orchestrator.synthesize_speech(request, providers_config)
