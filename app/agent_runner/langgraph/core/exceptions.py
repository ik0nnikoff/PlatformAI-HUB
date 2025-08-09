"""
LangGraph-специфичные исключения.
Расширяет базовые исключения для работы с LangGraph компонентами.
"""

from typing import Any, Dict, List, Optional
from app.agent_runner.core.exceptions import (
    NodeExecutionError, 
    WorkflowError, 
    ToolExecutionError,
    LLMError
)


class LangGraphNodeError(NodeExecutionError):
    """Ошибки выполнения LangGraph узлов."""
    
    def __init__(self, message: str, node_name: str, node_type: str, state: Optional[Dict[str, Any]] = None):
        super().__init__(message, node_name, state)
        self.node_type = node_type


class AgentNodeError(LangGraphNodeError):
    """Ошибки узла агента."""
    
    def __init__(self, message: str, tool_calls: Optional[List[Any]] = None, state: Optional[Dict[str, Any]] = None):
        super().__init__(message, "agent_node", "agent", state)
        self.tool_calls = tool_calls or []


class GradingNodeError(LangGraphNodeError):
    """Ошибки узла оценки документов."""
    
    def __init__(self, message: str, documents: Optional[List[Any]] = None, question: Optional[str] = None):
        super().__init__(message, "grading_node", "grading")
        self.documents = documents or []
        self.question = question


class GenerationNodeError(LangGraphNodeError):
    """Ошибки узла генерации."""
    
    def __init__(self, message: str, generation_context: Optional[Dict[str, Any]] = None):
        super().__init__(message, "generation_node", "generation")
        self.generation_context = generation_context or {}


class RewriteNodeError(LangGraphNodeError):
    """Ошибки узла переписывания."""
    
    def __init__(self, message: str, original_query: Optional[str] = None, rewrite_attempts: int = 0):
        super().__init__(message, "rewrite_node", "rewrite")
        self.original_query = original_query
        self.rewrite_attempts = rewrite_attempts


class GraphBuildError(WorkflowError):
    """Ошибки построения LangGraph."""
    
    def __init__(self, message: str, build_step: str, graph_config: Optional[Dict[str, Any]] = None):
        super().__init__(message, "building")
        self.build_step = build_step
        self.graph_config = graph_config or {}


class NodeConfigurationError(LangGraphNodeError):
    """Ошибки конфигурации узлов."""
    
    def __init__(self, message: str, node_name: str, node_type: str, config_issues: Optional[List[str]] = None):
        super().__init__(message, node_name, node_type)
        self.config_issues = config_issues or []


class EdgeConfigurationError(WorkflowError):
    """Ошибки конфигурации связей между узлами."""
    
    def __init__(self, message: str, edge_name: str, source_node: str, target_node: str):
        super().__init__(message, "edge_configuration")
        self.edge_name = edge_name
        self.source_node = source_node
        self.target_node = target_node


class ToolCallRecoveryError(ToolExecutionError):
    """Ошибки восстановления вызовов инструментов."""
    
    def __init__(self, message: str, invalid_calls: List[Any], recovery_attempts: int = 0):
        super().__init__(message, "tool_call_recovery")
        self.invalid_calls = invalid_calls
        self.recovery_attempts = recovery_attempts


class PromptBuildError(LangGraphNodeError):
    """Ошибки построения промптов."""
    
    def __init__(self, message: str, node_name: str, prompt_type: str, template_issues: Optional[List[str]] = None):
        super().__init__(message, node_name, prompt_type)
        self.template_issues = template_issues or []


class StateTransitionError(WorkflowError):
    """Ошибки переходов между состояниями."""
    
    def __init__(self, message: str, from_state: str, to_state: str, transition_data: Optional[Dict[str, Any]] = None):
        super().__init__(message, "state_transition")
        self.from_state = from_state
        self.to_state = to_state
        self.transition_data = transition_data or {}


class MessageProcessingError(LangGraphNodeError):
    """Ошибки обработки сообщений."""
    
    def __init__(self, message: str, node_name: str, message_type: str, processing_step: str):
        super().__init__(message, node_name, "message_processing")
        self.message_type = message_type
        self.processing_step = processing_step


class RoutingDecisionError(WorkflowError):
    """Ошибки принятия решений о маршрутизации."""
    
    def __init__(self, message: str, routing_condition: str, available_routes: Optional[List[str]] = None):
        super().__init__(message, "routing_decision")
        self.routing_condition = routing_condition
        self.available_routes = available_routes or []


class LLMBindingError(LLMError):
    """Ошибки привязки LLM к узлам."""
    
    def __init__(self, message: str, node_name: str, model_name: str, binding_step: str):
        super().__init__(message, model=model_name)
        self.node_name = node_name
        self.binding_step = binding_step


class ToolBindingError(ToolExecutionError):
    """Ошибки привязки инструментов к модели."""
    
    def __init__(self, message: str, tool_names: List[str], model_name: str):
        super().__init__(message, "tool_binding")
        self.tool_names = tool_names
        self.model_name = model_name


class GraphExecutionError(WorkflowError):
    """Ошибки выполнения графа."""
    
    def __init__(self, message: str, execution_step: str, node_path: Optional[List[str]] = None):
        super().__init__(message, "graph_execution")
        self.execution_step = execution_step
        self.node_path = node_path or []


class ConditionalEdgeError(EdgeConfigurationError):
    """Ошибки условных связей."""
    
    def __init__(self, message: str, condition_name: str, condition_function: str, evaluation_error: Optional[str] = None):
        super().__init__(message, condition_name, "conditional_source", "multiple_targets")
        self.condition_function = condition_function
        self.evaluation_error = evaluation_error


class StreamProcessingError(WorkflowError):
    """Ошибки обработки потока выполнения."""
    
    def __init__(self, message: str, stream_step: str, output_data: Optional[Dict[str, Any]] = None):
        super().__init__(message, "stream_processing")
        self.stream_step = stream_step
        self.output_data = output_data or {}
