"""
Утилиты для работы с намерениями пользователей в голосовых сообщениях
"""

import re
from typing import List, Optional, Dict, Any
import logging


class VoiceIntentDetector:
    """
    Детектор намерений пользователя для голосовых функций
    """
    
    def __init__(self, logger: Optional[logging.Logger] = None):
        self.logger = logger or logging.getLogger("voice_intent_detector")
    
    def detect_tts_intent(self, text: str, intent_keywords: List[str]) -> bool:
        """
        Определить, нужно ли озвучивать ответ на основе ключевых слов
        
        Args:
            text: Текст сообщения пользователя
            intent_keywords: Список ключевых слов для активации TTS
            
        Returns:
            True если нужно озвучить ответ
        """
        if not text or not intent_keywords:
            return False
            
        # Приводим к нижнему регистру для поиска
        text_lower = text.lower()
        
        # Проверяем наличие ключевых слов
        for keyword in intent_keywords:
            keyword_lower = keyword.lower()
            # Используем регулярное выражение для поиска целых слов
            pattern = r'\b' + re.escape(keyword_lower) + r'\b'
            if re.search(pattern, text_lower):
                self.logger.debug(f"TTS intent detected with keyword: '{keyword}'")
                return True
                
        return False
    
    def extract_voice_settings(self, agent_config: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        Извлечь голосовые настройки из конфигурации агента
        
        Args:
            agent_config: Конфигурация агента
            
        Returns:
            Голосовые настройки или None
        """
        # Попытаемся найти настройки в разных местах конфигурации
        voice_settings = None
        
        # Сначала проверяем прямой путь
        if "voice_settings" in agent_config:
            voice_settings = agent_config["voice_settings"]
        
        # Затем проверяем в config.simple.settings
        elif "config" in agent_config:
            config = agent_config["config"]
            if "simple" in config and "settings" in config["simple"]:
                settings = config["simple"]["settings"]
                if "voice_settings" in settings:
                    voice_settings = settings["voice_settings"]
        
        return voice_settings if voice_settings and voice_settings.get("enabled", False) else None
    
    def should_auto_process_voice(self, voice_settings: Dict[str, Any]) -> bool:
        """
        Определить, нужно ли автоматически обрабатывать голосовые сообщения
        
        Args:
            voice_settings: Голосовые настройки
            
        Returns:
            True если нужно автоматически обрабатывать
        """
        return voice_settings.get("auto_stt", False)
    
    def should_auto_tts_response(self, voice_settings: Dict[str, Any], user_message: str) -> bool:
        """
        Определить, нужно ли автоматически озвучивать ответ
        
        Args:
            voice_settings: Голосовые настройки
            user_message: Сообщение пользователя
            
        Returns:
            True если нужно озвучить ответ
        """
        # Проверяем включен ли автоматический TTS
        if not voice_settings.get("auto_tts_on_keywords", False):
            return False
        
        # Получаем ключевые слова для активации TTS
        intent_keywords = voice_settings.get("intent_keywords", [])
        if not intent_keywords:
            return False
        
        # Проверяем наличие ключевых слов в сообщении
        return self.detect_tts_intent(user_message, intent_keywords)
    
    def get_primary_tts_provider(self, voice_settings: Dict[str, Any]) -> Optional[str]:
        """
        Получить основной TTS провайдер из настроек
        
        Args:
            voice_settings: Голосовые настройки
            
        Returns:
            Название провайдера или None
        """
        providers = voice_settings.get("providers", [])
        if not providers:
            return None
        
        # Ищем провайдер с наименьшим приоритетом (1 = высший приоритет)
        tts_providers = [
            p for p in providers 
            if p.get("tts_config", {}).get("enabled", False)
        ]
        
        if not tts_providers:
            return None
        
        # Сортируем по приоритету
        tts_providers.sort(key=lambda x: x.get("priority", 999))
        
        return tts_providers[0].get("provider")
    
    def get_tts_config_for_provider(self, voice_settings: Dict[str, Any], provider: str) -> Optional[Dict[str, Any]]:
        """
        Получить конфигурацию TTS для конкретного провайдера
        
        Args:
            voice_settings: Голосовые настройки
            provider: Название провайдера
            
        Returns:
            Конфигурация TTS или None
        """
        providers = voice_settings.get("providers", [])
        
        for p in providers:
            if p.get("provider") == provider:
                tts_config = p.get("tts_config", {})
                if tts_config.get("enabled", False):
                    return tts_config
        
        return None


class AgentResponseProcessor:
    """
    Процессор ответов агентов для добавления TTS
    """
    
    def __init__(self, intent_detector: VoiceIntentDetector, logger: Optional[logging.Logger] = None):
        self.intent_detector = intent_detector
        self.logger = logger or logging.getLogger("agent_response_processor")
    
    async def process_agent_response(self, 
                                   agent_response: str,
                                   user_message: str,
                                   agent_config: Dict[str, Any],
                                   user_id: str,
                                   platform: str) -> Dict[str, Any]:
        """
        Обработать ответ агента и добавить TTS если необходимо
        
        Args:
            agent_response: Ответ агента
            user_message: Исходное сообщение пользователя
            agent_config: Конфигурация агента
            user_id: ID пользователя
            platform: Платформа (telegram, whatsapp)
            
        Returns:
            Словарь с обработанным ответом и аудиоданными (если есть)
        """
        result = {
            "text": agent_response,
            "audio_data": None,
            "has_audio": False,
            "tts_provider": None,
            "metadata": {}
        }
        
        try:
            # Извлекаем голосовые настройки
            voice_settings = self.intent_detector.extract_voice_settings(agent_config)
            if not voice_settings:
                self.logger.debug("No voice settings found in agent config")
                return result
            
            # Проверяем, нужно ли озвучивать ответ
            should_tts = self.intent_detector.should_auto_tts_response(voice_settings, user_message)
            if not should_tts:
                self.logger.debug("TTS not required for this response")
                return result
            
            # Получаем TTS провайдер
            tts_provider = self.intent_detector.get_primary_tts_provider(voice_settings)
            if not tts_provider:
                self.logger.warning("No TTS provider configured")
                return result
            
            # Получаем конфигурацию TTS
            tts_config = self.intent_detector.get_tts_config_for_provider(voice_settings, tts_provider)
            if not tts_config:
                self.logger.warning(f"No TTS config found for provider {tts_provider}")
                return result
            
            # Генерируем аудио
            audio_data = await self._generate_tts_audio(
                agent_response, tts_provider, tts_config, user_id, voice_settings
            )
            
            if audio_data:
                result.update({
                    "audio_data": audio_data,
                    "has_audio": True,
                    "tts_provider": tts_provider,
                    "metadata": {
                        "tts_provider": tts_provider,
                        "voice": tts_config.get("voice"),
                        "language": tts_config.get("language"),
                        "triggered_by_keywords": True
                    }
                })
                self.logger.info(f"TTS audio generated for user {user_id} using {tts_provider}")
            
        except Exception as e:
            self.logger.error(f"Error processing agent response for TTS: {e}", exc_info=True)
        
        return result
    
    async def _generate_tts_audio(self, 
                                text: str, 
                                provider: str, 
                                tts_config: Dict[str, Any],
                                user_id: str,
                                voice_settings: Dict[str, Any]) -> Optional[bytes]:
        """
        Генерация TTS аудио
        
        Args:
            text: Текст для озвучивания
            provider: TTS провайдер
            tts_config: Конфигурация TTS
            user_id: ID пользователя
            voice_settings: Общие голосовые настройки
            
        Returns:
            Аудиоданные или None
        """
        try:
            # Import orchestrator here to avoid circular imports
            from app.services.voice.voice_orchestrator import VoiceServiceOrchestrator
            from app.services.redis_wrapper import RedisService
            
            # Initialize orchestrator
            redis_service = RedisService()
            await redis_service.initialize()
            
            orchestrator = VoiceServiceOrchestrator(redis_service, self.logger)
            await orchestrator.initialize()
            
            # Initialize services for this agent config
            agent_config = {"voice_settings": voice_settings}
            await orchestrator.initialize_voice_services_for_agent("temp_agent", agent_config)
            
            # Generate TTS
            result = await orchestrator.process_tts(
                agent_id="temp_agent",
                user_id=user_id,
                text=text,
                agent_config=agent_config,
                preferred_provider=provider
            )
            
            # Cleanup
            await orchestrator.cleanup()
            await redis_service.cleanup()
            
            return result.audio_data if result else None
            
        except Exception as e:
            self.logger.error(f"Error generating TTS audio: {e}", exc_info=True)
            return None
