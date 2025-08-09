"""
Token Management для LangGraph workflows.
Рефакторинг token tracking с понижением CCN с 15 до ≤8.
"""

import logging
from typing import Dict, Any, Optional
from datetime import datetime, timezone

from langchain_core.messages import BaseMessage

from app.agent_runner.core import NodeExecutionError
from app.agent_runner.langgraph.models import TokenUsageData


class TokenTracker:
    """Трекер токенов с низкой сложностью."""
    
    def __init__(self, logger: logging.LoggerAdapter):
        self.logger = logger
    
    def extract_token_data(
        self, 
        response: BaseMessage, 
        call_type: str, 
        node_model_id: str
    ) -> Optional[TokenUsageData]:
        """
        Извлекает данные об использовании токенов (CCN ≤ 4).
        
        Args:
            response: Ответ от модели
            call_type: Тип вызова для логирования
            node_model_id: ID модели по умолчанию
            
        Returns:
            TokenUsageData объект или None если токены не найдены
        """
        # Попытка извлечения из usage_metadata
        token_data = self._extract_from_usage_metadata(response)
        if token_data:
            model_meta = self._get_model_metadata(response, node_model_id)
            return self._create_token_usage_data(token_data, call_type, model_meta)
        
        # Попытка извлечения из response_metadata
        token_data = self._extract_from_response_metadata(response)
        if token_data:
            model_meta = self._get_model_metadata(response, node_model_id)
            return self._create_token_usage_data(token_data, call_type, model_meta)
        
        # Токены не найдены
        self.logger.warning(f"Token usage data not found in usage_metadata or response_metadata for {call_type}.")
        return None
    
    def _extract_from_usage_metadata(self, response: BaseMessage) -> Optional[Dict[str, int]]:
        """Извлекает токены из usage_metadata (CCN ≤ 2)."""
        if not hasattr(response, 'usage_metadata') or not response.usage_metadata:
            return None
        
        usage_meta = response.usage_metadata
        token_data = {
            'prompt_tokens': self._get_prompt_tokens(usage_meta),
            'completion_tokens': self._get_completion_tokens(usage_meta),
            'total_tokens': usage_meta.get('total_tokens', 0)
        }
        
        self.logger.info(f"Token usage from usage_metadata: P:{token_data['prompt_tokens']} C:{token_data['completion_tokens']} T:{token_data['total_tokens']}")
        return token_data
    
    def _extract_from_response_metadata(self, response: BaseMessage) -> Optional[Dict[str, int]]:
        """Извлекает токены из response_metadata (CCN ≤ 2)."""
        if not hasattr(response, 'response_metadata') or not response.response_metadata:
            return None
        
        if 'token_usage' not in response.response_metadata:
            return None
        
        usage = response.response_metadata['token_usage']
        token_data = {
            'prompt_tokens': usage.get('prompt_tokens', 0),
            'completion_tokens': usage.get('completion_tokens', 0),
            'total_tokens': usage.get('total_tokens', 0)
        }
        
        self.logger.info(f"Token usage from response_metadata['token_usage']: P:{token_data['prompt_tokens']} C:{token_data['completion_tokens']} T:{token_data['total_tokens']}")
        return token_data
    
    def _get_prompt_tokens(self, usage_meta: Dict[str, Any]) -> int:
        """Получает prompt tokens с fallback (CCN ≤ 2)."""
        prompt_tokens = usage_meta.get('prompt_tokens')
        if prompt_tokens is not None:
            return prompt_tokens
        return usage_meta.get('input_tokens', 0)
    
    def _get_completion_tokens(self, usage_meta: Dict[str, Any]) -> int:
        """Получает completion tokens с fallback (CCN ≤ 2)."""
        completion_tokens = usage_meta.get('completion_tokens')
        if completion_tokens is not None:
            return completion_tokens
        return usage_meta.get('output_tokens', 0)
    
    def _get_model_metadata(self, response: BaseMessage, default_model: str) -> str:
        """Получает метаданные модели (CCN ≤ 2)."""
        if hasattr(response, 'response_metadata') and response.response_metadata:
            model_name = response.response_metadata.get('model_name')
            if model_name:
                return model_name
        
        return default_model
    
    def _create_token_usage_data(
        self, 
        token_data: Dict[str, int], 
        call_type: str, 
        model_meta: str
    ) -> Optional[TokenUsageData]:
        """Создает TokenUsageData объект (CCN ≤ 2)."""
        total_tokens = token_data['total_tokens']
        prompt_tokens = token_data['prompt_tokens'] 
        completion_tokens = token_data['completion_tokens']
        
        if total_tokens > 0 or prompt_tokens > 0 or completion_tokens > 0:
            return TokenUsageData(
                call_type=call_type,
                model_id=model_meta,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_tokens=total_tokens,
                timestamp=datetime.now(timezone.utc).isoformat()
            )
        
        return None
    
    def add_tokens_to_state(
        self, 
        state: Dict[str, Any], 
        call_type: str, 
        node_model_id: str, 
        response: BaseMessage
    ) -> None:
        """
        Извлекает данные об использовании токенов и добавляет их в state (CCN ≤ 2).
        
        Args:
            state: Состояние для добавления токенов
            call_type: Тип вызова
            node_model_id: ID модели по умолчанию
            response: Ответ от модели
        """
        token_event_data = self.extract_token_data(response, call_type, node_model_id)
        if token_event_data:
            self.logger.info(f"Token usage for {call_type}: {token_event_data.total_tokens} tokens recorded.")
            state.get("token_usage_events", []).append(token_event_data)
