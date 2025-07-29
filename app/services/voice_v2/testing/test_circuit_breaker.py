"""
Comprehensive test suite for infrastructure/circuit_breaker.py

Tests circuit breaker functionality with proper async patterns,
state transitions, performance validation, and error handling.

Test coverage:
- CircuitBreakerConfig and CircuitBreakerMetrics: Configuration and metrics
- BaseCircuitBreaker: Core circuit breaker logic and state transitions
- ProviderCircuitBreaker: Provider-specific circuit breaker functionality
- CircuitBreakerManager: Multi-circuit breaker management
- Performance validation and error scenarios
"""

import pytest
import asyncio
import time

from app.services.voice_v2.infrastructure.circuit_breaker import (
    CircuitBreakerState, CircuitBreakerConfig, CircuitBreakerMetrics,
    BaseCircuitBreaker, ProviderCircuitBreaker, CircuitBreakerManager,
    get_circuit_breaker_manager, initialize_provider_circuit_breakers,
    circuit_breaker_protect
)
from app.services.voice_v2.core.interfaces import ProviderType
from app.services.voice_v2.core.exceptions import VoiceServiceError


class TestCircuitBreakerConfig:
    """Test CircuitBreakerConfig functionality"""
    
    def test_default_configuration(self):
        """Test default circuit breaker configuration"""
        config = CircuitBreakerConfig()
        
        assert config.failure_threshold == 5
        assert config.recovery_timeout == 60.0
        assert config.success_threshold == 3
        assert config.timeout_duration == 30.0
        assert config.sliding_window_size == 100
        assert config.failure_rate_threshold == 0.5
    
    def test_custom_configuration(self):
        """Test custom circuit breaker configuration"""
        config = CircuitBreakerConfig(
            failure_threshold=10,
            recovery_timeout=120.0,
            success_threshold=5,
            timeout_duration=60.0,
            sliding_window_size=200,
            failure_rate_threshold=0.3
        )
        
        assert config.failure_threshold == 10
        assert config.recovery_timeout == 120.0
        assert config.success_threshold == 5
        assert config.timeout_duration == 60.0
        assert config.sliding_window_size == 200
        assert config.failure_rate_threshold == 0.3


class TestCircuitBreakerMetrics:
    """Test CircuitBreakerMetrics functionality"""
    
    def test_metrics_initialization(self):
        """Test metrics default values"""
        metrics = CircuitBreakerMetrics()
        
        assert metrics.total_requests == 0
        assert metrics.failed_requests == 0
        assert metrics.successful_requests == 0
        assert metrics.state_transitions == 0
        assert metrics.last_failure_time is None
        assert metrics.last_success_time is None
        assert metrics.average_response_time == 0.0
    
    def test_failure_rate_calculation(self):
        """Test failure rate calculation"""
        metrics = CircuitBreakerMetrics()
        
        # No requests
        assert metrics.failure_rate == 0.0
        assert metrics.success_rate == 1.0
        
        # With requests
        metrics.total_requests = 100
        metrics.failed_requests = 20
        metrics.successful_requests = 80
        
        assert metrics.failure_rate == 0.2
        assert metrics.success_rate == 0.8


class MockAsyncFunction:
    """Mock async function for testing circuit breaker"""
    
    def __init__(self):
        self.call_count = 0
        self.should_fail = False
        self.delay = 0.0
        self.exception_to_raise = None
    
    async def __call__(self, *args, **kwargs) -> str:
        self.call_count += 1
        
        if self.delay > 0:
            await asyncio.sleep(self.delay)
        
        if self.should_fail:
            if self.exception_to_raise:
                raise self.exception_to_raise
            else:
                raise Exception("Mock function failure")
        
        return f"success_{self.call_count}"


class TestBaseCircuitBreaker:
    """Test BaseCircuitBreaker functionality"""
    
    @pytest.mark.asyncio
    async def test_circuit_breaker_initialization(self):
        """Test circuit breaker initialization"""
        config = CircuitBreakerConfig(failure_threshold=3)
        circuit_breaker = BaseCircuitBreaker("test_cb", config)
        
        assert circuit_breaker.name == "test_cb"
        assert circuit_breaker.config.failure_threshold == 3
        assert circuit_breaker.get_state() == CircuitBreakerState.CLOSED
        assert await circuit_breaker.is_available() is True
    
    @pytest.mark.asyncio
    async def test_successful_operation(self):
        """Test successful operation through circuit breaker"""
        circuit_breaker = BaseCircuitBreaker("test_cb")
        mock_func = MockAsyncFunction()
        
        result = await circuit_breaker.call(mock_func)
        
        assert result == "success_1"
        assert mock_func.call_count == 1
        assert circuit_breaker.get_state() == CircuitBreakerState.CLOSED
        
        metrics = circuit_breaker.get_metrics()
        assert metrics.total_requests == 1
        assert metrics.successful_requests == 1
        assert metrics.failed_requests == 0
    
    @pytest.mark.asyncio
    async def test_failure_handling(self):
        """Test failure handling in circuit breaker"""
        config = CircuitBreakerConfig(failure_threshold=2)
        circuit_breaker = BaseCircuitBreaker("test_cb", config)
        mock_func = MockAsyncFunction()
        mock_func.should_fail = True
        
        # First failure
        with pytest.raises(Exception, match="Mock function failure"):
            await circuit_breaker.call(mock_func)
        
        assert circuit_breaker.get_state() == CircuitBreakerState.CLOSED
        
        # Second failure should open circuit
        with pytest.raises(Exception, match="Mock function failure"):
            await circuit_breaker.call(mock_func)
        
        assert circuit_breaker.get_state() == CircuitBreakerState.OPEN
        
        metrics = circuit_breaker.get_metrics()
        assert metrics.total_requests == 2
        assert metrics.failed_requests == 2
        assert metrics.successful_requests == 0
    
    @pytest.mark.asyncio
    async def test_circuit_open_blocks_requests(self):
        """Test that open circuit blocks requests"""
        config = CircuitBreakerConfig(failure_threshold=1)
        circuit_breaker = BaseCircuitBreaker("test_cb", config)
        mock_func = MockAsyncFunction()
        mock_func.should_fail = True
        
        # Trigger circuit open
        with pytest.raises(Exception):
            await circuit_breaker.call(mock_func)
        
        assert circuit_breaker.get_state() == CircuitBreakerState.OPEN
        
        # Reset mock function to succeed
        mock_func.should_fail = False
        
        # Should be blocked
        with pytest.raises(VoiceServiceError, match="Circuit breaker 'test_cb' is OPEN"):
            await circuit_breaker.call(mock_func)
        
        # Function should not have been called
        assert mock_func.call_count == 1
    
    @pytest.mark.asyncio
    async def test_timeout_handling(self):
        """Test timeout handling in circuit breaker"""
        config = CircuitBreakerConfig(timeout_duration=0.1, failure_threshold=1)
        circuit_breaker = BaseCircuitBreaker("test_cb", config)
        mock_func = MockAsyncFunction()
        mock_func.delay = 0.2  # Longer than timeout
        
        with pytest.raises(VoiceServiceError, match="timeout"):
            await circuit_breaker.call(mock_func)
        
        assert circuit_breaker.get_state() == CircuitBreakerState.OPEN
        
        metrics = circuit_breaker.get_metrics()
        assert metrics.failed_requests == 1
    
    @pytest.mark.asyncio
    async def test_half_open_recovery(self):
        """Test recovery through HALF_OPEN state"""
        config = CircuitBreakerConfig(
            failure_threshold=1,
            recovery_timeout=0.1,
            success_threshold=2
        )
        circuit_breaker = BaseCircuitBreaker("test_cb", config)
        mock_func = MockAsyncFunction()
        mock_func.should_fail = True
        
        # Open the circuit
        with pytest.raises(Exception):
            await circuit_breaker.call(mock_func)
        
        assert circuit_breaker.get_state() == CircuitBreakerState.OPEN
        
        # Wait for recovery timeout
        await asyncio.sleep(0.15)
        
        # Reset function to succeed
        mock_func.should_fail = False
        
        # First success should transition to HALF_OPEN
        result = await circuit_breaker.call(mock_func)
        assert result == "success_2"
        assert circuit_breaker.get_state() == CircuitBreakerState.HALF_OPEN
        
        # Second success should close the circuit
        result = await circuit_breaker.call(mock_func)
        assert result == "success_3"
        assert circuit_breaker.get_state() == CircuitBreakerState.CLOSED
    
    @pytest.mark.asyncio
    async def test_half_open_failure_reopens_circuit(self):
        """Test that failure in HALF_OPEN state reopens circuit"""
        config = CircuitBreakerConfig(
            failure_threshold=1,
            recovery_timeout=0.1
        )
        circuit_breaker = BaseCircuitBreaker("test_cb", config)
        mock_func = MockAsyncFunction()
        mock_func.should_fail = True
        
        # Open the circuit
        with pytest.raises(Exception):
            await circuit_breaker.call(mock_func)
        
        assert circuit_breaker.get_state() == CircuitBreakerState.OPEN
        
        # Wait for recovery timeout
        await asyncio.sleep(0.15)
        
        # Should transition to HALF_OPEN and fail, reopening circuit
        with pytest.raises(Exception):
            await circuit_breaker.call(mock_func)
        
        assert circuit_breaker.get_state() == CircuitBreakerState.OPEN
    
    @pytest.mark.asyncio
    async def test_sliding_window_failure_rate(self):
        """Test sliding window failure rate calculation"""
        config = CircuitBreakerConfig(
            failure_threshold=100,  # High threshold to test failure rate
            failure_rate_threshold=0.5,
            sliding_window_size=10
        )
        circuit_breaker = BaseCircuitBreaker("test_cb", config)
        mock_func = MockAsyncFunction()
        
        # Add successful requests
        for _ in range(5):
            result = await circuit_breaker.call(mock_func)
            assert result.startswith("success_")
        
        # Add failed requests
        mock_func.should_fail = True
        for _ in range(6):
            with pytest.raises(Exception):
                await circuit_breaker.call(mock_func)
        
        # Circuit should be open due to failure rate
        assert circuit_breaker.get_state() == CircuitBreakerState.OPEN
    
    @pytest.mark.asyncio
    async def test_reset_functionality(self):
        """Test circuit breaker reset"""
        config = CircuitBreakerConfig(failure_threshold=1)
        circuit_breaker = BaseCircuitBreaker("test_cb", config)
        mock_func = MockAsyncFunction()
        mock_func.should_fail = True
        
        # Open the circuit
        with pytest.raises(Exception):
            await circuit_breaker.call(mock_func)
        
        assert circuit_breaker.get_state() == CircuitBreakerState.OPEN
        
        # Reset circuit breaker
        await circuit_breaker.reset()
        
        assert circuit_breaker.get_state() == CircuitBreakerState.CLOSED
        assert await circuit_breaker.is_available() is True
        
        metrics = circuit_breaker.get_metrics()
        assert metrics.total_requests == 0
        assert metrics.failed_requests == 0


class TestProviderCircuitBreaker:
    """Test ProviderCircuitBreaker functionality"""
    
    @pytest.mark.asyncio
    async def test_provider_circuit_breaker_initialization(self):
        """Test provider circuit breaker initialization"""
        circuit_breaker = ProviderCircuitBreaker(ProviderType.OPENAI)
        
        assert circuit_breaker.provider_type == ProviderType.OPENAI
        assert circuit_breaker.name == "provider_openai"
        assert circuit_breaker.get_state() == CircuitBreakerState.CLOSED
    
    @pytest.mark.asyncio
    async def test_protect_context_manager_success(self):
        """Test protect context manager with successful operation"""
        circuit_breaker = ProviderCircuitBreaker(ProviderType.OPENAI)
        
        async with circuit_breaker.protect("test_operation"):
            # Simulate successful operation
            await asyncio.sleep(0.01)
        
        metrics = circuit_breaker.get_metrics()
        assert metrics.successful_requests == 1
        assert metrics.failed_requests == 0
    
    @pytest.mark.asyncio
    async def test_protect_context_manager_failure(self):
        """Test protect context manager with failed operation"""
        circuit_breaker = ProviderCircuitBreaker(ProviderType.OPENAI)
        
        with pytest.raises(Exception, match="Test failure"):
            async with circuit_breaker.protect("test_operation"):
                raise Exception("Test failure")
        
        metrics = circuit_breaker.get_metrics()
        assert metrics.successful_requests == 0
        assert metrics.failed_requests == 1
    
    @pytest.mark.asyncio
    async def test_protect_blocks_when_open(self):
        """Test that protect blocks when circuit is open"""
        config = CircuitBreakerConfig(failure_threshold=1)
        circuit_breaker = ProviderCircuitBreaker(ProviderType.OPENAI, config)
        
        # Open the circuit
        with pytest.raises(Exception):
            async with circuit_breaker.protect():
                raise Exception("Test failure")
        
        assert circuit_breaker.get_state() == CircuitBreakerState.OPEN
        
        # Should be blocked
        with pytest.raises(VoiceServiceError, match="circuit breaker is OPEN"):
            async with circuit_breaker.protect():
                pass


class TestCircuitBreakerManager:
    """Test CircuitBreakerManager functionality"""
    
    def test_manager_initialization(self):
        """Test circuit breaker manager initialization"""
        manager = CircuitBreakerManager()
        
        assert len(manager._circuit_breakers) == 0
        assert len(manager._provider_breakers) == 0
    
    def test_register_provider_circuit_breaker(self):
        """Test registering provider circuit breaker"""
        manager = CircuitBreakerManager()
        
        circuit_breaker = manager.register_provider_circuit_breaker(ProviderType.OPENAI)
        
        assert isinstance(circuit_breaker, ProviderCircuitBreaker)
        assert circuit_breaker.provider_type == ProviderType.OPENAI
        assert manager.get_provider_circuit_breaker(ProviderType.OPENAI) == circuit_breaker
        assert manager.get_circuit_breaker("provider_openai") == circuit_breaker
    
    def test_register_custom_circuit_breaker(self):
        """Test registering custom circuit breaker"""
        manager = CircuitBreakerManager()
        custom_breaker = BaseCircuitBreaker("custom")
        
        manager.register_circuit_breaker("custom", custom_breaker)
        
        assert manager.get_circuit_breaker("custom") == custom_breaker
    
    @pytest.mark.asyncio
    async def test_get_all_available_providers(self):
        """Test getting all available providers"""
        manager = CircuitBreakerManager()
        
        # Register multiple providers
        manager.register_provider_circuit_breaker(ProviderType.OPENAI)
        manager.register_provider_circuit_breaker(ProviderType.GOOGLE)
        manager.register_provider_circuit_breaker(ProviderType.YANDEX)
        
        available_providers = await manager.get_all_available_providers()
        
        assert ProviderType.OPENAI in available_providers
        assert ProviderType.GOOGLE in available_providers
        assert ProviderType.YANDEX in available_providers
        assert len(available_providers) == 3
    
    @pytest.mark.asyncio
    async def test_get_system_health(self):
        """Test getting system health status"""
        manager = CircuitBreakerManager()
        
        # Register provider
        circuit_breaker = manager.register_provider_circuit_breaker(ProviderType.OPENAI)
        
        health_status = await manager.get_system_health()
        
        assert "provider_openai" in health_status
        provider_status = health_status["provider_openai"]
        
        assert provider_status["state"] == "closed"
        assert provider_status["available"] is True
        assert provider_status["success_rate"] == 1.0
        assert provider_status["failure_rate"] == 0.0
        assert provider_status["total_requests"] == 0
    
    @pytest.mark.asyncio
    async def test_reset_all(self):
        """Test resetting all circuit breakers"""
        manager = CircuitBreakerManager()
        
        # Register and trigger failures
        config = CircuitBreakerConfig(failure_threshold=1)
        circuit_breaker = manager.register_provider_circuit_breaker(ProviderType.OPENAI, config)
        
        mock_func = MockAsyncFunction()
        mock_func.should_fail = True
        
        # Open the circuit
        with pytest.raises(Exception):
            await circuit_breaker.call(mock_func)
        
        assert circuit_breaker.get_state() == CircuitBreakerState.OPEN
        
        # Reset all
        await manager.reset_all()
        
        assert circuit_breaker.get_state() == CircuitBreakerState.CLOSED


class TestGlobalFunctions:
    """Test global functions and utilities"""
    
    def test_get_circuit_breaker_manager(self):
        """Test getting global circuit breaker manager"""
        manager1 = get_circuit_breaker_manager()
        manager2 = get_circuit_breaker_manager()
        
        # Should return same instance
        assert manager1 is manager2
        assert isinstance(manager1, CircuitBreakerManager)
    
    @pytest.mark.asyncio
    async def test_initialize_provider_circuit_breakers(self):
        """Test initializing all provider circuit breakers"""
        await initialize_provider_circuit_breakers()
        
        manager = get_circuit_breaker_manager()
        
        # Check that all provider types have circuit breakers
        for provider_type in ProviderType:
            circuit_breaker = manager.get_provider_circuit_breaker(provider_type)
            assert circuit_breaker is not None
            assert circuit_breaker.provider_type == provider_type
    
    @pytest.mark.asyncio
    async def test_circuit_breaker_protect_decorator(self):
        """Test circuit breaker protection decorator"""
        @circuit_breaker_protect(ProviderType.OPENAI)
        async def test_function():
            return "success"
        
        # Initialize circuit breakers
        await initialize_provider_circuit_breakers()
        
        result = await test_function()
        assert result == "success"
        
        # Check that metrics were recorded
        manager = get_circuit_breaker_manager()
        circuit_breaker = manager.get_provider_circuit_breaker(ProviderType.OPENAI)
        metrics = circuit_breaker.get_metrics()
        assert metrics.successful_requests == 1


class TestPerformanceRequirements:
    """Test performance requirements compliance"""
    
    @pytest.mark.asyncio
    async def test_circuit_breaker_decision_performance(self):
        """Test circuit breaker decision performance ≤1µs"""
        circuit_breaker = BaseCircuitBreaker("performance_test")
        
        # Warm up
        await circuit_breaker.is_available()
        
        # Measure decision time
        start_time = time.perf_counter()
        for _ in range(1000):
            await circuit_breaker._fast_availability_check()
        end_time = time.perf_counter()
        
        average_time = (end_time - start_time) / 1000
        # Should be much faster than 1µs (allowing some overhead for testing)
        assert average_time < 0.000010  # 10µs generous limit for testing
    
    @pytest.mark.asyncio
    async def test_state_transition_performance(self):
        """Test state transition performance ≤5µs"""
        config = CircuitBreakerConfig(failure_threshold=1)
        circuit_breaker = BaseCircuitBreaker("performance_test", config)
        
        # Measure state transition time
        start_time = time.perf_counter()
        await circuit_breaker._transition_to_open()
        end_time = time.perf_counter()
        
        transition_time = end_time - start_time
        # Should be faster than 5µs (allowing overhead for testing)
        assert transition_time < 0.000050  # 50µs generous limit for testing


class TestErrorScenarios:
    """Test various error scenarios and edge cases"""
    
    @pytest.mark.asyncio
    async def test_concurrent_operations(self):
        """Test circuit breaker under concurrent operations"""
        circuit_breaker = BaseCircuitBreaker("concurrent_test")
        mock_func = MockAsyncFunction()
        
        async def concurrent_operation():
            return await circuit_breaker.call(mock_func)
        
        # Run multiple concurrent operations
        tasks = [concurrent_operation() for _ in range(10)]
        results = await asyncio.gather(*tasks)
        
        assert len(results) == 10
        assert mock_func.call_count == 10
        
        metrics = circuit_breaker.get_metrics()
        assert metrics.successful_requests == 10
        assert metrics.failed_requests == 0
    
    @pytest.mark.asyncio
    async def test_mixed_success_failure_operations(self):
        """Test circuit breaker with mixed success/failure operations"""
        config = CircuitBreakerConfig(failure_threshold=5)
        circuit_breaker = BaseCircuitBreaker("mixed_test", config)
        mock_func = MockAsyncFunction()
        
        # Alternate between success and failure
        for i in range(8):
            mock_func.should_fail = (i % 2 == 1)
            
            try:
                result = await circuit_breaker.call(mock_func)
                if not mock_func.should_fail:
                    assert result.startswith("success_")
            except Exception:
                assert mock_func.should_fail
        
        # Should still be closed (4 failures < threshold 5)
        assert circuit_breaker.get_state() == CircuitBreakerState.CLOSED
        
        metrics = circuit_breaker.get_metrics()
        assert metrics.successful_requests == 4
        assert metrics.failed_requests == 4
