"""
Simplified test suite for infrastructure/cache.py

Tests basic functionality with simplified async patterns.
Focus on core cache functionality without complex Redis mocking.
"""

import pytest
import hashlib
from unittest.mock import MagicMock, patch

from app.services.voice_v2.infrastructure.cache import (
    CacheKeyGenerator, CacheMetrics, VoiceCache,
    create_voice_cache
)
from app.services.voice_v2.core.interfaces import ProviderType, VoiceLanguage
from app.services.voice_v2.core.exceptions import VoiceServiceError


class TestCacheKeyGenerator:
    """Test CacheKeyGenerator functionality"""

    def test_stt_key_generation(self):
        """Test STT cache key generation"""
        audio_hash = "test_hash_123"
        provider = ProviderType.OPENAI
        language = VoiceLanguage.ENGLISH

        key = CacheKeyGenerator.stt_key(audio_hash, provider, language)
        expected = "voice_v2:stt:openai:en-US:test_hash_123"

        assert key == expected

    def test_stt_key_with_format(self):
        """Test STT key generation with format info"""
        audio_hash = "test_hash_456"
        provider = ProviderType.GOOGLE
        language = VoiceLanguage.RUSSIAN
        format_info = "wav_16khz"

        key = CacheKeyGenerator.stt_key(audio_hash, provider, language, format_info)
        expected = "voice_v2:stt:google:ru-RU:test_hash_456:wav_16khz"

        assert key == expected

    def test_tts_key_generation(self):
        """Test TTS cache key generation"""
        text_hash = "text_hash_789"
        provider = ProviderType.YANDEX
        voice = "jane"
        language = VoiceLanguage.RUSSIAN

        key = CacheKeyGenerator.tts_key(text_hash, provider, voice, language)
        expected = "voice_v2:tts:yandex:ru-RU:jane:text_hash_789"

        assert key == expected

    def test_tts_key_with_format(self):
        """Test TTS key generation with format info"""
        text_hash = "text_hash_abc"
        provider = ProviderType.OPENAI
        voice = "alloy"
        language = VoiceLanguage.ENGLISH
        format_info = "mp3_22khz"

        key = CacheKeyGenerator.tts_key(text_hash, provider, voice, language, format_info)
        expected = "voice_v2:tts:openai:en-US:alloy:text_hash_abc:mp3_22khz"

        assert key == expected

    def test_audio_hash(self):
        """Test audio data hashing"""
        audio_data = b"fake_audio_data_12345"
        expected_hash = hashlib.sha256(audio_data).hexdigest()

        result = CacheKeyGenerator.audio_hash(audio_data)

        assert result == expected_hash
        assert len(result) == 64  # SHA256 hex length

    def test_text_hash(self):
        """Test text hashing"""
        text = "Hello, this is a test text for TTS"
        expected_hash = hashlib.sha256(text.encode('utf-8')).hexdigest()

        result = CacheKeyGenerator.text_hash(text)

        assert result == expected_hash
        assert len(result) == 64  # SHA256 hex length

    def test_text_hash_unicode(self):
        """Test text hashing with unicode characters"""
        text = "Привет, это тестовый текст для TTS"
        expected_hash = hashlib.sha256(text.encode('utf-8')).hexdigest()

        result = CacheKeyGenerator.text_hash(text)

        assert result == expected_hash

    @patch('builtins.open')
    def test_file_hash_success(self, mock_open):
        """Test file content hashing"""
        # Mock file content
        file_content = b"fake_file_content_for_testing"
        mock_file = MagicMock()
        mock_file.read.side_effect = [file_content, b""]  # First read content, second read EOF
        mock_open.return_value.__enter__.return_value = mock_file

        expected_hash = hashlib.sha256(file_content).hexdigest()

        result = CacheKeyGenerator.file_hash("/fake/path/audio.wav")

        assert result == expected_hash
        mock_open.assert_called_once_with("/fake/path/audio.wav", "rb")

    @patch('builtins.open')
    def test_file_hash_error(self, mock_open):
        """Test file hashing error handling"""
        mock_open.side_effect = FileNotFoundError("File not found")

        with pytest.raises(VoiceServiceError, match="Failed to hash file"):
            CacheKeyGenerator.file_hash("/nonexistent/file.wav")


class TestCacheMetrics:
    """Test CacheMetrics functionality"""

    def test_cache_metrics_initialization(self):
        """Test CacheMetrics default values"""
        metrics = CacheMetrics()

        assert metrics.hits == 0
        assert metrics.misses == 0
        assert metrics.total_operations == 0
        assert metrics.average_latency_ms == 0.0
        assert metrics.error_count == 0
        assert metrics.last_error is None
        assert metrics.uptime_seconds == 0.0

    def test_hit_rate_calculation(self):
        """Test hit rate calculation"""
        metrics = CacheMetrics()

        # No operations
        assert metrics.hit_rate == 0.0

        # With operations
        metrics.hits = 80
        metrics.misses = 20
        metrics.total_operations = 100

        assert metrics.hit_rate == 0.8

    def test_error_rate_calculation(self):
        """Test error rate calculation"""
        metrics = CacheMetrics()

        # No operations
        assert metrics.error_rate == 0.0

        # With operations
        metrics.error_count = 5
        metrics.total_operations = 100

        assert metrics.error_rate == 0.05


class MockCacheBackend:
    """Simple mock cache backend for testing"""

    def __init__(self):
        self.storage = {}
        self.call_count = 0

    async def get(self, key: str):
        self.call_count += 1
        return self.storage.get(key)

    async def set(self, key: str, value: str, ttl_seconds: int):
        self.call_count += 1
        self.storage[key] = value

    async def delete(self, key: str):
        self.call_count += 1
        if key in self.storage:
            del self.storage[key]
            return True
        return False

    async def exists(self, key: str):
        self.call_count += 1
        return key in self.storage

    async def health_check(self):
        return True

    def get_metrics(self):
        return CacheMetrics()


class TestVoiceCache:
    """Test VoiceCache functionality with mock backend"""

    @pytest.mark.asyncio
    async def test_stt_cache_operations(self):
        """Test STT cache get/set operations"""
        mock_backend = MockCacheBackend()
        voice_cache = VoiceCache(mock_backend)

        # Test caching STT result
        audio_hash = "test_audio_hash"
        provider = ProviderType.OPENAI
        language = VoiceLanguage.ENGLISH
        result = "This is transcribed text"

        # Set operation
        await voice_cache.cache_stt_result(audio_hash, provider, language, result)

        # Get operation
        retrieved = await voice_cache.get_stt_result(audio_hash, provider, language)

        assert retrieved == result
        assert mock_backend.call_count == 2  # set + get

    @pytest.mark.asyncio
    async def test_tts_cache_operations(self):
        """Test TTS cache get/set operations"""
        mock_backend = MockCacheBackend()
        voice_cache = VoiceCache(mock_backend)

        # Test caching TTS result
        text_hash = "test_text_hash"
        provider = ProviderType.GOOGLE
        voice = "en-US-Wavenet-D"
        language = VoiceLanguage.ENGLISH
        audio_url = "https://storage.example.com/audio/test.wav"

        # Set operation
        await voice_cache.cache_tts_result(text_hash, provider, voice, language, audio_url)

        # Get operation
        retrieved = await voice_cache.get_tts_result(text_hash, provider, voice, language)

        assert retrieved == audio_url
        assert mock_backend.call_count == 2  # set + get

    @pytest.mark.asyncio
    @patch('app.services.voice_v2.infrastructure.cache.CacheKeyGenerator.file_hash')
    async def test_stt_cache_by_file(self, mock_file_hash):
        """Test STT cache operations using file path"""
        mock_file_hash.return_value = "file_hash_123"
        mock_backend = MockCacheBackend()
        voice_cache = VoiceCache(mock_backend)

        file_path = "/path/to/audio.wav"
        provider = ProviderType.YANDEX
        language = VoiceLanguage.RUSSIAN
        result = "Это транскрибированный текст"

        # Cache by file
        await voice_cache.cache_stt_result_by_file(file_path, provider, language, result)

        mock_file_hash.assert_called_with(file_path)

        # Get by file
        retrieved = await voice_cache.get_stt_result_by_file(file_path, provider, language)

        assert retrieved == result
        assert mock_file_hash.call_count == 2  # called for both set and get

    @pytest.mark.asyncio
    async def test_tts_cache_by_text(self):
        """Test TTS cache operations using text"""
        mock_backend = MockCacheBackend()
        voice_cache = VoiceCache(mock_backend)

        text = "Hello, this is a test text"
        provider = ProviderType.OPENAI
        voice = "alloy"
        language = VoiceLanguage.ENGLISH
        audio_url = "https://storage.example.com/audio/tts_result.mp3"

        # Cache by text
        await voice_cache.cache_tts_result_by_text(text, provider, voice, language, audio_url)

        # Get by text
        retrieved = await voice_cache.get_tts_result_by_text(text, provider, voice, language)

        assert retrieved == audio_url
        assert mock_backend.call_count == 2  # set + get

    @pytest.mark.asyncio
    async def test_custom_ttl(self):
        """Test custom TTL values"""
        mock_backend = MockCacheBackend()
        voice_cache = VoiceCache(mock_backend)

        audio_hash = "test_hash"
        provider = ProviderType.OPENAI
        language = VoiceLanguage.ENGLISH
        result = "test result"
        custom_ttl = 7200  # 2 hours

        await voice_cache.cache_stt_result(audio_hash, provider, language, result, custom_ttl)

        # Check that result was cached
        retrieved = await voice_cache.get_stt_result(audio_hash, provider, language)
        assert retrieved == result

    @pytest.mark.asyncio
    async def test_cache_miss(self):
        """Test cache miss scenario"""
        mock_backend = MockCacheBackend()
        voice_cache = VoiceCache(mock_backend)

        # Try to get non-existent key
        result = await voice_cache.get_stt_result("nonexistent", ProviderType.OPENAI, VoiceLanguage.ENGLISH)

        assert result is None
        assert mock_backend.call_count == 1  # get operation

    @pytest.mark.asyncio
    async def test_get_cache_stats(self):
        """Test cache statistics retrieval"""
        mock_backend = MockCacheBackend()
        voice_cache = VoiceCache(mock_backend)

        stats = await voice_cache.get_cache_stats()

        assert "backend_type" in stats
        assert stats["backend_type"] == "MockCacheBackend"

    @pytest.mark.asyncio
    async def test_health_check(self):
        """Test cache health check"""
        mock_backend = MockCacheBackend()
        voice_cache = VoiceCache(mock_backend)

        result = await voice_cache.health_check()

        assert result is True


class TestCreateVoiceCache:
    """Test cache factory function"""

    @pytest.mark.asyncio
    async def test_create_redis_cache(self):
        """Test creating Redis cache"""
        cache = await create_voice_cache("redis")

        assert isinstance(cache, VoiceCache)
        # Can't test Redis backend without actual Redis, just check it's created

    @pytest.mark.asyncio
    async def test_create_unsupported_cache(self):
        """Test creating unsupported cache backend"""
        with pytest.raises(VoiceServiceError, match="Unsupported cache backend"):
            await create_voice_cache("unsupported_backend")


class TestCacheIntegration:
    """Integration tests for cache functionality"""

    @pytest.mark.asyncio
    async def test_full_stt_workflow(self):
        """Test complete STT caching workflow"""
        mock_backend = MockCacheBackend()
        cache = VoiceCache(mock_backend)

        # Simulate STT workflow
        audio_data = b"fake_audio_content"
        text = "Transcribed result"
        provider = ProviderType.OPENAI
        language = VoiceLanguage.ENGLISH

        # Generate hash and cache result
        audio_hash = CacheKeyGenerator.audio_hash(audio_data)
        await cache.cache_stt_result(audio_hash, provider, language, text)

        # Retrieve from cache
        cached_result = await cache.get_stt_result(audio_hash, provider, language)

        assert cached_result == text

    @pytest.mark.asyncio
    async def test_full_tts_workflow(self):
        """Test complete TTS caching workflow"""
        mock_backend = MockCacheBackend()
        cache = VoiceCache(mock_backend)

        # Simulate TTS workflow
        text = "Hello, this is a test"
        audio_url = "https://storage.example.com/result.mp3"
        provider = ProviderType.GOOGLE
        voice = "en-US-Standard-A"
        language = VoiceLanguage.ENGLISH

        # Cache using text directly
        await cache.cache_tts_result_by_text(text, provider, voice, language, audio_url)

        # Retrieve from cache
        cached_url = await cache.get_tts_result_by_text(text, provider, voice, language)

        assert cached_url == audio_url

    @pytest.mark.asyncio
    async def test_key_generation_consistency(self):
        """Test that key generation is consistent"""
        # Same input should always generate same key
        text = "Test text for consistency"
        hash1 = CacheKeyGenerator.text_hash(text)
        hash2 = CacheKeyGenerator.text_hash(text)

        assert hash1 == hash2

        # Same key should be generated for cache operations
        provider = ProviderType.OPENAI
        voice = "alloy"
        language = VoiceLanguage.ENGLISH

        key1 = CacheKeyGenerator.tts_key(hash1, provider, voice, language)
        key2 = CacheKeyGenerator.tts_key(hash2, provider, voice, language)

        assert key1 == key2
