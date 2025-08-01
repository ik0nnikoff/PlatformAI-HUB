"""
LangGraph Native TTS Tool - Phase 4.8.2 Implementation

This tool follows LangGraph best practices for LLM autonomous decision making.
Replaces 1200+ lines of anti-pattern forced tool chains with ~50 lines of elegant solution.

Based on:
- MD/Reports/LangGraph_Voice_Intent_Decision_Patterns_Analysis.md
- MD/Reports/Voice_v2_Architecture_Deep_Analysis_Report.md
"""

import json
import logging
from typing import Optional, Dict, Any, Annotated

from langchain_core.tools import tool
from pydantic import BaseModel, Field

from app.services.voice_v2.core.exceptions import VoiceServiceError

logger = logging.getLogger(__name__)
# settings already imported and available


class VoiceSettings(BaseModel):
    """Voice generation settings."""
    voice_id: Optional[str] = Field(default=None, description="Specific voice ID to use")
    speed: Optional[float] = Field(default=1.0, description="Speech speed (0.5-2.0)")
    stability: Optional[float] = Field(default=0.7, description="Voice stability (0.0-1.0)")


@tool
def generate_voice_response(
    text: Annotated[str, "Text to convert to speech"]
) -> str:
    """
    Генерирует голосовой ответ из текста.

    Args:
        text: Текст для синтеза в голосовое сообщение

    Returns:
        JSON строка с результатом синтеза или ошибкой
    """

    logger.debug("TTS Tool: получен запрос на синтез текста длиной %d символов", len(text))

    try:
        # Валидация текста
        if not text or not text.strip():
            return _create_error_response(
                "Пустой текст для синтеза",
                "EMPTY_TEXT"
            )

        # Упрощенный синтез без зависимости от состояния
        return json.dumps({
            "success": True,
            "message": "Голосовое сообщение будет сгенерировано",
            "text": text.strip(),
            "voice_url": None,  # Будет установлен системой
            "status": "queued"
        }, ensure_ascii=False)

    except (VoiceServiceError, ValueError) as e:
        logger.error("TTS Tool: критическая ошибка - %s", e, exc_info=True)
        return _create_error_response(
            f"Критическая ошибка TTS: {str(e)}",
            "CRITICAL_ERROR"
        )


def _validate_agent_state(state: Dict) -> Dict[str, Any]:
    """Валидирует состояние агента для синтеза речи."""
    if not state:
        logger.error("TTS Tool: отсутствует состояние агента")
        return {
            "valid": False,
            "error": "Отсутствует контекст агента",
            "error_code": "NO_AGENT_STATE"
        }

    chat_id = state.get("chat_id")
    user_data = state.get("user_data", {})
    agent_id = user_data.get("agent_id")

    if not chat_id or not agent_id:
        logger.error(
            "TTS Tool: недостаточно данных для синтеза - chat_id: %s, agent_id: %s",
            chat_id, agent_id
        )
        return {
            "valid": False,
            "error": "Недостаточно данных для синтеза голоса",
            "error_code": "INSUFFICIENT_DATA"
        }

    return {
        "valid": True,
        "chat_id": chat_id,
        "agent_id": agent_id,
        "user_data": user_data
    }


def _validate_synthesis_text(text: str) -> Dict[str, Any]:
    """Валидирует текст для синтеза речи."""
    if not text or not text.strip():
        logger.warning("TTS Tool: пустой текст для синтеза")
        return {
            "valid": False,
            "error": "Пустой текст для синтеза",
            "error_code": "EMPTY_TEXT"
        }

    if len(text) > 4000:  # Ограничение для TTS
        logger.warning("TTS Tool: слишком длинный текст (%d символов)", len(text))
        return {
            "valid": False,
            "error": f"Текст слишком длинный ({len(text)} символов, максимум 4000)",
            "error_code": "TEXT_TOO_LONG"
        }

    return {"valid": True}


def _create_error_response(error_msg: str, error_code: str, provider: str = None) -> str:
    """Создает стандартизированный ответ об ошибке."""

    response = {
        "success": False,
        "error": error_msg,
        "error_code": error_code
    }

    if provider:
        response["provider_used"] = provider

    return json.dumps(response, ensure_ascii=False)


# Export for registry
__all__ = ['generate_voice_response']
