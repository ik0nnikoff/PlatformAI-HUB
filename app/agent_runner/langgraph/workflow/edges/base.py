"""
Base Edge Router для LangGraph workflow edges.
Базовая абстракция для всех edge routers согласно SOLID принципам.
"""

import logging
from abc import ABC, abstractmethod
from typing import Any

from app.agent_runner.langgraph.models import AgentState


class BaseEdgeRouter(ABC):
    """Базовый маршрутизатор edges с общим функционалом."""
    
    def __init__(self, factory, logger: logging.LoggerAdapter):
        """
        Инициализирует базовый edge router.
        
        Args:
            factory: Фабрика графа с доступом к конфигурации
            logger: Логгер для edge router
        """
        self.factory = factory
        self.logger = logger
    
    @abstractmethod
    def route(self, state: AgentState) -> Any:
        """
        Выполняет маршрутизацию на основе состояния.
        
        Args:
            state: Состояние агента
            
        Returns:
            Any: Результат маршрутизации (обычно строка с именем узла)
        """
        pass
    
    def _log_routing_decision(self, decision: str, details: str = "") -> None:
        """Логирует решение маршрутизации."""
        log_message = f"---DECISION: {decision}---"
        if details:
            log_message += f" ({details})"
        self.logger.info(log_message)
    
    def _validate_state_for_routing(self, state: AgentState, required_keys: list) -> bool:
        """
        Валидирует состояние для маршрутизации.
        
        Args:
            state: Состояние для проверки
            required_keys: Список обязательных ключей
            
        Returns:
            bool: True если состояние валидно
        """
        for key in required_keys:
            if key not in state:
                self.logger.warning(f"Required state key '{key}' not found for routing")
                return False
        return True
