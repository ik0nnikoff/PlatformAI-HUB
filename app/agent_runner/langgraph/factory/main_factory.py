"""
MainFactory - Главная фабрика для создания и координации LangGraph агентов.

Эта фабрика является главным координатором и отвечает за:
- Общую координацию создания графа агента
- Интеграцию специализированных фабрик
- Управление жизненным циклом компонентов
- Создание конечного приложения LangGraph

Фаза 7 SOLID рефакторинга - главная фабрика из factory.py (≤300 строк)
"""

import logging
from typing import Any, Dict, Literal
from langchain_core.messages import BaseMessage

from app.agent_runner.langgraph.models import AgentState, TokenUsageData
from app.agent_runner.langgraph.workflow.builder import WorkflowGraphBuilder
from app.agent_runner.core.config_mixin import AgentConfigMixin

from .llm_factory import LLMFactory
from .component_factory import ComponentFactory
from .node_factory import NodeFactory


class MainFactory(AgentConfigMixin):
    """
    Главная фабрика для создания и настройки графа агента с использованием LangGraph.
    
    Координирует работу специализированных фабрик:
    - LLMFactory: создание и настройка LLM
    - ComponentFactory: конфигурация компонентов
    - NodeFactory: создание узлов workflow
    
    Размер: ≤300 строк согласно требованиям Фазы 7
    """
    
    def __init__(self, agent_config: Dict, agent_id: str, logger: logging.LoggerAdapter):
        """
        Инициализирует главную фабрику и все специализированные фабрики.
        
        Args:
            agent_config: Конфигурация агента
            agent_id: Идентификатор агента
            logger: Адаптер логгера
        """
        super().__init__()
        self.agent_config = agent_config
        self.agent_id = agent_id
        self.logger = logger
        
        # Инициализация специализированных фабрик
        self.llm_factory = LLMFactory(agent_config, agent_id, logger)
        self.component_factory = ComponentFactory(agent_config, agent_id, logger, self)
        self.node_factory = NodeFactory(agent_config, agent_id, logger, self)
        
        # Настройка основного LLM
        self.llm_factory.configure_main_llm()
    
    @property
    def llm(self):
        """Доступ к основному LLM через LLMFactory."""
        return self.llm_factory.main_llm
    
    @property 
    def tools(self):
        """Доступ к инструментам через ComponentFactory."""
        return self.component_factory.tools
    
    @property
    def system_prompt(self):
        """Доступ к системному промпту через ComponentFactory."""
        return self.component_factory.system_prompt
    
    @property
    def enable_context_memory(self):
        """Доступ к настройкам памяти через ComponentFactory."""
        return self.component_factory.enable_context_memory
    
    def create_graph(self) -> Any:
        """
        Создает граф LangGraph приложения используя WorkflowGraphBuilder.
        
        Главный метод фабрики - координирует:
        1. Конфигурацию компонентов
        2. Построение графа через Builder
        3. Финальную настройку
        
        Returns:
            Готовое LangGraph приложение
        """
        self.logger.info("Creating agent graph in MainFactory...")

        # Конфигурация всех компонентов
        self._configure_all_components()
        
        # Построение графа через WorkflowGraphBuilder
        builder = WorkflowGraphBuilder(self)
        app = builder.build_graph()
        
        self.logger.info("Agent graph created successfully")
        return app
    
    def _configure_all_components(self) -> None:
        """
        Конфигурирует все компоненты системы через специализированные фабрики.
        """
        # Конфигурация через ComponentFactory
        self.component_factory.configure_tools()
        self.component_factory.build_system_prompt()
        self.component_factory.configure_memory_settings()
        
        # Передача инструментов в LLMFactory для привязки
        self.llm_factory.tools = self.component_factory.tools
        
        self.logger.info("All components configured successfully")
    
    # === ДЕЛЕГИРОВАНИЕ К СПЕЦИАЛИЗИРОВАННЫМ ФАБРИКАМ ===
    
    def _create_llm_instance(self, provider: str, model_name: str, temperature: float, streaming: bool, log_adapter_override: logging.LoggerAdapter = None):
        """Делегирует создание LLM к LLMFactory."""
        return self.llm_factory.create_llm_instance(provider, model_name, temperature, streaming, log_adapter_override)
    
    def _create_node_llm(self, node_type: str = "default", kb_ids: list = None):
        """Делегирует создание LLM узла к LLMFactory."""
        return self.llm_factory.create_node_llm(node_type, kb_ids)
    
    def _bind_tools_to_model(self, model):
        """Делегирует привязку инструментов к LLMFactory."""
        return self.llm_factory.bind_tools_to_model(model)
    
    def _extract_token_data(self, response: BaseMessage, call_type: str, node_model_id: str) -> TokenUsageData:
        """Делегирует извлечение данных токенов к LLMFactory."""
        return self.llm_factory.extract_token_data(response, call_type, node_model_id)
    
    def _get_tokens(self, state: AgentState, call_type: str, node_model_id: str, response: BaseMessage) -> None:
        """Получает и обрабатывает данные о токенах."""
        token_data = self._extract_token_data(response, call_type, node_model_id)
        if token_data:
            # Логика обработки токенов здесь
            self.logger.debug(f"Token usage: {token_data}")
    
    # Узлы workflow - делегирование к NodeFactory
    async def _agent_node(self, state: AgentState, config: dict):
        """Делегирует к NodeFactory."""
        return await self.node_factory.agent_node(state, config)
    
    async def _grade_docs_node(self, state: AgentState):
        """Делегирует к NodeFactory."""
        return await self.node_factory.grade_docs_node(state)
    
    async def _rewrite_node(self, state: AgentState):
        """Делегирует к NodeFactory."""
        return await self.node_factory.rewrite_node(state)
    
    async def _generate_node(self, state: AgentState):
        """Делегирует к NodeFactory."""
        return await self.node_factory.generate_node(state)
    
    # Edge маршрутизация - делегирование к ComponentFactory
    def _route_tools_edge(self, state: AgentState) -> Literal["retrieve", "safe_tools", "__end__"]:
        """Делегирует маршрутизацию к ComponentFactory."""
        return self.component_factory.tools_edge_router.route(state)
    
    async def _decide_to_generate_edge(self, state: AgentState) -> Literal["generate", "rewrite"]:
        """Делегирует решение к ComponentFactory."""
        return await self.component_factory.decision_edge_router.route(state)
    
    # Утилиты - делегирование к NodeFactory
    def _create_prompt_with_time(self, system_prompt: str):
        """Делегирует создание промпта к NodeFactory."""
        return self.node_factory.create_prompt_with_time(system_prompt)
    
    def _handle_llm_error(self, node_type: str, provider: str, error_message: str = None):
        """Делегирует обработку ошибок к NodeFactory."""
        return self.node_factory.handle_llm_error(node_type, provider, error_message)


def create_agent_app(agent_config: Dict, agent_id: str, logger: logging.LoggerAdapter) -> Any:
    """
    Создает и конфигурирует граф агента используя MainFactory.
    
    Функция точки входа для создания агентов.
    
    Args:
        agent_config: Конфигурация агента
        agent_id: Идентификатор агента
        logger: Адаптер логгера
        
    Returns:
        Готовое LangGraph приложение
        
    Raises:
        ValueError: При некорректной конфигурации
        Exception: При ошибках создания графа
    """
    logger.info(f"Attempting to create agent graph for agent_id: {agent_id} using MainFactory.")

    if not isinstance(agent_config, dict) or "config" not in agent_config:
        logger.error("Invalid agent configuration structure: 'config' key missing for agent_id: %s", agent_id)
        raise ValueError("Invalid agent configuration: 'config' key missing.")

    try:
        factory = MainFactory(agent_config, agent_id, logger)
        app = factory.create_graph()
        logger.info(f"MainFactory successfully created graph for agent_id: {agent_id}.")
        return app
    except Exception as e:
        logger.error(f"Error creating graph with MainFactory for agent_id: {agent_id}: {e}", exc_info=True)
        raise
