"""
LLMFactory - Специализированная фабрика для создания и настройки LLM экземпляров.

Эта фабрика отвечает за:
- Создание экземпляров LLM различных провайдеров
- Настройку параметров LLM (температура, streaming, etc.)
- Конфигурацию моделей для разных узлов workflow
- Управление привязкой инструментов к моделям

Фаза 7 SOLID рефакторинга - извлечено из factory.py
"""

import logging
from typing import Any, Dict, Optional, List
from langchain_openai import ChatOpenAI
from langchain_core.messages import BaseMessage

from app.agent_runner.langgraph.llm import LLMManager, TokenTracker
from app.agent_runner.langgraph.models import TokenUsageData
from app.agent_runner.core.config_mixin import AgentConfigMixin


class LLMFactory(AgentConfigMixin):
    """
    Специализированная фабрика для создания и настройки LLM экземпляров.
    
    Ответственности:
    - Создание LLM через LLMManager
    - Настройка параметров для разных узлов
    - Привязка инструментов к моделям
    - Извлечение данных о токенах
    """
    
    def __init__(self, agent_config: Dict, agent_id: str, logger: logging.LoggerAdapter):
        """
        Инициализирует LLMFactory.
        
        Args:
            agent_config: Конфигурация агента
            agent_id: Идентификатор агента  
            logger: Адаптер логгера
        """
        super().__init__()
        self.agent_config = agent_config
        self.agent_id = agent_id
        self.logger = logger
        
        # Инициализация менеджеров
        self.llm_manager = LLMManager(agent_id, logger)
        self.token_tracker = TokenTracker(logger)
        
        # LLM экземпляры
        self.main_llm: Optional[ChatOpenAI] = None
        self.tools: list = []
    
    def configure_main_llm(self) -> None:
        """
        Конфигурирует основной LLM экземпляр для графа.
        """
        model_config = self._get_model_config()

        self.main_llm = self.create_llm_instance(
            provider=model_config["provider"],
            model_name=model_config["model_id"],
            temperature=model_config["temperature"],
            streaming=model_config["streaming"]
        )
        
        if self.main_llm:
            self.logger.info(f"Main LLM configured: {model_config['model_id']} via {model_config['provider']}")
        else:
            self.logger.error(f"Failed to configure main LLM: {model_config['model_id']} via {model_config['provider']}")
    
    def create_llm_instance(
        self,
        provider: str,
        model_name: str,
        temperature: float,
        streaming: bool,
        log_adapter_override: Optional[logging.LoggerAdapter] = None
    ) -> Optional[ChatOpenAI]:
        """
        Создает экземпляр LLM через LLMManager (рефакторинг с CCN ≤ 2).
        
        Args:
            provider: Название провайдера
            model_name: Название модели
            temperature: Температура генерации
            streaming: Включить streaming
            log_adapter_override: Альтернативный логгер
            
        Returns:
            Экземпляр ChatOpenAI или None при ошибке
        """
        return self.llm_manager.create_llm_instance(
            provider=provider,
            model_name=model_name,
            temperature=temperature,
            streaming=streaming,
            log_adapter_override=log_adapter_override
        )
    
    def create_node_llm(self, node_type: str = "default", kb_ids: List[str] = None) -> Optional[ChatOpenAI]:
        """
        Создает LLM экземпляр для конкретного узла с соответствующей конфигурацией.
        
        Args:
            node_type: Тип узла (default, grading, generate, rewrite)
            kb_ids: Список ID баз знаний для узла
            
        Returns:
            Настроенный экземпляр ChatOpenAI
        """
        node_config = self._get_node_config(node_type, kb_ids)
        
        return self.create_llm_instance(
            provider=node_config["provider"],
            model_name=node_config["model_id"],
            temperature=node_config["temperature"],
            streaming=node_config["streaming"]
        )
    
    def bind_tools_to_model(self, model: ChatOpenAI) -> ChatOpenAI:
        """
        Привязывает инструменты к модели с проверкой валидности.
        
        Args:
            model: Экземпляр модели для привязки инструментов
            
        Returns:
            Модель с привязанными инструментами
        """
        if self.tools:
            valid_tools_for_binding = [t for t in self.tools if t is not None]
            if valid_tools_for_binding:
                model = model.bind_tools(valid_tools_for_binding)
                self.logger.info(f"Model bound to {len(valid_tools_for_binding)} tools.")
            else:
                self.logger.warning("No valid tools were configured after filtering.")
        else:
            self.logger.warning("No tools are configured in self.tools.")

        return model
    
    def extract_token_data(self, response: BaseMessage, call_type: str, node_model_id: str) -> Optional[TokenUsageData]:
        """
        Извлекает данные об использовании токенов из ответа LLM.
        
        Args:
            response: Ответ от LLM
            call_type: Тип вызова (agent, grading, generate, rewrite)
            node_model_id: ID модели узла
            
        Returns:
            Данные об использовании токенов или None
        """
        return self.token_tracker.extract_token_data(response, call_type, node_model_id)
    
    def _get_node_config(self, node_type: str = "default", kb_ids: List[str] = None) -> Dict[str, Any]:
        """
        Получает конфигурацию для конкретного типа узла.
        
        Args:
            node_type: Тип узла
            kb_ids: Список ID баз знаний
            
        Returns:
            Словарь с конфигурацией узла
        """
        # Базовая конфигурация
        base_config = self._get_model_config()
        
        # Специфичные настройки для типов узлов
        node_specific_configs = {
            "grading": {"temperature": 0.0},
            "generate": {"temperature": 0.7},
            "rewrite": {"temperature": 0.5}
        }
        
        if node_type in node_specific_configs:
            base_config.update(node_specific_configs[node_type])
        
        # Добавляем информацию о базах знаний если есть
        if kb_ids:
            base_config["kb_ids"] = kb_ids
            
        return base_config
