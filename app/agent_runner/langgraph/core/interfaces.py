"""
LangGraph-специфичные интерфейсы и протоколы.
Расширяет базовые интерфейсы для работы с LangGraph компонентами.
"""

from typing import Any, Dict, List, Optional, Protocol, runtime_checkable
from langchain_core.messages import BaseMessage
from langgraph.graph import StateGraph

from app.agent_runner.core.interfaces import WorkflowNode, ConfigProvider


@runtime_checkable
class LangGraphNode(Protocol):
    """Протокол для LangGraph узлов."""
    
    async def execute(self, state: Dict[str, Any], config: Dict[str, Any]) -> Dict[str, Any]:
        """Выполняет логику узла."""
        ...
    
    def get_node_name(self) -> str:
        """Возвращает имя узла."""
        ...
    
    def get_node_config(self, node_type: str) -> Dict[str, Any]:
        """Получает конфигурацию для конкретного типа узла."""
        ...
    
    def bind_tools_to_model(self, model: Any, tools: List[Any]) -> Any:
        """Привязывает инструменты к модели."""
        ...


@runtime_checkable  
class LangGraphFactory(Protocol):
    """Протокол для фабрик LangGraph компонентов."""
    
    def create_graph(self) -> StateGraph:
        """Создает граф workflow."""
        ...
    
    def configure_nodes(self, graph: StateGraph) -> None:
        """Настраивает узлы в графе."""
        ...
    
    def configure_edges(self, graph: StateGraph) -> None:
        """Настраивает связи между узлами."""
        ...


@runtime_checkable
class EdgeRouter(Protocol):
    """Протокол для маршрутизации между узлами."""
    
    def should_continue(self, state: Dict[str, Any]) -> str:
        """Определяет следующий узел для выполнения."""
        ...
    
    def route_tools(self, state: Dict[str, Any]) -> str:
        """Маршрутизирует на основе типа инструментов."""
        ...


@runtime_checkable
class GraphBuilder(Protocol):
    """Протокол для построения графов."""
    
    def build_workflow_graph(self, config: Dict[str, Any]) -> StateGraph:
        """Строит полный граф workflow."""
        ...
    
    def add_conditional_edges(self, graph: StateGraph, config: Dict[str, Any]) -> None:
        """Добавляет условные связи в граф."""
        ...


@runtime_checkable
class NodeFactory(Protocol):
    """Протокол для фабрики узлов."""
    
    def create_agent_node(self) -> LangGraphNode:
        """Создает узел агента."""
        ...
    
    def create_grading_node(self) -> LangGraphNode:
        """Создает узел оценки документов."""
        ...
    
    def create_generation_node(self) -> LangGraphNode:
        """Создает узел генерации ответа."""
        ...
    
    def create_rewrite_node(self) -> LangGraphNode:
        """Создает узел переписывания запроса."""
        ...


@runtime_checkable
class LLMNodeMixin(Protocol):
    """Протокол для узлов, использующих LLM."""
    
    def create_node_llm(self, node_type: str) -> Any:
        """Создает LLM для конкретного узла."""
        ...
    
    def get_node_config(self, node_type: str) -> Dict[str, Any]:
        """Получает конфигурацию узла."""
        ...
    
    def handle_llm_error(self, node_type: str, provider: str) -> BaseMessage:
        """Обрабатывает ошибки LLM."""
        ...


@runtime_checkable
class PromptBuilder(Protocol):
    """Протокол для построения промптов."""
    
    def create_prompt_with_time(self, system_prompt: str) -> Any:
        """Создает промпт с текущим временем."""
        ...
    
    def create_node_prompt(self, node_type: str, system_prompt: str) -> Any:
        """Создает промпт для конкретного узла."""
        ...


@runtime_checkable
class ToolManager(Protocol):
    """Протокол для управления инструментами в LangGraph."""
    
    def get_safe_tools(self) -> List[Any]:
        """Возвращает безопасные инструменты."""
        ...
    
    def get_datastore_tools(self) -> List[Any]:
        """Возвращает инструменты для работы с данными."""
        ...
    
    def bind_tools_to_model(self, model: Any) -> Any:
        """Привязывает инструменты к модели."""
        ...


@runtime_checkable
class StateUpdater(Protocol):
    """Протокол для обновления состояния."""
    
    def update_messages(self, state: Dict[str, Any], new_message: BaseMessage) -> Dict[str, Any]:
        """Обновляет сообщения в состоянии."""
        ...
    
    def update_context(self, state: Dict[str, Any], context_updates: Dict[str, Any]) -> Dict[str, Any]:
        """Обновляет контекст в состоянии."""
        ...


@runtime_checkable
class ResponseProcessor(Protocol):
    """Протокол для обработки ответов."""
    
    def extract_agent_response(self, response_data: Dict[str, Any]) -> tuple[str, Optional[BaseMessage]]:
        """Извлекает ответ агента."""
        ...
    
    def format_error_response(self, error: Exception) -> BaseMessage:
        """Форматирует ответ об ошибке."""
        ...


@runtime_checkable
class ToolCallProcessor(Protocol):
    """Протокол для обработки вызовов инструментов."""
    
    def process_invalid_tool_calls(self, response: BaseMessage) -> List[Any]:
        """Обрабатывает некорректные вызовы инструментов."""
        ...
    
    def recover_tool_calls(self, invalid_calls: List[Any]) -> List[Any]:
        """Восстанавливает корректные вызовы инструментов."""
        ...


@runtime_checkable
class DocumentGrader(Protocol):
    """Протокол для оценки документов."""
    
    def grade_documents(self, documents: List[Any], question: str) -> str:
        """Оценивает релевантность документов."""
        ...
    
    def extract_kb_context(self, state: Dict[str, Any]) -> Dict[str, Any]:
        """Извлекает контекст knowledge base."""
        ...


@runtime_checkable
class QueryRewriter(Protocol):
    """Протокол для переписывания запросов."""
    
    def rewrite_query(self, question: str, context: Dict[str, Any]) -> str:
        """Переписывает запрос для улучшения поиска."""
        ...
    
    def should_rewrite(self, state: Dict[str, Any]) -> bool:
        """Определяет, нужно ли переписывать запрос."""
        ...
