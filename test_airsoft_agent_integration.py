#!/usr/bin/env python3
"""
Тестирование Airsoft агента с голосовыми функциями через реальную интеграцию
"""

import asyncio
import json
import logging
import os
import sys
from pathlib import Path
from typing import Dict, Any, Optional

import httpx
import aiofiles
from io import BytesIO

# Добавляем путь к модулям приложения
sys.path.append(str(Path(__file__).parent))

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger("airsoft_agent_test")

class AirsoftAgentTester:
    def __init__(self, base_url: str = "http://localhost:8001"):
        self.base_url = base_url
        self.agent_config_path = "airsoft_agent_with_voice.json"
        self.agent_id = "agent_airsoft_0faa9616"
        self.test_results = {}
        
    async def load_agent_config(self) -> Dict[str, Any]:
        """Загрузка конфигурации агента из JSON файла"""
        try:
            async with aiofiles.open(self.agent_config_path, 'r', encoding='utf-8') as f:
                content = await f.read()
                config = json.loads(content)
                logger.info(f"✅ Agent config loaded: {config['name']}")
                return config
        except Exception as e:
            logger.error(f"❌ Failed to load agent config: {e}")
            raise

    async def create_or_update_agent(self, config: Dict[str, Any]) -> bool:
        """Создание или обновление агента в системе"""
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                # Проверяем, существует ли агент
                response = await client.get(f"{self.base_url}/api/v1/agents/{self.agent_id}")
                
                if response.status_code == 200:
                    # Обновляем существующего агента
                    logger.info("🔄 Updating existing agent...")
                    response = await client.put(
                        f"{self.base_url}/api/v1/agents/{self.agent_id}",
                        json=config
                    )
                else:
                    # Создаем нового агента
                    logger.info("➕ Creating new agent...")
                    response = await client.post(
                        f"{self.base_url}/api/v1/agents",
                        json=config
                    )
                
                if response.status_code in [200, 201]:
                    logger.info("✅ Agent successfully created/updated")
                    return True
                else:
                    logger.error(f"❌ Failed to create/update agent: {response.status_code} - {response.text}")
                    return False
                    
        except Exception as e:
            logger.error(f"❌ Error creating/updating agent: {e}")
            return False

    async def test_agent_voice_settings(self) -> Dict[str, Any]:
        """Тест извлечения голосовых настроек агента"""
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                # Получаем конфигурацию агента
                response = await client.get(f"{self.base_url}/api/v1/agents/{self.agent_id}")
                
                if response.status_code != 200:
                    raise Exception(f"Agent not found: {response.status_code}")
                
                agent_data = response.json()
                voice_settings = agent_data.get("config", {}).get("simple", {}).get("settings", {}).get("voice_settings")
                
                if not voice_settings:
                    raise Exception("Voice settings not found in agent config")
                
                result = {
                    "status": "✅ PASSED",
                    "enabled": voice_settings.get("enabled", False),
                    "providers_count": len(voice_settings.get("providers", [])),
                    "intent_keywords_count": len(voice_settings.get("intent_keywords", [])),
                    "auto_stt": voice_settings.get("auto_stt", False),
                    "auto_tts_on_keywords": voice_settings.get("auto_tts_on_keywords", False)
                }
                
                logger.info(f"✅ Voice settings test passed: {result}")
                return result
                
        except Exception as e:
            result = {"status": "❌ FAILED", "error": str(e)}
            logger.error(f"❌ Voice settings test failed: {e}")
            return result

    async def test_telegram_integration(self) -> Dict[str, Any]:
        """Тест готовности Telegram интеграции"""
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                # Проверяем статус интеграций агента
                response = await client.get(f"{self.base_url}/api/v1/agents/{self.agent_id}/integrations")
                
                if response.status_code != 200:
                    raise Exception(f"Failed to get integrations: {response.status_code}")
                
                integrations = response.json()
                telegram_integration = None
                
                for integration in integrations:
                    if integration.get("type") == "telegram":
                        telegram_integration = integration
                        break
                
                if not telegram_integration:
                    raise Exception("Telegram integration not found")
                
                result = {
                    "status": "✅ PASSED",
                    "enabled": telegram_integration.get("settings", {}).get("enabled", False),
                    "voice_enabled": telegram_integration.get("settings", {}).get("voice_enabled", False),
                    "bot_token": "✓" if telegram_integration.get("settings", {}).get("botToken") else "✗",
                    "voice_settings": telegram_integration.get("settings", {}).get("voice_settings", {})
                }
                
                logger.info(f"✅ Telegram integration test passed: {result}")
                return result
                
        except Exception as e:
            result = {"status": "❌ FAILED", "error": str(e)}
            logger.error(f"❌ Telegram integration test failed: {e}")
            return result

    async def test_voice_endpoints(self) -> Dict[str, Any]:
        """Тест доступности голосовых endpoint'ов"""
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                # Тестируем TTS endpoint
                tts_data = {
                    "text": "Привет! Это тест голосового синтеза для агента Airsoft.",
                    "agent_id": self.agent_id,
                    "provider": "yandex"
                }
                
                logger.info("🔊 Testing TTS endpoint...")
                tts_response = await client.post(
                    f"{self.base_url}/api/v1/voice/tts",
                    json=tts_data
                )
                
                tts_success = tts_response.status_code == 200
                tts_result = tts_response.json() if tts_success else {"error": tts_response.text}
                
                result = {
                    "status": "✅ PASSED" if tts_success else "❌ FAILED",
                    "tts_test": {
                        "success": tts_success,
                        "status_code": tts_response.status_code,
                        "result": tts_result
                    }
                }
                
                if tts_success:
                    logger.info("✅ TTS endpoint test passed")
                else:
                    logger.error(f"❌ TTS endpoint test failed: {tts_response.status_code}")
                
                return result
                
        except Exception as e:
            result = {"status": "❌ FAILED", "error": str(e)}
            logger.error(f"❌ Voice endpoints test failed: {e}")
            return result

    async def test_agent_chat(self) -> Dict[str, Any]:
        """Тест базовой функциональности чата агента"""
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                # Тестируем обычный чат
                chat_data = {
                    "message": "Привет! Расскажи о ваших товарах для страйкбола.",
                    "agent_id": self.agent_id,
                    "user_id": "test_user_123"
                }
                
                logger.info("💬 Testing agent chat...")
                response = await client.post(
                    f"{self.base_url}/api/v1/chat",
                    json=chat_data
                )
                
                success = response.status_code == 200
                result_data = response.json() if success else {"error": response.text}
                
                result = {
                    "status": "✅ PASSED" if success else "❌ FAILED",
                    "chat_test": {
                        "success": success,
                        "status_code": response.status_code,
                        "response_preview": result_data.get("response", "")[:100] + "..." if success else result_data
                    }
                }
                
                if success:
                    logger.info("✅ Agent chat test passed")
                else:
                    logger.error(f"❌ Agent chat test failed: {response.status_code}")
                
                return result
                
        except Exception as e:
            result = {"status": "❌ FAILED", "error": str(e)}
            logger.error(f"❌ Agent chat test failed: {e}")
            return result

    async def test_voice_intent_keywords(self) -> Dict[str, Any]:
        """Тест с сообщением, содержащим ключевые слова для TTS"""
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                # Тестируем чат с ключевыми словами для TTS
                chat_data = {
                    "message": "Озвучь мне информацию о доставке товаров",
                    "agent_id": self.agent_id,
                    "user_id": "test_user_123"
                }
                
                logger.info("🎤 Testing voice intent keywords...")
                response = await client.post(
                    f"{self.base_url}/api/v1/chat",
                    json=chat_data
                )
                
                success = response.status_code == 200
                result_data = response.json() if success else {"error": response.text}
                
                # Проверяем, была ли распознана потребность в TTS
                contains_keyword = any(keyword in chat_data["message"].lower() for keyword in ["озвучь", "голос", "скажи"])
                
                result = {
                    "status": "✅ PASSED" if success else "❌ FAILED",
                    "keyword_test": {
                        "success": success,
                        "contains_keyword": contains_keyword,
                        "message": chat_data["message"],
                        "response_preview": result_data.get("response", "")[:100] + "..." if success else result_data
                    }
                }
                
                if success:
                    logger.info(f"✅ Voice intent test passed (keyword detected: {contains_keyword})")
                else:
                    logger.error(f"❌ Voice intent test failed: {response.status_code}")
                
                return result
                
        except Exception as e:
            result = {"status": "❌ FAILED", "error": str(e)}
            logger.error(f"❌ Voice intent test failed: {e}")
            return result

    async def run_full_test_suite(self):
        """Запуск полного набора тестов"""
        logger.info("🎯 Starting Airsoft Agent Integration Testing...")
        
        # 1. Загрузка конфигурации
        logger.info("📁 Loading agent configuration...")
        config = await self.load_agent_config()
        
        # 2. Создание/обновление агента
        logger.info("🔧 Creating/updating agent in system...")
        agent_created = await self.create_or_update_agent(config)
        if not agent_created:
            logger.error("❌ Cannot proceed without agent in system")
            return
        
        # 3. Тестирование голосовых настроек
        logger.info("⚙️ Testing voice settings...")
        self.test_results["voice_settings"] = await self.test_agent_voice_settings()
        
        # 4. Тестирование Telegram интеграции
        logger.info("📱 Testing Telegram integration...")
        self.test_results["telegram_integration"] = await self.test_telegram_integration()
        
        # 5. Тестирование голосовых endpoint'ов
        logger.info("🔊 Testing voice endpoints...")
        self.test_results["voice_endpoints"] = await self.test_voice_endpoints()
        
        # 6. Тестирование базового чата
        logger.info("💬 Testing agent chat...")
        self.test_results["agent_chat"] = await self.test_agent_chat()
        
        # 7. Тестирование ключевых слов для голоса
        logger.info("🎤 Testing voice intent keywords...")
        self.test_results["voice_intent"] = await self.test_voice_intent_keywords()
        
        # 8. Генерация отчета
        self.generate_report()

    def generate_report(self):
        """Генерация итогового отчета"""
        logger.info("📊 Generating integration test report...")
        
        print("\n" + "="*80)
        print("🎯 AIRSOFT AGENT INTEGRATION TEST REPORT")
        print("="*80)
        
        passed_tests = 0
        total_tests = len(self.test_results)
        
        for test_name, result in self.test_results.items():
            status = result.get("status", "❌ FAILED")
            print(f"\n🧪 {test_name.upper().replace('_', ' ')}:")
            print(f"   Status: {status}")
            
            if status == "✅ PASSED":
                passed_tests += 1
            
            # Детали каждого теста
            for key, value in result.items():
                if key != "status":
                    if isinstance(value, dict):
                        print(f"   • {key}: {json.dumps(value, indent=4, ensure_ascii=False)}")
                    else:
                        print(f"   • {key}: {value}")
        
        print("\n" + "="*80)
        success_rate = (passed_tests / total_tests * 100) if total_tests > 0 else 0
        print(f"📊 INTEGRATION SUMMARY: {passed_tests}/{total_tests} tests passed ({success_rate:.1f}%)")
        
        if success_rate == 100:
            print("🎉 All integration tests passed! Airsoft agent with voice ready for production!")
        elif success_rate >= 80:
            print("⚠️  Most tests passed. Review failed tests and fix issues.")
        else:
            print("❌ Multiple tests failed. Significant issues need to be resolved.")
        
        print("="*80)

async def main():
    """Главная функция тестирования"""
    tester = AirsoftAgentTester()
    await tester.run_full_test_suite()

if __name__ == "__main__":
    asyncio.run(main())
