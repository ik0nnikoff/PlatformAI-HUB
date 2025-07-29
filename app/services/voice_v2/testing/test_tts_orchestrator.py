"""
Unit tests for TTS Orchestrator - Phase 3.2.5

Tests Phase 1.3 architectural compliance:
- Multi-provider coordination validation
- Circuit breaker pattern testing
- Fallback mechanism verification
- Health monitoring и recovery testing

Architecture Validation:
- Phase_1_3_1_architecture_review.md: LSP compliance с provider abstraction
- Phase_1_2_2_solid_principles.md: Single responsibility validation
- Phase_1_2_3_performance_optimization.md: Performance patterns testing
- Phase_1_1_4_architecture_patterns.md: Multi-provider fallback patterns
"""

import pytest
import time
from unittest.mock import AsyncMock, patch
from typing import Dict, Any, List

# Import voice_v2 components
from app.services.voice_v2.core.exceptions import VoiceServiceError, AudioProcessingError, ProviderNotAvailableError
from app.services.voice_v2.providers.tts.orchestrator import TTSOrchestrator, ProviderHealthStatus
from app.services.voice_v2.providers.tts.factory import TTSProviderFactory
from app.services.voice_v2.providers.tts.base_tts import BaseTTSProvider
from app.services.voice_v2.providers.tts.models import TTSRequest, TTSResult, TTSQuality
from app.services.voice_v2.core.interfaces import AudioFormat


class TestTTSOrchestrator:
    """Test suite for TTS Orchestrator implementation."""

    @pytest.fixture
    def mock_factory(self) -> AsyncMock:
        """Create mock TTS factory for testing."""
        factory = AsyncMock(spec=TTSProviderFactory)
        factory.initialize = AsyncMock()
        factory.get_available_providers = AsyncMock()
        factory.health_check_all_providers = AsyncMock()
        factory.cleanup = AsyncMock()
        return factory

    @pytest.fixture
    def orchestrator(self, mock_factory) -> TTSOrchestrator:
        """Create orchestrator instance with mock factory."""
        return TTSOrchestrator(mock_factory)

    @pytest.fixture
    def providers_config(self) -> List[Dict[str, Any]]:
        """Multi-provider configuration for testing."""
        return [
            {
                "provider": "openai",
                "config": {"api_key": "test-openai-key"},
                "priority": 1,
                "enabled": True
            },
            {
                "provider": "google",
                "config": {"credentials_path": "/path/to/creds.json"},
                "priority": 2,
                "enabled": True
            },
            {
                "provider": "yandex",
                "config": {"api_key": "test-yandex-key", "folder_id": "test-folder"},
                "priority": 3,
                "enabled": True
            }
        ]

    @pytest.fixture
    def mock_providers(self) -> List[AsyncMock]:
        """Create mock provider instances."""
        providers = []
        for name, priority in [("openai", 1), ("google", 2), ("yandex", 3)]:
            provider = AsyncMock(spec=BaseTTSProvider)
            provider.provider_name = name
            provider.priority = priority
            provider.synthesize_speech = AsyncMock()
            provider.health_check = AsyncMock(return_value=True)
            provider.cleanup = AsyncMock()
            providers.append(provider)
        return providers

    @pytest.fixture
    def sample_request(self) -> TTSRequest:
        """Sample TTS request for testing."""
        return TTSRequest(
            text="Test synthesis text",
            voice="alloy",
            language="en-US",
            quality=TTSQuality.STANDARD,
            speed=1.0,
            output_format=AudioFormat.MP3
        )

    @pytest.fixture
    def sample_result(self) -> TTSResult:
        """Sample TTS result for testing."""
        return TTSResult(
            audio_url="https://storage.example.com/test.mp3",
            text_length=18,
            processing_time=1.5,
            voice_used="alloy",
            language_used="en-US",
            provider_metadata={
                "provider": "openai",
                "model": "tts-1"
            }
        )

    # Phase 1.3 Architecture Compliance Tests

    def test_orchestrator_lsp_compliance(self, orchestrator):
        """Test LSP compliance - orchestrator должен работать с любыми BaseTTSProvider implementations."""
        # Should work with abstract provider interface
        assert hasattr(orchestrator, 'synthesize_speech')
        assert hasattr(orchestrator, 'get_available_providers')
        assert hasattr(orchestrator, 'health_check_all_providers')

        # Should not depend on specific provider implementations
        assert not hasattr(orchestrator, '_openai_specific_method')
        assert not hasattr(orchestrator, '_google_specific_method')
        assert not hasattr(orchestrator, '_yandex_specific_method')

    def test_solid_srp_single_responsibility(self, orchestrator):
        """Test Single Responsibility Principle - только orchestration logic."""
        # Should only handle orchestration and coordination
        orchestration_methods = {
            'synthesize_speech', 'get_available_providers',
            'health_check_all_providers', 'configure_fallback',
            'configure_circuit_breaker'
        }

        # Should not contain provider-specific synthesis logic
        prohibited_methods = {
            'create_audio_file', 'convert_format', 'upload_to_storage',
            'parse_ssml', 'validate_voice_parameters'
        }

        for method in orchestration_methods:
            assert hasattr(orchestrator, method), f"Missing orchestration method: {method}"

        for method in prohibited_methods:
            assert not hasattr(orchestrator, method), f"Should not have method: {method}"

    def test_solid_ocp_open_closed_principle(self, orchestrator):
        """Test Open/Closed Principle - extensible для новых fallback strategies."""
        # Configuration should be modifiable without changing core logic
        original_fallback = orchestrator._fallback_enabled
        original_circuit_breaker = orchestrator._circuit_breaker_enabled

        # Should be extensible through configuration
        orchestrator.configure_fallback(not original_fallback)
        orchestrator.configure_circuit_breaker(not original_circuit_breaker)

        assert orchestrator._fallback_enabled != original_fallback
        assert orchestrator._circuit_breaker_enabled != original_circuit_breaker

        # Reset to original state
        orchestrator.configure_fallback(original_fallback)
        orchestrator.configure_circuit_breaker(original_circuit_breaker)

    def test_solid_isp_interface_segregation(self, orchestrator):
        """Test Interface Segregation - focused orchestration interface."""
        # Should have minimal, focused interface
        required_methods = {
            'synthesize_speech', 'get_available_providers',
            'health_check_all_providers', 'initialize', 'cleanup'
        }

        # Should not mix unrelated functionality
        prohibited_methods = {
            'create_user_account', 'process_payment', 'send_email',
            'validate_credentials', 'generate_report'
        }

        for method in required_methods:
            assert callable(getattr(orchestrator, method, None)), f"Missing method: {method}"

        for method in prohibited_methods:
            assert not hasattr(orchestrator, method), f"Should not have method: {method}"

    def test_solid_dip_dependency_inversion(self, orchestrator):
        """Test Dependency Inversion - depends on factory abstraction."""
        # Should depend on factory abstraction, not concrete implementations
        assert hasattr(orchestrator, '_factory')

        # Factory should be injectable through constructor
        new_factory = AsyncMock(spec=TTSProviderFactory)
        new_orchestrator = TTSOrchestrator(new_factory)
        assert new_orchestrator._factory is new_factory

    # Orchestrator Initialization Tests

    @pytest.mark.asyncio
    async def test_orchestrator_initialization_success(self, orchestrator, providers_config):
        """Test successful orchestrator initialization."""
        await orchestrator.initialize(providers_config)

        # Should initialize factory
        orchestrator._factory.initialize.assert_called_once_with(providers_config)

        # Should setup health tracking
        assert len(orchestrator._provider_health) == 3
        assert "openai" in orchestrator._provider_health
        assert "google" in orchestrator._provider_health
        assert "yandex" in orchestrator._provider_health

        # All providers should start as healthy
        for health_status in orchestrator._provider_health.values():
            assert health_status.is_healthy is True
            assert health_status.consecutive_failures == 0

    @pytest.mark.asyncio
    async def test_orchestrator_initialization_factory_failure(self, orchestrator, providers_config):
        """Test orchestrator initialization when factory fails."""
        orchestrator._factory.initialize.side_effect = Exception("Factory initialization failed")

        with pytest.raises(VoiceServiceError, match="TTS Orchestrator initialization failed"):
            await orchestrator.initialize(providers_config)

    @pytest.mark.asyncio
    async def test_orchestrator_initialization_empty_config(self, orchestrator):
        """Test orchestrator initialization with empty configuration."""
        await orchestrator.initialize([])

        orchestrator._factory.initialize.assert_called_once_with([])
        assert len(orchestrator._provider_health) == 0

    # Multi-Provider Synthesis Tests

    @pytest.mark.asyncio
    async def test_synthesize_speech_first_provider_success(self, orchestrator, providers_config, mock_providers, sample_request, sample_result):
        """Test synthesis success with first provider."""
        await orchestrator.initialize(providers_config)

        # Setup successful first provider
        mock_providers[0].synthesize_speech.return_value = sample_result
        orchestrator._factory.get_available_providers.return_value = mock_providers

        result = await orchestrator.synthesize_speech(sample_request, providers_config)

        assert result is sample_result
        # Only first provider should be called
        mock_providers[0].synthesize_speech.assert_called_once_with(sample_request)
        mock_providers[1].synthesize_speech.assert_not_called()
        mock_providers[2].synthesize_speech.assert_not_called()

        # Should include orchestrator metadata
        assert "orchestrator_info" in result.provider_metadata
        assert result.provider_metadata["orchestrator_info"]["successful_provider"] == "openai"
        assert result.provider_metadata["orchestrator_info"]["fallback_used"] is False

    @pytest.mark.asyncio
    async def test_synthesize_speech_fallback_success(self, orchestrator, providers_config, mock_providers, sample_request, sample_result):
        """Test synthesis success with fallback to second provider."""
        await orchestrator.initialize(providers_config)

        # First provider fails, second succeeds
        mock_providers[0].synthesize_speech.side_effect = AudioProcessingError("Provider 1 failed")
        mock_providers[1].synthesize_speech.return_value = sample_result
        orchestrator._factory.get_available_providers.return_value = mock_providers

        result = await orchestrator.synthesize_speech(sample_request, providers_config)

        assert result is sample_result
        # Both providers should be attempted
        mock_providers[0].synthesize_speech.assert_called_once()
        mock_providers[1].synthesize_speech.assert_called_once_with(sample_request)
        mock_providers[2].synthesize_speech.assert_not_called()

        # Should indicate fallback was used
        assert result.provider_metadata["orchestrator_info"]["fallback_used"] is True
        assert result.provider_metadata["orchestrator_info"]["attempted_providers"] == ["openai", "google"]
        assert result.provider_metadata["orchestrator_info"]["successful_provider"] == "google"

    @pytest.mark.asyncio
    async def test_synthesize_speech_all_providers_fail(self, orchestrator, providers_config, mock_providers, sample_request):
        """Test synthesis when all providers fail."""
        await orchestrator.initialize(providers_config)

        # All providers fail
        for provider in mock_providers:
            provider.synthesize_speech.side_effect = AudioProcessingError(f"{provider.provider_name} failed")

        orchestrator._factory.get_available_providers.return_value = mock_providers

        with pytest.raises(AudioProcessingError, match="All TTS providers failed"):
            await orchestrator.synthesize_speech(sample_request, providers_config)

        # All providers should be attempted
        for provider in mock_providers:
            provider.synthesize_speech.assert_called_once()

    @pytest.mark.asyncio
    async def test_synthesize_speech_no_healthy_providers(self, orchestrator, providers_config, sample_request):
        """Test synthesis when no healthy providers available."""
        await orchestrator.initialize(providers_config)

        # No healthy providers
        orchestrator._factory.get_available_providers.return_value = []

        with pytest.raises(ProviderNotAvailableError, match="No healthy TTS providers available"):
            await orchestrator.synthesize_speech(sample_request, providers_config)

    @pytest.mark.asyncio
    async def test_synthesize_speech_fallback_disabled(self, orchestrator, providers_config, mock_providers, sample_request):
        """Test synthesis behavior when fallback is disabled."""
        await orchestrator.initialize(providers_config)
        orchestrator.configure_fallback(False)

        # First provider fails
        mock_providers[0].synthesize_speech.side_effect = AudioProcessingError("Provider failed")
        orchestrator._factory.get_available_providers.return_value = mock_providers

        with pytest.raises(AudioProcessingError):
            await orchestrator.synthesize_speech(sample_request, providers_config)

        # Only first provider should be attempted
        mock_providers[0].synthesize_speech.assert_called_once()
        mock_providers[1].synthesize_speech.assert_not_called()
        mock_providers[2].synthesize_speech.assert_not_called()

    # Circuit Breaker Pattern Tests

    @pytest.mark.asyncio
    async def test_circuit_breaker_blocks_unhealthy_provider(self, orchestrator, providers_config, mock_providers, sample_request):
        """Test circuit breaker blocks provider after consecutive failures."""
        await orchestrator.initialize(providers_config)

        # Simulate consecutive failures for first provider
        provider_health = orchestrator._provider_health["openai"]
        provider_health.consecutive_failures = orchestrator.MAX_CONSECUTIVE_FAILURES
        provider_health.is_healthy = False
        provider_health.last_failure_time = time.time()

        # Setup second provider to succeed
        sample_result = TTSResult(
            audio_url="https://storage.example.com/test.mp3",
            text_length=18,
            processing_time=1.5,
            voice_used="google-voice",
            language_used="en-US",
            provider_metadata={"provider": "google"}
        )
        mock_providers[1].synthesize_speech.return_value = sample_result

        # Only return healthy providers (circuit breaker should filter)
        orchestrator._factory.get_available_providers.return_value = mock_providers[1:]

        result = await orchestrator.synthesize_speech(sample_request, providers_config)

        # First provider should be skipped due to circuit breaker
        mock_providers[0].synthesize_speech.assert_not_called()
        mock_providers[1].synthesize_speech.assert_called_once()

    @pytest.mark.asyncio
    async def test_circuit_breaker_recovery_after_timeout(self, orchestrator, providers_config, mock_providers, sample_request, sample_result):
        """Test circuit breaker allows provider after recovery timeout."""
        await orchestrator.initialize(providers_config)

        # Simulate provider in circuit breaker state but timeout expired
        provider_health = orchestrator._provider_health["openai"]
        provider_health.consecutive_failures = orchestrator.MAX_CONSECUTIVE_FAILURES
        provider_health.is_healthy = False
        provider_health.last_failure_time = time.time() - (orchestrator.CIRCUIT_BREAKER_TIMEOUT + 1)

        # Setup provider to succeed on recovery
        mock_providers[0].synthesize_speech.return_value = sample_result
        orchestrator._factory.get_available_providers.return_value = mock_providers

        result = await orchestrator.synthesize_speech(sample_request, providers_config)

        # Provider should be allowed to attempt recovery
        mock_providers[0].synthesize_speech.assert_called_once()
        assert result is sample_result

    @pytest.mark.asyncio
    async def test_circuit_breaker_disabled(self, orchestrator, providers_config, mock_providers, sample_request, sample_result):
        """Test synthesis when circuit breaker is disabled."""
        await orchestrator.initialize(providers_config)
        orchestrator.configure_circuit_breaker(False)

        # Simulate unhealthy provider
        provider_health = orchestrator._provider_health["openai"]
        provider_health.consecutive_failures = orchestrator.MAX_CONSECUTIVE_FAILURES
        provider_health.is_healthy = False

        # Setup provider to succeed anyway
        mock_providers[0].synthesize_speech.return_value = sample_result
        orchestrator._factory.get_available_providers.return_value = mock_providers

        result = await orchestrator.synthesize_speech(sample_request, providers_config)

        # Provider should still be attempted when circuit breaker disabled
        mock_providers[0].synthesize_speech.assert_called_once()
        assert result is sample_result

    # Health Monitoring Tests

    @pytest.mark.asyncio
    async def test_health_status_update_all_healthy(self, orchestrator, providers_config):
        """Test health status update when all providers healthy."""
        await orchestrator.initialize(providers_config)

        # Mock all providers as healthy
        orchestrator._factory.health_check_all_providers.return_value = {
            "openai": True,
            "google": True,
            "yandex": True
        }

        await orchestrator._update_health_status(force=True)

        # All providers should be marked healthy
        for provider_name in ["openai", "google", "yandex"]:
            health_status = orchestrator._provider_health[provider_name]
            assert health_status.is_healthy is True
            assert health_status.consecutive_failures == 0

    @pytest.mark.asyncio
    async def test_health_status_update_some_unhealthy(self, orchestrator, providers_config):
        """Test health status update with mixed provider health."""
        await orchestrator.initialize(providers_config)

        # Mock mixed health status
        orchestrator._factory.health_check_all_providers.return_value = {
            "openai": True,
            "google": False,
            "yandex": True
        }

        await orchestrator._update_health_status(force=True)

        # Health status should be updated accordingly
        assert orchestrator._provider_health["openai"].is_healthy is True
        assert orchestrator._provider_health["google"].is_healthy is False
        assert orchestrator._provider_health["yandex"].is_healthy is True

        # Unhealthy provider should have increased failure count
        assert orchestrator._provider_health["google"].consecutive_failures == 1

    @pytest.mark.asyncio
    async def test_provider_recovery_detection(self, orchestrator, providers_config):
        """Test detection of provider recovery."""
        await orchestrator.initialize(providers_config)

        # Start with unhealthy provider
        provider_health = orchestrator._provider_health["openai"]
        provider_health.is_healthy = False
        provider_health.consecutive_failures = 2

        # Mock provider becoming healthy
        orchestrator._factory.health_check_all_providers.return_value = {
            "openai": True,
            "google": True,
            "yandex": True
        }

        await orchestrator._update_health_status(force=True)

        # Provider should be marked as recovered
        assert provider_health.is_healthy is True
        assert provider_health.consecutive_failures == 0
        assert provider_health.recovery_time is not None

    @pytest.mark.asyncio
    async def test_health_check_rate_limiting(self, orchestrator, providers_config):
        """Test health check rate limiting behavior."""
        await orchestrator.initialize(providers_config)

        # Set last check time to recent
        orchestrator._last_health_check = time.time()

        # Mock factory health check
        orchestrator._factory.health_check_all_providers.return_value = {}

        # Should not perform health check due to rate limiting
        await orchestrator._update_health_status(force=False)

        # Factory health check should not be called
        orchestrator._factory.health_check_all_providers.assert_not_called()

    # Retry Logic Tests

    @pytest.mark.asyncio
    async def test_retry_logic_success_on_second_attempt(self, orchestrator, providers_config, mock_providers, sample_request, sample_result):
        """Test retry logic succeeds on second attempt."""
        await orchestrator.initialize(providers_config)

        # First attempt fails, second succeeds
        mock_providers[0].synthesize_speech.side_effect = [
            AudioProcessingError("Temporary failure"),
            sample_result
        ]
        orchestrator._factory.get_available_providers.return_value = [mock_providers[0]]

        result = await orchestrator.synthesize_speech(sample_request, providers_config)

        assert result is sample_result
        # Should have retried
        assert mock_providers[0].synthesize_speech.call_count == 2

    @pytest.mark.asyncio
    async def test_retry_logic_no_retry_for_auth_errors(self, orchestrator, providers_config, mock_providers, sample_request):
        """Test retry logic doesn't retry authentication errors."""
        await orchestrator.initialize(providers_config)

        # Provider fails with auth error
        mock_providers[0].synthesize_speech.side_effect = ProviderNotAvailableError("Authentication failed")
        orchestrator._factory.get_available_providers.return_value = mock_providers

        with pytest.raises(AudioProcessingError, match="All TTS providers failed"):
            await orchestrator.synthesize_speech(sample_request, providers_config)

        # Should not retry auth errors
        assert mock_providers[0].synthesize_speech.call_count == 1

    # Provider Health Tracking Tests

    @pytest.mark.asyncio
    async def test_mark_provider_healthy_after_success(self, orchestrator, providers_config, mock_providers, sample_request, sample_result):
        """Test provider marked as healthy after successful synthesis."""
        await orchestrator.initialize(providers_config)

        # Start with unhealthy provider
        provider_health = orchestrator._provider_health["openai"]
        provider_health.is_healthy = False
        provider_health.consecutive_failures = 2

        # Provider succeeds
        mock_providers[0].synthesize_speech.return_value = sample_result
        orchestrator._factory.get_available_providers.return_value = [mock_providers[0]]

        await orchestrator.synthesize_speech(sample_request, providers_config)

        # Provider should be marked healthy
        assert provider_health.is_healthy is True
        assert provider_health.consecutive_failures == 0
        assert provider_health.recovery_time is not None

    @pytest.mark.asyncio
    async def test_mark_provider_unhealthy_after_failure(self, orchestrator, providers_config, mock_providers, sample_request):
        """Test provider marked as unhealthy after failure."""
        await orchestrator.initialize(providers_config)

        # All providers fail
        for provider in mock_providers:
            provider.synthesize_speech.side_effect = AudioProcessingError(f"{provider.provider_name} failed")

        orchestrator._factory.get_available_providers.return_value = mock_providers

        with pytest.raises(AudioProcessingError):
            await orchestrator.synthesize_speech(sample_request, providers_config)

        # All providers should be marked unhealthy
        for provider_name in ["openai", "google", "yandex"]:
            health_status = orchestrator._provider_health[provider_name]
            assert health_status.is_healthy is False
            assert health_status.consecutive_failures == 1
            assert health_status.last_failure_time is not None

    # Configuration and Status Tests

    def test_get_provider_health_status_detailed(self, orchestrator, providers_config):
        """Test detailed provider health status retrieval."""
        # Setup some health status data
        health_status = ProviderHealthStatus(
            provider_name="openai",
            is_healthy=False,
            last_check_time=time.time(),
            consecutive_failures=2,
            last_failure_time=time.time() - 30,
            recovery_time=None
        )
        orchestrator._provider_health["openai"] = health_status

        detailed_status = orchestrator.get_provider_health_status()

        assert "openai" in detailed_status
        openai_status = detailed_status["openai"]
        assert openai_status["is_healthy"] is False
        assert openai_status["consecutive_failures"] == 2
        assert openai_status["last_failure_time"] is not None
        assert "circuit_breaker_active" in openai_status

    def test_fallback_configuration(self, orchestrator):
        """Test fallback mechanism configuration."""
        # Test enabling fallback
        orchestrator.configure_fallback(True)
        assert orchestrator._fallback_enabled is True

        # Test disabling fallback
        orchestrator.configure_fallback(False)
        assert orchestrator._fallback_enabled is False

    def test_circuit_breaker_configuration(self, orchestrator):
        """Test circuit breaker configuration."""
        # Test enabling circuit breaker
        orchestrator.configure_circuit_breaker(True)
        assert orchestrator._circuit_breaker_enabled is True

        # Test disabling circuit breaker
        orchestrator.configure_circuit_breaker(False)
        assert orchestrator._circuit_breaker_enabled is False

    # Cleanup Tests

    @pytest.mark.asyncio
    async def test_orchestrator_cleanup(self, orchestrator, providers_config):
        """Test orchestrator cleanup."""
        await orchestrator.initialize(providers_config)

        # Add some health status data
        orchestrator._provider_health["test"] = ProviderHealthStatus(
            provider_name="test",
            is_healthy=True,
            last_check_time=time.time(),
            consecutive_failures=0
        )

        await orchestrator.cleanup()

        # Should cleanup factory and clear health data
        orchestrator._factory.cleanup.assert_called_once()
        assert len(orchestrator._provider_health) == 0

    # Module-level Function Tests

    @pytest.mark.asyncio
    async def test_module_level_convenience_function(self, sample_request, providers_config, sample_result):
        """Test module-level convenience function."""
        with patch('app.services.voice_v2.providers.tts.orchestrator.tts_orchestrator') as mock_global_orchestrator:
            mock_global_orchestrator.synthesize_speech.return_value = sample_result

            from app.services.voice_v2.providers.tts.orchestrator import synthesize_speech_with_fallback

            result = await synthesize_speech_with_fallback(sample_request, providers_config)

            assert result is sample_result
            mock_global_orchestrator.synthesize_speech.assert_called_once_with(sample_request, providers_config)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
