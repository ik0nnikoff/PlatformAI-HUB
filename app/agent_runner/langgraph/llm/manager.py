"""
LLM Manager для создания и управления LLM экземплярами.
Рефакторинг _create_llm_instance с понижением CCN с 12 до ≤8.
"""

import logging
from typing import Dict, Any, Optional

from langchain_openai import ChatOpenAI
from langchain_core.language_models.base import BaseLanguageModel

from app.core.config import settings
from .providers import LLMProviderFactory, BaseLLMProvider, LLMProviderError


class LLMManager:
    """
    Менеджер для создания и управления LLM экземплярами.
    Использует Provider Pattern для SOLID архитектуры.
    CCN ≤ 4 для каждого метода.
    """
    
    def __init__(self, agent_id: str, logger: logging.LoggerAdapter):
        self.agent_id = agent_id
        self.logger = logger
        self._providers: Dict[str, BaseLLMProvider] = {}
        self._register_default_providers()
    
    def _register_default_providers(self) -> None:
        """Регистрирует стандартные LLM провайдеры (CCN ≤ 2)."""
        try:
            openai_provider = LLMProviderFactory.create_provider("openai", self.agent_id, self.logger)
            openrouter_provider = LLMProviderFactory.create_provider("openrouter", self.agent_id, self.logger)
            
            self._providers["openai"] = openai_provider
            self._providers["openrouter"] = openrouter_provider
            
            self.logger.debug("Default LLM providers registered successfully")
            
        except Exception as e:
            self.logger.error(f"Failed to register default providers: {e}", exc_info=True)
            raise LLMProviderError(f"Provider registration failed: {e}") from e
    
    def create_llm_instance(
        self,
        provider: str,
        model_name: str,
        temperature: float = 0.1,
        max_tokens: Optional[int] = None,
        **kwargs
    ) -> BaseLanguageModel:
        """
        Создает экземпляр LLM через выбранного провайдера (CCN ≤ 3).
        
        Args:
            provider: Тип провайдера ("openai", "openrouter")
            model_name: Название модели
            temperature: Температура генерации
            max_tokens: Максимальное количество токенов
            **kwargs: Дополнительные параметры
            
        Returns:
            BaseLanguageModel: Настроенный экземпляр LLM
            
        Raises:
            LLMProviderError: При ошибке создания LLM
        """
        try:
            if provider not in self._providers:
                raise LLMProviderError(f"Provider '{provider}' not registered")
            
            llm_provider = self._providers[provider]
            
            self.logger.debug(f"Creating LLM instance: provider={provider}, model={model_name}")
            
            return llm_provider.create_llm(
                model_name=model_name,
                temperature=temperature,
                max_tokens=max_tokens,
                **kwargs
            )
            
        except Exception as e:
            error_msg = f"LLM creation failed: {e}"
            self.logger.error(error_msg, exc_info=True)
            raise LLMProviderError(error_msg) from e
    
    def get_supported_providers(self) -> list[str]:
        """Возвращает список поддерживаемых провайдеров (CCN ≤ 1)."""
        return list(self._providers.keys())
    
    def validate_provider_model(self, provider: str, model_name: str) -> bool:
        """
        Проверяет поддержку модели провайдером (CCN ≤ 2).
        
        Args:
            provider: Тип провайдера
            model_name: Название модели
            
        Returns:
            bool: True если модель поддерживается
        """
        if provider not in self._providers:
            return False
        
        return self._providers[provider].validate_model(model_name)
    
    def get_default_model(self, provider: str) -> Optional[str]:
        """
        Возвращает модель по умолчанию для провайдера (CCN ≤ 2).
        
        Args:
            provider: Тип провайдера
            
        Returns:
            Optional[str]: Модель по умолчанию или None
        """
        if provider not in self._providers:
            return None
        
        return self._providers[provider].get_default_model()


# Легаси классы для обратной совместимости
class OpenAIProvider:
    """Легаси класс для обратной совместимости."""
    
    def __init__(self, logger: logging.LoggerAdapter):
        self.logger = logger
        
    def create_llm(self, **kwargs) -> BaseLanguageModel:
        """Создает OpenAI LLM через новую архитектуру."""
        provider = LLMProviderFactory.create_provider("openai", "legacy", self.logger)
        return provider.create_llm(**kwargs)


class OpenRouterProvider:
    """Легаси класс для обратной совместимости."""
    
    def __init__(self, logger: logging.LoggerAdapter):
        self.logger = logger
        
    def create_llm(self, **kwargs) -> BaseLanguageModel:
        """Создает OpenRouter LLM через новую архитектуру.""" 
        provider = LLMProviderFactory.create_provider("openrouter", "legacy", self.logger)
        return provider.create_llm(**kwargs)
