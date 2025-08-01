"""
Simplified RetryMixin для voice_v2 провайдеров - Phase 3.1.2 Implementation

Removes connection manager dependencies, integrates retry logic directly.
Centralizes retry configuration without enterprise over-engineering.
"""

import time
import asyncio
from typing import Dict, Any, Callable, TypeVar
from functools import wraps
import logging

logger = logging.getLogger(__name__)

T = TypeVar('T')


class RetryConfig:
    """Simple retry configuration."""
    def __init__(
        self,
        max_attempts: int = 3,
        base_delay: float = 1.0,
        max_delay: float = 30.0,
        backoff_factor: float = 2.0
    ):
        self.max_attempts = max_attempts
        self.base_delay = base_delay
        self.max_delay = max_delay
        self.backoff_factor = backoff_factor


class RetryMixin:
    """
    Simplified mixin for retry functionality.

    Removes enterprise connection manager patterns,
    provides essential retry logic for voice providers.
    """

    def __init__(self, *args, **kwargs):
        """Initialize retry mixin with simplified connection manager."""
        super().__init__()
        self._connection_manager = None  # Simplified version doesn't use connection manager

    def _has_connection_manager(self) -> bool:
        """Check if connection manager is available (always False in simplified version)."""
        return False

    def _get_retry_config(self, config: Dict[str, Any]) -> RetryConfig:
        """Extract retry configuration from provider config."""
        return RetryConfig(
            max_attempts=config.get('max_retry_attempts', 3),
            base_delay=config.get('retry_base_delay', 1.0),
            max_delay=config.get('retry_max_delay', 30.0),
            backoff_factor=config.get('retry_backoff_factor', 2.0)
        )

    async def _retry_async_operation(
        self,
        operation: Callable[..., T],
        retry_config: RetryConfig,
        *args,
        **kwargs
    ) -> T:
        """Execute async operation with retry logic."""
        last_exception = None

        for attempt in range(retry_config.max_attempts):
            try:
                if asyncio.iscoroutinefunction(operation):
                    return await operation(*args, **kwargs)
                else:
                    return operation(*args, **kwargs)

            except Exception as e:
                last_exception = e

                if attempt == retry_config.max_attempts - 1:
                    # Last attempt failed
                    break

                # Calculate delay with exponential backoff
                delay = min(
                    retry_config.base_delay * (retry_config.backoff_factor ** attempt),
                    retry_config.max_delay
                )

                logger.warning(
                    f"Operation failed (attempt {attempt + 1}/{retry_config.max_attempts}), "
                    f"retrying in {delay:.2f}s: {e}"
                )

                await asyncio.sleep(delay)

        # All attempts failed
        logger.error(f"All {retry_config.max_attempts} attempts failed")
        raise last_exception

    def _retry_sync_operation(
        self,
        operation: Callable[..., T],
        retry_config: RetryConfig,
        *args,
        **kwargs
    ) -> T:
        """Execute sync operation with retry logic."""
        last_exception = None

        for attempt in range(retry_config.max_attempts):
            try:
                return operation(*args, **kwargs)

            except Exception as e:
                last_exception = e

                if attempt == retry_config.max_attempts - 1:
                    # Last attempt failed
                    break

                # Calculate delay with exponential backoff
                delay = min(
                    retry_config.base_delay * (retry_config.backoff_factor ** attempt),
                    retry_config.max_delay
                )

                logger.warning(
                    f"Operation failed (attempt {attempt + 1}/{retry_config.max_attempts}), "
                    f"retrying in {delay:.2f}s: {e}"
                )

                time.sleep(delay)

        # All attempts failed
        logger.error(f"All {retry_config.max_attempts} attempts failed")
        raise last_exception


def provider_operation(operation_name: str):
    """
    Simple decorator for provider operations.

    Logs operation start/end for debugging purposes.
    Simplified version without complex connection manager integration.
    """
    def decorator(func):
        @wraps(func)
        async def async_wrapper(*args, **kwargs):
            logger.debug(f"Starting {operation_name}")
            try:
                result = await func(*args, **kwargs)
                logger.debug(f"Completed {operation_name}")
                return result
            except Exception as e:
                logger.error(f"Failed {operation_name}: {e}")
                raise

        @wraps(func)
        def sync_wrapper(*args, **kwargs):
            logger.debug(f"Starting {operation_name}")
            try:
                result = func(*args, **kwargs)
                logger.debug(f"Completed {operation_name}")
                return result
            except Exception as e:
                logger.error(f"Failed {operation_name}: {e}")
                raise

        if asyncio.iscoroutinefunction(func):
            return async_wrapper
        else:
            return sync_wrapper
    return decorator


__all__ = ['RetryMixin', 'RetryConfig', 'provider_operation']
