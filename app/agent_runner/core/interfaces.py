"""
Базовые интерфейсы и протоколы для AgentRunner компонентов.
Определяет контракты для LLM провайдеров, узлов workflow и инструментов.
"""

from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional, Protocol, runtime_checkable
from langchain_core.messages import BaseMessage


@runtime_checkable
class LLMProvider(Protocol):
    """Протокол для LLM провайдеров."""
    
    def create_instance(
        self, 
        model_name: str, 
        temperature: float = 0.1,
        streaming: bool = True,
        **kwargs
    ) -> Any:
        """Создает экземпляр LLM."""
        ...
    
    def supports_model(self, model_name: str) -> bool:
        """Проверяет поддержку модели провайдером."""
        ...


@runtime_checkable  
class WorkflowNode(Protocol):
    """Протокол для узлов workflow."""
    
    async def execute(self, state: Dict[str, Any], config: Dict[str, Any]) -> Dict[str, Any]:
        """Выполняет логику узла."""
        ...
    
    def get_node_name(self) -> str:
        """Возвращает имя узла."""
        ...


@runtime_checkable
class ToolProvider(Protocol):
    """Протокол для провайдеров инструментов."""
    
    def get_tools(self) -> List[Any]:
        """Возвращает список инструментов."""
        ...
    
    def configure_tools(self, config: Dict[str, Any]) -> None:
        """Настраивает инструменты на основе конфигурации."""
        ...


class BaseWorkflowNode(ABC):
    """Базовый абстрактный класс для узлов workflow."""
    
    def __init__(self, node_name: str, agent_id: str, logger):
        self.node_name = node_name
        self.agent_id = agent_id
        self.logger = logger
    
    @abstractmethod
    async def execute(self, state: Dict[str, Any], config: Dict[str, Any]) -> Dict[str, Any]:
        """Выполняет логику узла."""
        pass
    
    def get_node_name(self) -> str:
        """Возвращает имя узла."""
        return self.node_name
    
    def _log_execution_start(self, state: Dict[str, Any]) -> None:
        """Логирует начало выполнения узла."""
        messages_count = len(state.get("messages", []))
        self.logger.info(f"---CALL {self.node_name.upper()} NODE (Agent ID: {self.agent_id}, Messages: {messages_count})---")
    
    def _log_execution_end(self, result: Dict[str, Any]) -> None:
        """Логирует завершение выполнения узла."""
        self.logger.info(f"---{self.node_name.upper()} NODE COMPLETED---")


class BaseLLMManager(ABC):
    """Базовый абстрактный класс для менеджеров LLM."""
    
    def __init__(self, agent_id: str, logger):
        self.agent_id = agent_id
        self.logger = logger
        self.providers: Dict[str, LLMProvider] = {}
    
    @abstractmethod
    def create_llm_instance(
        self,
        provider: str,
        model_name: str,
        temperature: float,
        streaming: bool,
        **kwargs
    ) -> Optional[Any]:
        """Создает экземпляр LLM."""
        pass
    
    def register_provider(self, name: str, provider: LLMProvider) -> None:
        """Регистрирует LLM провайдера."""
        self.providers[name] = provider
        self.logger.debug(f"LLM provider '{name}' registered")
    
    def get_provider(self, name: str) -> Optional[LLMProvider]:
        """Получает LLM провайдера по имени."""
        return self.providers.get(name)


class BaseTokenTracker(ABC):
    """Базовый абстрактный класс для отслеживания токенов."""
    
    def __init__(self, agent_id: str, logger):
        self.agent_id = agent_id
        self.logger = logger
    
    @abstractmethod
    async def extract_and_save_tokens(
        self,
        state: Dict[str, Any],
        llm_type: str,
        model_id: str,
        response: BaseMessage,
        redis_client = None
    ) -> None:
        """Извлекает и сохраняет информацию о токенах."""
        pass


@runtime_checkable
class ConfigProvider(Protocol):
    """Протокол для провайдеров конфигурации."""
    
    def get_model_config(self) -> Dict[str, Any]:
        """Возвращает конфигурацию модели."""
        ...
    
    def get_tools_settings(self) -> List[Dict[str, Any]]:
        """Возвращает настройки инструментов."""
        ...
    
    def get_agent_settings(self) -> Dict[str, Any]:
        """Возвращает настройки агента."""
        ...


@runtime_checkable
class StateManager(Protocol):
    """Протокол для управления состоянием workflow."""
    
    def update_state(self, state: Dict[str, Any], updates: Dict[str, Any]) -> Dict[str, Any]:
        """Обновляет состояние."""
        ...
    
    def validate_state(self, state: Dict[str, Any]) -> bool:
        """Валидирует состояние."""
        ...
