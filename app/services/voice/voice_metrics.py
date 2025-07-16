"""
Система метрик для голосовых сервисов
"""

import time
import json
import logging
from typing import Optional, Dict, Any
from dataclasses import dataclass, asdict
from app.services.redis_wrapper import RedisService


@dataclass
class VoiceMetrics:
    """Метрики голосовых сервисов"""
    timestamp: float
    agent_id: str
    user_id: str
    operation: str  # 'stt' или 'tts'
    provider: str  # 'openai', 'google', 'yandex'
    success: bool
    processing_time: float
    error_message: Optional[str] = None
    input_size_bytes: Optional[int] = None
    output_size_bytes: Optional[int] = None
    duration_seconds: Optional[float] = None
    accuracy_score: Optional[float] = None
    

class VoiceMetricsCollector:
    """
    Сборщик и хранитель метрик голосовых сервисов
    """
    
    def __init__(self, 
                 redis_service: RedisService,
                 logger: Optional[logging.Logger] = None):
        self.redis_service = redis_service
        self.logger = logger or logging.getLogger("voice_metrics")
        self.metrics_key_prefix = "voice_metrics:"
        self.daily_stats_key_prefix = "voice_daily_stats:"
        
    async def record_metric(self, metric: VoiceMetrics) -> None:
        """
        Записывает метрику в Redis
        
        Args:
            metric: Метрика для записи
        """
        try:
            # Сохраняем индивидуальную метрику
            metric_key = f"{self.metrics_key_prefix}{metric.agent_id}:{metric.operation}"
            metric_data = json.dumps(asdict(metric))
            
            # Добавляем в timeline (sorted set по времени)
            await self.redis_service.zadd(
                metric_key,
                {metric_data: metric.timestamp}
            )
            
            # Устанавливаем TTL на 7 дней для индивидуальных метрик
            await self.redis_service.expire(metric_key, 7 * 24 * 3600)
            
            # Обновляем дневную статистику
            await self._update_daily_stats(metric)
            
            self.logger.debug(f"Recorded voice metric: {metric.operation} for {metric.agent_id}")
            
        except Exception as e:
            self.logger.error(f"Error recording voice metric: {e}", exc_info=True)
    
    async def _update_daily_stats(self, metric: VoiceMetrics) -> None:
        """
        Обновляет дневную статистику
        
        Args:
            metric: Метрика для обновления статистики
        """
        try:
            # Ключ для дневной статистики (YYYY-MM-DD)
            day_key = time.strftime("%Y-%m-%d", time.gmtime(metric.timestamp))
            stats_key = f"{self.daily_stats_key_prefix}{metric.agent_id}:{day_key}"
            
            # Получаем текущую статистику
            current_stats = await self.redis_service.get(stats_key)
            
            if current_stats:
                stats = json.loads(current_stats)
            else:
                stats = {
                    "date": day_key,
                    "agent_id": metric.agent_id,
                    "stt": {"total": 0, "success": 0, "total_time": 0.0, "providers": {}},
                    "tts": {"total": 0, "success": 0, "total_time": 0.0, "providers": {}},
                    "total_requests": 0,
                    "unique_users": set()
                }
            
            # Конвертируем set в list для JSON serialization
            if isinstance(stats.get("unique_users"), list):
                stats["unique_users"] = set(stats["unique_users"])
            elif "unique_users" not in stats:
                stats["unique_users"] = set()
            
            # Обновляем статистику
            operation_stats = stats[metric.operation]
            operation_stats["total"] += 1
            if metric.success:
                operation_stats["success"] += 1
            operation_stats["total_time"] += metric.processing_time
            
            # Статистика по провайдерам
            if metric.provider not in operation_stats["providers"]:
                operation_stats["providers"][metric.provider] = {
                    "total": 0, "success": 0, "total_time": 0.0
                }
            
            provider_stats = operation_stats["providers"][metric.provider]
            provider_stats["total"] += 1
            if metric.success:
                provider_stats["success"] += 1
            provider_stats["total_time"] += metric.processing_time
            
            # Общая статистика
            stats["total_requests"] += 1
            stats["unique_users"].add(metric.user_id)
            
            # Конвертируем set обратно в list для JSON
            stats_to_save = stats.copy()
            stats_to_save["unique_users"] = list(stats["unique_users"])
            
            # Сохраняем обновленную статистику
            await self.redis_service.setex(
                stats_key,
                30 * 24 * 3600,  # 30 дней TTL
                json.dumps(stats_to_save)
            )
            
        except Exception as e:
            self.logger.error(f"Error updating daily stats: {e}", exc_info=True)
    
    async def get_agent_metrics(self, 
                               agent_id: str, 
                               operation: str = None,
                               hours: int = 24) -> Dict[str, Any]:
        """
        Получает метрики агента за последние N часов
        
        Args:
            agent_id: ID агента
            operation: Тип операции ('stt', 'tts') или None для всех
            hours: Количество часов назад
            
        Returns:
            Словарь с метриками
        """
        try:
            operations = [operation] if operation else ["stt", "tts"]
            metrics = {"agent_id": agent_id, "operations": {}}
            
            cutoff_time = time.time() - (hours * 3600)
            
            for op in operations:
                metric_key = f"{self.metrics_key_prefix}{agent_id}:{op}"
                
                # Получаем метрики за последние N часов
                raw_metrics = await self.redis_service.zrangebyscore(
                    metric_key,
                    cutoff_time,
                    "+inf",
                    withscores=True
                )
                
                op_metrics = []
                for metric_data, timestamp in raw_metrics:
                    try:
                        metric = json.loads(metric_data)
                        op_metrics.append(metric)
                    except json.JSONDecodeError:
                        continue
                
                # Вычисляем статистику
                total = len(op_metrics)
                success = sum(1 for m in op_metrics if m.get("success", False))
                avg_time = sum(m.get("processing_time", 0) for m in op_metrics) / max(1, total)
                providers = {}
                
                for metric in op_metrics:
                    provider = metric.get("provider", "unknown")
                    if provider not in providers:
                        providers[provider] = {"total": 0, "success": 0}
                    providers[provider]["total"] += 1
                    if metric.get("success", False):
                        providers[provider]["success"] += 1
                
                metrics["operations"][op] = {
                    "total_requests": total,
                    "successful_requests": success,
                    "success_rate": success / max(1, total),
                    "average_processing_time": avg_time,
                    "providers": providers,
                    "raw_metrics": op_metrics if total <= 100 else []  # Лимит на детали
                }
            
            return metrics
            
        except Exception as e:
            self.logger.error(f"Error getting agent metrics: {e}", exc_info=True)
            return {"error": str(e)}
    
    async def get_daily_stats(self, agent_id: str, days: int = 7) -> Dict[str, Any]:
        """
        Получает дневную статистику агента
        
        Args:
            agent_id: ID агента
            days: Количество дней назад
            
        Returns:
            Словарь с дневной статистикой
        """
        try:
            stats = {"agent_id": agent_id, "daily_stats": []}
            
            for i in range(days):
                day_timestamp = time.time() - (i * 24 * 3600)
                day_key = time.strftime("%Y-%m-%d", time.gmtime(day_timestamp))
                stats_key = f"{self.daily_stats_key_prefix}{agent_id}:{day_key}"
                
                day_stats = await self.redis_service.get(stats_key)
                if day_stats:
                    try:
                        day_data = json.loads(day_stats)
                        # Добавляем вычисленные поля
                        for op in ["stt", "tts"]:
                            if op in day_data:
                                op_data = day_data[op]
                                op_data["success_rate"] = op_data["success"] / max(1, op_data["total"])
                                op_data["avg_processing_time"] = op_data["total_time"] / max(1, op_data["total"])
                        
                        stats["daily_stats"].append(day_data)
                    except json.JSONDecodeError:
                        continue
                else:
                    # Добавляем пустую статистику для дня
                    stats["daily_stats"].append({
                        "date": day_key,
                        "agent_id": agent_id,
                        "stt": {"total": 0, "success": 0, "success_rate": 0.0, "avg_processing_time": 0.0},
                        "tts": {"total": 0, "success": 0, "success_rate": 0.0, "avg_processing_time": 0.0},
                        "total_requests": 0,
                        "unique_users": []
                    })
            
            return stats
            
        except Exception as e:
            self.logger.error(f"Error getting daily stats: {e}", exc_info=True)
            return {"error": str(e)}
