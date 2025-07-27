"""
Voice_v2 Intelligent Caching Layer

Implements performance-optimized caching with Redis backend, TTL management,
and intelligent cache key generation. Follows SOLID principles and async patterns.

Performance targets:
- Cache operations: ≤100µs for Redis operations
- Key generation: ≤10µs per key
- Batch operations: ≤500µs for 100 keys
- Memory efficiency: Minimal overhead per cached item

SOLID Compliance:
- SRP: CacheKeyGenerator, RedisCacheManager, VoiceCache separate responsibilities
- OCP: Extensible for new cache backends via CacheInterface
- LSP: All cache implementations fully substitutable
- ISP: Specialized interfaces for STT/TTS caching
- DIP: Depends on CacheInterface abstraction
"""

import hashlib
import json
import asyncio
import time
from typing import Dict, Any, Optional, List, Union, Tuple
from contextlib import asynccontextmanager

import redis.asyncio as redis
from pydantic import BaseModel, Field, ConfigDict

from ..core.interfaces import (
    CacheInterface, STTCacheInterface, TTSCacheInterface,
    ProviderType, VoiceLanguage, AudioFormat
)
from ..core.exceptions import VoiceServiceError, VoiceServiceTimeout
from ..core.config import get_config


class CacheMetrics(BaseModel):
    """Cache performance metrics"""
    model_config = ConfigDict(arbitrary_types_allowed=True)
    
    hits: int = 0
    misses: int = 0
    total_operations: int = 0
    average_latency_ms: float = 0.0
    error_count: int = 0
    last_error: Optional[str] = None
    uptime_seconds: float = 0.0
    
    @property
    def hit_rate(self) -> float:
        """Calculate cache hit rate"""
        if self.total_operations == 0:
            return 0.0
        return self.hits / self.total_operations
    
    @property
    def error_rate(self) -> float:
        """Calculate error rate"""
        if self.total_operations == 0:
            return 0.0
        return self.error_count / self.total_operations


class CacheKeyGenerator:
    """
    Intelligent cache key generation
    
    SRP: Single responsibility for key generation logic
    Performance: ≤10µs per key generation
    """
    
    @staticmethod
    def stt_key(
        audio_hash: str,
        provider: ProviderType,
        language: VoiceLanguage,
        format_info: Optional[str] = None
    ) -> str:
        """Generate STT cache key"""
        key_parts = [
            "voice_v2",
            "stt",
            provider.value,
            language.value,
            audio_hash
        ]
        if format_info:
            key_parts.append(format_info)
        
        return ":".join(key_parts)
    
    @staticmethod
    def tts_key(
        text_hash: str,
        provider: ProviderType,
        voice: str,
        language: VoiceLanguage,
        format_info: Optional[str] = None
    ) -> str:
        """Generate TTS cache key"""
        key_parts = [
            "voice_v2",
            "tts",
            provider.value,
            language.value,
            voice,
            text_hash
        ]
        if format_info:
            key_parts.append(format_info)
            
        return ":".join(key_parts)
    
    @staticmethod
    def audio_hash(audio_data: bytes) -> str:
        """Generate audio content hash (≤5µs target)"""
        return hashlib.md5(audio_data).hexdigest()
    
    @staticmethod
    def text_hash(text: str) -> str:
        """Generate text content hash (≤2µs target)"""
        return hashlib.md5(text.encode('utf-8')).hexdigest()
    
    @staticmethod
    def file_hash(file_path: str, chunk_size: int = 8192) -> str:
        """Generate file content hash efficiently"""
        hash_md5 = hashlib.md5()
        try:
            with open(file_path, "rb") as f:
                while chunk := f.read(chunk_size):
                    hash_md5.update(chunk)
            return hash_md5.hexdigest()
        except Exception as e:
            raise VoiceServiceError(f"Failed to hash file {file_path}: {e}")


class RedisCacheManager:
    """
    High-performance Redis cache implementation
    
    Performance targets:
    - Single operations: ≤100µs
    - Batch operations: ≤500µs for 100 items
    - Connection pooling optimized
    """
    
    def __init__(self, redis_url: Optional[str] = None):
        self.redis_url = redis_url or get_config().cache.redis_url
        self._redis_pool: Optional[redis.Redis] = None
        self._start_time = time.time()
        self.metrics = CacheMetrics()
        
    async def _get_redis_pool(self) -> redis.Redis:
        """Get Redis connection with optimized pooling"""
        if not self._redis_pool:
            self._redis_pool = redis.from_url(
                self.redis_url,
                encoding='utf-8',
                decode_responses=True,
                max_connections=50,
                socket_keepalive=True,
                socket_keepalive_options={},
                health_check_interval=30,
                retry_on_timeout=True,
                retry_on_error=[redis.ConnectionError, redis.TimeoutError]
            )
        return self._redis_pool
    
    @asynccontextmanager
    async def _measure_latency(self):
        """Context manager for latency measurement"""
        start_time = time.perf_counter()
        try:
            yield
        finally:
            latency_ms = (time.perf_counter() - start_time) * 1000
            self._update_latency(latency_ms)
    
    def _update_latency(self, latency_ms: float):
        """Update average latency metric"""
        if self.metrics.total_operations == 0:
            self.metrics.average_latency_ms = latency_ms
        else:
            # Running average
            total = self.metrics.total_operations
            current_avg = self.metrics.average_latency_ms
            self.metrics.average_latency_ms = (current_avg * total + latency_ms) / (total + 1)
    
    async def get(self, key: str) -> Optional[str]:
        """Retrieve cached value with performance tracking"""
        async with self._measure_latency():
            try:
                redis_client = await self._get_redis_pool()
                result = await redis_client.get(key)
                
                self.metrics.total_operations += 1
                if result:
                    self.metrics.hits += 1
                else:
                    self.metrics.misses += 1
                    
                return result
                
            except Exception as e:
                self.metrics.error_count += 1
                self.metrics.last_error = str(e)
                raise VoiceServiceError(f"Cache get error for key {key}: {e}")
    
    async def set(self, key: str, value: str, ttl_seconds: int) -> None:
        """Store value with TTL and performance tracking"""
        async with self._measure_latency():
            try:
                redis_client = await self._get_redis_pool()
                await redis_client.setex(key, ttl_seconds, value)
                self.metrics.total_operations += 1
                
            except Exception as e:
                self.metrics.error_count += 1
                self.metrics.last_error = str(e)
                raise VoiceServiceError(f"Cache set error for key {key}: {e}")
    
    async def delete(self, key: str) -> bool:
        """Delete cached value"""
        async with self._measure_latency():
            try:
                redis_client = await self._get_redis_pool()
                result = await redis_client.delete(key)
                self.metrics.total_operations += 1
                return bool(result)
                
            except Exception as e:
                self.metrics.error_count += 1
                self.metrics.last_error = str(e)
                raise VoiceServiceError(f"Cache delete error for key {key}: {e}")
    
    async def exists(self, key: str) -> bool:
        """Check if key exists"""
        async with self._measure_latency():
            try:
                redis_client = await self._get_redis_pool()
                result = await redis_client.exists(key)
                self.metrics.total_operations += 1
                return bool(result)
                
            except Exception as e:
                self.metrics.error_count += 1
                self.metrics.last_error = str(e)
                raise VoiceServiceError(f"Cache exists error for key {key}: {e}")
    
    async def batch_get(self, keys: List[str]) -> Dict[str, Optional[str]]:
        """Batch get operation for performance (≤500µs target)"""
        if not keys:
            return {}
            
        async with self._measure_latency():
            try:
                redis_client = await self._get_redis_pool()
                values = await redis_client.mget(keys)
                
                result = {}
                hits = 0
                for key, value in zip(keys, values):
                    result[key] = value
                    if value:
                        hits += 1
                
                self.metrics.total_operations += len(keys)
                self.metrics.hits += hits
                self.metrics.misses += len(keys) - hits
                
                return result
                
            except Exception as e:
                self.metrics.error_count += len(keys)
                self.metrics.last_error = str(e)
                raise VoiceServiceError(f"Cache batch_get error: {e}")
    
    async def batch_set(self, items: Dict[str, Tuple[str, int]]) -> None:
        """Batch set operation (value, ttl_seconds)"""
        if not items:
            return
            
        async with self._measure_latency():
            try:
                redis_client = await self._get_redis_pool()
                async with redis_client.pipeline() as pipe:
                    for key, (value, ttl) in items.items():
                        pipe.setex(key, ttl, value)
                    await pipe.execute()
                
                self.metrics.total_operations += len(items)
                
            except Exception as e:
                self.metrics.error_count += len(items)
                self.metrics.last_error = str(e)
                raise VoiceServiceError(f"Cache batch_set error: {e}")
    
    async def health_check(self) -> bool:
        """Check Redis connectivity"""
        try:
            redis_client = await self._get_redis_pool()
            await redis_client.ping()
            return True
        except Exception:
            return False
    
    def get_metrics(self) -> CacheMetrics:
        """Get cache performance metrics"""
        self.metrics.uptime_seconds = time.time() - self._start_time
        return self.metrics
    
    async def cleanup(self):
        """Cleanup Redis connections"""
        if self._redis_pool:
            await self._redis_pool.aclose()
            self._redis_pool = None


class VoiceCache:
    """
    Voice-specific intelligent caching layer
    
    Implements STTCacheInterface and TTSCacheInterface
    DIP: Depends on CacheInterface abstraction
    OCP: Open for extension with new backends
    """
    
    def __init__(self, cache_backend: Optional[CacheInterface] = None):
        self.cache_backend = cache_backend or RedisCacheManager()
        self.key_generator = CacheKeyGenerator()
        
        # TTL configuration (from Phase 1.2.3 optimization)
        self.stt_ttl = 86400  # 24 hours for STT results
        self.tts_ttl = 3600   # 1 hour for TTS results
        self.metrics_ttl = 604800  # 7 days for metrics
    
    # STTCacheInterface implementation
    async def get_stt_result(
        self,
        audio_file_hash: str,
        provider: ProviderType,
        language: VoiceLanguage
    ) -> Optional[str]:
        """Get cached STT result"""
        cache_key = self.key_generator.stt_key(audio_file_hash, provider, language)
        return await self.cache_backend.get(cache_key)
    
    async def cache_stt_result(
        self,
        audio_file_hash: str,
        provider: ProviderType,
        language: VoiceLanguage,
        result: str,
        ttl_seconds: int = None
    ) -> None:
        """Cache STT result"""
        cache_key = self.key_generator.stt_key(audio_file_hash, provider, language)
        ttl = ttl_seconds or self.stt_ttl
        await self.cache_backend.set(cache_key, result, ttl)
    
    # TTSCacheInterface implementation  
    async def get_tts_result(
        self,
        text_hash: str,
        provider: ProviderType,
        voice: str,
        language: VoiceLanguage
    ) -> Optional[str]:
        """Get cached TTS result URL"""
        cache_key = self.key_generator.tts_key(text_hash, provider, voice, language)
        return await self.cache_backend.get(cache_key)
    
    async def cache_tts_result(
        self,
        text_hash: str,
        provider: ProviderType,
        voice: str,
        language: VoiceLanguage,
        audio_url: str,
        ttl_seconds: int = None
    ) -> None:
        """Cache TTS result URL"""
        cache_key = self.key_generator.tts_key(text_hash, provider, voice, language)
        ttl = ttl_seconds or self.tts_ttl
        await self.cache_backend.set(cache_key, audio_url, ttl)
    
    # Extended functionality
    async def get_stt_result_by_file(
        self,
        file_path: str,
        provider: ProviderType,
        language: VoiceLanguage
    ) -> Optional[str]:
        """Get STT result using file path (generates hash)"""
        audio_hash = self.key_generator.file_hash(file_path)
        return await self.get_stt_result(audio_hash, provider, language)
    
    async def cache_stt_result_by_file(
        self,
        file_path: str,
        provider: ProviderType,
        language: VoiceLanguage,
        result: str,
        ttl_seconds: int = None
    ) -> None:
        """Cache STT result using file path"""
        audio_hash = self.key_generator.file_hash(file_path)
        await self.cache_stt_result(audio_hash, provider, language, result, ttl_seconds)
    
    async def get_tts_result_by_text(
        self,
        text: str,
        provider: ProviderType,
        voice: str,
        language: VoiceLanguage
    ) -> Optional[str]:
        """Get TTS result using text (generates hash)"""
        text_hash = self.key_generator.text_hash(text)
        return await self.get_tts_result(text_hash, provider, voice, language)
    
    async def cache_tts_result_by_text(
        self,
        text: str,
        provider: ProviderType,
        voice: str,
        language: VoiceLanguage,
        audio_url: str,
        ttl_seconds: int = None
    ) -> None:
        """Cache TTS result using text"""
        text_hash = self.key_generator.text_hash(text)
        await self.cache_tts_result(text_hash, provider, voice, language, audio_url, ttl_seconds)
    
    async def invalidate_provider_cache(self, provider: ProviderType) -> int:
        """Invalidate all cache entries for a provider"""
        # This would need Redis pattern matching in real implementation
        # For now, we'll implement basic functionality
        try:
            redis_client = await self.cache_backend._get_redis_pool()
            pattern = f"voice_v2:*:{provider.value}:*"
            
            keys = []
            async for key in redis_client.scan_iter(match=pattern):
                keys.append(key)
            
            if keys:
                deleted = await redis_client.delete(*keys)
                return deleted
            return 0
            
        except Exception as e:
            raise VoiceServiceError(f"Failed to invalidate provider cache: {e}")
    
    async def get_cache_stats(self) -> Dict[str, Any]:
        """Get comprehensive cache statistics"""
        if hasattr(self.cache_backend, 'get_metrics'):
            metrics = self.cache_backend.get_metrics()
            return {
                "hit_rate": metrics.hit_rate,
                "error_rate": metrics.error_rate,
                "total_operations": metrics.total_operations,
                "average_latency_ms": metrics.average_latency_ms,
                "uptime_seconds": metrics.uptime_seconds,
                "backend_type": type(self.cache_backend).__name__
            }
        return {"backend_type": type(self.cache_backend).__name__}
    
    async def health_check(self) -> bool:
        """Check cache backend health"""
        if hasattr(self.cache_backend, 'health_check'):
            return await self.cache_backend.health_check()
        return True
    
    async def cleanup(self):
        """Cleanup cache resources"""
        if hasattr(self.cache_backend, 'cleanup'):
            await self.cache_backend.cleanup()


# Factory function for DIP compliance
async def create_voice_cache(backend_type: str = "redis") -> VoiceCache:
    """
    Factory function for creating VoiceCache with specified backend
    
    DIP: Creates cache with appropriate backend without tight coupling
    """
    if backend_type == "redis":
        backend = RedisCacheManager()
    else:
        raise VoiceServiceError(f"Unsupported cache backend: {backend_type}")
    
    return VoiceCache(backend)
