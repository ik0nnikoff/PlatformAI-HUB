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
import secrets
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

    def get_retry_configuration(self) -> Dict[str, Any]:
        """
        Возвращает стандартную конфигурацию retry параметров.

        Returns:
            Словарь с конфигурацией retry
        """
        return {
            'max_retries': 3,
            'base_delay': 1.0,
            'max_delay': 60.0,
            'jitter_range': (0.1, 0.3)
        }

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

                if not await self._should_retry_transcription(
                    error,
                    {
                        "attempt": attempt,
                        "max_retries": max_retries,
                        "provider_name": provider_name
                    },
                    error_handlers
                ):
                    break

                await self._handle_retry_delay(
                    {
                        "attempt": attempt,
                        "base_delay": base_delay,
                        "max_delay": max_delay,
                        "provider_name": provider_name
                    },
                    error
                )

        # Все попытки исчерпаны
        raise VoiceServiceError(
            f"{provider_name} transcription failed after {max_retries + 1} attempts: "
            f"{last_exception}"
        )

    async def _should_retry_transcription(
        self,
        error: Exception,
        retry_state: Dict[str, Any],
        error_handlers: Dict[Type[Exception], Callable[[Exception, int], bool]]
    ) -> bool:
        """
        Определяет, нужно ли повторять попытку транскрипции.

        Args:
            error: Возникшая ошибка
            retry_state: Состояние retry (attempt, max_retries, provider_name)
            error_handlers: Обработчики ошибок

        Returns:
            True если нужно повторить попытку, False иначе
        """
        # Проверяем, нужно ли повторять
        should_retry = False
        for error_type, handler in error_handlers.items():
            if isinstance(error, error_type):
                should_retry = handler(error, retry_state["attempt"])
                break

        if not should_retry or retry_state["attempt"] >= retry_state["max_retries"]:
            logger.error("%s transcription failed permanently: %s",
                       retry_state["provider_name"], error)
            return False

        return True

    async def _handle_retry_delay(
        self,
        retry_context: Dict[str, Any],
        error: Exception
    ) -> None:
        """
        Обрабатывает задержку перед повторной попыткой.

        Args:
            retry_context: Контекст retry (attempt, base_delay, max_delay, provider_name)
            error: Ошибка, которая привела к retry
        """
        delay = await self._calculate_retry_delay(
            retry_context["attempt"],
            retry_context["base_delay"],
            retry_context["max_delay"]
        )
        logger.warning(
            "%s transcription failed (attempt %d), retrying in %.2fs: %s",
            retry_context["provider_name"],
            retry_context["attempt"] + 1,
            delay,
            error
        )
        await asyncio.sleep(delay)

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
        # Используем криптографически безопасный генератор
        jitter_factor = secrets.randbelow(21) / 100.0 + 0.1  # Диапазон 0.1-0.3
        jitter = jitter_factor * delay

        return delay + jitter

    def get_default_error_handlers(self) -> Dict[Type[Exception],
                                                   Callable[[Exception, int], bool]]:
        """
        Возвращает базовые error handlers для общих случаев.

        Returns:
            Словарь обработчиков ошибок для типичных сценариев
        """
        return self._get_default_error_handlers()

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
