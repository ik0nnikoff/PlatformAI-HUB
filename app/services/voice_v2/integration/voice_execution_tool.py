"""
Voice Execution Tool for LangGraph Integration

Pure TTS execution tool that performs voice synthesis without decision making.
Migrated from app/agent_runner/agent_runner.py._process_response_with_tts().

Architecture:
- LangGraph Agent: Makes ALL decisions (provider, voice settings, TTS decision)
- voice_execution_tool: PURE EXECUTION - synthesizes speech and returns audio URL
- No decision logic, no intent detection - agent already decided everything

Performance targets:
- TTS execution: ≤2.0s (95th percentile)
- Memory usage: Minimal overhead
- Error handling: Graceful fallback to text response
"""

import logging
from typing import Optional, Dict, Any, Annotated
from langchain_core.tools import tool
from langgraph.prebuilt import InjectedState

from app.services.voice_v2.core.orchestrator.tts_manager import VoiceTTSManager
from app.services.voice_v2.core.schemas import TTSRequest, TTSResponse
from app.services.voice_v2.core.exceptions import VoiceServiceError

logger = logging.getLogger(__name__)


class VoiceExecutionResult:
    """Voice execution result container"""

    def __init__(self, success: bool, audio_url: Optional[str] = None,
                 error_message: Optional[str] = None, processing_time: float = 0.0,
                 provider: Optional[str] = None, audio_format: Optional[str] = None):
        self.success = success
        self.audio_url = audio_url
        self.error_message = error_message
        self.processing_time = processing_time
        self.provider = provider
        self.audio_format = audio_format

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for LangGraph state"""
        return {
            "success": self.success,
            "audio_url": self.audio_url,
            "error_message": self.error_message,
            "processing_time": self.processing_time,
            "provider": self.provider,
            "format": self.audio_format
        }


@tool
async def voice_execution_tool(
    text: Annotated[str, "Text to synthesize with TTS"],
    voice_config: Annotated[Dict[str, Any], "Voice configuration from agent decision"] = None,
    state: Annotated[Dict[str, Any], InjectedState] = None
) -> str:
    """
    Execute TTS synthesis based on agent decision (PURE EXECUTION TOOL)

    This tool performs ONLY TTS execution - NO decision making.
    Agent already decided: text to synthesize, provider preference, voice settings.

    Args:
        text: Text to synthesize (decided by agent)
        voice_config: Voice configuration (provider, voice, speed, etc.) from agent
        state: LangGraph agent state for context

    Returns:
        str: JSON string with execution result containing audio_url or error

    Performance:
    - Target: ≤2.0s TTS execution (95th percentile)
    - Uses voice_v2 orchestrator with provider fallback
    - Aggressive caching for repeated requests
    """
    # Extract context from agent state
    agent_id = state.get("config", {}).get("configurable", {}).get("agent_id", "unknown_agent")
    chat_id = state.get("chat_id", "unknown_chat")
    # user_data = state.get("user_data", {})

    # Setup logger with agent context
    log_adapter = logging.LoggerAdapter(logger, {
        'agent_id': agent_id,
        'chat_id': chat_id,
        'operation': 'voice_execution'
    })

    log_adapter.info(f"Starting TTS execution for text length: {len(text)}")

    # Validate input
    if not text or not text.strip():
        error_msg = "Empty text provided for TTS synthesis"
        log_adapter.warning(error_msg)
        return VoiceExecutionResult(
            success=False,
            error_message=error_msg
        ).to_dict().__str__()

    # Extract voice configuration (agent already decided)
    voice_config = voice_config or {}
    language = voice_config.get("language", "ru")
    voice = voice_config.get("voice")
    speed = voice_config.get("speed", 1.0)
    preferred_provider = voice_config.get("provider")

    try:
        # Initialize TTS manager
        tts_manager = VoiceTTSManager()
        await tts_manager.initialize()

        # Create TTS request
        tts_request = TTSRequest(
            text=text.strip(),
            language=language,
            voice=voice,
            speed=speed
        )

        log_adapter.info(f"Executing TTS with provider preference: {preferred_provider}")

        # Execute TTS synthesis (pure execution, no decisions)
        tts_response: TTSResponse = await tts_manager.synthesize_speech(tts_request)

        # Generate audio URL for platform delivery
        audio_url = await _generate_audio_url(tts_response, agent_id, chat_id, log_adapter)

        if audio_url:
            result = VoiceExecutionResult(
                success=True,
                audio_url=audio_url,
                processing_time=tts_response.processing_time,
                provider=tts_response.provider,
                audio_format=tts_response.format.value
            )

            log_adapter.info(f"TTS execution successful: {audio_url} "
                             f"(provider: {tts_response.provider}, "
                             f"time: {tts_response.processing_time:.2f}s)")

            return str(result.to_dict())
        else:
            error_msg = "Failed to generate audio URL from TTS response"
            log_adapter.error(error_msg)
            return VoiceExecutionResult(
                success=False,
                error_message=error_msg,
                processing_time=tts_response.processing_time,
                provider=tts_response.provider
            ).to_dict().__str__()

    except VoiceServiceError as e:
        error_msg = f"Voice service error: {str(e)}"
        log_adapter.error(error_msg)
        return VoiceExecutionResult(
            success=False,
            error_message=error_msg
        ).to_dict().__str__()

    except Exception as e:
        error_msg = f"Unexpected error in TTS execution: {str(e)}"
        log_adapter.error(error_msg, exc_info=True)
        return VoiceExecutionResult(
            success=False,
            error_message=error_msg
        ).to_dict().__str__()

    finally:
        # Cleanup TTS manager
        try:
            await tts_manager.cleanup()
        except Exception as e:
            log_adapter.warning(f"TTS manager cleanup error: {e}")


async def _generate_audio_url(tts_response: TTSResponse, agent_id: str,
                              chat_id: str, log_adapter) -> Optional[str]:
    """
    Generate audio URL from TTS response for platform delivery

    Args:
        tts_response: TTS response with audio data
        agent_id: Agent identifier
        chat_id: Chat identifier
        log_adapter: Logger adapter

    Returns:
        Audio URL or None if failed
    """
    try:
        # For now, create MinIO-style URL structure
        # TODO: Integrate with proper MinIO file manager for URL generation

        if not tts_response.audio_data:
            log_adapter.warning("No audio data in TTS response")
            return None

        # Generate file key for MinIO storage
        import hashlib
        import time

        # Create unique file key
        text_hash = hashlib.sha256(tts_response.provider.encode() +
                                   str(time.time()).encode()).hexdigest()[:16]
        file_key = f"voice/{agent_id}/{chat_id}/tts_{text_hash}.{tts_response.format.value}"

        # TODO: Replace with actual MinIO upload and presigned URL generation
        # For now, return minio:// URL structure that AgentRunner expects
        audio_url = f"minio://voice-files/{file_key}"

        log_adapter.debug(f"Generated audio URL: {audio_url}")
        return audio_url

    except Exception as e:
        log_adapter.error(f"Error generating audio URL: {e}")
        return None


@tool
async def voice_capabilities_query_tool(
    state: Annotated[Dict[str, Any], InjectedState] = None
) -> str:
    """
    Query current voice capabilities and configuration (INFORMATION TOOL)

    This tool provides current voice capabilities without making decisions.
    Agent uses this information to make informed voice decisions.

    Returns:
        str: Current voice capabilities and configuration
    """
    # Extract agent context
    agent_id = state.get("config", {}).get("configurable", {}).get("agent_id", "unknown_agent")
    log_adapter = logging.LoggerAdapter(logger, {'agent_id': agent_id})

    log_adapter.debug("Querying voice capabilities")

    try:
        # Initialize TTS manager to check capabilities
        tts_manager = VoiceTTSManager()
        await tts_manager.initialize()

        # Query available providers and capabilities
        # TODO: Implement proper capabilities query from orchestrator
        capabilities = {
            "tts_available": True,
            "providers": ["openai", "google", "yandex"],
            "languages": ["ru", "en", "de", "fr"],
            "formats": ["wav", "mp3", "ogg"],
            "max_text_length": 4000,
            "features": {
                "speed_control": True,
                "voice_selection": True,
                "caching": True,
                "fallback": True
            }
        }

        await tts_manager.cleanup()

        # Format capabilities for agent understanding
        capabilities_text = f"""Голосовые возможности агента:

✅ TTS доступен: {capabilities['tts_available']}
🔊 Провайдеры: {', '.join(capabilities['providers'])}
🌍 Языки: {', '.join(capabilities['languages'])}
📁 Форматы: {', '.join(capabilities['formats'])}
📝 Макс. длина: {capabilities['max_text_length']} символов

Функции:
• Управление скоростью: {capabilities['features']['speed_control']}
• Выбор голоса: {capabilities['features']['voice_selection']}
• Кэширование: {capabilities['features']['caching']}
• Резервные провайдеры: {capabilities['features']['fallback']}

Для голосового ответа агент может принять решение о синтезе речи на основе контекста беседы."""

        log_adapter.info("Voice capabilities query completed")
        return capabilities_text

    except Exception as e:
        error_msg = f"Error querying voice capabilities: {str(e)}"
        log_adapter.error(error_msg)
        return f"Ошибка получения голосовых возможностей: {error_msg}"


# Export tools for LangGraph registration
__all__ = ["voice_execution_tool", "voice_capabilities_query_tool"]
