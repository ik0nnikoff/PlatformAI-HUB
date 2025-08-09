"""
Workflow Builder для LangGraph - построение графа агентов.
Рефакторинг create_graph с понижением CCN.
"""

import logging
from typing import Any, Dict, Optional

from langgraph.graph import StateGraph, START, END
from langgraph.prebuilt import ToolNode, tools_condition
from langgraph.checkpoint.memory import MemorySaver

from app.agent_runner.langgraph.models import AgentState
from .edges.routing import ToolsEdgeRouter, DecisionEdgeRouter


class WorkflowGraphBuilder:
    """Построитель LangGraph workflow с низкой сложностью."""
    
    def __init__(self, factory):
        """
        Инициализирует график builder.
        
        Args:
            factory: Factory для доступа к компонентам и конфигурации
        """
        self.factory = factory
        self.logger = factory.logger
        self.workflow = None
        self.tools_router = None
        self.decision_router = None
    
    def build_graph(self) -> Any:
        """
        Создаёт и компилирует LangGraph workflow (CCN ≤ 6).
        
        Returns:
            Any: Скомпилированный граф приложения
        """
        self.logger.info("Building agent graph with WorkflowGraphBuilder...")
        
        # Инициализация
        self._initialize_components()
        
        # Построение
        self._create_workflow()
        self._add_all_nodes()
        self._add_all_edges()
        
        # Компиляция
        return self._compile_workflow()
    
    def _initialize_components(self) -> None:
        """Инициализирует компоненты builder'а (CCN ≤ 2)."""
        # Инициализация edge routers
        self.tools_router = ToolsEdgeRouter(self.factory, self.logger)
        self.decision_router = DecisionEdgeRouter(self.factory, self.logger)
        
        # Инициализация StateGraph
        self.workflow = StateGraph(AgentState)
        self.logger.info("WorkflowGraphBuilder components initialized")
    
    def _add_all_nodes(self) -> None:
        """Добавляет все узлы в workflow (CCN ≤ 5)."""
        # Базовый agent узел
        self.workflow.add_node("agent", self.factory._agent_node)
        self.logger.info("Added node: agent")
        
        # Voice/Tools узлы
        self._add_tools_nodes()
        
        # RAG узлы
        self._add_rag_nodes()
        
        # Safe tools узлы  
        self._add_safe_tools_nodes()
    
    def _add_tools_nodes(self) -> None:
        """Добавляет voice/tools узлы (CCN ≤ 3)."""
        if self.factory._is_voice_v2_available() and self.factory.tools:
            tools_node = ToolNode(self.factory.tools, name="tools_node")
            self.workflow.add_node("tools", tools_node)
            self.logger.info("Added LangGraph native tools node (includes generate_voice_response)")
        else:
            self.logger.info("Voice v2 tools not available or no tools configured")
    
    def _add_rag_nodes(self) -> None:
        """Добавляет RAG узлы (CCN ≤ 4)."""
        if not self.factory.datastore_names:
            self.logger.info("No datastore tools configured. RAG nodes skipped.")
            return
        
        if not self.factory.datastore_tools:
            self.logger.warning("Datastore tool names configured, but no datastore tools were created. Skipping RAG nodes.")
            return
        
        # Добавляем все RAG узлы
        retrieve_node = ToolNode(self.factory.datastore_tools, name="retrieve_node")
        self.workflow.add_node("retrieve", retrieve_node)
        self.workflow.add_node("grade_documents", self.factory._grade_docs_node)
        self.workflow.add_node("rewrite", self.factory._rewrite_node)
        self.workflow.add_node("generate", self.factory._generate_node)
        self.logger.info("Added RAG nodes: retrieve, grade_documents, rewrite, generate")
    
    def _add_safe_tools_nodes(self) -> None:
        """Добавляет safe tools узлы (CCN ≤ 3)."""
        if not self.factory.safe_tool_names:
            self.logger.info("No safe tools configured. Safe_tools node skipped.")
            return
        
        if not self.factory.safe_tools:
            self.logger.warning("Safe tool names configured, but no safe tools were created. Skipping safe_tools node.")
            return
        
        safe_tools_node = ToolNode(self.factory.safe_tools, name="safe_tools_node")
        self.workflow.add_node("safe_tools", safe_tools_node)
        self.logger.info("Added node: safe_tools")
    
    def _add_all_edges(self) -> None:
        """Добавляет все edges в workflow (CCN ≤ 4)."""
        # Стартовый edge
        self.workflow.add_edge(START, "agent")
        
        # Простые edges
        self._add_simple_edges()
        
        # Conditional edges
        self._add_conditional_edges()
    
    def _add_simple_edges(self) -> None:
        """Добавляет простые edges (CCN ≤ 4)."""
        # Safe tools edges
        if "safe_tools" in self.workflow.nodes:
            self.workflow.add_edge("safe_tools", "agent")
            self.logger.info("Added edge: safe_tools -> agent")
        
        # Tools edges
        if "tools" in self.workflow.nodes:
            self.workflow.add_edge("tools", "agent")
            self.logger.info("Added edge: tools -> agent (LangGraph native routing)")
        
        # RAG edges
        self._add_rag_edges()
    
    def _add_rag_edges(self) -> None:
        """Добавляет RAG edges (CCN ≤ 3)."""
        if "retrieve" not in self.workflow.nodes:
            return
        
        self.workflow.add_edge("retrieve", "grade_documents")
        self.workflow.add_edge("rewrite", "agent")
        self.workflow.add_edge("generate", "agent")
        self.logger.info("Added RAG edges: retrieve->grade, rewrite->agent, generate->agent")
    
    def _add_conditional_edges(self) -> None:
        """Добавляет conditional edges (CCN ≤ 5)."""
        # RAG conditional edge
        if "retrieve" in self.workflow.nodes:
            self.workflow.add_conditional_edges(
                "grade_documents",
                self.decision_router.route_decision_to_generate,
                {"rewrite": "rewrite", "generate": "generate"},
            )
            self.logger.info("Added conditional edge: grade_documents -> decide_to_generate")
        
        # Agent routing conditional edges
        self._add_agent_routing_edges()
    
    def _add_agent_routing_edges(self) -> None:
        """Добавляет agent routing edges (CCN ≤ 4)."""
        # Native LangGraph tools routing
        if "tools" in self.workflow.nodes:
            self.workflow.add_conditional_edges(
                "agent",
                tools_condition,
                {"tools": "tools", END: END}
            )
            self.logger.info("Added LangGraph native conditional edge: agent -> tools_condition (autonomous)")
            return
        
        # Custom tools routing
        self._add_custom_tools_routing()
    
    def _add_custom_tools_routing(self) -> None:
        """Добавляет custom tools routing (CCN ≤ 3)."""
        possible_routes = self._build_possible_routes()
        
        if len(possible_routes) > 1:  # More than just END
            self.workflow.add_conditional_edges(
                "agent",
                self.tools_router.route,
                possible_routes,
            )
            self.logger.info(f"Added conditional edge: agent -> route_tools (possible: {list(possible_routes.keys())})")
        else:
            # No tools at all
            self.workflow.add_edge("agent", END)
            self.logger.info("No tools configured, agent output directly routed to END.")
    
    def _build_possible_routes(self) -> Dict[str, str]:
        """Строит возможные routes (CCN ≤ 2)."""
        possible_routes = {END: END}  # Always have an END route
        
        if "safe_tools" in self.workflow.nodes:
            possible_routes["safe_tools"] = "safe_tools"
        if "retrieve" in self.workflow.nodes:
            possible_routes["retrieve"] = "retrieve"
        
        return possible_routes
    
    def _compile_workflow(self) -> Any:
        """Компилирует workflow с memory (CCN ≤ 3)."""
        memory_saver_instance = None
        
        if self.factory.enable_context_memory:
            memory_saver_instance = MemorySaver()
            self.logger.info("Memory saver enabled for workflow")
        
        app = self.workflow.compile(checkpointer=memory_saver_instance)
        self.logger.info("Agent graph compiled successfully")
        
        return app
    
    def _create_workflow(self) -> None:
        """Создаёт базовый StateGraph workflow (CCN ≤ 1)."""
        self.workflow = StateGraph(AgentState)
