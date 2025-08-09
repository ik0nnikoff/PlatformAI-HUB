"""
Edges модуль для LangGraph edge handlers.
Экспорт основных edge handler'ов.
"""

from .routing import ToolsEdgeRouter, DecisionEdgeRouter

__all__ = [
    "ToolsEdgeRouter",
    "DecisionEdgeRouter"
]
