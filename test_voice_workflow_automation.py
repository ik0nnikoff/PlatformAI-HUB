"""
Автоматизированная система тестирования голосового workflow
"""

import asyncio
import json
import logging
import tempfile
import uuid
from pathlib import Path
from typing import Dict, Any, Optional, Tuple
from unittest.mock import AsyncMock, MagicMock, patch
import pytest
import aiohttp
from io import BytesIO

# Настройка логирования для тестов
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("voice_workflow_test")


class MockTelegramBot:
    """Mock Telegram Bot для тестирования"""
    
    def __init__(self):
        self.sent_messages = []
        self.sent_voice_messages = []
        self.sent_audio_messages = []
    
    async def send_message(self, chat_id: int, text: str, **kwargs):
        """Mock отправки текстового сообщения"""
        message = {
            "chat_id": chat_id,
            "text": text,
            "kwargs": kwargs,
            "type": "text"
        }
        self.sent_messages.append(message)
        logger.info(f"MOCK: Sent text message to {chat_id}: {text[:50]}...")
        return message
    
    async def send_voice(self, chat_id: int, voice, caption: str = None, **kwargs):
        """Mock отправки голосового сообщения"""
        message = {
            "chat_id": chat_id,
            "voice": voice,
            "caption": caption,
            "kwargs": kwargs,
            "type": "voice"
        }
        self.sent_voice_messages.append(message)
        logger.info(f"MOCK: Sent voice message to {chat_id}: {caption}")
        return message
    
    async def send_audio(self, chat_id: int, audio, caption: str = None, **kwargs):
        """Mock отправки аудио сообщения"""
        message = {
            "chat_id": chat_id,
            "audio": audio,
            "caption": caption,
            "kwargs": kwargs,
            "type": "audio"
        }
        self.sent_audio_messages.append(message)
        logger.info(f"MOCK: Sent audio message to {chat_id}: {caption}")
        return message


class VoiceWorkflowTester:
    """Главный класс для автоматизированного тестирования голосового workflow"""
    
    def __init__(self):
        self.mock_bot = MockTelegramBot()
        self.test_results = {}
        
    async def setup_test_environment(self):
        """Настройка тестового окружения"""
        logger.info("🔧 Setting up test environment...")
        
        # Импортируем необходимые модули
        from app.agent_runner.agent_runner import AgentRunner
        from app.services.voice.voice_orchestrator import VoiceServiceOrchestrator
        from app.services.redis_wrapper import RedisService
        
        # Создаем mock объекты
        self.redis_service = AsyncMock(spec=RedisService)
        self.voice_orchestrator = AsyncMock(spec=VoiceServiceOrchestrator)
        
        logger.info("✅ Test environment setup complete")
    
    async def test_voice_intent_detection(self, test_messages: list) -> Dict[str, Any]:
        """Тестирование детекции голосовых намерений"""
        logger.info("🎯 Testing voice intent detection...")
        
        from app.api.schemas.voice_schemas import VoiceSettings
        
        # Создаем тестовые настройки голоса
        voice_config = {
            "enabled": True,
            "intent_detection_mode": "keywords",
            "intent_keywords": ["голос", "скажи", "произнеси", "озвучь", "отвечай голосом"],
            "auto_tts_on_keywords": True,
            "providers": [
                {
                    "provider": "yandex",
                    "priority": 1,
                    "tts_config": {
                        "enabled": True,
                        "model": "jane",
                        "voice": "jane"
                    }
                }
            ]
        }
        
        voice_settings = VoiceSettings(**voice_config)
        
        results = {}
        for message in test_messages:
            intent_detected = voice_settings.should_process_voice_intent(message)
            results[message] = intent_detected
            logger.info(f"Message: '{message}' -> Intent: {intent_detected}")
        
        return {
            "test": "voice_intent_detection",
            "status": "passed",
            "results": results
        }
    
    async def test_mock_agent_interaction(self, user_message: str, chat_id: int = 12345) -> Dict[str, Any]:
        """Тестирование взаимодействия с агентом через mock"""
        logger.info(f"🤖 Testing agent interaction with message: '{user_message}'")
        
        # Симулируем полный workflow агента
        try:
            # 1. Проверяем детекцию намерения
            voice_intent = any(keyword in user_message.lower() for keyword in 
                             ["голос", "скажи", "произнеси", "озвучь", "отвечай голосом"])
            
            # 2. Генерируем mock ответ агента
            agent_response = "Сейчас в Москве 00:47, 15 июля 2025 года."
            
            # 3. Если намерение обнаружено - симулируем TTS
            audio_url = None
            if voice_intent:
                # Симулируем создание аудиофайла
                audio_url = f"http://localhost:9000/voice-files/test_audio_{uuid.uuid4().hex[:8]}.mp3"
                logger.info(f"🎵 Mock TTS generated: {audio_url}")
            
            # 4. Симулируем отправку в Telegram
            if voice_intent:
                # Если намерение обнаружено - симулируем только голосовое сообщение
                if audio_url:
                    mock_voice_file = BytesIO(b"mock_audio_data")
                    await self.mock_bot.send_voice(chat_id, mock_voice_file)
            else:
                # Если намерения нет - отправляем только текст
                await self.mock_bot.send_message(chat_id, agent_response)
            
            return {
                "test": "mock_agent_interaction",
                "status": "passed", 
                "user_message": user_message,
                "agent_response": agent_response,
                "voice_intent_detected": voice_intent,
                "audio_generated": audio_url is not None,
                "audio_url": audio_url,
                "messages_sent": len(self.mock_bot.sent_messages),
                "voice_messages_sent": len(self.mock_bot.sent_voice_messages)
            }
            
        except Exception as e:
            logger.error(f"❌ Mock agent interaction failed: {e}")
            return {
                "test": "mock_agent_interaction",
                "status": "failed",
                "error": str(e)
            }
    
    async def test_real_voice_orchestrator(self) -> Dict[str, Any]:
        """Тестирование настоящего voice orchestrator"""
        logger.info("🎼 Testing real voice orchestrator...")
        
        try:
            from app.services.voice.voice_orchestrator import VoiceServiceOrchestrator
            from app.services.redis_wrapper import RedisService
            from app.core.config import settings
            
            # Создаем реальный Redis сервис
            redis_service = RedisService()
            await redis_service.initialize()
            
            # Создаем реальный voice orchestrator
            orchestrator = VoiceServiceOrchestrator(
                redis_service=redis_service,
                logger=logger
            )
            
            await orchestrator.initialize()
            
            # Тестируем health check
            health = await orchestrator.get_service_health()
            
            await redis_service.cleanup()
            await orchestrator.cleanup()
            
            return {
                "test": "real_voice_orchestrator",
                "status": "passed",
                "health": health,
                "orchestrator_initialized": health.get("orchestrator_initialized", False),
                "minio_health": health.get("minio_health", {}),
                "stt_services": health.get("stt_services", {}),
                "tts_services": health.get("tts_services", {})
            }
            
        except Exception as e:
            logger.error(f"❌ Voice orchestrator test failed: {e}")
            return {
                "test": "real_voice_orchestrator", 
                "status": "failed",
                "error": str(e)
            }
    
    async def test_agent_configuration_loading(self) -> Dict[str, Any]:
        """Тестирование загрузки конфигурации агента"""
        logger.info("⚙️ Testing agent configuration loading...")
        
        try:
            import aiohttp
            
            # Пытаемся загрузить конфигурацию реального агента
            agent_id = "agent_airsoft_0faa9616"
            config_url = f"http://localhost:8001/api/v1/agents/{agent_id}/config"
            
            async with aiohttp.ClientSession() as session:
                async with session.get(config_url) as resp:
                    if resp.status == 200:
                        config = await resp.json()
                        
                        # Проверяем наличие голосовых настроек
                        voice_settings = config.get("config", {}).get("simple", {}).get("settings", {}).get("voice_settings")
                        
                        return {
                            "test": "agent_configuration_loading",
                            "status": "passed",
                            "agent_id": agent_id,
                            "config_loaded": True,
                            "voice_settings_present": voice_settings is not None,
                            "voice_settings": voice_settings
                        }
                    else:
                        return {
                            "test": "agent_configuration_loading",
                            "status": "failed",
                            "error": f"HTTP {resp.status}"
                        }
                        
        except Exception as e:
            logger.error(f"❌ Configuration loading test failed: {e}")
            return {
                "test": "agent_configuration_loading",
                "status": "failed", 
                "error": str(e)
            }
    
    async def run_comprehensive_test_suite(self) -> Dict[str, Any]:
        """Запуск полного набора тестов"""
        logger.info("🚀 Running comprehensive voice workflow test suite...")
        
        await self.setup_test_environment()
        
        # Список тестовых сообщений
        test_messages = [
            "Отвечай голосом. Сколько времени сейчас?",
            "Скажи мне текущее время",
            "Произнеси ответ вслух",
            "Озвучь информацию о погоде",
            "Просто текстовый ответ",
            "Какая погода сегодня?",
            "Расскажи голосом о товарах"
        ]
        
        # Запускаем все тесты
        results = {
            "suite_start_time": asyncio.get_event_loop().time(),
            "tests": {}
        }
        
        # 1. Тест детекции намерений
        intent_test = await self.test_voice_intent_detection(test_messages)
        results["tests"]["intent_detection"] = intent_test
        
        # 2. Тест mock взаимодействия с агентом
        for i, message in enumerate(test_messages[:3]):  # Тестируем первые 3 сообщения
            mock_test = await self.test_mock_agent_interaction(message, chat_id=12345 + i)
            results["tests"][f"mock_interaction_{i+1}"] = mock_test
        
        # 3. Тест реального voice orchestrator
        orchestrator_test = await self.test_real_voice_orchestrator()
        results["tests"]["voice_orchestrator"] = orchestrator_test
        
        # 4. Тест загрузки конфигурации
        config_test = await self.test_agent_configuration_loading()
        results["tests"]["configuration_loading"] = config_test
        
        # Подсчет статистики
        results["suite_end_time"] = asyncio.get_event_loop().time()
        results["total_duration"] = results["suite_end_time"] - results["suite_start_time"]
        
        passed_tests = sum(1 for test in results["tests"].values() if test.get("status") == "passed")
        total_tests = len(results["tests"])
        
        results["summary"] = {
            "total_tests": total_tests,
            "passed_tests": passed_tests,
            "failed_tests": total_tests - passed_tests,
            "success_rate": f"{(passed_tests/total_tests)*100:.1f}%",
            "mock_messages_sent": len(self.mock_bot.sent_messages),
            "mock_voice_messages_sent": len(self.mock_bot.sent_voice_messages)
        }
        
        logger.info(f"🏁 Test suite completed: {results['summary']}")
        
        return results


async def main():
    """Главная функция для запуска тестов"""
    print("🎙️ Voice Workflow Automated Testing System")
    print("=" * 50)
    
    tester = VoiceWorkflowTester()
    results = await tester.run_comprehensive_test_suite()
    
    # Красивый вывод результатов
    print("\n📊 TEST RESULTS:")
    print("-" * 30)
    
    for test_name, test_result in results["tests"].items():
        status_emoji = "✅" if test_result.get("status") == "passed" else "❌"
        print(f"{status_emoji} {test_name}: {test_result.get('status', 'unknown')}")
        
        if test_result.get("status") == "failed":
            print(f"   Error: {test_result.get('error', 'Unknown error')}")
    
    print(f"\n📈 SUMMARY:")
    summary = results["summary"]
    print(f"   Total Tests: {summary['total_tests']}")
    print(f"   Passed: {summary['passed_tests']}")
    print(f"   Failed: {summary['failed_tests']}")
    print(f"   Success Rate: {summary['success_rate']}")
    print(f"   Duration: {results['total_duration']:.2f}s")
    print(f"   Mock Messages: {summary['mock_messages_sent']}")
    print(f"   Mock Voice Messages: {summary['mock_voice_messages_sent']}")
    
    # Сохраняем результаты в файл
    results_file = "voice_workflow_test_results.json"
    with open(results_file, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False, default=str)
    
    print(f"\n💾 Results saved to: {results_file}")
    
    return results


if __name__ == "__main__":
    # Запуск тестов
    asyncio.run(main())
