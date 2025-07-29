"""
Tests for RedisRateLimiter - voice_v2 infrastructure
"""

import pytest
import pytest_asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch

from ..infrastructure.rate_limiter import RedisRateLimiter, RateLimitInfo
from ..core.exceptions import VoiceServiceError


@pytest.fixture
def mock_redis_service():
    """Mock Redis service with pipeline support"""
    mock = AsyncMock()
    
    # Mock pipeline operations - pipeline itself is not async, but its methods are
    pipeline_mock = MagicMock()
    pipeline_mock.execute = AsyncMock(return_value=[1, 0, 1, True])  # Default: success, no existing requests
    pipeline_mock.zremrangebyscore = AsyncMock(return_value=1)
    pipeline_mock.zadd = AsyncMock(return_value=1)
    pipeline_mock.zcard = AsyncMock(return_value=0)
    pipeline_mock.expire = AsyncMock(return_value=True)
    mock.pipeline = AsyncMock(return_value=pipeline_mock)
    
    # Mock standard Redis operations
    mock.zcard.return_value = 0
    mock.zrange.return_value = []
    mock.delete.return_value = 1
    mock.ping.return_value = True
    
    return mock


@pytest_asyncio.fixture
async def rate_limiter(mock_redis_service):
    """RedisRateLimiter instance with mocked Redis"""
    limiter = RedisRateLimiter(
        redis_service=mock_redis_service,
        max_requests=10,
        window_seconds=60,
        key_prefix="test:rate_limit:",
        fail_open=True
    )
    
    await limiter.initialize()
    yield limiter
    await limiter.cleanup()


@pytest_asyncio.fixture
async def strict_rate_limiter(mock_redis_service):
    """RedisRateLimiter instance with fail_open=False"""
    limiter = RedisRateLimiter(
        redis_service=mock_redis_service,
        max_requests=5,
        window_seconds=30,
        key_prefix="test:rate_limit:",
        fail_open=False
    )
    
    await limiter.initialize()
    yield limiter
    await limiter.cleanup()


class TestRedisRateLimiterInitialization:
    """Test rate limiter initialization and cleanup"""
    
    @pytest.mark.asyncio
    async def test_initialization_success(self, mock_redis_service):
        """Test successful initialization"""
        limiter = RedisRateLimiter(
            redis_service=mock_redis_service,
            max_requests=100,
            window_seconds=60
        )
        
        await limiter.initialize()
        
        assert limiter._initialized is True
        mock_redis_service.ping.assert_called_once()

    @pytest.mark.asyncio
    async def test_initialization_redis_failure_fail_open(self, mock_redis_service):
        """Test initialization with Redis failure and fail_open=True"""
        mock_redis_service.ping.side_effect = Exception("Redis connection failed")
        
        limiter = RedisRateLimiter(
            redis_service=mock_redis_service,
            max_requests=100,
            window_seconds=60,
            fail_open=True
        )
        
        # Should not raise exception with fail_open=True
        await limiter.initialize()
        assert limiter._initialized is False

    @pytest.mark.asyncio
    async def test_initialization_redis_failure_fail_close(self, mock_redis_service):
        """Test initialization with Redis failure and fail_open=False"""
        mock_redis_service.ping.side_effect = Exception("Redis connection failed")
        
        limiter = RedisRateLimiter(
            redis_service=mock_redis_service,
            max_requests=100,
            window_seconds=60,
            fail_open=False
        )
        
        # Should raise exception with fail_open=False
        with pytest.raises(VoiceServiceError, match="Rate limiter initialization failed"):
            await limiter.initialize()

    @pytest.mark.asyncio
    async def test_cleanup(self, rate_limiter):
        """Test resource cleanup"""
        await rate_limiter.cleanup()
        assert rate_limiter._initialized is False


class TestRateLimitChecking:
    """Test rate limit checking functionality"""
    
    @pytest.mark.asyncio
    async def test_is_allowed_under_limit(self, rate_limiter, mock_redis_service):
        """Test request allowed when under limit"""
        # Mock pipeline execution
        pipeline_mock = AsyncMock()
        pipeline_mock.execute.return_value = [1, 3, 1, True]  # [zremrangebyscore, zcard, zadd, expire]
        mock_redis_service.pipeline.return_value = pipeline_mock
        
        result = await rate_limiter.is_allowed("user123", "test_op")
        
        assert result is True

    @pytest.mark.asyncio
    async def test_is_allowed_over_limit(self, rate_limiter, mock_redis_service):
        """Test request denied when over limit"""
        # Mock pipeline execution - current count >= max_requests
        pipeline_mock = AsyncMock()
        pipeline_mock.execute.return_value = [1, 10, 1, True]  # At limit (10/10)
        mock_redis_service.pipeline.return_value = pipeline_mock
        
        result = await rate_limiter.is_allowed("user123", "test_op")
        
        assert result is False
        # Should call zrem to rollback the added request
        mock_redis_service.zrem.assert_called_once()

    @pytest.mark.asyncio
    async def test_check_rate_limit_success(self, rate_limiter, mock_redis_service):
        """Test detailed rate limit check"""
        # Mock pipeline execution
        pipeline_mock = AsyncMock()
        pipeline_mock.execute.return_value = [1, 3, 1, True]
        mock_redis_service.pipeline.return_value = pipeline_mock
        mock_redis_service.zrange.return_value = [(b"req1", 1000.0)]
        
        info = await rate_limiter.check_rate_limit("user123", "test_op")
        
        assert isinstance(info, RateLimitInfo)
        assert info.allowed is True
        assert info.current_requests == 4  # 3 + 1 new request
        assert info.remaining_requests == 6  # 10 - 4
        assert info.limit == 10
        assert info.window_seconds == 60

    @pytest.mark.asyncio
    async def test_check_rate_limit_over_limit(self, rate_limiter, mock_redis_service):
        """Test rate limit check when over limit"""
        # Mock pipeline execution - at limit
        pipeline_mock = AsyncMock()
        pipeline_mock.execute.return_value = [1, 10, 1, True]
        mock_redis_service.pipeline.return_value = pipeline_mock
        mock_redis_service.zrange.return_value = [(b"req1", time.time() - 30)]
        
        info = await rate_limiter.check_rate_limit("user123", "test_op")
        
        assert info.allowed is False
        assert info.current_requests == 10
        assert info.remaining_requests == 0

    @pytest.mark.asyncio
    async def test_check_rate_limit_redis_failure_fail_open(self, rate_limiter, mock_redis_service):
        """Test rate limit check with Redis failure and fail_open=True"""
        mock_redis_service.pipeline.side_effect = Exception("Redis error")
        
        info = await rate_limiter.check_rate_limit("user123", "test_op")
        
        assert info.allowed is True  # Should allow request on failure
        assert info.remaining_requests == 10

    @pytest.mark.asyncio
    async def test_check_rate_limit_redis_failure_fail_close(self, strict_rate_limiter, mock_redis_service):
        """Test rate limit check with Redis failure and fail_open=False"""
        mock_redis_service.pipeline.side_effect = Exception("Redis error")
        
        with pytest.raises(VoiceServiceError, match="Rate limit check failed"):
            await strict_rate_limiter.check_rate_limit("user123", "test_op")


class TestRateLimitUtilities:
    """Test utility functions for rate limiting"""
    
    @pytest.mark.asyncio
    async def test_get_remaining_requests(self, rate_limiter, mock_redis_service):
        """Test getting remaining requests"""
        # Mock check_rate_limit_info response
        with patch.object(rate_limiter, 'check_rate_limit_info') as mock_check:
            mock_info = RateLimitInfo(
                allowed=True,
                remaining_requests=7,
                reset_time=30.0,
                current_requests=3,
                limit=10,
                window_seconds=60
            )
            mock_check.return_value = mock_info
            
            remaining = await rate_limiter.get_remaining_requests("user123")
            
            assert remaining == 7
            mock_check.assert_called_once_with("user123", "default")

    @pytest.mark.asyncio
    async def test_get_reset_time(self, rate_limiter, mock_redis_service):
        """Test getting reset time"""
        now = time.time()
        oldest_time = now - 30  # 30 seconds ago
        mock_redis_service.zrange.return_value = [(b"req1", oldest_time)]
        
        reset_time = await rate_limiter.get_reset_time("user123")
        
        # Should be approximately 30 seconds (60 - 30)
        assert 25 <= reset_time <= 35

    @pytest.mark.asyncio
    async def test_get_reset_time_no_requests(self, rate_limiter, mock_redis_service):
        """Test getting reset time when no requests exist"""
        mock_redis_service.zrange.return_value = []
        
        reset_time = await rate_limiter.get_reset_time("user123")
        
        assert reset_time == 0.0

    @pytest.mark.asyncio
    async def test_clear_user_limit(self, rate_limiter, mock_redis_service):
        """Test clearing user rate limit"""
        mock_redis_service.delete.return_value = 1
        
        result = await rate_limiter.clear_user_limit("user123", "test_op")
        
        assert result is True
        mock_redis_service.delete.assert_called_once_with("test:rate_limit:user123:test_op")

    @pytest.mark.asyncio
    async def test_clear_user_limit_failure(self, rate_limiter, mock_redis_service):
        """Test clearing user rate limit with Redis error"""
        mock_redis_service.delete.side_effect = Exception("Redis error")
        
        result = await rate_limiter.clear_user_limit("user123")
        
        assert result is False

    @pytest.mark.asyncio
    async def test_get_rate_limit_stats(self, rate_limiter, mock_redis_service):
        """Test getting detailed rate limit statistics"""
        now = time.time()
        requests = [
            (b"req1", now - 30),
            (b"req2", now - 20),
            (b"req3", now - 10)
        ]
        mock_redis_service.zrangebyscore.return_value = requests
        
        stats = await rate_limiter.get_rate_limit_stats("user123", "test_op")
        
        assert stats["user_id"] == "user123"
        assert stats["operation"] == "test_op"
        assert stats["current_requests"] == 3
        assert stats["remaining_requests"] == 7
        assert len(stats["request_times"]) == 3
        assert stats["limit"] == 10
        assert stats["window_seconds"] == 60

    @pytest.mark.asyncio
    async def test_get_rate_limit_stats_no_requests(self, rate_limiter, mock_redis_service):
        """Test getting stats when no requests exist"""
        mock_redis_service.zrangebyscore.return_value = []
        
        stats = await rate_limiter.get_rate_limit_stats("user123")
        
        assert stats["current_requests"] == 0
        assert stats["remaining_requests"] == 10
        assert stats["reset_time"] == 0.0
        assert stats["request_times"] == []

    @pytest.mark.asyncio
    async def test_get_rate_limit_stats_error(self, rate_limiter, mock_redis_service):
        """Test getting stats with Redis error"""
        mock_redis_service.zrangebyscore.side_effect = Exception("Redis error")
        
        stats = await rate_limiter.get_rate_limit_stats("user123")
        
        assert "error" in stats
        assert stats["user_id"] == "user123"

    @pytest.mark.asyncio
    async def test_check_rate_limit_info(self, rate_limiter, mock_redis_service):
        """Test checking rate limit info without consuming request"""
        # Mock pipeline execution
        pipeline_mock = AsyncMock()
        pipeline_mock.execute.return_value = [1, 3]  # [zremrangebyscore, zcard]
        mock_redis_service.pipeline.return_value = pipeline_mock
        mock_redis_service.zrange.return_value = [(b"req1", time.time() - 30)]
        
        info = await rate_limiter.check_rate_limit_info("user123")
        
        assert isinstance(info, RateLimitInfo)
        assert info.allowed is True
        assert info.current_requests == 3
        assert info.remaining_requests == 7


class TestRateLimitEdgeCases:
    """Test edge cases and error conditions"""
    
    @pytest.mark.asyncio
    async def test_uninitialized_strict_limiter(self, mock_redis_service):
        """Test using uninitialized limiter with fail_open=False"""
        limiter = RedisRateLimiter(
            redis_service=mock_redis_service,
            max_requests=10,
            window_seconds=60,
            fail_open=False
        )
        
        with pytest.raises(VoiceServiceError, match="Rate limiter not initialized"):
            await limiter.check_rate_limit("user123")

    @pytest.mark.asyncio
    async def test_key_generation(self, rate_limiter):
        """Test Redis key generation"""
        key = rate_limiter._get_key("user123", "voice_stt")
        assert key == "test:rate_limit:user123:voice_stt"
        
        default_key = rate_limiter._get_key("user456", "default")
        assert default_key == "test:rate_limit:user456:default"

    @pytest.mark.asyncio
    async def test_reset_time_calculation_edge_cases(self, rate_limiter, mock_redis_service):
        """Test reset time calculation edge cases"""
        # Test with Redis error
        mock_redis_service.zrange.side_effect = Exception("Redis error")
        
        reset_time = await rate_limiter._get_reset_time("test_key", time.time())
        assert reset_time == 0.0

    @pytest.mark.asyncio
    async def test_multiple_operations_same_user(self, rate_limiter, mock_redis_service):
        """Test rate limiting for different operations of same user"""
        pipeline_mock = AsyncMock()
        pipeline_mock.execute.return_value = [1, 2, 1, True]
        mock_redis_service.pipeline.return_value = pipeline_mock
        
        # Different operations should have different limits
        result1 = await rate_limiter.is_allowed("user123", "stt")
        result2 = await rate_limiter.is_allowed("user123", "tts")
        
        assert result1 is True
        assert result2 is True
        
        # Should call pipeline twice for different keys
        assert mock_redis_service.pipeline.call_count == 2


@pytest.mark.asyncio
async def test_rate_limiter_integration():
    """Integration test for rate limiter functionality"""
    mock_redis = AsyncMock()
    mock_redis.ping.return_value = "PONG"
    
    limiter = RedisRateLimiter(
        redis_service=mock_redis,
        max_requests=3,
        window_seconds=10,
        key_prefix="integration_test:",
        fail_open=True
    )
    
    await limiter.initialize()
    
    # Test basic functionality
    assert limiter._initialized is True
    assert limiter.max_requests == 3
    assert limiter.window_seconds == 10
    
    # Test cleanup
    await limiter.cleanup()
    assert limiter._initialized is False
