"""
Rewrite Node Handler для LangGraph workflow.
Рефакторинг _rewrite_node с понижением CCN с 12 до ≤8.
"""

import logging
from typing import Dict, Any, Optional, List

from langchain_core.messages import BaseMessage, AIMessage, HumanMessage, ToolMessage

from app.agent_runner.langgraph.models import AgentState
from .base import BaseNodeHandler


class RewriteNodeHandler(BaseNodeHandler):
    """
    Handler для rewrite узла с низкой сложностью.
    Переформулирует вопрос если релевантные документы не найдены.
    CCN ≤ 4 для каждого метода.
    """
    
    def __init__(self, factory, logger: logging.LoggerAdapter):
        super().__init__(factory, logger)
    
    async def execute(self, state: AgentState) -> Dict[str, Any]:
        """
        Выполняет переформулирование вопроса (CCN ≤ 3).
        
        Args:
            state: Состояние агента
            
        Returns:
            Dict: Обновленное состояние с переформулированным вопросом или финальным ответом
        """
        self.logger.info(f"---TRANSFORM QUERY (Agent ID: {self.factory.agent_id})---")
        
        rewrite_context = self._extract_rewrite_context(state)
        
        if self._should_attempt_rewrite(rewrite_context):
            return await self._perform_rewrite(state, rewrite_context)
        else:
            return self._create_no_answer_response(rewrite_context)
    
    def _extract_rewrite_context(self, state: AgentState) -> Dict[str, Any]:
        """
        Извлекает контекст для переформулирования (CCN ≤ 2).
        
        Args:
            state: Состояние агента
            
        Returns:
            Dict: Контекст с необходимыми данными
        """
        original_question = state["original_question"]
        messages = state["messages"]
        rewrite_count = state.get("rewrite_count", 0)
        max_rewrites = self.factory.max_rewrites
        
        # Извлекаем KB IDs из последнего datastore tool message
        kb_ids = self._extract_kb_ids_from_messages(messages)
        
        return {
            "original_question": original_question,
            "messages": messages,
            "rewrite_count": rewrite_count,
            "max_rewrites": max_rewrites,
            "kb_ids": kb_ids
        }
    
    def _extract_kb_ids_from_messages(self, messages: List[BaseMessage]) -> List[str]:
        """
        Извлекает KB IDs из tool messages (CCN ≤ 2).
        
        Args:
            messages: Список сообщений
            
        Returns:
            List[str]: Список KB IDs
        """
        for message in reversed(messages):
            if isinstance(message, ToolMessage) and message.name in self.factory.datastore_names:
                return self.factory._extract_kb_ids_from_tool_message(message)
        
        return []
    
    def _should_attempt_rewrite(self, context: Dict[str, Any]) -> bool:
        """
        Проверяет, следует ли пытаться переформулировать (CCN ≤ 1).
        
        Args:
            context: Контекст переформулирования
            
        Returns:
            bool: True если можно переформулировать
        """
        return context["rewrite_count"] < context["max_rewrites"]
    
    async def _perform_rewrite(self, state: AgentState, context: Dict[str, Any]) -> Dict[str, Any]:
        """
        Выполняет переформулирование вопроса (CCN ≤ 4).
        
        Args:
            state: Состояние агента
            context: Контекст переформулирования
            
        Returns:
            Dict: Результат переформулирования или fallback ответ
        """
        original_question = context["original_question"]
        rewrite_count = context["rewrite_count"]
        kb_ids = context["kb_ids"]
        
        self.logger.info(f"Rewrite attempt {rewrite_count + 1}/{context['max_rewrites']} for question: '{original_question}'")
        
        try:
            # Получаем модель для переформулирования
            model = self.factory._create_node_llm("rewrite", kb_ids)
            if not model:
                self.logger.error("Could not initialize LLM for rewriting")
                return self._create_no_answer_response(context)
            
            # Создаем промпт и получаем ответ
            prompt_msg = self.factory._create_rewrite_prompt(original_question, context["messages"])
            response = await model.ainvoke([prompt_msg])
            
            if not isinstance(response, AIMessage):
                self.logger.error(f"Rewrite node received unexpected response type: {type(response)}")
                return self._create_no_answer_response(context)
            
            rewritten_question = response.content.strip()
            
            # Учитываем токены
            node_model_id = self.factory._get_node_config("rewrite", kb_ids).get("model_id", "gpt-4o-mini")
            self.factory._get_tokens(state, "rewrite_llm", node_model_id, response)
            
            # Валидируем результат переформулирования
            if self._is_valid_rewrite(rewritten_question, original_question):
                return self._create_rewrite_response(rewritten_question, rewrite_count)
            else:
                self.logger.warning("Rewriting resulted in empty or identical question")
                return self._create_no_answer_response(context)
                
        except Exception as e:
            self.logger.error(f"Error during question rewriting: {e}", exc_info=True)
            return self._create_no_answer_response(context)
    
    def _is_valid_rewrite(self, rewritten: str, original: str) -> bool:
        """
        Проверяет валидность переформулированного вопроса (CCN ≤ 2).
        
        Args:
            rewritten: Переформулированный вопрос
            original: Оригинальный вопрос
            
        Returns:
            bool: True если переформулирование валидно
        """
        if not rewritten:
            return False
        
        return rewritten.lower() != original.lower()
    
    def _create_rewrite_response(self, rewritten_question: str, rewrite_count: int) -> Dict[str, Any]:
        """
        Создает ответ с переформулированным вопросом (CCN ≤ 1).
        
        Args:
            rewritten_question: Переформулированный вопрос
            rewrite_count: Текущий счетчик переформулирований
            
        Returns:
            Dict: Состояние с новым вопросом
        """
        self.logger.info(f"Rewritten question: {rewritten_question}")
        
        trigger_message = HumanMessage(content=f"Произведи поиск в базе данных заново: {rewritten_question}")
        
        return {
            "messages": [trigger_message],
            "question": rewritten_question,
            "rewrite_count": rewrite_count + 1,
        }
    
    def _create_no_answer_response(self, context: Dict[str, Any]) -> Dict[str, Any]:
        """
        Создает финальный ответ при невозможности переформулирования (CCN ≤ 1).
        
        Args:
            context: Контекст переформулирования
            
        Returns:
            Dict: Финальное состояние с ответом об отсутствии информации
        """
        original_question = context["original_question"]
        max_rewrites = context["max_rewrites"]
        
        self.logger.warning(f"Max rewrites ({max_rewrites}) reached or rewrite failed for original question: '{original_question}'")
        
        no_answer_message = AIMessage(
            content="К сожалению, я не смог найти релевантную информацию по вашему запросу даже после его уточнения. Попробуйте задать вопрос по-другому."
        )
        
        return {
            "messages": [no_answer_message],
            "rewrite_count": 0,
        }
