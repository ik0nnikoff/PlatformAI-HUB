"""
Voice v2 Redis Rate Limiter - High-performance distributed rate limiting
"""

import time
import logging
from typing import Dict, Any
from dataclasses import dataclass

from ..core.interfaces import RateLimiterInterface
from ..core.exceptions import VoiceServiceError


logger = logging.getLogger(__name__)


@dataclass
class RateLimitInfo:
    """Rate limit information"""
    
    allowed: bool
    remaining_requests: int
    reset_time: float
    current_requests: int
    limit: int
    window_seconds: int


class RedisRateLimiter(RateLimiterInterface):
    """
    High-performance Redis-based rate limiter with sliding window algorithm
    
    Features:
    - Distributed sliding window rate limiting
    - Configurable time windows and request limits
    - Performance-optimized Redis operations (≤200µs/op target)
    - Graceful error handling with fail-open strategy
    - Detailed rate limit metrics and monitoring
    - SOLID principles compliance (SRP, OCP, LSP, ISP, DIP)
    """

    def __init__(
        self,
        redis_service,  # Type hint avoided to prevent circular imports
        max_requests: int = 100,
        window_seconds: int = 60,
        key_prefix: str = "voice_v2:rate_limit:",
        fail_open: bool = True
    ):
        """
        Initialize Redis rate limiter
        
        Args:
            redis_service: Redis service instance (RedisService)
            max_requests: Maximum requests per window
            window_seconds: Time window size in seconds
            key_prefix: Redis key prefix for rate limiting data
            fail_open: Allow requests if Redis is unavailable
        """
        self.redis_service = redis_service
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self.key_prefix = key_prefix
        self.fail_open = fail_open
        
        self._initialized = False

    async def initialize(self) -> None:
        """Initialize rate limiter"""
        try:
            # Test Redis connection
            await self.redis_service.ping()
            self._initialized = True
            logger.info(
                f"RedisRateLimiter initialized - "
                f"limit: {self.max_requests}/{self.window_seconds}s"
            )
            
        except Exception as e:
            logger.error(f"Failed to initialize RedisRateLimiter: {e}", exc_info=True)
            if not self.fail_open:
                raise VoiceServiceError(f"Rate limiter initialization failed: {e}")

    async def cleanup(self) -> None:
        """Cleanup resources"""
        self._initialized = False
        logger.info("RedisRateLimiter cleaned up")

    async def is_allowed(self, user_id: str, operation: str = "default") -> bool:
        """
        Check if request is allowed for user
        
        Args:
            user_id: User identifier
            operation: Operation type for fine-grained limiting
            
        Returns:
            True if request is allowed
        """
        try:
            info = await self.check_rate_limit(user_id, operation)
            return info.allowed
            
        except Exception as e:
            logger.error(f"Error checking rate limit for {user_id}: {e}", exc_info=True)
            return self.fail_open

    async def check_rate_limit(
        self,
        user_id: str,
        operation: str = "default"
    ) -> RateLimitInfo:
        """
        Check rate limit and return detailed information
        
        Args:
            user_id: User identifier
            operation: Operation type
            
        Returns:
            RateLimitInfo with detailed rate limit data
            
        Raises:
            VoiceServiceError: If rate limiter not initialized and fail_open=False
        """
        if not self._initialized and not self.fail_open:
            raise VoiceServiceError("Rate limiter not initialized")
            
        try:
            key = self._get_key(user_id, operation)
            now = time.time()
            
            # Use pipeline for atomic operations (performance optimization)
            pipeline = await self.redis_service.pipeline()
            
            # Remove expired entries
            await pipeline.zremrangebyscore(key, 0, now - self.window_seconds)
            
            # Get current count
            await pipeline.zcard(key)
            
            # Add current request with score=timestamp
            request_id = f"{now}:{id(self)}"  # Unique request identifier
            await pipeline.zadd(key, {request_id: now})
            
            # Set expiration (window + buffer for cleanup)
            await pipeline.expire(key, self.window_seconds + 10)
            
            # Execute pipeline
            results = await pipeline.execute()
            current_count = results[1]  # zcard result
            
            # Check if limit exceeded
            if current_count >= self.max_requests:
                # Remove the request we just added (rollback)
                await self.redis_service.zrem(key, request_id)
                
                # Get accurate remaining and reset time
                remaining = 0
                reset_time = await self._get_reset_time(key, now)
                
                logger.warning(
                    f"Rate limit exceeded - user: {user_id}, operation: {operation}, "
                    f"requests: {current_count}/{self.max_requests}"
                )
                
                return RateLimitInfo(
                    allowed=False,
                    remaining_requests=remaining,
                    reset_time=reset_time,
                    current_requests=current_count,
                    limit=self.max_requests,
                    window_seconds=self.window_seconds
                )
            
            # Request allowed
            remaining = self.max_requests - (current_count + 1)
            reset_time = await self._get_reset_time(key, now)
            
            logger.debug(
                f"Rate limit OK - user: {user_id}, operation: {operation}, "
                f"requests: {current_count + 1}/{self.max_requests}"
            )
            
            return RateLimitInfo(
                allowed=True,
                remaining_requests=remaining,
                reset_time=reset_time,
                current_requests=current_count + 1,
                limit=self.max_requests,
                window_seconds=self.window_seconds
            )
            
        except Exception as e:
            logger.error(
                f"Rate limit check failed - user: {user_id}, operation: {operation}: {e}",
                exc_info=True
            )
            
            # Fail-open strategy: allow request if Redis fails
            if self.fail_open:
                return RateLimitInfo(
                    allowed=True,
                    remaining_requests=self.max_requests,
                    reset_time=0.0,
                    current_requests=0,
                    limit=self.max_requests,
                    window_seconds=self.window_seconds
                )
            else:
                raise VoiceServiceError(f"Rate limit check failed: {e}")

    async def get_remaining_requests(
        self,
        user_id: str,
        operation: str = "default"
    ) -> int:
        """
        Get remaining requests for user
        
        Args:
            user_id: User identifier
            operation: Operation type
            
        Returns:
            Number of remaining requests
        """
        try:
            info = await self.check_rate_limit_info(user_id, operation)
            return info.remaining_requests
            
        except Exception as e:
            logger.error(f"Error getting remaining requests for {user_id}: {e}")
            return self.max_requests if self.fail_open else 0

    async def get_reset_time(
        self,
        user_id: str,
        operation: str = "default"
    ) -> float:
        """
        Get time until rate limit reset
        
        Args:
            user_id: User identifier
            operation: Operation type
            
        Returns:
            Seconds until rate limit resets
        """
        try:
            key = self._get_key(user_id, operation)
            now = time.time()
            return await self._get_reset_time(key, now)
            
        except Exception as e:
            logger.error(f"Error getting reset time for {user_id}: {e}")
            return 0.0

    async def clear_user_limit(
        self,
        user_id: str,
        operation: str = "default"
    ) -> bool:
        """
        Clear rate limit for user (admin function)
        
        Args:
            user_id: User identifier
            operation: Operation type
            
        Returns:
            True if cleared successfully
        """
        try:
            key = self._get_key(user_id, operation)
            result = await self.redis_service.delete(key)
            
            logger.info(f"Rate limit cleared for user {user_id}, operation {operation}")
            return bool(result)
            
        except Exception as e:
            logger.error(f"Error clearing rate limit for {user_id}: {e}")
            return False

    async def get_rate_limit_stats(
        self,
        user_id: str,
        operation: str = "default"
    ) -> Dict[str, Any]:
        """
        Get detailed rate limit statistics
        
        Args:
            user_id: User identifier
            operation: Operation type
            
        Returns:
            Dictionary with rate limit statistics
        """
        try:
            key = self._get_key(user_id, operation)
            now = time.time()
            
            # Get all requests in current window
            requests = await self.redis_service.zrangebyscore(
                key,
                now - self.window_seconds,
                now,
                withscores=True
            )
            
            if not requests:
                return {
                    "user_id": user_id,
                    "operation": operation,
                    "current_requests": 0,
                    "remaining_requests": self.max_requests,
                    "reset_time": 0.0,
                    "request_times": [],
                    "window_start": now - self.window_seconds,
                    "window_end": now
                }
            
            current_count = len(requests)
            remaining = max(0, self.max_requests - current_count)
            oldest_time = requests[0][1] if requests else now
            reset_time = max(0.0, oldest_time + self.window_seconds - now)
            
            return {
                "user_id": user_id,
                "operation": operation,
                "current_requests": current_count,
                "remaining_requests": remaining,
                "reset_time": reset_time,
                "request_times": [req[1] for req in requests],
                "window_start": now - self.window_seconds,
                "window_end": now,
                "limit": self.max_requests,
                "window_seconds": self.window_seconds
            }
            
        except Exception as e:
            logger.error(f"Error getting rate limit stats for {user_id}: {e}")
            return {
                "user_id": user_id,
                "operation": operation,
                "error": str(e)
            }

    async def check_rate_limit_info(
        self,
        user_id: str,
        operation: str = "default"
    ) -> RateLimitInfo:
        """
        Check rate limit without consuming a request
        
        Args:
            user_id: User identifier
            operation: Operation type
            
        Returns:
            RateLimitInfo with current state (without consuming request)
        """
        try:
            key = self._get_key(user_id, operation)
            now = time.time()
            
            # Clean up expired entries and get current count
            pipeline = await self.redis_service.pipeline()
            await pipeline.zremrangebyscore(key, 0, now - self.window_seconds)
            await pipeline.zcard(key)
            
            results = await pipeline.execute()
            current_count = results[1]
            
            remaining = max(0, self.max_requests - current_count)
            reset_time = await self._get_reset_time(key, now)
            allowed = current_count < self.max_requests
            
            return RateLimitInfo(
                allowed=allowed,
                remaining_requests=remaining,
                reset_time=reset_time,
                current_requests=current_count,
                limit=self.max_requests,
                window_seconds=self.window_seconds
            )
            
        except Exception as e:
            logger.error(f"Error checking rate limit info for {user_id}: {e}")
            
            if self.fail_open:
                return RateLimitInfo(
                    allowed=True,
                    remaining_requests=self.max_requests,
                    reset_time=0.0,
                    current_requests=0,
                    limit=self.max_requests,
                    window_seconds=self.window_seconds
                )
            else:
                raise VoiceServiceError(f"Rate limit info check failed: {e}")

    # Private methods
    def _get_key(self, user_id: str, operation: str) -> str:
        """Generate Redis key for user and operation"""
        return f"{self.key_prefix}{user_id}:{operation}"

    async def _get_reset_time(self, key: str, now: float) -> float:
        """Get reset time for rate limit window"""
        try:
            # Get oldest entry in current window
            oldest_entries = await self.redis_service.zrange(
                key, 0, 0, withscores=True
            )
            
            if not oldest_entries:
                return 0.0
                
            oldest_time = oldest_entries[0][1]
            reset_time = oldest_time + self.window_seconds - now
            
            return max(0.0, reset_time)
            
        except Exception as e:
            logger.error(f"Error calculating reset time: {e}")
            return 0.0
