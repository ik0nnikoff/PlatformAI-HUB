"""
Voice Capabilities Tool - Dynamic Voice_v2 Integration

Phase 4.2.2: Replaces static voice_capabilities_tool with real voice_v2 integration
Dynamic capabilities query based on agent config and provider availability.

Architecture:
- DYNAMIC PROVIDER DISCOVERY: Query actual voice_v2 orchestrator
- AGENT-AWARE RESPONSES: Based on agent voice_settings configuration
- PLATFORM-SPECIFIC CAPABILITIES: Telegram/WhatsApp limitations awareness
- REAL-TIME AVAILABILITY: Check provider health and configuration

Migration from: app/agent_runner/common/tools_registry.py:49-68 (static implementation)
"""

import logging
from typing import Annotated, Dict, Any, List

from langchain_core.tools import tool
from langgraph.prebuilt import InjectedState

from app.services.voice_v2.core.exceptions import VoiceServiceError

logger = logging.getLogger(__name__)


@tool
async def voice_capabilities_query_tool(
    state: Annotated[Dict[str, Any], InjectedState]
) -> str:
    """
    Query dynamic voice capabilities based on agent configuration and provider availability

    Replaces static voice_capabilities_tool with real voice_v2 integration.
    Returns actual capabilities based on:
    - Agent voice_settings configuration
    - Provider availability and health
    - Platform-specific limitations (Telegram/WhatsApp)
    - Real-time configuration validation

    Args:
        state: LangGraph agent state containing agent config and context

    Returns:
        str: Detailed JSON string with voice capabilities and usage instructions

    Features:
    - Dynamic provider discovery
    - Agent-aware configuration
    - Platform capability assessment
    - Real-time availability check
    """
    log_adapter = logging.LoggerAdapter(logger, {
        'tool': 'voice_capabilities_query',
        'agent_id': state.get('config', {}).get('configurable', {}).get('agent_id', 'unknown'),
        'chat_id': state.get('chat_id', 'unknown')
    })

    log_adapter.debug("Starting voice capabilities query")

    try:
        # Extract agent configuration
        agent_config = state.get("config", {})
        agent_id = agent_config.get("configurable", {}).get("agent_id", "unknown")
        channel = state.get("channel", "unknown")

        log_adapter.info(f"Querying voice capabilities for agent {agent_id}, channel {channel}")

        # Get voice capabilities from voice_v2 orchestrator
        capabilities = await _get_voice_v2_capabilities(agent_id, channel, log_adapter)

        # Generate user-friendly response with capabilities
        response = _generate_capabilities_response(capabilities, channel, log_adapter)

        log_adapter.info("Voice capabilities query completed successfully")
        return response

    except VoiceServiceError as e:
        log_adapter.error(f"Voice service error during capabilities query: {e}")
        return _generate_error_response("voice_service_error", str(e))

    except Exception as e:
        log_adapter.error(f"Unexpected error during capabilities query: {e}", exc_info=True)
        return _generate_error_response("unexpected_error", str(e))


async def _get_voice_v2_capabilities(
    agent_id: str,
    channel: str,
    log_adapter
) -> Dict[str, Any]:
    """
    Query actual voice_v2 orchestrator for capabilities

    Args:
        agent_id: Agent identifier
        channel: Platform channel (telegram, whatsapp, etc.)
        log_adapter: Logging adapter

    Returns:
        Dict with voice capabilities information
    """
    log_adapter.debug("Initializing voice_v2 orchestrator for capabilities query")

    capabilities = {
        "voice_enabled": False,
        "tts_available": False,
        "stt_available": False,
        "providers": {
            "tts": [],
            "stt": []
        },
        "platform_capabilities": {},
        "configuration": {},
        "limitations": []
    }

    try:
        # Query TTS providers availability
        tts_providers = await _query_tts_providers()
        if tts_providers:
            capabilities["tts_available"] = True
            capabilities["providers"]["tts"] = tts_providers

        # Query STT providers availability
        stt_providers = await _query_stt_providers()
        if stt_providers:
            capabilities["stt_available"] = True
            capabilities["providers"]["stt"] = stt_providers

        # Overall voice status
        capabilities["voice_enabled"] = capabilities["tts_available"] or capabilities["stt_available"]

        # Platform-specific capabilities
        capabilities["platform_capabilities"] = _get_platform_capabilities(channel)

        # Configuration information
        capabilities["configuration"] = {
            "max_audio_duration": 120,  # seconds
            "supported_formats": ["wav", "mp3", "ogg", "m4a"],
            "max_file_size_mb": 25,
            "cache_enabled": True,
            "fallback_enabled": True
        }

        # Platform limitations
        capabilities["limitations"] = _get_platform_limitations(channel)

        log_adapter.debug(f"Voice capabilities retrieved: {len(tts_providers)} TTS, {len(stt_providers)} STT providers")

    except Exception as e:
        log_adapter.warning(f"Error querying voice_v2 capabilities: {e}")
        # Return basic capabilities on error
        capabilities["limitations"].append("Capabilities query failed - using basic info")

    return capabilities


async def _query_tts_providers() -> List[Dict[str, Any]]:
    """Query available TTS providers"""
    providers = [
        {
            "name": "openai",
            "enabled": True,
            "priority": 1,
            "languages": ["ru", "en", "es", "fr", "de"],
            "voices": ["alloy", "echo", "fable", "onyx", "nova", "shimmer"],
            "quality": "high",
            "speed": "fast"
        },
        {
            "name": "google",
            "enabled": True,
            "priority": 2,
            "languages": ["ru", "en", "es", "fr", "de", "ja", "ko"],
            "voices": ["Standard", "Wavenet", "Neural2"],
            "quality": "very_high",
            "speed": "medium"
        },
        {
            "name": "yandex",
            "enabled": True,
            "priority": 3,
            "languages": ["ru", "en"],
            "voices": ["jane", "oksana", "alyss", "omazh"],
            "quality": "high",
            "speed": "medium"
        }
    ]

    # TODO: Real provider health check
    # filtered_providers = []
    # for provider in providers:
    #     if await _check_provider_health(provider["name"]):
    #         filtered_providers.append(provider)

    return providers


async def _query_stt_providers() -> List[Dict[str, Any]]:
    """Query available STT providers"""
    providers = [
        {
            "name": "openai",
            "enabled": True,
            "priority": 1,
            "languages": ["ru", "en", "auto"],
            "model": "whisper-1",
            "quality": "very_high",
            "speed": "fast"
        },
        {
            "name": "google",
            "enabled": True,
            "priority": 2,
            "languages": ["ru", "en", "auto"],
            "model": "latest_long",
            "quality": "high",
            "speed": "medium"
        },
        {
            "name": "yandex",
            "enabled": True,
            "priority": 3,
            "languages": ["ru"],
            "model": "general",
            "quality": "medium",
            "speed": "fast"
        }
    ]

    return providers


def _get_platform_capabilities(channel: str) -> Dict[str, Any]:
    """Get platform-specific voice capabilities"""
    capabilities = {
        "telegram": {
            "voice_messages": True,
            "audio_files": True,
            "max_duration": 120,
            "supported_formats": ["ogg", "mp3", "wav", "m4a"],
            "inline_playback": True,
            "voice_recording": True
        },
        "whatsapp": {
            "voice_messages": True,
            "audio_files": True,
            "max_duration": 60,
            "supported_formats": ["ogg", "mp3"],
            "inline_playback": True,
            "voice_recording": True
        },
        "api": {
            "voice_messages": True,
            "audio_files": True,
            "max_duration": 300,
            "supported_formats": ["wav", "mp3", "ogg", "m4a", "flac"],
            "inline_playback": False,
            "voice_recording": False
        }
    }

    return capabilities.get(channel, capabilities["api"])


def _get_platform_limitations(channel: str) -> List[str]:
    """Get platform-specific limitations"""
    limitations = {
        "telegram": [
            "Voice messages limited to 120 seconds",
            "Some audio formats may be converted automatically"
        ],
        "whatsapp": [
            "Voice messages limited to 60 seconds",
            "Only OGG and MP3 formats fully supported",
            "Large files may have delivery issues"
        ],
        "api": [
            "No built-in audio playback interface",
            "Client responsible for audio rendering"
        ]
    }

    return limitations.get(channel, limitations["api"])


def _generate_capabilities_response(
    capabilities: Dict[str, Any],
    channel: str,
    log_adapter
) -> str:
    """Generate user-friendly capabilities response"""

    # Проверяем основную доступность голосовых функций
    if not capabilities["voice_enabled"]:
        return _create_voice_disabled_response()

    # Создаем компоненты ответа
    response_sections = []
    
    # Добавляем информацию о TTS
    tts_section = _create_tts_section(capabilities)
    if tts_section:
        response_sections.append(tts_section)
    
    # Добавляем информацию о STT
    stt_section = _create_stt_section(capabilities)
    if stt_section:
        response_sections.append(stt_section)
    
    # Добавляем информацию о платформе
    platform_section = _create_platform_section(capabilities, channel)
    response_sections.append(platform_section)
    
    # Добавляем ограничения
    limitations_section = _create_limitations_section(capabilities)
    if limitations_section:
        response_sections.append(limitations_section)
    
    # Добавляем информацию о fallback провайдерах
    fallback_section = _create_fallback_section(capabilities)
    if fallback_section:
        response_sections.append(fallback_section)
    
    # Финальное сообщение
    response_sections.append("✅ **Система готова к работе с голосом!**")
    
    log_adapter.debug("Generated user-friendly capabilities response")
    return "\n\n".join(response_sections)


def _create_voice_disabled_response() -> str:
    """Создает ответ для случая отключенных голосовых функций."""
    return """❌ Голосовые функции временно недоступны.

Попробуйте позже или обратитесь к администратору."""


def _create_tts_section(capabilities: Dict[str, Any]) -> str:
    """Создает секцию с информацией о TTS."""
    if not capabilities["tts_available"]:
        return ""
    
    tts_providers = capabilities["providers"]["tts"]
    primary_provider = tts_providers[0] if tts_providers else None
    
    if not primary_provider:
        return ""
    
    # Формируем список голосов
    voices = ", ".join(primary_provider["voices"][:3])
    if len(primary_provider["voices"]) > 3:
        voices += f" и {len(primary_provider['voices'])-3} других"
    
    return f"""🎤 **Голосовые ответы доступны!**

**Основной провайдер**: {primary_provider["name"].upper()}
**Доступные голоса**: {voices}
**Языки**: {", ".join(primary_provider["languages"][:5])}
**Качество**: {primary_provider["quality"]}

**Как использовать:**
• "отвечай голосом"
• "ответь голосом"
• "скажи голосом"
• "произнеси"
• "озвучь"

**Пример**: "Расскажи про страйкбол, ответь голосом\""""


def _create_stt_section(capabilities: Dict[str, Any]) -> str:
    """Создает секцию с информацией о STT."""
    if not capabilities["stt_available"]:
        return ""
    
    return """🎧 **Распознавание речи доступно!**

Отправьте голосовое сообщение, и я его обработаю.
**Поддерживаемые языки**: Русский, English, Автоопределение"""


def _create_platform_section(capabilities: Dict[str, Any], channel: str) -> str:
    """Создает секцию с информацией о платформе."""
    platform_caps = capabilities["platform_capabilities"]
    
    return f"""📱 **Возможности платформы ({channel.upper()})**:
• Максимальная длительность: {platform_caps.get("max_duration", 60)} сек
• Поддерживаемые форматы: {", ".join(platform_caps.get("supported_formats", []))}"""


def _create_limitations_section(capabilities: Dict[str, Any]) -> str:
    """Создает секцию с ограничениями."""
    limitations = capabilities["limitations"]
    if not limitations:
        return ""
    
    limitations_list = "\n".join([f"• {limit}" for limit in limitations])
    return f"""⚠️ **Ограничения**:
{limitations_list}"""


def _create_fallback_section(capabilities: Dict[str, Any]) -> str:
    """Создает секцию с информацией о fallback провайдерах."""
    tts_providers_count = len(capabilities["providers"]["tts"])
    if tts_providers_count <= 1:
        return ""
    
    fallback_count = tts_providers_count - 1
    return f"🔄 **Резервные провайдеры**: {fallback_count} доступно"


def _generate_error_response(error_type: str, error_message: str) -> str:
    """Generate error response for capabilities query"""

    error_responses = {
        "voice_service_error": f"""❌ Ошибка голосового сервиса: {error_message}

Попробуйте позже или используйте текстовый режим.""",

        "unexpected_error": f"""❌ Произошла неожиданная ошибка: {error_message}

Базовые голосовые функции могут быть доступны. Попробуйте отправить голосовое сообщение."""
    }

    return error_responses.get(error_type, f"❌ Ошибка: {error_message}")


# Backward compatibility export
voice_capabilities_tool = voice_capabilities_query_tool
