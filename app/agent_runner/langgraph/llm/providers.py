"""
LLM Provider абстракции для SOLID архитектуры.
Обеспечивает единообразный интерфейс для различных LLM провайдеров.
"""

import logging
from abc import ABC, abstractmethod
from typing import Dict, Any, Optional

from langchain_openai import ChatOpenAI
from langchain_core.language_models.base import BaseLanguageModel

from app.core.config import settings


class LLMProviderError(Exception):
    """Исключение для ошибок LLM провайдера."""
    pass


class BaseLLMProvider(ABC):
    """
    Базовый абстрактный класс для LLM провайдеров.
    
    Обеспечивает SOLID принципы:
    - Single Responsibility: Каждый провайдер отвечает за один тип LLM
    - Open/Closed: Легко добавлять новые провайдеры
    - Liskov Substitution: Все провайдеры взаимозаменяемы
    - Interface Segregation: Минимальный интерфейс
    - Dependency Inversion: Зависимость от абстракции
    """
    
    def __init__(self, agent_id: str, logger: logging.LoggerAdapter):
        self.agent_id = agent_id
        self.logger = logger
    
    @abstractmethod
    def create_llm(
        self,
        model_name: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        **kwargs
    ) -> BaseLanguageModel:
        """
        Создает экземпляр LLM.
        
        Args:
            model_name: Название модели
            temperature: Температура генерации
            max_tokens: Максимальное количество токенов
            **kwargs: Дополнительные параметры
            
        Returns:
            BaseLanguageModel: Настроенный экземпляр LLM
            
        Raises:
            LLMProviderError: При ошибке создания LLM
        """
        pass
    
    @abstractmethod
    def get_default_model(self) -> str:
        """Возвращает модель по умолчанию для провайдера."""
        pass
    
    @abstractmethod
    def validate_model(self, model_name: str) -> bool:
        """Проверяет, поддерживается ли модель провайдером."""
        pass


class OpenAIProvider(BaseLLMProvider):
    """
    Провайдер для OpenAI моделей.
    CCN ≤ 4 для каждого метода.
    """
    
    SUPPORTED_MODELS = {
        "gpt-4o-mini",
        "gpt-4o", 
        "gpt-4-turbo",
        "gpt-4",
        "gpt-3.5-turbo"
    }
    
    def create_llm(
        self,
        model_name: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        **kwargs
    ) -> BaseLanguageModel:
        """
        Создает OpenAI LLM экземпляр (CCN ≤ 3).
        """
        try:
            effective_model = model_name or self.get_default_model()
            
            if not self.validate_model(effective_model):
                raise LLMProviderError(f"Unsupported OpenAI model: {effective_model}")
            
            self.logger.debug(f"Creating OpenAI LLM: {effective_model}")
            
            llm_params = {
                "model": effective_model,
                "temperature": temperature,
                "api_key": settings.openai.api_key,
                **kwargs
            }
            
            if max_tokens:
                llm_params["max_tokens"] = max_tokens
            
            return ChatOpenAI(**llm_params)
            
        except Exception as e:
            error_msg = f"Failed to create OpenAI LLM: {e}"
            self.logger.error(error_msg, exc_info=True)
            raise LLMProviderError(error_msg) from e
    
    def get_default_model(self) -> str:
        """Возвращает модель OpenAI по умолчанию (CCN ≤ 1)."""
        return "gpt-4o-mini"
    
    def validate_model(self, model_name: str) -> bool:
        """Проверяет поддержку модели OpenAI (CCN ≤ 1)."""
        return model_name in self.SUPPORTED_MODELS


class OpenRouterProvider(BaseLLMProvider):
    """
    Провайдер для OpenRouter моделей.
    CCN ≤ 4 для каждого метода.
    """
    
    SUPPORTED_MODELS = {
        "openai/gpt-4o-mini",
        "openai/gpt-4o",
        "anthropic/claude-3-sonnet",
        "anthropic/claude-3-haiku",
        "google/gemini-pro"
    }
    
    def create_llm(
        self,
        model_name: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: Optional[int] = None,
        **kwargs
    ) -> BaseLanguageModel:
        """
        Создает OpenRouter LLM экземпляр (CCN ≤ 3).
        """
        try:
            effective_model = model_name or self.get_default_model()
            
            if not self.validate_model(effective_model):
                raise LLMProviderError(f"Unsupported OpenRouter model: {effective_model}")
            
            self.logger.debug(f"Creating OpenRouter LLM: {effective_model}")
            
            llm_params = {
                "model": effective_model,
                "temperature": temperature,
                "api_key": settings.openrouter.api_key,
                "base_url": "https://openrouter.ai/api/v1",
                **kwargs
            }
            
            if max_tokens:
                llm_params["max_tokens"] = max_tokens
            
            return ChatOpenAI(**llm_params)
            
        except Exception as e:
            error_msg = f"Failed to create OpenRouter LLM: {e}"
            self.logger.error(error_msg, exc_info=True)
            raise LLMProviderError(error_msg) from e
    
    def get_default_model(self) -> str:
        """Возвращает модель OpenRouter по умолчанию (CCN ≤ 1)."""
        return "openai/gpt-4o-mini"
    
    def validate_model(self, model_name: str) -> bool:
        """Проверяет поддержку модели OpenRouter (CCN ≤ 1)."""
        return model_name in self.SUPPORTED_MODELS


class LLMProviderFactory:
    """
    Фабрика для создания LLM провайдеров.
    CCN ≤ 3 для каждого метода.
    """
    
    PROVIDERS = {
        "openai": OpenAIProvider,
        "openrouter": OpenRouterProvider
    }
    
    @classmethod
    def create_provider(
        cls, 
        provider_type: str, 
        agent_id: str, 
        logger: logging.LoggerAdapter
    ) -> BaseLLMProvider:
        """
        Создает провайдер по типу (CCN ≤ 2).
        
        Args:
            provider_type: Тип провайдера ("openai", "openrouter")
            agent_id: ID агента
            logger: Логгер
            
        Returns:
            BaseLLMProvider: Экземпляр провайдера
            
        Raises:
            LLMProviderError: При неподдерживаемом типе провайдера
        """
        if provider_type not in cls.PROVIDERS:
            raise LLMProviderError(f"Unsupported provider type: {provider_type}")
        
        provider_class = cls.PROVIDERS[provider_type]
        return provider_class(agent_id, logger)
    
    @classmethod
    def get_supported_providers(cls) -> list[str]:
        """Возвращает список поддерживаемых провайдеров (CCN ≤ 1)."""
        return list(cls.PROVIDERS.keys())
