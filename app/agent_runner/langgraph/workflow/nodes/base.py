"""
Base Node Handler для LangGraph workflow nodes.
Базовая абстракция для всех узлов согласно SOLID принципам.
"""

import logging
from abc import ABC, abstractmethod
from typing import Dict, Any

from app.agent_runner.core import NodeExecutionError
from app.agent_runner.langgraph.core import LangGraphNode
from app.agent_runner.langgraph.models import AgentState


class BaseNodeHandler(LangGraphNode, ABC):
    """Базовый обработчик узлов с общим функционалом."""
    
    def __init__(self, factory, logger: logging.LoggerAdapter):
        """
        Инициализирует базовый узел.
        
        Args:
            factory: Фабрика графа с доступом к LLM и конфигурации
            logger: Логгер для узла
        """
        self.factory = factory
        self.logger = logger
    
    @abstractmethod
    async def execute(self, state: AgentState, config: dict = None) -> Dict[str, Any]:
        """
        Выполняет логику узла.
        
        Args:
            state: Состояние агента
            config: Конфигурация выполнения
            
        Returns:
            Dict[str, Any]: Обновленное состояние
            
        Raises:
            NodeExecutionError: При ошибке выполнения
        """
        pass
    
    def _log_node_entry(self, node_name: str, agent_id: str) -> None:
        """Логирует вход в узел."""
        self.logger.info(f"---{node_name.upper()} (Agent ID: {agent_id})---")
    
    def _create_error_response(self, error_message: str) -> Dict[str, Any]:
        """Создает стандартный ответ об ошибке."""
        from langchain_core.messages import AIMessage
        error_response = AIMessage(content=f"Извините, произошла ошибка: {error_message}")
        return {"messages": [error_response]}
    
    def _validate_state_requirements(self, state: AgentState, required_keys: list) -> bool:
        """
        Валидирует наличие обязательных ключей в состоянии.
        
        Args:
            state: Состояние для проверки
            required_keys: Список обязательных ключей
            
        Returns:
            bool: True если все ключи присутствуют
        """
        for key in required_keys:
            if key not in state:
                self.logger.error(f"Required state key '{key}' not found")
                return False
        return True
