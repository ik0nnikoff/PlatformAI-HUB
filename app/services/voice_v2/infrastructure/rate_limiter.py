"""
Simple Rate Limiter for voice_v2

Basic rate limiting functionality with essential throttling:
- Simple request counting
- Basic time window tracking
- Provider rate limiting in base classes

Reduced from enterprise 430-line implementation to essential functionality.
"""

import time
import logging
from typing import Dict
from dataclasses import dataclass

from ..core.interfaces import RateLimiterInterface


logger = logging.getLogger(__name__)


@dataclass
class RateLimitInfo:
    """Simple rate limit information"""
    allowed: bool
    remaining_requests: int
    reset_time: float
    current_requests: int
    limit: int


class SimpleRateLimiter(RateLimiterInterface):
    """Simple in-memory rate limiter with basic throttling"""

    def __init__(
        self,
        max_requests: int = 100,
        window_seconds: int = 60,
        fail_open: bool = True
    ):
        """Initialize simple rate limiter"""
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self.fail_open = fail_open
        
        # In-memory storage for rate limit tracking
        self._request_counts: Dict[str, int] = {}
        self._window_starts: Dict[str, float] = {}
        self._initialized = True

    async def initialize(self) -> None:
        """Initialize rate limiter"""
        self._initialized = True
        logger.info(f"SimpleRateLimiter initialized - limit: {self.max_requests}/{self.window_seconds}s")

    async def cleanup(self) -> None:
        """Cleanup resources"""
        self._request_counts.clear()
        self._window_starts.clear()
        self._initialized = False

    async def is_allowed(self, user_id: str, operation: str = "default") -> bool:
        """Check if request is allowed for user"""
        try:
            info = await self.check_rate_limit(user_id, operation)
            return info.allowed
        except Exception as e:
            logger.error(f"Error checking rate limit for {user_id}: {e}")
            return self.fail_open

    async def check_rate_limit(self, user_id: str, operation: str = "default") -> RateLimitInfo:
        """Check rate limit and return information"""
        key = f"{user_id}:{operation}"
        now = time.time()
        
        # Initialize or reset window if needed
        if key not in self._window_starts or (now - self._window_starts[key]) >= self.window_seconds:
            self._window_starts[key] = now
            self._request_counts[key] = 0

        current_count = self._request_counts.get(key, 0)
        
        # Check if limit exceeded
        if current_count >= self.max_requests:
            window_start = self._window_starts[key]
            reset_time = max(0.0, (window_start + self.window_seconds) - now)
            
            return RateLimitInfo(
                allowed=False,
                remaining_requests=0,
                reset_time=reset_time,
                current_requests=current_count,
                limit=self.max_requests
            )

        # Allow request and increment counter
        self._request_counts[key] = current_count + 1
        remaining = self.max_requests - (current_count + 1)
        
        window_start = self._window_starts[key]
        reset_time = max(0.0, (window_start + self.window_seconds) - now)

        return RateLimitInfo(
            allowed=True,
            remaining_requests=remaining,
            reset_time=reset_time,
            current_requests=current_count + 1,
            limit=self.max_requests
        )

    async def get_remaining_requests(self, user_id: str, operation: str = "default") -> int:
        """Get remaining requests for user"""
        try:
            info = await self.check_rate_limit(user_id, operation)
            return info.remaining_requests
        except Exception:
            return self.max_requests if self.fail_open else 0

    async def get_reset_time(self, user_id: str, operation: str = "default") -> float:
        """Get time until rate limit reset"""
        key = f"{user_id}:{operation}"
        
        if key not in self._window_starts:
            return 0.0
            
        now = time.time()
        window_start = self._window_starts[key]
        reset_time = max(0.0, (window_start + self.window_seconds) - now)
        
        return reset_time

    async def clear_user_limit(self, user_id: str, operation: str = "default") -> bool:
        """Clear rate limit for user"""
        key = f"{user_id}:{operation}"
        
        if key in self._request_counts:
            del self._request_counts[key]
        if key in self._window_starts:
            del self._window_starts[key]
            
        return True
