"""
LangGraph Native TTS Tool - Real Audio Generation Implementation

Implements actual audio file generation using voice_v2 providers with MinIO storage.
Uses Command pattern for proper state updates in LangGraph.
"""

import logging
import uuid
from typing import Optional, Dict, Any, Annotated

from langchain_core.tools import tool, InjectedToolCallId
from langchain_core.messages import ToolMessage
from langgraph.prebuilt import InjectedState
from langgraph.types import Command
from pydantic import BaseModel, Field

from app.core.config import settings
from app.services.voice_v2.providers.unified_factory import VoiceProviderFactory
from app.services.voice_v2.infrastructure.minio_manager import MinioFileManager
from app.services.voice_v2.core.interfaces import AudioFormat

logger = logging.getLogger(__name__)


class VoiceSettings(BaseModel):
    """Voice generation settings."""
    voice_id: Optional[str] = Field(default=None, description="Specific voice ID to use")
    speed: Optional[float] = Field(default=1.0, description="Speech speed (0.5-2.0)")
    stability: Optional[float] = Field(default=0.7, description="Voice stability (0.0-1.0)")
    provider: Optional[str] = Field(default=None, description="TTS provider to use")


@tool
async def generate_voice_response(
    text: Annotated[str, "Text to convert to speech"],
    state: Annotated[Dict, InjectedState] = None,
    tool_call_id: Annotated[str, InjectedToolCallId] = None
) -> Command:
    """
    Генерирует голосовой ответ из текста с реальным созданием аудио файла.
    Обновляет состояние агента с audio_url для передачи в интеграции.

    Args:
        text: Текст для синтеза в голосовое сообщение
        state: Состояние агента с контекстом
        tool_call_id: ID вызова tool для ToolMessage

    Returns:
        Command объект для обновления состояния агента
    """

    logger.debug("TTS Tool: получен запрос на синтез текста длиной %d символов", len(text))

    try:
        # Валидация входных данных
        if not text or not text.strip():
            return Command(update={
                "messages": [ToolMessage(
                    content="Ошибка: Пустой текст для синтеза голоса",
                    tool_call_id=tool_call_id
                )]
            })

        # Валидация состояния агента
        validation_result = _validate_agent_state(state)
        if not validation_result["valid"]:
            return Command(update={
                "messages": [ToolMessage(
                    content=f"Ошибка валидации: {validation_result['error']}",
                    tool_call_id=tool_call_id
                )]
            })

        agent_id = validation_result["agent_id"]
        chat_id = validation_result["chat_id"]
        user_data = validation_result["user_data"]
        user_id = user_data.get("platform_user_id", "unknown")

        # Валидация текста
        text_validation = _validate_synthesis_text(text)
        if not text_validation["valid"]:
            return Command(update={
                "messages": [ToolMessage(
                    content=f"Ошибка текста: {text_validation['error']}",
                    tool_call_id=tool_call_id
                )]
            })

        # Генерация реального аудио файла
        audio_result = await _generate_audio_file(
            text=text.strip(),
            agent_id=agent_id,
            user_id=user_id,
            chat_id=chat_id
        )

        if not audio_result["success"]:
            return Command(update={
                "messages": [ToolMessage(
                    content=f"Ошибка генерации аудио: {audio_result['error']}",
                    tool_call_id=tool_call_id
                )]
            })

        # Успешно сгенерировали аудио - обновляем состояние агента
        logger.info(f"TTS Tool: аудио файл создан - {audio_result['voice_url']}")
        
        # Обновляем состояние с audio_url и уведомляем пользователя
        return Command(update={
            "audio_url": audio_result["voice_url"],  # Добавляем audio_url в состояние
            "messages": [ToolMessage(
                content=f"Голосовой ответ готов: {text.strip()}",
                tool_call_id=tool_call_id
            )]
        })

    except Exception as e:
        logger.error("TTS Tool: неожиданная ошибка - %s", e, exc_info=True)
        return Command(update={
            "messages": [ToolMessage(
                content=f"Критическая ошибка TTS: {str(e)}",
                tool_call_id=tool_call_id
            )]
        })


async def _generate_audio_file(text: str, agent_id: str, user_id: str, chat_id: str) -> Dict[str, Any]:
    """
    Генерирует реальный аудио файл используя voice providers.
    
    Args:
        text: Текст для синтеза
        agent_id: ID агента
        user_id: ID пользователя
        chat_id: ID чата
        
    Returns:
        Dict с результатом генерации
    """
    try:
        # Создаем фабрику провайдеров
        factory = VoiceProviderFactory()
        
        # Получаем TTS провайдер (с fallback)
        tts_provider = None
        provider_name = None
        
        # Импортируем ProviderType для правильного использования
        from app.services.voice_v2.core.interfaces import ProviderType
        
        # Пробуем провайдеры по приоритету
        provider_types = [ProviderType.OPENAI, ProviderType.YANDEX, ProviderType.GOOGLE]
        for provider_type in provider_types:
            try:
                tts_provider = await factory.create_tts_provider(provider_type)
                provider_name = provider_type.value
                logger.debug(f"TTS Tool: используем провайдер {provider_name}")
                break
            except Exception as e:
                logger.warning(f"TTS Tool: провайдер {provider_type.value} недоступен: {e}")
                continue
        
        if not tts_provider:
            return {
                "success": False,
                "error": "Все TTS провайдеры недоступны"
            }

        # Синтез аудио
        logger.debug(f"TTS Tool: начинаем синтез с провайдером {provider_name}")
        
        # Создаем TTS запрос
        from app.services.voice_v2.core.schemas import TTSRequest
        
        # Конвертируем язык для OpenAI (ru-RU -> ru)
        language = settings.VOICE_DEFAULT_LANGUAGE
        if language == "ru-RU":
            language = "ru"  # OpenAI использует короткие коды языков
        
        tts_request = TTSRequest(
            text=text,
            language=language,
            voice=None,  # Будет использован дефолтный голос
            speed=1.0,
            format=AudioFormat.MP3
        )
        
        # Вызываем синтез речи асинхронно
        tts_result = await tts_provider.synthesize_speech(tts_request)
        
        if not tts_result.audio_data and not tts_result.audio_url:
            return {
                "success": False,
                "error": f"Провайдер {provider_name} не смог синтезировать аудио",
                "provider_used": provider_name
            }
        
        # Если есть audio_url (уже загружено), используем его
        if tts_result.audio_url:
            logger.info(f"TTS Tool: аудио файл уже создан - {tts_result.audio_url}")
            return {
                "success": True,
                "voice_url": tts_result.audio_url,
                "file_info": {
                    "duration_seconds": tts_result.audio_duration,
                    "text_length": tts_result.text_length,
                    "voice_used": tts_result.voice_used,
                    "language_used": tts_result.language_used
                },
                "provider_used": provider_name,
                "duration_seconds": tts_result.audio_duration
            }
        
        # Если есть только audio_data, загружаем в MinIO через TTS tool
        audio_data = tts_result.audio_data

        # Загружаем в MinIO
        logger.debug("TTS Tool: загружаем аудио в MinIO")
        minio_manager = MinioFileManager(
            endpoint=settings.MINIO_ENDPOINT,
            access_key=settings.MINIO_ACCESS_KEY,
            secret_key=settings.MINIO_SECRET_KEY,
            bucket_name=settings.MINIO_VOICE_BUCKET_NAME,
            secure=settings.MINIO_SECURE
        )
        await minio_manager.initialize()
        
        # Генерируем уникальное имя файла
        filename = f"tts_response_{uuid.uuid4().hex[:8]}.mp3"
        
        # Загрузка файла
        file_info = await minio_manager.upload_audio_file(
            audio_data=audio_data,
            agent_id=agent_id,
            user_id=user_id,
            original_filename=filename,
            mime_type="audio/mpeg",
            audio_format=None,  # MinIO manager использует другой enum
            metadata={
                "chat_id": chat_id,
                "synthesis_text": text[:100],  # Первые 100 символов
                "provider": provider_name,
                "tool": "tts_tool"
            }
        )
        
        # Получаем presigned URL
        voice_url = await minio_manager.get_file_url(file_info, expiry_hours=24)
        
        logger.info(f"TTS Tool: аудио файл создан - {file_info.minio_key}")
        
        return {
            "success": True,
            "voice_url": voice_url,
            "file_info": {
                "file_id": file_info.file_id,
                "filename": file_info.original_filename,
                "size_bytes": file_info.size_bytes,
                "mime_type": file_info.mime_type,
                "duration_seconds": file_info.duration_seconds
            },
            "provider_used": provider_name,
            "duration_seconds": file_info.duration_seconds
        }
        
    except Exception as e:
        logger.error(f"TTS Tool: ошибка генерации аудио - {e}", exc_info=True)
        return {
            "success": False,
            "error": f"Ошибка генерации аудио: {str(e)}",
            "provider_used": provider_name
        }


def _validate_agent_state(state: Dict) -> Dict[str, Any]:
    """Валидирует состояние агента для синтеза речи."""
    if not state:
        logger.error("TTS Tool: отсутствует состояние агента")
        return {
            "valid": False,
            "error": "Отсутствует контекст агента"
        }

    # Подробное логирование состояния для отладки
    logger.debug(f"TTS Tool: получено состояние агента: {list(state.keys())}")
    
    user_data = state.get("user_data", {})
    channel = state.get("channel", "")
    
    # Извлекаем agent_id и chat_id из различных источников
    agent_id = None
    chat_id = None
    
    # Вариант 1: напрямую из состояния (приоритетный)
    chat_id = state.get("chat_id")
    platform_user_id = state.get("platform_user_id")
    agent_id = state.get("agent_id")
    
    # Вариант 2: из user_data
    if isinstance(user_data, dict):
        if not agent_id:
            agent_id = user_data.get("agent_id")
        if not chat_id:
            chat_id = user_data.get("chat_id")
        if not platform_user_id:
            platform_user_id = user_data.get("platform_user_id") or user_data.get("user_id")
    
    # Вариант 3: из контекста LangGraph (messages могут содержать thread_id)
    messages = state.get("messages", [])
    if messages and hasattr(messages[-1], 'additional_kwargs'):
        # Попробуем извлечь из последнего сообщения
        additional_kwargs = getattr(messages[-1], 'additional_kwargs', {})
        if not chat_id:
            chat_id = additional_kwargs.get('thread_id') or additional_kwargs.get('chat_id')
    
    # Вариант 4: используем известный agent_id (это временное решение)
    if not agent_id:
        agent_id = "agent_airsoft_0faa9616"  # Активный агент
        logger.warning(f"TTS Tool: используем известный agent_id: {agent_id}")
    
    # Создаем chat_id если его нет
    if not chat_id:
        chat_id = f"voice_chat_{uuid.uuid4().hex[:8]}"
        logger.warning(f"TTS Tool: создан временный chat_id: {chat_id}")

    logger.info(f"TTS Tool: валидация успешна - agent_id: {agent_id}, chat_id: {chat_id}, channel: {channel}")

    return {
        "valid": True,
        "chat_id": str(chat_id),
        "agent_id": agent_id,
        "user_data": user_data
    }


def _validate_synthesis_text(text: str) -> Dict[str, Any]:
    """Валидирует текст для синтеза речи."""
    if not text or not text.strip():
        logger.warning("TTS Tool: пустой текст для синтеза")
        return {
            "valid": False,
            "error": "Пустой текст для синтеза"
        }

    if len(text) > 4000:  # Ограничение для TTS
        logger.warning("TTS Tool: слишком длинный текст (%d символов)", len(text))
        return {
            "valid": False,
            "error": f"Текст слишком длинный ({len(text)} символов, максимум 4000)"
        }

    return {"valid": True}


# Export for registry
__all__ = ['generate_voice_response']
