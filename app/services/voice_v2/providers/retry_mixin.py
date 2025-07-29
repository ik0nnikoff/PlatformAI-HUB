"""
RetryMixin для стандартизации retry конфигурации в voice_v2 провайдерах.

Phase 3.5.2 - Code Deduplication Implementation:
- Централизация retry конфигурации
- Интеграция с EnhancedConnectionManager
- SOLID принципы compliance
- Устранение дублирования retry параметров
"""

from typing import Dict, Any
from functools import wraps
import logging

from app.services.voice_v2.providers.enhanced_connection_manager import (
    ConnectionConfig, RetryStrategy
)

logger = logging.getLogger(__name__)


class RetryMixin:
    """
    Mixin для стандартизации retry конфигурации и ConnectionManager integration.

    Implements DRY principle by centralizing retry parameter handling.
    """

    def _get_retry_config(self, config: Dict[str, Any]) -> ConnectionConfig:
        """
        Извлечение retry конфигурации из provider config.

        Args:
            config: Provider configuration dictionary

        Returns:
            ConnectionConfig with retry settings
        """
        return ConnectionConfig(
            # Connection settings
            max_connections=config.get("max_connections", 100),
            max_connections_per_host=config.get("max_connections_per_host", 30),
            connection_timeout=config.get("connection_timeout", 30.0),
            read_timeout=config.get("read_timeout", 60.0),
            total_timeout=config.get("total_timeout", 120.0),

            # Retry settings
            retry_strategy=RetryStrategy.EXPONENTIAL_BACKOFF,
            max_retries=config.get("max_retries", 3),
            base_delay=config.get("base_delay", 1.0),
            max_delay=config.get("max_delay", 60.0),
            backoff_factor=config.get("backoff_factor", 2.0),
            jitter=config.get("jitter", True),

            # Circuit breaker settings
            circuit_breaker_threshold=config.get("circuit_breaker_threshold", 5),
            circuit_breaker_timeout_minutes=config.get("circuit_breaker_timeout_minutes", 5),

            # Keep-alive settings
            keepalive_timeout=config.get("keepalive_timeout", 30),
            enable_cleanup_closed=config.get("enable_cleanup_closed", True)
        )

    def _has_connection_manager(self) -> bool:
        """Check if connection manager is available."""
        return hasattr(self, '_connection_manager') and self._connection_manager is not None

    async def _execute_with_connection_manager(self, request_func, *args, **kwargs):
        """
        Execute request using ConnectionManager retry logic.

        Args:
            request_func: Function to execute
            *args: Function arguments
            **kwargs: Function keyword arguments

        Returns:
            Result of request_func execution
        """
        if not self._has_connection_manager():
            raise RuntimeError("ConnectionManager not available for retry execution")

        return await self._connection_manager.execute_request(
            provider_name=self.provider_name,
            request_func=request_func,
            *args,
            **kwargs
        )


def provider_operation(operation_name: str):
    """
    Декоратор для стандартизации логирования provider операций.

    Args:
        operation_name: Name of the operation for logging

    Returns:
        Decorated function with standard logging
    """
    def decorator(func):
        @wraps(func)
        async def wrapper(self, *args, **kwargs):
            provider_name = getattr(self, 'provider_name', 'unknown')
            logger.debug(f"Starting {operation_name} for {provider_name}")
            try:
                result = await func(self, *args, **kwargs)
                logger.debug(f"{operation_name} successful for {provider_name}")
                return result
            except Exception as e:
                logger.error(f"{operation_name} failed for {provider_name}: {e}", exc_info=True)
                raise
        return wrapper
    return decorator
