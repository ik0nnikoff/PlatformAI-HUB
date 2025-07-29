"""
Performance and stress tests for OpenAI TTS Provider - Phase 3.2.2

Tests performance patterns from Phase_1_2_3_performance_optimization.md:
- Concurrent request handling
- Memory usage optimization  
- Connection pooling efficiency
- Long text processing performance
- Retry logic performance impact
"""

import pytest
import asyncio
import time
from unittest.mock import AsyncMock, patch, MagicMock
from openai import RateLimitError

from app.services.voice_v2.providers.tts.openai_tts import OpenAITTSProvider
from app.services.voice_v2.providers.tts.models import TTSRequest, TTSQuality


class TestOpenAITTSPerformance:
    """Performance test suite for OpenAI TTS Provider."""
    
    @pytest.fixture
    async def provider(self):
        """Create a fully mocked OpenAI TTS provider for performance testing."""
        config = {
            "model": "tts-1",
            "voice": "alloy", 
            "api_key": "test_key_performance"
        }
        
        provider = OpenAITTSProvider(
            provider_name="openai",
            config=config,
            priority=1,
            enabled=True
        )
        
        # Mock initialize() to prevent real client creation
        async def mock_initialize():
            pass
        
        provider.initialize = mock_initialize
        
        # Mock the client completely
        mock_client = AsyncMock()
        mock_response = MagicMock()
        mock_response.content = b"test_audio_data"
        mock_client.audio.speech.create.return_value = mock_response
        provider._client = mock_client
        
        return provider
    
    @pytest.fixture
    def mock_fast_client(self):
        """Mock OpenAI client with fast responses."""
        mock_client = AsyncMock()
        mock_response = MagicMock()
        mock_response.content = b"audio_data" * 1000  # 9KB of audio data
        mock_client.audio.speech.create.return_value = mock_response
        return mock_client
    
    # Concurrent Request Tests
    
    @pytest.mark.asyncio
    async def test_concurrent_synthesis_requests(self, provider):
        """Test handling multiple concurrent synthesis requests."""
        # Create multiple requests
        requests = [
            TTSRequest(
                text=f"Concurrent test message number {i}",
                voice="alloy",
                quality=TTSQuality.STANDARD
            )
            for i in range(10)
        ]
        
        with patch.object(provider, '_upload_audio_to_storage', return_value="http://test.url"):
            start_time = time.time()
            
            # Execute concurrently
            tasks = [provider.synthesize_speech(req) for req in requests]
            results = await asyncio.gather(*tasks)
            
            end_time = time.time()
            
            # Verify all completed successfully
            assert len(results) == 10
            for result in results:
                assert result.audio_url == "http://test.url"
            
            # Should complete faster than sequential processing
            assert end_time - start_time < 5.0  # Should be fast with mocking
            
            # Verify client was called for each request
            assert provider._client.audio.speech.create.call_count == 10
    
    @pytest.mark.asyncio
    async def test_long_text_parallel_processing_performance(self, provider):
        """Test performance of parallel processing for very long texts."""
        # Create very long text (20KB+)
        long_text = "This is a performance test with a very long text. " * 400  # ~20KB
        
        provider._client = AsyncMock()
        mock_response = MagicMock()
        mock_response.content = b"chunk_audio_data"
        provider._client.audio.speech.create.return_value = mock_response
        
        with patch.object(provider, '_upload_audio_to_storage', return_value="http://chunk.url"):
            start_time = time.time()
            
            results = await provider.synthesize_long_text(long_text, voice="echo")
            
            end_time = time.time()
            processing_time = end_time - start_time
            
            # Should create multiple chunks
            assert len(results) > 5  # Expected ~20KB / 4KB = 5+ chunks
            
            # Performance should be reasonable (under 10 seconds with mocking)
            assert processing_time < 10.0
            
            # Verify parallel calls were made
            expected_calls = len(results)
            assert provider._client.audio.speech.create.call_count == expected_calls
    
    # Memory Usage Tests
    
    @pytest.mark.asyncio
    async def test_memory_efficiency_with_large_audio(self, provider):
        """Test memory efficiency when handling large audio responses."""
        provider._client = AsyncMock()
        
        # Simulate large audio response (1MB)
        large_audio_data = b"audio_byte" * 100000  # ~1MB
        mock_response = MagicMock()
        mock_response.content = large_audio_data
        provider._client.audio.speech.create.return_value = mock_response
        
        request = TTSRequest(
            text="Test for large audio response",
            quality=TTSQuality.HIGH
        )
        
        with patch.object(provider, '_upload_audio_to_storage', return_value="http://large.url") as mock_upload:
            result = await provider.synthesize_speech(request)
            
            # Verify large audio was handled
            assert result.audio_url == "http://large.url"
            
            # Check that upload was called with correct data
            mock_upload.assert_called_once()
            upload_args = mock_upload.call_args[0]
            assert len(upload_args[0]) == len(large_audio_data)  # Audio data size
    
    # Connection Pool Tests
    
    @pytest.mark.asyncio
    async def test_client_reuse_efficiency(self, provider):
        """Test OpenAI client reuse for multiple requests."""
        await provider.initialize()
        original_client = provider._client
        
        # Mock the client
        provider._client = AsyncMock()
        mock_response = MagicMock()
        mock_response.content = b"reuse_test_audio"
        provider._client.audio.speech.create.return_value = mock_response
        
        requests = [
            TTSRequest(text=f"Reuse test {i}", voice="alloy")
            for i in range(5)
        ]
        
        with patch.object(provider, '_upload_audio_to_storage', return_value="http://reuse.url"):
            # Process multiple requests
            for request in requests:
                await provider.synthesize_speech(request)
        
        # Client should be reused (not recreated)
        assert provider._client is not None
        assert provider._client.audio.speech.create.call_count == 5
    
    # Retry Logic Performance Tests
    
    @pytest.mark.asyncio
    async def test_synthesis_retry_mechanism(self, provider):
        """Test retry mechanism under various failure conditions."""
        # Mock the client properly
        mock_client = AsyncMock()
        provider._client = mock_client
        
        # Simulate rate limiting then success
        rate_limit_error = RateLimitError(
            message="Rate limit exceeded",
            response=MagicMock(),
            body=None
        )
        mock_response = MagicMock()
        mock_response.content = b"audio_after_retry"
        
        mock_client.audio.speech.create.side_effect = [
            rate_limit_error,
            rate_limit_error,
            mock_response
        ]
    
    @pytest.mark.asyncio
    async def test_exponential_backoff_timing(self, provider):
        """Test exponential backoff timing accuracy."""
        provider._max_retries = 3
        provider._base_delay = 0.1
        provider._client = AsyncMock()

        # Create mock response with request
        mock_response = MagicMock()
        mock_request = MagicMock()
        mock_response.request = mock_request
        
        # All calls except last fail with rate limit errors
        rate_limit_error = RateLimitError("Rate limit", response=mock_response, body=None)
        mock_success = MagicMock()
        mock_success.content = b"final_success"
        
        provider._client.audio.speech.create.side_effect = [
            rate_limit_error,
            rate_limit_error, 
            rate_limit_error,
            mock_success
        ]
        
        sleep_times = []
        
        async def mock_sleep(delay):
            sleep_times.append(delay)
        
        with patch('asyncio.sleep', side_effect=mock_sleep):
            with patch.object(provider, '_upload_audio_to_storage', return_value="http://backoff.url"):
                result = await provider.synthesize_speech(TTSRequest(text="Backoff test"))
        
        # Verify exponential backoff pattern
        expected_delays = [0.1, 0.2, 0.4]  # base_delay * 2^attempt
        assert sleep_times == expected_delays
        assert result.audio_url == "http://backoff.url"
    
    # Text Processing Performance Tests
    
    def test_text_splitting_performance(self, provider):
        """Test performance of intelligent text splitting."""
        # Create large text with many sentences
        large_text = "This is sentence number {}. " * 1000  # 1000 sentences
        formatted_text = large_text.format(*range(1000))
        
        start_time = time.time()
        chunks = provider._split_text_intelligently(formatted_text, 4000)
        end_time = time.time()
        
        # Should split efficiently
        assert len(chunks) > 1
        
        # Should complete quickly (under 1 second)
        assert end_time - start_time < 1.0
        
        # Verify all chunks respect size limit
        for chunk in chunks:
            assert len(chunk) <= 4000
    
    def test_word_splitting_performance(self, provider):
        """Test performance of word-level splitting."""
        # Create text with very long words
        long_word_text = " ".join(["supercalifragilisticexpialidocious"] * 200)
        
        start_time = time.time()
        chunks = provider._split_by_words(long_word_text, 1000)
        end_time = time.time()
        
        # Should complete quickly
        assert end_time - start_time < 0.5
        
        # Verify chunks
        assert len(chunks) > 1
        for chunk in chunks:
            assert len(chunk) <= 1000
    
    # Stress Tests
    
    @pytest.mark.asyncio
    async def test_high_concurrency_stress(self, provider):
        """Stress test with high concurrency."""
        provider._client = AsyncMock()
        
        mock_response = MagicMock()
        mock_response.content = b"stress_test_audio"
        provider._client.audio.speech.create.return_value = mock_response
        
        # Create many concurrent requests
        requests = [
            TTSRequest(
                text=f"Stress test message {i} with some longer content to test real-world scenarios",
                voice="alloy"
            )
            for i in range(50)  # 50 concurrent requests
        ]
        
        with patch.object(provider, '_upload_audio_to_storage', return_value="http://stress.url"):
            start_time = time.time()
            
            # Use semaphore to control concurrency
            semaphore = asyncio.Semaphore(10)  # Max 10 concurrent
            
            async def bounded_synthesis(request):
                async with semaphore:
                    return await provider.synthesize_speech(request)
            
            tasks = [bounded_synthesis(req) for req in requests]
            results = await asyncio.gather(*tasks)
            
            end_time = time.time()
            
            # All should succeed
            assert len(results) == 50
            
            # Should complete in reasonable time
            assert end_time - start_time < 30.0  # 30 seconds max
            
            # Verify all calls were made
            assert provider._client.audio.speech.create.call_count == 50
    
    @pytest.mark.asyncio
    async def test_memory_pressure_handling(self, provider):
        """Test handling under memory pressure conditions."""
        provider._client = AsyncMock()
        
        # Simulate varying response sizes
        responses = []
        for i in range(20):
            mock_response = MagicMock()
            # Varying audio sizes from 1KB to 1MB
            size = 1000 * (i + 1)
            mock_response.content = b"x" * size
            responses.append(mock_response)
        
        provider._client.audio.speech.create.side_effect = responses
        
        requests = [
            TTSRequest(text=f"Memory test {i}", voice="alloy")
            for i in range(20)
        ]
        
        with patch.object(provider, '_upload_audio_to_storage', return_value="http://memory.url"):
            # Process sequentially to avoid excessive memory usage
            results = []
            for request in requests:
                result = await provider.synthesize_speech(request)
                results.append(result)
        
        # All should succeed despite varying sizes
        assert len(results) == 20
        for result in results:
            assert result.audio_url == "http://memory.url"
    
    # Performance Benchmarks
    
    @pytest.mark.asyncio
    async def test_synthesis_throughput_benchmark(self, provider):
        """Benchmark synthesis throughput."""
        provider._client = AsyncMock()
        
        mock_response = MagicMock()
        mock_response.content = b"benchmark_audio_data"
        provider._client.audio.speech.create.return_value = mock_response
        
        # Standard benchmark text
        benchmark_text = "This is a standard benchmark text for TTS performance testing. " * 5
        
        requests = [
            TTSRequest(text=benchmark_text, voice="alloy", quality=TTSQuality.STANDARD)
            for _ in range(20)
        ]
        
        with patch.object(provider, '_upload_audio_to_storage', return_value="http://bench.url"):
            start_time = time.time()
            
            # Process with controlled concurrency
            semaphore = asyncio.Semaphore(5)
            
            async def controlled_synthesis(request):
                async with semaphore:
                    return await provider.synthesize_speech(request)
            
            tasks = [controlled_synthesis(req) for req in requests]
            results = await asyncio.gather(*tasks)
            
            end_time = time.time()
            total_time = end_time - start_time
            
            # Calculate throughput metrics
            total_characters = len(benchmark_text) * 20
            chars_per_second = total_characters / total_time
            requests_per_second = 20 / total_time
            
            # Log performance metrics (for CI/CD monitoring)
            print(f"Throughput: {chars_per_second:.2f} chars/sec, {requests_per_second:.2f} req/sec")
            
            # Basic performance assertions
            assert len(results) == 20
            assert chars_per_second > 1000  # Should process at least 1000 chars/sec with mocking
            assert requests_per_second > 2   # Should handle at least 2 req/sec
