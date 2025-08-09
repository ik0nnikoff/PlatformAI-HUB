"""
State Manager для LangGraph workflows.
Управление состоянием агентов и валидация.
"""

import logging
from typing import Dict, Any, List, Optional

from app.agent_runner.langgraph.models import AgentState


class WorkflowStateManager:
    """Менеджер состояния workflow с валидацией и утилитами."""
    
    def __init__(self, logger: logging.LoggerAdapter):
        """
        Инициализирует state manager.
        
        Args:
            logger: Логгер для state operations
        """
        self.logger = logger
    
    def validate_state(self, state: AgentState, required_keys: List[str]) -> bool:
        """
        Валидирует состояние агента (CCN ≤ 3).
        
        Args:
            state: Состояние для проверки
            required_keys: Список обязательных ключей
            
        Returns:
            bool: True если состояние валидно
        """
        if not isinstance(state, dict):
            self.logger.error(f"State is not a dict: {type(state)}")
            return False
        
        for key in required_keys:
            if key not in state:
                self.logger.warning(f"Required state key '{key}' not found")
                return False
        
        return True
    
    def extract_messages(self, state: AgentState) -> List[Any]:
        """
        Безопасно извлекает сообщения из состояния (CCN ≤ 2).
        
        Args:
            state: Состояние агента
            
        Returns:
            List[Any]: Список сообщений или пустой список
        """
        messages = state.get("messages", [])
        if not isinstance(messages, list):
            self.logger.warning(f"Messages is not a list: {type(messages)}")
            return []
        
        return messages
    
    def extract_user_data(self, state: AgentState) -> Dict[str, Any]:
        """
        Безопасно извлекает данные пользователя (CCN ≤ 2).
        
        Args:
            state: Состояние агента
            
        Returns:
            Dict[str, Any]: Данные пользователя или пустой словарь
        """
        user_data = state.get("user_data", {})
        if not isinstance(user_data, dict):
            self.logger.warning(f"User data is not a dict: {type(user_data)}")
            return {}
        
        return user_data
    
    def extract_documents(self, state: AgentState) -> List[Any]:
        """
        Безопасно извлекает документы из состояния (CCN ≤ 2).
        
        Args:
            state: Состояние агента
            
        Returns:
            List[Any]: Список документов или пустой список
        """
        documents = state.get("documents", [])
        if not isinstance(documents, list):
            self.logger.warning(f"Documents is not a list: {type(documents)}")
            return []
        
        return documents
    
    def update_state_safely(self, state: AgentState, updates: Dict[str, Any]) -> AgentState:
        """
        Безопасно обновляет состояние (CCN ≤ 3).
        
        Args:
            state: Исходное состояние
            updates: Обновления для применения
            
        Returns:
            AgentState: Обновлённое состояние
        """
        if not isinstance(updates, dict):
            self.logger.error(f"Updates is not a dict: {type(updates)}")
            return state
        
        try:
            updated_state = state.copy()
            updated_state.update(updates)
            self.logger.debug(f"State updated with keys: {list(updates.keys())}")
            return updated_state
        except Exception as e:
            self.logger.error(f"Failed to update state: {e}")
            return state
    
    def get_chat_context(self, state: AgentState) -> Dict[str, str]:
        """
        Извлекает контекст чата из состояния (CCN ≤ 2).
        
        Args:
            state: Состояние агента
            
        Returns:
            Dict[str, str]: Контекст чата
        """
        return {
            "chat_id": state.get("chat_id", ""),
            "platform_user_id": state.get("platform_user_id", ""),
            "channel": state.get("channel", ""),
        }
    
    def validate_tool_call_format(self, tool_call: Any) -> bool:
        """
        Валидирует формат tool call (CCN ≤ 3).
        
        Args:
            tool_call: Tool call для проверки
            
        Returns:
            bool: True если формат корректный
        """
        if not isinstance(tool_call, dict):
            self.logger.warning(f"Tool call is not a dict: {type(tool_call)}")
            return False
        
        if "name" not in tool_call:
            self.logger.warning("Tool call missing 'name' field")
            return False
        
        return True
    
    def extract_last_message(self, state: AgentState) -> Optional[Any]:
        """
        Извлекает последнее сообщение из состояния (CCN ≤ 2).
        
        Args:
            state: Состояние агента
            
        Returns:
            Optional[Any]: Последнее сообщение или None
        """
        messages = self.extract_messages(state)
        if not messages:
            self.logger.warning("No messages found in state")
            return None
        
        return messages[-1]
    
    def has_tool_calls(self, state: AgentState) -> bool:
        """
        Проверяет наличие tool calls в последнем сообщении (CCN ≤ 3).
        
        Args:
            state: Состояние агента
            
        Returns:
            bool: True если есть tool calls
        """
        last_message = self.extract_last_message(state)
        if not last_message:
            return False
        
        if not hasattr(last_message, "tool_calls"):
            return False
        
        tool_calls = getattr(last_message, "tool_calls", [])
        return bool(tool_calls and len(tool_calls) > 0)
