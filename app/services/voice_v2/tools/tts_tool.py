"""
LangGraph Native TTS Tool - Phase 4.8.2 Implementation

This tool follows LangGraph best practices for LLM autonomous decision making.
Replaces 1200+ lines of anti-pattern forced tool chains with ~50 lines of elegant solution.

Based on:
- MD/Reports/LangGraph_Voice_Intent_Decision_Patterns_Analysis.md
- MD/Reports/Voice_v2_Architecture_Deep_Analysis_Report.md
"""

import logging
from typing import Optional, Dict, Any, Annotated

from langchain_core.tools import tool, InjectedState
from pydantic import BaseModel, Field

from app.services.voice_v2.core.orchestrator.base_orchestrator import VoiceServiceOrchestrator

logger = logging.getLogger(__name__)
# settings already imported and available


class VoiceSettings(BaseModel):
    """Voice generation settings."""
    voice_id: Optional[str] = Field(default=None, description="Specific voice ID to use")
    speed: Optional[float] = Field(default=1.0, description="Speech speed (0.5-2.0)")
    stability: Optional[float] = Field(default=0.7, description="Voice stability (0.0-1.0)")


@tool
def generate_voice_response(
    text: Annotated[str, "Текст для синтеза речи"],
    voice_config: Annotated[Dict[str, Any], "Конфигурация голоса"] = None,
    state: Annotated[Dict, InjectedState] = None
) -> str:
    """
    Генерирует голосовой ответ из текста с настройками голоса.
    
    Args:
        text: Текст для синтеза в голосовое сообщение
        voice_config: Настройки голоса (provider, voice_id, speed, etc.)
        state: Состояние агента (автоматически передается)
    
    Returns:
        JSON строка с результатом синтеза или ошибкой
    """
    
    logger.debug(f"TTS Tool: получен запрос на синтез текста длиной {len(text)} символов")
    
    try:
        # Валидация состояния агента
        state_validation = _validate_agent_state(state)
        if not state_validation["valid"]:
            return _create_error_response(
                state_validation["error"],
                state_validation["error_code"]
            )
        
        chat_id = state_validation["chat_id"]
        agent_id = state_validation["agent_id"]
        user_data = state_validation["user_data"]
        
        # Валидация текста
        text_validation = _validate_synthesis_text(text)
        if not text_validation["valid"]:
            return _create_error_response(
                text_validation["error"],
                text_validation["error_code"]
            )
        
        # Выполнение синтеза
        synthesis_result = _execute_speech_synthesis(
            text, voice_config, agent_id, chat_id, user_data, state
        )
        
        return synthesis_result
            
    except Exception as e:
        logger.error(f"TTS Tool: критическая ошибка - {e}", exc_info=True)
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
        logger.error(f"TTS Tool: недостаточно данных для синтеза - chat_id: {chat_id}, agent_id: {agent_id}")
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
        logger.warning(f"TTS Tool: слишком длинный текст ({len(text)} символов)")
        return {
            "valid": False,
            "error": f"Текст слишком длинный ({len(text)} символов, максимум 4000)",
            "error_code": "TEXT_TOO_LONG"
        }
    
    return {"valid": True}


def _execute_speech_synthesis(
    text: str, 
    voice_config: Dict[str, Any], 
    agent_id: str, 
    chat_id: str, 
    user_data: Dict[str, Any], 
    state: Dict
) -> str:
    """Выполняет синтез речи через оркестратор."""
    import asyncio
    import json
    
    # Создаем оркестратор
    orchestrator = VoiceServiceOrchestrator()
    
    # Подготавливаем конфигурацию TTS
    tts_config = voice_config or {}
    
    # Добавляем контекстную информацию
    synthesis_context = {
        "agent_id": agent_id,
        "chat_id": chat_id,
        "user_id": user_data.get("user_id"),
        "channel": state.get("channel", "unknown")
    }
    
    # Выполняем синтез асинхронно
    logger.debug(f"TTS Tool: начинаем синтез с конфигурацией: {tts_config}")
    
    # Создаем и запускаем корутину
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        result = loop.run_until_complete(
            orchestrator.synthesize_speech(
                text=text,
                voice_settings=tts_config,
                context=synthesis_context
            )
        )
    finally:
        loop.close()
    
    if result.get("success"):
        logger.info(f"TTS Tool: успешный синтез для агента {agent_id}")
        return json.dumps({
            "success": True,
            "audio_url": result.get("audio_url"),
            "audio_file": result.get("audio_file"),
            "provider_used": result.get("provider"),
            "synthesis_time": result.get("processing_time", 0),
            "voice_config_used": result.get("voice_config", {})
        }, ensure_ascii=False)
    else:
        logger.error(f"TTS Tool: ошибка синтеза - {result.get('error')}")
        return _create_error_response(
            result.get("error", "Неизвестная ошибка синтеза"),
            result.get("error_code", "SYNTHESIS_FAILED"),
            result.get("provider")
        )


def _create_error_response(error_msg: str, error_code: str, provider: str = None) -> str:
    """Создает стандартизированный ответ об ошибке."""
    import json
    
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
