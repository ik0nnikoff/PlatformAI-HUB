"""
Agent Node Handler для LangGraph workflows.
Рефакторинг _agent_node с понижением CCN с 24 до ≤8.
"""

import logging
from typing import Dict, Any, Optional, List

from langchain_core.messages import AIMessage, BaseMessage
from datetime import datetime, timezone

from app.agent_runner.core import NodeExecutionError
from app.agent_runner.langgraph.models import AgentState, TokenUsageData
from .base import BaseNodeHandler


class AgentNodeHandler(BaseNodeHandler):
    """Обработчик узла агента с низкой сложностью."""
    
    async def execute(self, state: AgentState, config: dict) -> Dict[str, Any]:
        """
        Выполняет логику узла агента (CCN ≤ 4).
        
        Args:
            state: Состояние агента
            config: Конфигурация выполнения
            
        Returns:
            Dict[str, Any]: Обновленное состояние
            
        Raises:
            NodeExecutionError: При ошибке выполнения узла
        """
        self.logger.info(f"---CALL AGENT NODE (Agent ID: {self.factory.agent_id})---")
        
        try:
            # Подготовка данных
            messages = state["messages"]
            llm_config = self.factory._get_node_config("agent")
            
            # Создание модели и промпта
            model = self._create_configured_model(llm_config)
            chain = self._create_prompt_chain(model)
            
            # Выполнение запроса
            response = await self._execute_chain(chain, messages, config)
            
            # Обработка результата
            processed_response = self._process_response(response, state, llm_config)
            
            return {"messages": [processed_response]}
            
        except Exception as e:
            self.logger.error(f"Error in agent node: {e}", exc_info=True)
            return self._create_fallback_response()
    
    def _create_configured_model(self, llm_config: Dict[str, Any]):
        """Создает настроенную модель (CCN ≤ 2)."""
        model = self.factory._create_node_llm("agent")
        if not model:
            provider = llm_config.get("provider", "OpenAI")
            error_message = self.factory._handle_llm_error("agent", provider)
            raise NodeExecutionError(f"Failed to create LLM model: {error_message}")
        
        return self.factory._bind_tools_to_model(model)
    
    def _create_prompt_chain(self, model):
        """Создает цепочку промпт + модель (CCN ≤ 1)."""
        system_prompt = self.factory.system_prompt
        prompt = self.factory._create_prompt_with_time(system_prompt)
        return prompt | model
    
    async def _execute_chain(self, chain, messages: List[BaseMessage], config: dict) -> AIMessage:
        """Выполняет цепочку и возвращает ответ (CCN ≤ 3)."""
        response_raw = await chain.ainvoke({"messages": messages}, config=config)
        
        if not isinstance(response_raw, AIMessage):
            self.logger.error(f"Unexpected response type: {type(response_raw)}")
            content_str = str(response_raw) if not hasattr(response_raw, 'content') else response_raw.content
            return AIMessage(content=f"Error: Unexpected response structure. Raw: {content_str[:100]}")
        
        return response_raw
    
    def _process_response(self, response: AIMessage, state: AgentState, llm_config: Dict[str, Any]) -> AIMessage:
        """Обрабатывает ответ и токены (CCN ≤ 3)."""
        self._log_response_info(response)
        
        # Учет токенов
        node_model_id = llm_config.get("model_name", "gpt-4o-mini")
        self.factory._get_tokens(state, "agent_llm", node_model_id, response)
        
        # Восстановление инструментов при необходимости
        return self._recover_tool_calls_if_needed(response)
    
    def _log_response_info(self, response: AIMessage) -> None:
        """Логирует информацию об ответе (CCN ≤ 1)."""
        content_preview = response.content[:100] if response and response.content else 'N/A'
        self.logger.info(f"Agent node response content preview: {content_preview}")
        
        if hasattr(response, 'response_metadata'):
            self.logger.info(f"Agent node response_metadata: {response.response_metadata}")
        if hasattr(response, 'usage_metadata'):
            self.logger.info(f"Agent node usage_metadata: {response.usage_metadata}")
    
    def _recover_tool_calls_if_needed(self, response: AIMessage) -> AIMessage:
        """Восстанавливает tool calls при необходимости (CCN ≤ 6)."""
        if not (hasattr(response, 'invalid_tool_calls') and response.invalid_tool_calls):
            return response
        
        if hasattr(response, 'tool_calls') and response.tool_calls:
            return response
        
        self.logger.warning(f"Response has invalid_tool_calls but no valid tool_calls. Attempting recovery.")
        
        recovered_calls = []
        remaining_invalid = []
        
        for itc_obj in response.invalid_tool_calls:
            recovered_call = self._try_recover_tool_call(itc_obj)
            if recovered_call:
                recovered_calls.append(recovered_call)
            else:
                remaining_invalid.append(itc_obj)
        
        if recovered_calls:
            self._apply_recovered_calls(response, recovered_calls, remaining_invalid)
        
        return response
    
    def _try_recover_tool_call(self, itc_obj) -> Optional[Dict[str, Any]]:
        """Пытается восстановить один tool call (CCN ≤ 4)."""
        itc_dict = self._extract_tool_call_dict(itc_obj)
        if not itc_dict:
            return None
        
        error_value = itc_dict.get('error')
        if error_value is not None:
            return None
        
        # Восстановление call'а
        call_to_add = {
            "name": itc_dict.get("name"),
            "args": itc_dict.get("args", {}),
            "id": itc_dict.get("id"),
            "type": itc_dict.get("type", "function")
        }
        
        if call_to_add.get("name") and call_to_add.get("id"):
            self.logger.info(f"Recovered tool call: {call_to_add}")
            return call_to_add
        
        self.logger.warning(f"Could not fully recover tool call, missing name or id: {itc_dict}")
        return None
    
    def _extract_tool_call_dict(self, itc_obj) -> Optional[Dict[str, Any]]:
        """Извлекает словарь из объекта tool call (CCN ≤ 3)."""
        if isinstance(itc_obj, dict):
            return itc_obj
        
        if hasattr(itc_obj, 'error') and hasattr(itc_obj, 'name') and hasattr(itc_obj, 'args') and hasattr(itc_obj, 'id'):
            return {
                "name": itc_obj.name,
                "args": itc_obj.args,
                "id": itc_obj.id,
                "type": getattr(itc_obj, 'type', 'function'),
                "error": itc_obj.error
            }
        
        self.logger.warning(f"Unrecognized invalid_tool_call object: {itc_obj}")
        return None
    
    def _apply_recovered_calls(self, response: AIMessage, recovered_calls: List[Dict], remaining_invalid: List) -> None:
        """Применяет восстановленные calls к ответу (CCN ≤ 2)."""
        if not hasattr(response, 'tool_calls') or response.tool_calls is None:
            response.tool_calls = []
        
        response.tool_calls.extend(recovered_calls)
        response.invalid_tool_calls = remaining_invalid
        self.logger.info(f"Successfully recovered/added {len(recovered_calls)} tool_calls")
    
    def _create_fallback_response(self) -> Dict[str, Any]:
        """Создает fallback ответ при ошибке (CCN ≤ 1)."""
        fallback_response = AIMessage(content="Извините, произошла ошибка при обработке вашего запроса.")
        return {"messages": [fallback_response]}
