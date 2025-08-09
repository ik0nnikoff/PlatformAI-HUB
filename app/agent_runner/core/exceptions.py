"""
Специализированные исключения для AgentRunner компонентов.
Обеспечивает четкую типизацию ошибок и упрощает отладку.
"""

from typing import Any, Dict, Optional


class AgentRunnerError(Exception):
    """Базовое исключение для всех ошибок AgentRunner."""
    
    def __init__(self, message: str, error_type: str = "UnknownError", details: Optional[Dict[str, Any]] = None):
        super().__init__(message)
        self.error_type = error_type
        self.details = details or {}


class ConfigurationError(AgentRunnerError):
    """Ошибки конфигурации агента."""
    
    def __init__(self, message: str, config_path: Optional[str] = None, details: Optional[Dict[str, Any]] = None):
        super().__init__(message, "ConfigurationError", details)
        self.config_path = config_path


class LLMError(AgentRunnerError):
    """Ошибки работы с LLM."""
    
    def __init__(self, message: str, provider: Optional[str] = None, model: Optional[str] = None, details: Optional[Dict[str, Any]] = None):
        super().__init__(message, "LLMError", details)
        self.provider = provider
        self.model = model


class LLMProviderNotFoundError(LLMError):
    """LLM провайдер не найден."""
    
    def __init__(self, provider: str, available_providers: Optional[list] = None):
        message = f"LLM provider '{provider}' not found"
        if available_providers:
            message += f". Available providers: {', '.join(available_providers)}"
        super().__init__(message, provider=provider)
        self.available_providers = available_providers or []


class LLMModelNotSupportedError(LLMError):
    """Модель не поддерживается провайдером."""
    
    def __init__(self, model: str, provider: str, supported_models: Optional[list] = None):
        message = f"Model '{model}' not supported by provider '{provider}'"
        if supported_models:
            message += f". Supported models: {', '.join(supported_models)}"
        super().__init__(message, provider=provider, model=model)
        self.supported_models = supported_models or []


class LLMConnectionError(LLMError):
    """Ошибка подключения к LLM сервису."""
    
    def __init__(self, message: str, provider: str, status_code: Optional[int] = None):
        super().__init__(message, provider=provider)
        self.status_code = status_code


class NodeExecutionError(AgentRunnerError):
    """Ошибки выполнения узлов workflow."""
    
    def __init__(self, message: str, node_name: str, state: Optional[Dict[str, Any]] = None, details: Optional[Dict[str, Any]] = None):
        super().__init__(message, "NodeExecutionError", details)
        self.node_name = node_name
        self.state = state


class NodeValidationError(NodeExecutionError):
    """Ошибки валидации входных данных узла."""
    
    def __init__(self, message: str, node_name: str, validation_errors: Optional[list] = None):
        super().__init__(message, node_name)
        self.validation_errors = validation_errors or []


class ToolExecutionError(AgentRunnerError):
    """Ошибки выполнения инструментов."""
    
    def __init__(self, message: str, tool_name: str, tool_args: Optional[Dict[str, Any]] = None, details: Optional[Dict[str, Any]] = None):
        super().__init__(message, "ToolExecutionError", details)
        self.tool_name = tool_name
        self.tool_args = tool_args


class ToolNotFoundError(ToolExecutionError):
    """Инструмент не найден."""
    
    def __init__(self, tool_name: str, available_tools: Optional[list] = None):
        message = f"Tool '{tool_name}' not found"
        if available_tools:
            message += f". Available tools: {', '.join(available_tools)}"
        super().__init__(message, tool_name)
        self.available_tools = available_tools or []


class ToolConfigurationError(ToolExecutionError):
    """Ошибки конфигурации инструмента."""
    
    def __init__(self, message: str, tool_name: str, config_errors: Optional[list] = None):
        super().__init__(message, tool_name)
        self.config_errors = config_errors or []


class WorkflowError(AgentRunnerError):
    """Ошибки workflow."""
    
    def __init__(self, message: str, workflow_state: Optional[str] = None, details: Optional[Dict[str, Any]] = None):
        super().__init__(message, "WorkflowError", details)
        self.workflow_state = workflow_state


class WorkflowBuildError(WorkflowError):
    """Ошибки построения workflow."""
    
    def __init__(self, message: str, graph_config: Optional[Dict[str, Any]] = None):
        super().__init__(message, "building")
        self.graph_config = graph_config


class EdgeRoutingError(WorkflowError):
    """Ошибки маршрутизации между узлами."""
    
    def __init__(self, message: str, source_node: str, target_node: Optional[str] = None, routing_condition: Optional[str] = None):
        super().__init__(message, "routing")
        self.source_node = source_node
        self.target_node = target_node
        self.routing_condition = routing_condition


class StateValidationError(AgentRunnerError):
    """Ошибки валидации состояния."""
    
    def __init__(self, message: str, state_field: Optional[str] = None, expected_type: Optional[str] = None, actual_type: Optional[str] = None):
        super().__init__(message, "StateValidationError")
        self.state_field = state_field
        self.expected_type = expected_type
        self.actual_type = actual_type


class TokenTrackingError(AgentRunnerError):
    """Ошибки отслеживания токенов."""
    
    def __init__(self, message: str, model_id: Optional[str] = None, llm_type: Optional[str] = None, details: Optional[Dict[str, Any]] = None):
        super().__init__(message, "TokenTrackingError", details)
        self.model_id = model_id
        self.llm_type = llm_type


class VisionToolError(ToolExecutionError):
    """Ошибки vision инструментов."""
    
    def __init__(self, message: str, image_urls: Optional[list] = None, provider: Optional[str] = None):
        super().__init__(message, "vision_tool")
        self.image_urls = image_urls or []
        self.provider = provider


class VoiceToolError(ToolExecutionError):
    """Ошибки voice инструментов."""
    
    def __init__(self, message: str, audio_format: Optional[str] = None, provider: Optional[str] = None):
        super().__init__(message, "voice_tool")
        self.audio_format = audio_format
        self.provider = provider


class KnowledgeBaseError(ToolExecutionError):
    """Ошибки инструментов knowledge base."""
    
    def __init__(self, message: str, kb_ids: Optional[list] = None, collection: Optional[str] = None):
        super().__init__(message, "knowledge_base_tool")
        self.kb_ids = kb_ids or []
        self.collection = collection


class WebSearchError(ToolExecutionError):
    """Ошибки web search инструментов."""
    
    def __init__(self, message: str, search_query: Optional[str] = None, provider: Optional[str] = None):
        super().__init__(message, "web_search_tool")
        self.search_query = search_query
        self.provider = provider
