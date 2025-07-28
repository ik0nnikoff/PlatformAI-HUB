"""
STT Provider Factory - Phase 3.1.5
Динамическое создание STT провайдеров на основе конфигурации
"""

from typing import Dict, Type, Any
import logging

from ..providers.stt.base_stt import BaseSTTProvider
from ..providers.stt.openai_stt import OpenAISTTProvider
from ..providers.stt.yandex_stt import YandexSTTProvider
from .exceptions import ProviderNotAvailableError, ConfigurationError

logger = logging.getLogger(__name__)


class STTProviderFactory:
    """Factory для создания STT провайдеров"""
    
    # Регистр доступных провайдеров
    PROVIDERS: Dict[str, Type[BaseSTTProvider]] = {
        'openai': OpenAISTTProvider,
        'yandex': YandexSTTProvider,
    }
    
    @classmethod
    async def create_provider(cls, provider_name: str, config: Dict[str, Any]) -> BaseSTTProvider:
        """
        Создает экземпляр STT провайдера
        
        Args:
            provider_name: Имя провайдера ('openai', 'yandex', etc.)
            config: Конфигурация провайдера
            
        Returns:
            Инициализированный провайдер
            
        Raises:
            ProviderNotAvailableError: Если провайдер не найден
            ConfigurationError: Если конфигурация некорректна
        """
        try:
            # Проверяем наличие провайдера
            if provider_name not in cls.PROVIDERS:
                available = list(cls.PROVIDERS.keys())
                raise ProviderNotAvailableError(
                    f"Provider '{provider_name}' not found. Available: {available}"
                )
            
            provider_class = cls.PROVIDERS[provider_name]
            
            # Создаем экземпляр провайдера
            provider = provider_class(config)
            
            # Инициализируем провайдер
            await provider.initialize()
            
            logger.info(f"Successfully created STT provider: {provider_name}")
            return provider
            
        except Exception as e:
            logger.error(f"Failed to create STT provider {provider_name}: {e}")
            raise
    
    @classmethod
    def get_available_providers(cls) -> list[str]:
        """Возвращает список доступных провайдеров"""
        return list(cls.PROVIDERS.keys())
    
    @classmethod
    def is_provider_available(cls, provider_name: str) -> bool:
        """Проверяет доступность провайдера"""
        return provider_name in cls.PROVIDERS
    
    @classmethod
    def register_provider(cls, name: str, provider_class: Type[BaseSTTProvider]) -> None:
        """
        Регистрирует новый провайдер
        
        Args:
            name: Имя провайдера
            provider_class: Класс провайдера
        """
        if not issubclass(provider_class, BaseSTTProvider):
            raise TypeError(f"Provider class must inherit from BaseSTTProvider")
        
        cls.PROVIDERS[name] = provider_class
        logger.info(f"Registered new STT provider: {name}")
