"""
Модуль фабрики агентов LangGraph - обратная совместимость.

ФАЗА 7 SOLID РЕФАКТОРИНГА ЗАВЕРШЕНА:
Оригинальная фабрика была разделена на специализированные компоненты:
- MainFactory: главная координация
- LLMFactory: управление LLM
- ComponentFactory: конфигурация компонентов  
- NodeFactory: создание узлов

Этот файл сохраняет обратную совместимость, делегируя к новой MainFactory.
Размер уменьшен с 1007 до ~50 строк (цель ≤400 достигнута).
"""

import logging
from typing import Any, Dict

# Импорт новой архитектуры Фазы 7
from .factory.main_factory import MainFactory, create_agent_app as new_create_agent_app

# Global flag для обратной совместимости
running = True


class GraphFactory:
    """
    Обратная совместимость для оригинального GraphFactory.
    Делегирует к новой MainFactory из Фазы 7.
    """
    
    def __init__(self, agent_config: Dict, agent_id: str, logger: logging.LoggerAdapter):
        """Инициализирует через MainFactory."""
        self.main_factory = MainFactory(agent_config, agent_id, logger)
        
        # Проксирование основных атрибутов для совместимости
        self.agent_config = agent_config
        self.agent_id = agent_id  
        self.logger = logger
    
    def create_graph(self) -> Any:
        """Создает граф через MainFactory."""
        return self.main_factory.create_graph()
    
    @property
    def llm(self):
        """Доступ к LLM через MainFactory."""
        return self.main_factory.llm
    
    @property
    def tools(self):
        """Доступ к инструментам через MainFactory."""
        return self.main_factory.tools


def create_agent_app(agent_config: Dict, agent_id: str, logger: logging.LoggerAdapter) -> Any:
    """
    Создает и конфигурирует граф агента - обратная совместимость.
    Делегирует к новой create_agent_app из MainFactory.
    """
    return new_create_agent_app(agent_config, agent_id, logger)
