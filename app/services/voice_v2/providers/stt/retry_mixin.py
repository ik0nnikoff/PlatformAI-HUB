"""
STT Retry Mixin - Phase 1.2 Deduplication

Mixin для стандартизации retry логики STT операций.
Устраняет дублирование ~40 строк retry кода на провайдер.

Architecture Compliance:
- DRY Principle: Единая retry логика для всех провайдеров
- SOLID: Single Responsibility - только retry функционал
- Performance: Exponential backoff с настраиваемыми параметрами
"""

import asyncio
import logging
import random
from typing import Any, Awaitable, Callable, Dict, Type

from ...core.exceptions import VoiceServiceError

logger = logging.getLogger(__name__)


class STTRetryMixin:
    """
    Mixin для стандартизации retry логики STT операций.

    Устраняет дублирование:
    - Exponential backoff логики
    - Error handling patterns
    - Retry attempt logging
    - Timeout обработки
    """

    async def _standard_transcribe_with_retry(
        self,
        transcription_func: Callable[[], Awaitable[Any]],
        error_handlers: Dict[Type[Exception], Callable[[Exception, int], bool]],
        **kwargs
    ) -> Any:
        """
        Стандартная retry логика для транскрипции.

        Args:
            transcription_func: Функция выполнения транскрипции
            error_handlers: Словарь обработчиков ошибок
            **kwargs: Дополнительные параметры (max_retries, base_delay, max_delay, provider_name)

        Returns:
            Результат транскрипции

        Raises:
            VoiceServiceError: После исчерпания всех попыток
        """
        max_retries = kwargs.get('max_retries', 3)
        base_delay = kwargs.get('base_delay', 1.0)
        max_delay = kwargs.get('max_delay', 60.0)
        provider_name = kwargs.get('provider_name', "STT Provider")
        last_exception = None

        for attempt in range(max_retries + 1):
            try:
                logger.debug("%s transcription attempt %d/%d",
                           provider_name, attempt + 1, max_retries + 1)
                return await transcription_func()

            except (ConnectionError, TimeoutError, OSError, VoiceServiceError) as error:
                last_exception = error

                # Проверяем, нужно ли повторять
                should_retry = False
                for error_type, handler in error_handlers.items():
                    if isinstance(error, error_type):
                        should_retry = handler(error, attempt)
                        break

                if not should_retry or attempt >= max_retries:
                    logger.error("%s transcription failed permanently: %s",
                               provider_name, error)
                    break

                # Применяем exponential backoff
                delay = await self._calculate_retry_delay(
                    attempt, base_delay, max_delay
                )
                logger.warning(
                    "%s transcription failed (attempt %d), retrying in %.2fs: %s",
                    provider_name, attempt + 1, delay, error
                )
                await asyncio.sleep(delay)        # Все попытки исчерпаны
        raise VoiceServiceError(
            f"{provider_name} transcription failed after {max_retries + 1} attempts: "
            f"{last_exception}"
        )

    async def _calculate_retry_delay(
        self,
        attempt: int,
        base_delay: float,
        max_delay: float
    ) -> float:
        """
        Вычисляет задержку для retry с exponential backoff и jitter.

        Args:
            attempt: Номер попытки (0-based)
            base_delay: Базовая задержка
            max_delay: Максимальная задержка

        Returns:
            Задержка в секундах
        """
        # Exponential backoff: base_delay * 2^attempt
        delay = base_delay * (2 ** attempt)

        # Ограничиваем максимальной задержкой
        delay = min(delay, max_delay)

        # Добавляем jitter для предотвращения thundering herd
        jitter = random.uniform(0.1, 0.3) * delay

        return delay + jitter

    def _get_default_error_handlers(self) -> Dict[Type[Exception],
                                                   Callable[[Exception, int], bool]]:
        """
        Создает базовые error handlers для общих случаев.

        Returns:
            Словарь обработчиков ошибок для типичных сценариев
        """
        def handle_network_error(_error: Exception, _attempt: int) -> bool:
            """Обработка сетевых ошибок - всегда повторяем"""
            return True

        # Возвращаем базовые обработчики - провайдеры могут расширить
        return {
            ConnectionError: handle_network_error,
            TimeoutError: handle_network_error,
            OSError: handle_network_error,  # Включает network-related OS errors
        }


# Экспорт для использования в провайдерах
__all__ = ['STTRetryMixin']
