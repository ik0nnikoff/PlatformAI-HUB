"""
Voice_v2 Performance Utilities.

Высокопроизводительные utilities для measurements и optimization.
Следует принципам SRP и performance-first из Phase 1.3.

Performance utilities:
- PerformanceTimer: Context manager для измерения времени
- MetricsHelpers: Performance metrics collection
- time_async_operation: Async operation timing utility
"""

import asyncio
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Callable, TypeVar
import logging

logger = logging.getLogger(__name__)
T = TypeVar('T')


class PerformanceTimer:
    """
    High-performance timer для metrics collection.
    
    Single Responsibility: Только измерение времени выполнения.
    """
    
    def __init__(self, operation_name: str = "operation"):
        self.operation_name = operation_name
        self.start_time: Optional[float] = None
        self.end_time: Optional[float] = None
    
    def __enter__(self) -> 'PerformanceTimer':
        self.start_time = time.perf_counter()
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        self.end_time = time.perf_counter()
        execution_time = self.elapsed_ms
        
        logger.debug(
            f"Performance: {self.operation_name} completed in {execution_time:.2f}ms"
        )
    
    @property
    def elapsed_ms(self) -> float:
        """Время выполнения в миллисекундах."""
        if self.start_time is None:
            return 0.0
        
        end_time = self.end_time or time.perf_counter()
        return (end_time - self.start_time) * 1000
    
    @property
    def elapsed_seconds(self) -> float:
        """Время выполнения в секундах."""
        return self.elapsed_ms / 1000.0


class MetricsHelpers:
    """
    Performance metrics collection utilities.
    
    Single Responsibility: Только metrics collection и aggregation.
    """
    
    @staticmethod
    def create_operation_metrics(
        operation: str,
        duration_ms: float,
        success: bool,
        provider: Optional[str] = None
    ) -> Dict[str, Any]:
        """Создает метрики для операции."""
        metrics = {
            'operation': operation,
            'duration_ms': round(duration_ms, 2),
            'success': success,
            'timestamp': datetime.now(timezone.utc).isoformat()
        }
        
        if provider:
            metrics['provider'] = provider
        
        return metrics
    
    @staticmethod
    def aggregate_provider_metrics(
        metrics_list: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """Агрегирует метрики по провайдерам."""
        if not metrics_list:
            return {}
        
        total_operations = len(metrics_list)
        successful_operations = sum(1 for m in metrics_list if m.get('success', False))
        total_duration = sum(m.get('duration_ms', 0) for m in metrics_list)
        
        providers = {}
        for metric in metrics_list:
            provider = metric.get('provider')
            if provider:
                if provider not in providers:
                    providers[provider] = {'count': 0, 'success': 0, 'total_duration': 0}
                
                providers[provider]['count'] += 1
                if metric.get('success', False):
                    providers[provider]['success'] += 1
                providers[provider]['total_duration'] += metric.get('duration_ms', 0)
        
        return {
            'total_operations': total_operations,
            'success_rate': successful_operations / total_operations if total_operations > 0 else 0,
            'average_duration_ms': total_duration / total_operations if total_operations > 0 else 0,
            'providers': providers
        }


async def time_async_operation(
    operation: Callable,
    operation_name: str,
    *args,
    **kwargs
) -> tuple[Any, Dict[str, Any]]:
    """
    Измеряет время выполнения async операции.
    
    Returns:
        Tuple из (результат_операции, метрики)
    """
    start_time = time.perf_counter()
    success = False
    result = None
    
    try:
        result = await operation(*args, **kwargs)
        success = True
        return result, MetricsHelpers.create_operation_metrics(
            operation_name,
            (time.perf_counter() - start_time) * 1000,
            success
        )
    except Exception as e:
        logger.error(f"Operation {operation_name} failed: {e}", exc_info=True)
        return None, MetricsHelpers.create_operation_metrics(
            operation_name,
            (time.perf_counter() - start_time) * 1000,
            success
        )
