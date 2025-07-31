"""
Performance Optimization Utils - Shared utilities for voice optimization

Содержит общие утилиты для STT/TTS оптимизаторов:
- Cache key generation utilities
- Connection pool factory
- Common optimization helpers
"""

import hashlib
from typing import Dict, Any, Optional


class CacheKeyGenerator:
    """Utility class for generating cache keys for voice operations"""

    @staticmethod
    def generate_stt_key(audio_data: bytes, language: Optional[str] = None) -> str:
        """
        Generate optimized cache key for STT result.
        Uses SHA256 for security.
        """
        hasher = hashlib.sha256()
        hasher.update(audio_data)
        if language:
            hasher.update(language.encode())

        return f"stt:{hasher.hexdigest()[:16]}"

    @staticmethod
    def generate_tts_key(text: str, voice_config: Dict[str, Any]) -> str:
        """
        Generate optimized cache key for TTS result.
        Uses SHA256 for security.
        """
        hasher = hashlib.sha256()
        hasher.update(text.encode('utf-8'))

        # Include voice configuration in cache key
        cache_params = {
            'voice': voice_config.get('voice', ''),
            'speed': voice_config.get('speed', 1.0),
            'pitch': voice_config.get('pitch', 0.0),
            'format': voice_config.get('format', 'mp3')
        }

        for key in sorted(cache_params.keys()):
            hasher.update(f"{key}:{cache_params[key]}".encode())

        return f"tts:{hasher.hexdigest()[:16]}"


class PerformanceUtils:
    """Common performance optimization utilities"""

    @staticmethod
    def calculate_performance_score(latency: float, success_rate: float,
                                    target_latency: float = 3.0) -> float:
        """
        Calculate overall performance score (0-100)
        """
        if success_rate == 0:
            return 0.0

        latency_score = max(0, 100 - (latency / target_latency * 50))
        success_score = success_rate

        return (latency_score * 0.6 + success_score * 0.4)

    @staticmethod
    def should_trigger_optimization(metrics_dict: Dict, health_threshold: float = 80.0) -> bool:
        """
        Determine if optimization should be triggered based on metrics
        """
        unhealthy_count = 0
        total_providers = len(metrics_dict)

        for metrics in metrics_dict.values():
            if hasattr(metrics, 'is_healthy') and not metrics.is_healthy:
                unhealthy_count += 1

        unhealthy_percentage = (
            unhealthy_count /
            total_providers *
            100) if total_providers > 0 else 0
        return unhealthy_percentage > (100 - health_threshold)
