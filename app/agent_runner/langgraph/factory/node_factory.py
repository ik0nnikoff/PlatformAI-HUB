"""
NodeFactory - Специализированная фабрика для создания узлов workflow.

Эта фабрика отвечает за:
- Создание различных типов узлов (agent, grading, generate, rewrite)
- Конфигурацию узлов с соответствующими параметрами
- Управление делегированием к специализированным handlers
- Создание промптов и шаблонов для узлов

Фаза 7 SOLID рефакторинга - извлечено из factory.py
"""

import logging
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List
from langchain_core.messages import BaseMessage, HumanMessage, AIMessage
from langchain_core.prompts import PromptTemplate, ChatPromptTemplate, MessagesPlaceholder

from app.agent_runner.langgraph.models import AgentState
from app.agent_runner.langgraph.workflow import (
    AgentNodeHandler, 
    GradingNodeHandler, 
    GenerateNodeHandler, 
    RewriteNodeHandler
)
from app.agent_runner.core.config_mixin import AgentConfigMixin


class NodeFactory(AgentConfigMixin):
    """
    Специализированная фабрика для создания узлов workflow.
    
    Ответственности:
    - Создание и конфигурация узлов workflow
    - Управление handlers для узлов
    - Создание промптов и шаблонов
    - Обработка ошибок узлов
    """
    
    def __init__(self, agent_config: Dict, agent_id: str, logger: logging.LoggerAdapter, parent_factory: Any):
        """
        Инициализирует NodeFactory.
        
        Args:
            agent_config: Конфигурация агента
            agent_id: Идентификатор агента
            logger: Адаптер логгера
            parent_factory: Ссылка на родительскую фабрику для доступа к компонентам
        """
        super().__init__()
        self.agent_config = agent_config
        self.agent_id = agent_id
        self.logger = logger
        self.parent_factory = parent_factory
        
        # Node handlers - делегирует к существующим handlers
        self.agent_node_handler = AgentNodeHandler(parent_factory, logger)
        self.grading_node_handler = GradingNodeHandler(parent_factory, logger)
        self.generate_node_handler = GenerateNodeHandler(parent_factory, logger)
        self.rewrite_node_handler = RewriteNodeHandler(parent_factory, logger)
    
    async def agent_node(self, state: AgentState, config: dict):
        """
        Узел агента - делегирует к AgentNodeHandler.
        
        Args:
            state: Состояние агента
            config: Конфигурация узла
            
        Returns:
            Обновленное состояние
        """
        return await self.agent_node_handler.handle(state, config)
    
    async def grade_docs_node(self, state: AgentState) -> Dict[str, Any]:
        """
        Узел оценки документов - делегирует к GradingNodeHandler.
        
        Args:
            state: Состояние агента
            
        Returns:
            Обновленное состояние с результатом оценки
        """
        return await self.grading_node_handler.handle(state)
    
    async def rewrite_node(self, state: AgentState) -> Dict[str, Any]:
        """
        Узел переписывания запроса - делегирует к RewriteNodeHandler.
        
        Args:
            state: Состояние агента
            
        Returns:
            Обновленное состояние с переписанным запросом
        """
        return await self.rewrite_node_handler.handle(state)
    
    async def generate_node(self, state: AgentState) -> Dict[str, Any]:
        """
        Узел генерации ответа - делегирует к GenerateNodeHandler.
        
        Args:
            state: Состояние агента
            
        Returns:
            Обновленное состояние с сгенерированным ответом
        """
        return await self.generate_node_handler.handle(state)
    
    def create_prompt_with_time(self, system_prompt: str) -> ChatPromptTemplate:
        """
        Создает промпт с текущим временем для контекста.
        
        Args:
            system_prompt: Системный промпт
            
        Returns:
            Настроенный ChatPromptTemplate
        """
        current_time = self._get_moscow_time()
        
        enhanced_system_prompt = f"""
{system_prompt}

Текущее время: {current_time}
Используй эту информацию для контекста, если это релевантно для ответа.
"""
        
        return ChatPromptTemplate.from_messages([
            ("system", enhanced_system_prompt),
            MessagesPlaceholder(variable_name="messages"),
        ])
    
    def create_rag_template(self) -> PromptTemplate:
        """
        Создает шаблон для RAG генерации.
        
        Returns:
            PromptTemplate для RAG
        """
        template = """
Ты помощник, который отвечает на вопросы на основе предоставленного контекста.

Контекст: {context}

Вопрос: {question}

Пожалуйста, предоставь подробный и точный ответ, основанный исключительно на предоставленном контексте.
Если в контексте нет достаточной информации для ответа, честно об этом скажи.
"""
        return PromptTemplate(
            input_variables=["context", "question"],
            template=template
        )
    
    def create_grading_template(self) -> PromptTemplate:
        """
        Создает шаблон для оценки документов.
        
        Returns:
            PromptTemplate для оценки релевантности документов
        """
        template = """
Ты оценщик, который определяет релевантность документов для вопроса пользователя.

Документы: {documents}

Вопрос пользователя: {question}

Оцени, являются ли предоставленные документы релевантными для ответа на вопрос пользователя.

Ответь только одним словом:
- "yes" если документы релевантны
- "no" если документы не релевантны

Оценка:"""
        
        return PromptTemplate(
            input_variables=["documents", "question"],
            template=template
        )
    
    def create_rewrite_prompt(self, original_question: str, messages: List[BaseMessage]) -> HumanMessage:
        """
        Создает промпт для переписывания вопроса.
        
        Args:
            original_question: Оригинальный вопрос
            messages: История сообщений
            
        Returns:
            HumanMessage с промптом для переписывания
        """
        rewrite_content = f"""
Оригинальный вопрос: {original_question}

Переформулируй этот вопрос, чтобы он был более четким и конкретным.
Учти контекст предыдущих сообщений, если он есть.

Переформулированный вопрос:"""
        
        return HumanMessage(content=rewrite_content)
    
    def handle_llm_error(self, node_type: str, provider: str, error_message: str = None) -> AIMessage:
        """
        Обрабатывает ошибки LLM в узлах.
        
        Args:
            node_type: Тип узла где произошла ошибка
            provider: Провайдер LLM
            error_message: Сообщение об ошибке
            
        Returns:
            AIMessage с сообщением об ошибке
        """
        if error_message:
            content = f"Извините, произошла ошибка при обработке запроса в узле {node_type} с провайдером {provider}: {error_message}"
        else:
            content = f"Извините, произошла ошибка при обработке запроса в узле {node_type} с провайдером {provider}."
        
        self.logger.error(f"{node_type} node error: {content}")
        return AIMessage(content=content)
    
    def _get_moscow_time(self) -> str:
        """
        Получает текущее время в московском часовом поясе.
        
        Returns:
            Строка с текущим временем
        """
        moscow_tz = timezone(timedelta(hours=3))
        current_time = datetime.now(moscow_tz)
        return current_time.strftime("%Y-%m-%d %H:%M:%S %Z")
