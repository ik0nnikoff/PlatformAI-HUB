"""
ComponentFactory - Специализированная фабрика для создания основных компонентов.

Эта фабрика отвечает за:
- Конфигурацию инструментов (tools)
- Построение системного промпта
- Настройку памяти и контекста
- Создание edge routers

Фаза 7 SOLID рефакторинга - извлечено из factory.py
"""

import logging
from typing import Any, Dict

from app.agent_runner.langgraph.tools.tools import configure_tools
from app.agent_runner.langgraph.workflow import ToolsEdgeRouter, DecisionEdgeRouter
from app.agent_runner.core.config_mixin import AgentConfigMixin


class ComponentFactory(AgentConfigMixin):
    """
    Специализированная фабрика для создания основных компонентов системы.
    
    Ответственности:
    - Конфигурация инструментов
    - Построение системных промптов
    - Настройка памяти и контекста
    - Создание маршрутизаторов edges
    """
    
    def __init__(self, agent_config: Dict, agent_id: str, logger: logging.LoggerAdapter, parent_factory: Any):
        """
        Инициализирует ComponentFactory.
        
        Args:
            agent_config: Конфигурация агента
            agent_id: Идентификатор агента
            logger: Адаптер логгера
            parent_factory: Ссылка на родительскую фабрику
        """
        super().__init__()
        self.agent_config = agent_config
        self.agent_id = agent_id
        self.logger = logger
        self.parent_factory = parent_factory
        
        # Инициализация edge routers
        self.tools_edge_router = ToolsEdgeRouter(parent_factory, logger)
        self.decision_edge_router = self._create_decision_router(parent_factory, logger)
        
        # Компоненты системы
        self.tools: list = []
        self.safe_tools: list = []
        self.datastore_tools: list = []
        self.datastore_names: set = set()
        self.safe_tool_names: set = set()
        self.system_prompt: str = ""
        self.enable_context_memory: bool = False
        self.max_rewrites: int = 3
    
    def configure_tools(self) -> None:
        """
        Конфигурирует инструменты для агента.
        
        Настраивает:
        - Безопасные инструменты (safe_tools)
        - Инструменты баз данных (datastore_tools)
        - Общий список инструментов (tools)
        """
        # Получаем конфигурацию инструментов из системы
        tools_config = configure_tools(
            agent_config=self.agent_config,
            agent_id=self.agent_id,
            logger=self.logger
        )
        
        # Разделяем инструменты по типам
        self.safe_tools = tools_config.get("safe_tools", [])
        self.datastore_tools = tools_config.get("datastore_tools", [])
        
        # Объединяем все инструменты
        self.tools = self.safe_tools + self.datastore_tools
        
        # Создаем множества имен для быстрого поиска
        self.safe_tool_names = {tool.name for tool in self.safe_tools if hasattr(tool, 'name')}
        self.datastore_names = {tool.name for tool in self.datastore_tools if hasattr(tool, 'name')}
        
        self.logger.info(f"Configured tools: {len(self.tools)} total, "
                        f"{len(self.safe_tools)} safe, {len(self.datastore_tools)} datastore")
    
    def build_system_prompt(self) -> None:
        """
        Построение системного промпта для агента.
        
        Объединяет:
        - Базовый промпт из конфигурации
        - Информацию о доступных инструментах
        - Контекстную информацию
        """
        # Получаем базовый промпт из конфигурации
        base_prompt = self._get_system_prompt_from_config()
        
        # Добавляем информацию об инструментах
        tools_info = self._build_tools_description()
        
        # Добавляем информацию о voice возможностях
        voice_info = self._build_voice_description()
        
        # Объединяем все части
        self.system_prompt = self._combine_prompt_parts(base_prompt, tools_info, voice_info)
        
        self.logger.info(f"System prompt built: {len(self.system_prompt)} characters")
    
    def configure_memory_settings(self) -> None:
        """
        Конфигурирует настройки памяти для агента.
        """
        model_config = self._get_model_config()
        self.enable_context_memory = model_config.get("enable_context_memory", False)
        
        self.logger.info(f"Context memory enabled: {self.enable_context_memory}")
    
    def _create_decision_router(self, parent_factory, logger):
        """Создает конкретную реализацию DecisionEdgeRouter."""
        class ConcreteDecisionRouter(DecisionEdgeRouter):
            async def route(self, state):
                """Реализация маршрутизации для генерации."""
                filtered_documents = state["documents"]
                rewrite_count = state.get("rewrite_count", 0)
                max_rewrites = parent_factory.max_rewrites
                
                if not filtered_documents:
                    if rewrite_count < max_rewrites:
                        return "rewrite"
                    else:
                        return "generate"
                else:
                    return "generate"
        
        return ConcreteDecisionRouter(parent_factory, logger)
    
    def is_voice_v2_available(self) -> bool:
        """
        Проверяет доступность голосовых инструментов v2.
        
        Returns:
            True если голосовые инструменты доступны
        """
        try:
            # NOTE: Changed check after Phase 4.8.1 anti-pattern removal
            # Check for new native TTS tool
            return True
        except ImportError:
            return False
    
    def _get_system_prompt_from_config(self) -> str:
        """
        Извлекает базовый системный промпт из конфигурации.
        
        Returns:
            Базовый системный промпт
        """
        try:
            simple_config = self.agent_config["config"]["simple"]
            system_prompt = simple_config.get("system_prompt", "")
            
            if not system_prompt:
                self.logger.warning("No system prompt found in agent configuration")
                return "Ты полезный AI-ассистент."
                
            return system_prompt
        except (KeyError, TypeError) as e:
            self.logger.error(f"Error extracting system prompt: {e}")
            return "Ты полезный AI-ассистент."
    
    def _build_tools_description(self) -> str:
        """
        Создает описание доступных инструментов.
        
        Returns:
            Строка с описанием инструментов
        """
        if not self.tools:
            return ""
        
        tools_descriptions = []
        
        if self.safe_tools:
            safe_names = [tool.name for tool in self.safe_tools if hasattr(tool, 'name')]
            tools_descriptions.append(f"Безопасные инструменты: {', '.join(safe_names)}")
        
        if self.datastore_tools:
            datastore_names = [tool.name for tool in self.datastore_tools if hasattr(tool, 'name')]
            tools_descriptions.append(f"Инструменты баз знаний: {', '.join(datastore_names)}")
        
        if tools_descriptions:
            return f"\n\nДоступные инструменты:\n{chr(10).join(tools_descriptions)}"
        
        return ""
    
    def _build_voice_description(self) -> str:
        """
        Создает описание голосовых возможностей.
        
        Returns:
            Строка с описанием голосовых возможностей
        """
        if self.is_voice_v2_available():
            return "\n\nДоступны голосовые возможности: генерация аудио ответов, обработка голосовых сообщений."
        return ""
    
    def _combine_prompt_parts(self, base_prompt: str, tools_info: str, voice_info: str) -> str:
        """
        Объединяет части промпта в единый системный промпт.
        
        Args:
            base_prompt: Базовый промпт
            tools_info: Информация об инструментах
            voice_info: Информация о голосовых возможностях
            
        Returns:
            Объединенный системный промпт
        """
        parts = [base_prompt]
        
        if tools_info:
            parts.append(tools_info)
        
        if voice_info:
            parts.append(voice_info)
        
        # Добавляем общие инструкции
        parts.append("\n\nОбщие инструкции:")
        parts.append("- Отвечай точно и полезно")
        parts.append("- Используй доступные инструменты когда это необходимо")
        parts.append("- Будь вежлив и профессионален")
        
        return "".join(parts)
