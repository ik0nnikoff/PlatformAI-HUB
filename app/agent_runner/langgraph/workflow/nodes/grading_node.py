"""
Document Grading Node Handler для LangGraph workflows.
Рефакторинг _grade_docs_node с понижением CCN с 20 до ≤8.
"""

import logging
from typing import Dict, Any, List, Optional

from langchain_core.messages import ToolMessage
from pydantic import BaseModel, Field

from app.agent_runner.core import NodeExecutionError
from app.agent_runner.langgraph.models import AgentState
from .base import BaseNodeHandler


class Grade(BaseModel):
    """Binary score for relevance check."""
    binary_score: str = Field(description="Relevance score 'yes' or 'no'")


class GradingNodeHandler(BaseNodeHandler):
    """Обработчик узла оценки документов с низкой сложностью."""
    
    async def execute(self, state: AgentState, config: dict = None) -> Dict[str, Any]:
        """
        Выполняет оценку документов на релевантность (CCN ≤ 4).
        
        Args:
            state: Состояние агента
            config: Конфигурация выполнения (опционально)
            
        Returns:
            Dict[str, Any]: Результат оценки документов
            
        Raises:
            NodeExecutionError: При ошибке выполнения узла
        """
        self.logger.info(f"---CHECK RELEVANCE (Agent ID: {self.factory.agent_id})---")
        
        try:
            # Валидация входных данных
            validation_result = self._validate_input(state)
            if validation_result:
                return validation_result
            
            # Подготовка данных для оценки
            grade_data = self._prepare_grading_data(state)
            
            # Выполнение оценки
            graded_docs = await self._grade_documents(grade_data)
            
            return self._build_result(graded_docs, state["question"])
            
        except Exception as e:
            self.logger.error(f"Error in grading node: {e}", exc_info=True)
            raise NodeExecutionError(f"Failed to grade documents: {e}")
    
    def _validate_input(self, state: AgentState) -> Optional[Dict[str, Any]]:
        """Валидирует входные данные (CCN ≤ 3)."""
        messages = state["messages"]
        current_question = state["question"]
        
        if not messages:
            self.logger.warning("No messages found for grading")
            return {"documents": [], "question": current_question}
        
        last_message = messages[-1]
        if not self._is_valid_tool_message(last_message):
            self.logger.warning(
                f"Grade documents called, but last message is not a valid ToolMessage "
                f"from a configured datastore tool. Last message: {type(last_message)}, "
                f"name: {getattr(last_message, 'name', 'N/A')}. "
                f"Expected one of: {self.factory.datastore_names}"
            )
            return {"documents": [], "question": current_question}
        
        self.logger.info(f"Grading documents for question: '{current_question}'")
        return None
    
    def _is_valid_tool_message(self, message) -> bool:
        """Проверяет валидность tool message (CCN ≤ 2)."""
        if not isinstance(message, ToolMessage):
            return False
        
        if not hasattr(message, 'name') or not message.name:
            return False
        
        return message.name in self.factory.datastore_names
    
    def _prepare_grading_data(self, state: AgentState) -> Dict[str, Any]:
        """Подготавливает данные для оценки (CCN ≤ 2)."""
        last_message = state["messages"][-1]
        kb_ids = self.factory._extract_kb_ids_from_tool_message(last_message)
        llm_config = self.factory._get_node_config("grading", kb_ids)
        
        if kb_ids:
            self.logger.info(f"Using KB-specific model configuration for KB IDs: {kb_ids}")
        else:
            self.logger.info("No KB IDs found, using default grading configuration")
        
        return {
            "last_message": last_message,
            "kb_ids": kb_ids,
            "llm_config": llm_config,
            "current_question": state["question"]
        }
    
    async def _grade_documents(self, grade_data: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Выполняет оценку документов (CCN ≤ 5)."""
        last_message = grade_data["last_message"]
        llm_config = grade_data["llm_config"]
        current_question = grade_data["current_question"]
        
        # Создание модели для оценки
        model = await self._create_grading_model(llm_config)
        
        # Парсинг документов из tool message
        documents = self._parse_documents_from_message(last_message)
        
        # Оценка каждого документа
        graded_docs = []
        for doc in documents:
            grade_result = await self._grade_single_document(model, doc, current_question, llm_config)
            graded_docs.append(grade_result)
        
        return graded_docs
    
    async def _create_grading_model(self, llm_config: Dict[str, Any]):
        """Создает модель для оценки (CCN ≤ 3)."""
        node_model_id = llm_config.get("model_id", "gpt-4o-mini")
        model = self.factory._create_node_llm("grading")
        
        if not model:
            provider = llm_config.get("provider", "OpenAI")
            error_message = self.factory._handle_llm_error("grading", provider)
            raise NodeExecutionError(f"Failed to create grading LLM: {error_message}")
        
        # Создание structured LLM для оценки
        structured_llm = model.with_structured_output(Grade)
        return structured_llm
    
    def _parse_documents_from_message(self, message: ToolMessage) -> List[str]:
        """Парсит документы из tool message (CCN ≤ 2)."""
        try:
            # Парсинг content как строки документов
            content = message.content
            if isinstance(content, str):
                # Простое разделение по paragraphs или sections
                documents = [doc.strip() for doc in content.split('\n\n') if doc.strip()]
                return documents if documents else [content]
            return [str(content)]
            
        except Exception as e:
            self.logger.warning(f"Failed to parse documents from message: {e}")
            return [str(message.content)]
    
    async def _grade_single_document(
        self, 
        model, 
        document: str, 
        question: str, 
        llm_config: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Оценивает один документ (CCN ≤ 4)."""
        try:
            grade_prompt = self._create_grade_prompt(document, question)
            
            self.logger.debug(f"Grading document chunk: {document[:100]}...")
            response = await model.ainvoke([grade_prompt])
            
            # Учет токенов
            self._track_grading_tokens(response, llm_config)
            
            is_relevant = response.binary_score.lower() == "yes"
            self.logger.info(f"Document relevance: {response.binary_score}")
            
            return {
                "document": document,
                "grade": response.binary_score,
                "relevant": is_relevant
            }
            
        except Exception as e:
            self.logger.error(f"Error grading document: {e}")
            return {
                "document": document,
                "grade": "no",
                "relevant": False,
                "error": str(e)
            }
    
    def _create_grade_prompt(self, document: str, question: str) -> Dict[str, str]:
        """Создает промпт для оценки (CCN ≤ 1)."""
        return {
            "role": "user",
            "content": f"""You are a grader assessing relevance of a retrieved document to a user question.
            
Here is the retrieved document:
{document}

Here is the user question:
{question}

If the document contains keywords related to the user question, grade it as relevant.
Give a binary score 'yes' or 'no' score to indicate whether the document is relevant to the question."""
        }
    
    def _track_grading_tokens(self, response, llm_config: Dict[str, Any]) -> None:
        """Отслеживает токены оценки (CCN ≤ 1)."""
        node_model_id = llm_config.get("model_id", "gpt-4o-mini")
        # Примечание: response от structured_output может не иметь token metadata
        # В будущем можно добавить отслеживание через callback handler
        self.logger.debug(f"Grading completed with model: {node_model_id}")
    
    def _build_result(self, graded_docs: List[Dict[str, Any]], question: str) -> Dict[str, Any]:
        """Строит итоговый результат (CCN ≤ 2)."""
        # Фильтруем только релевантные документы
        relevant_docs = [doc for doc in graded_docs if doc.get("relevant", False)]
        
        self.logger.info(f"Found {len(relevant_docs)} relevant documents out of {len(graded_docs)} total")
        
        return {
            "documents": relevant_docs,
            "question": question
        }
