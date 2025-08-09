"""
Edge Handlers для LangGraph workflows.
Рефакторинг routing edges с понижением CCN.
"""

import logging
from typing import Literal, Dict, Any, List

from langgraph.graph import END
from langgraph.prebuilt import tools_condition

from app.agent_runner.core import NodeExecutionError
from app.agent_runner.langgraph.models import AgentState
from .base import BaseEdgeRouter


class ToolsEdgeRouter(BaseEdgeRouter):
    """Маршрутизатор для инструментов с низкой сложностью."""
    
    def route(self, state: AgentState) -> Literal["retrieve", "safe_tools", "__end__"]:
        """
        Маршрутизирует к соответствующему узлу инструментов (CCN ≤ 6).
        
        Args:
            state: Состояние агента
            
        Returns:
            Literal: Название следующего узла или END
        """
        self.logger.info("---ROUTE TOOLS---")
        
        # Проверка через LangGraph tools_condition
        next_node = tools_condition(state)
        if next_node == END:
            self.logger.info("---DECISION: NO TOOLS CALLED, END---")
            return END
        
        # Валидация tool calls
        tool_name = self._extract_tool_name(state)
        if not tool_name:
            return END
        
        # Маршрутизация по типу инструмента
        return self._route_by_tool_type(tool_name)
    
    def _extract_tool_name(self, state: AgentState) -> str:
        """Извлекает название инструмента из состояния (CCN ≤ 4)."""
        messages = state["messages"]
        if not messages:
            self.logger.warning("No messages found for tool routing.")
            return ""
        
        last_message = messages[-1]
        if not hasattr(last_message, "tool_calls") or not last_message.tool_calls:
            self.logger.warning("Routing tools, but last message has no tool calls. Ending.")
            return ""
        
        # Валидация формата tool_calls
        if not isinstance(last_message.tool_calls, list) or not last_message.tool_calls:
            self.logger.warning(f"Tool calls are present but not in expected format or empty: {last_message.tool_calls}. Ending.")
            return ""
        
        return self._validate_tool_call_format(last_message.tool_calls[0])
    
    def _validate_tool_call_format(self, first_tool_call) -> str:
        """Валидирует формат tool call (CCN ≤ 2)."""
        if not isinstance(first_tool_call, dict) or "name" not in first_tool_call:
            self.logger.warning(f"First tool call is not a dict or missing 'name': {first_tool_call}. Ending.")
            return ""
        
        tool_name = first_tool_call["name"]
        self.logger.info(f"Tool call detected: {tool_name}")
        return tool_name
    
    def _route_by_tool_type(self, tool_name: str) -> Literal["retrieve", "safe_tools", "__end__"]:
        """Маршрутизирует по типу инструмента (CCN ≤ 3)."""
        node_datastore_names = self.factory.datastore_names
        node_safe_names = self.factory.safe_tool_names
        
        if tool_name in node_datastore_names:
            self.logger.info(f"---DECISION: ROUTE TO RETRIEVE ({tool_name})---")
            return "retrieve"
        elif tool_name in node_safe_names:
            self.logger.info(f"---DECISION: ROUTE TO SAFE TOOLS ({tool_name})---")
            return "safe_tools"
        else:
            self.logger.warning(f"Tool call '{tool_name}' does not match any configured tool node. Ending.")
            return END


class DecisionEdgeRouter(BaseEdgeRouter):
    """Маршрутизатор для принятия решений с низкой сложностью."""
    
    def route_decision_to_generate(self, state: AgentState) -> Literal["generate", "rewrite"]:
        """
        Принимает решение о генерации или переписывании (CCN ≤ 2).
        
        Args:
            state: Состояние агента
            
        Returns:
            Literal: "generate" или "rewrite"
        """
        documents = state.get("documents", [])
        
        if documents:
            self.logger.info("---DECISION: DOCUMENTS FOUND, GENERATE---")
            return "generate"
        else:
            self.logger.info("---DECISION: NO DOCUMENTS, REWRITE QUERY---")
            return "rewrite"
