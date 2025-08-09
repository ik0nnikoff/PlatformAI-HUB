"""
Generate Node Handler для LangGraph workflows.
Рефакторинг _generate_node с понижением CCN с 13 до ≤8.
"""

import logging
from typing import Dict, Any, List, Optional

from langchain_core.messages import AIMessage, BaseMessage, ToolMessage

from app.agent_runner.core import NodeExecutionError
from app.agent_runner.langgraph.models import AgentState
from .base import BaseNodeHandler


class GenerateNodeHandler(BaseNodeHandler):
    """Обработчик узла генерации ответов с низкой сложностью."""
    
    async def execute(self, state: AgentState, config: dict = None) -> Dict[str, Any]:
        """
        Генерирует ответ используя вопрос и документы (CCN ≤ 4).
        
        Args:
            state: Состояние агента
            config: Конфигурация выполнения (опционально)
            
        Returns:
            Dict[str, Any]: Сгенерированный ответ
            
        Raises:
            NodeExecutionError: При ошибке выполнения узла
        """
        self.logger.info(f"---GENERATE (Agent ID: {self.factory.agent_id})---")
        
        try:
            # Валидация входных данных
            validation_result = self._validate_input(state)
            if validation_result:
                return validation_result
            
            # Подготовка данных для генерации
            generation_data = self._prepare_generation_data(state)
            
            # Выполнение генерации
            response = await self._generate_response(generation_data)
            
            # Обработка токенов
            self._track_generation_tokens(response, generation_data, state)
            
            return {"messages": [self._ensure_base_message(response)]}
            
        except Exception as e:
            self.logger.error(f"Error in generation node: {e}", exc_info=True)
            raise NodeExecutionError(f"Failed to generate response: {e}")
    
    def _validate_input(self, state: AgentState) -> Optional[Dict[str, Any]]:
        """Валидирует входные данные и возвращает fallback если нужно (CCN ≤ 4)."""
        documents = state["documents"]
        messages = state["messages"]
        
        if not documents:
            self.logger.warning("Generate node called with no relevant documents.")
            return self._handle_no_documents(messages)
        
        current_question = state["question"]
        self.logger.info(f"Generating answer for question: '{current_question}' using {len(documents)} documents.")
        return None
    
    def _handle_no_documents(self, messages: List[BaseMessage]) -> Dict[str, Any]:
        """Обрабатывает случай отсутствия документов (CCN ≤ 2)."""
        # Проверяем на сообщение о максимальных переписываниях
        if self._has_max_rewrites_message(messages):
            self.logger.info("Passing through 'max rewrites reached' message from rewrite_node.")
            return {"messages": [messages[-1]]}
        
        no_docs_response = AIMessage(
            content="К сожалению, я не смог найти информацию по вашему запросу в доступных источниках."
        )
        return {"messages": [no_docs_response]}
    
    def _has_max_rewrites_message(self, messages: List[BaseMessage]) -> bool:
        """Проверяет наличие сообщения о максимальных переписываниях (CCN ≤ 2)."""
        if not messages:
            return False
        
        last_message = messages[-1]
        if not isinstance(last_message, AIMessage):
            return False
        
        return "не смог найти релевантную информацию по вашему запросу даже после его уточнения" in last_message.content
    
    def _prepare_generation_data(self, state: AgentState) -> Dict[str, Any]:
        """Подготавливает данные для генерации (CCN ≤ 2)."""
        # Находим KB IDs для конфигурации
        kb_ids = self._extract_kb_ids_from_state(state)
        llm_config = self.factory._get_node_config("generate", kb_ids)
        
        if kb_ids:
            self.logger.info(f"Using KB-specific model configuration for generation. KB IDs: {kb_ids}")
        else:
            self.logger.info("No KB IDs found, using default generation configuration")
        
        return {
            "question": state["question"],
            "documents": state["documents"],
            "kb_ids": kb_ids,
            "llm_config": llm_config
        }
    
    def _extract_kb_ids_from_state(self, state: AgentState) -> List[str]:
        """Извлекает KB IDs из сообщений состояния (CCN ≤ 3)."""
        messages = state["messages"]
        
        # Ищем последнее ToolMessage от datastore tool
        for message in reversed(messages):
            if isinstance(message, ToolMessage) and message.name in self.factory.datastore_names:
                return self.factory._extract_kb_ids_from_tool_message(message)
        
        return []
    
    async def _generate_response(self, generation_data: Dict[str, Any]) -> BaseMessage:
        """Генерирует ответ через LLM (CCN ≤ 3)."""
        # Подготовка LLM и промпта
        llm = self.factory._create_node_llm("generate", generation_data["kb_ids"])
        if not llm:
            self.logger.error("Could not initialize LLM for generation.")
            raise NodeExecutionError("Could not initialize LLM for generation")
        
        prompt = self.factory._create_rag_template()
        rag_chain = prompt | llm
        
        # Подготовка контекста
        documents_str = "\\n\\n".join(generation_data["documents"])
        
        # Выполнение генерации
        response = await rag_chain.ainvoke({
            "context": documents_str, 
            "question": generation_data["question"]
        })
        
        return response
    
    def _track_generation_tokens(
        self, 
        response: BaseMessage, 
        generation_data: Dict[str, Any], 
        state: AgentState
    ) -> None:
        """Отслеживает токены генерации (CCN ≤ 1)."""
        node_model_id = generation_data["llm_config"].get("model_id", "gpt-4o-mini")
        self.factory._get_tokens(state, "generation_llm", node_model_id, response)
    
    def _ensure_base_message(self, response) -> BaseMessage:
        """Обеспечивает, что ответ является BaseMessage (CCN ≤ 2)."""
        if isinstance(response, BaseMessage):
            return response
        
        self.logger.warning(f"Generate node got non-BaseMessage response: {type(response)}. Converting to AIMessage.")
        return AIMessage(content=str(response))
