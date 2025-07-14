"""
Интеграционные тесты для проверки реального взаимодействия с сервисами
"""

import asyncio
import json
import logging
import os
import tempfile
import uuid
from pathlib import Path
from typing import Dict, Any, List
import aiohttp
import aiofiles
from datetime import datetime

logger = logging.getLogger("voice_integration_test")


class VoiceIntegrationTester:
    """Тестировщик интеграции голосовых сервисов"""
    
    def __init__(self, base_url: str = "http://localhost:8001"):
        self.base_url = base_url
        self.session = None
        self.test_files = []
    
    async def __aenter__(self):
        self.session = aiohttp.ClientSession()
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self.session:
            await self.session.close()
        
        # Очищаем временные файлы
        for file_path in self.test_files:
            try:
                if os.path.exists(file_path):
                    os.remove(file_path)
            except Exception as e:
                logger.warning(f"Failed to cleanup test file {file_path}: {e}")
    
    async def test_health_endpoints(self) -> Dict[str, Any]:
        """Тестирование health endpoints всех сервисов"""
        logger.info("🏥 Testing service health endpoints...")
        
        health_endpoints = [
            {"name": "main_api", "url": f"{self.base_url}/health"},
            {"name": "voice_service", "url": f"{self.base_url}/api/v1/voice/health"},
            {"name": "redis_service", "url": f"{self.base_url}/api/v1/redis/health"}
        ]
        
        results = {}
        
        for endpoint in health_endpoints:
            try:
                async with self.session.get(endpoint["url"]) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        results[endpoint["name"]] = {
                            "status": "healthy",
                            "response_code": resp.status,
                            "data": data
                        }
                    else:
                        results[endpoint["name"]] = {
                            "status": "unhealthy",
                            "response_code": resp.status
                        }
            except Exception as e:
                results[endpoint["name"]] = {
                    "status": "error",
                    "error": str(e)
                }
        
        return {
            "test": "health_endpoints",
            "results": results,
            "overall_health": all(r.get("status") == "healthy" for r in results.values())
        }
    
    async def test_agent_configuration_api(self, agent_id: str) -> Dict[str, Any]:
        """Тестирование API конфигурации агента"""
        logger.info(f"⚙️ Testing agent configuration API for {agent_id}...")
        
        try:
            # Получаем конфигурацию агента
            config_url = f"{self.base_url}/api/v1/agents/{agent_id}/config"
            
            async with self.session.get(config_url) as resp:
                if resp.status == 200:
                    config = await resp.json()
                    
                    # Проверяем структуру конфигурации
                    has_voice_settings = False
                    voice_settings = None
                    
                    if "config" in config and "simple" in config["config"]:
                        settings = config["config"]["simple"].get("settings", {})
                        voice_settings = settings.get("voice_settings")
                        has_voice_settings = voice_settings is not None
                    
                    # Проверяем обновление конфигурации
                    update_test = await self.test_config_update(agent_id)
                    
                    return {
                        "test": "agent_configuration_api",
                        "status": "passed",
                        "agent_id": agent_id,
                        "config_loaded": True,
                        "has_voice_settings": has_voice_settings,
                        "voice_settings": voice_settings,
                        "update_test": update_test
                    }
                else:
                    return {
                        "test": "agent_configuration_api",
                        "status": "failed",
                        "error": f"HTTP {resp.status}"
                    }
                    
        except Exception as e:
            return {
                "test": "agent_configuration_api",
                "status": "failed",
                "error": str(e)
            }
    
    async def test_config_update(self, agent_id: str) -> Dict[str, Any]:
        """Тестирование обновления конфигурации"""
        try:
            update_url = f"{self.base_url}/api/v1/agents/{agent_id}/config"
            
            # Создаем тестовое обновление
            test_update = {
                "voice_settings": {
                    "test_flag": True,
                    "updated_at": datetime.now().isoformat()
                }
            }
            
            async with self.session.patch(update_url, json=test_update) as resp:
                if resp.status in [200, 204]:
                    return {"status": "passed", "updated": True}
                else:
                    return {"status": "failed", "error": f"HTTP {resp.status}"}
                    
        except Exception as e:
            return {"status": "failed", "error": str(e)}
    
    async def test_voice_tts_synthesis(self) -> Dict[str, Any]:
        """Тестирование синтеза речи через API"""
        logger.info("🎵 Testing TTS synthesis via API...")
        
        try:
            tts_url = f"{self.base_url}/api/v1/voice/tts/synthesize"
            
            test_payload = {
                "text": "Это тестовое сообщение для проверки синтеза речи.",
                "provider": "yandex",
                "voice_config": {
                    "voice": "jane",
                    "speed": 1.0,
                    "emotion": "neutral"
                }
            }
            
            async with self.session.post(tts_url, json=test_payload) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    
                    # Проверяем структуру ответа
                    has_audio_url = "audio_url" in data
                    has_file_info = "file_info" in data
                    
                    # Пытаемся скачать аудио файл
                    download_test = None
                    if has_audio_url:
                        download_test = await self.test_audio_download(data["audio_url"])
                    
                    return {
                        "test": "voice_tts_synthesis",
                        "status": "passed",
                        "response_structure_valid": has_audio_url and has_file_info,
                        "audio_url": data.get("audio_url"),
                        "file_info": data.get("file_info"),
                        "download_test": download_test
                    }
                else:
                    response_text = await resp.text()
                    return {
                        "test": "voice_tts_synthesis",
                        "status": "failed",
                        "error": f"HTTP {resp.status}: {response_text}"
                    }
                    
        except Exception as e:
            return {
                "test": "voice_tts_synthesis",
                "status": "failed",
                "error": str(e)
            }
    
    async def test_audio_download(self, audio_url: str) -> Dict[str, Any]:
        """Тестирование скачивания аудио файла"""
        try:
            async with self.session.get(audio_url) as resp:
                if resp.status == 200:
                    content = await resp.read()
                    
                    # Сохраняем во временный файл
                    temp_file = tempfile.NamedTemporaryFile(delete=False, suffix=".mp3")
                    temp_file.write(content)
                    temp_file.close()
                    
                    self.test_files.append(temp_file.name)
                    
                    file_size = len(content)
                    
                    return {
                        "status": "passed",
                        "downloaded": True,
                        "file_size": file_size,
                        "content_type": resp.headers.get("content-type"),
                        "temp_file": temp_file.name
                    }
                else:
                    return {
                        "status": "failed",
                        "error": f"HTTP {resp.status}"
                    }
                    
        except Exception as e:
            return {
                "status": "failed",
                "error": str(e)
            }
    
    async def test_agent_voice_chat(self, agent_id: str) -> Dict[str, Any]:
        """Тестирование полного голосового чата с агентом"""
        logger.info(f"💬 Testing full voice chat with agent {agent_id}...")
        
        chat_url = f"{self.base_url}/api/v1/agents/{agent_id}/chat"
        
        test_messages = [
            {
                "message": "Отвечай голосом. Расскажи о страйкболе кратко.",
                "expect_voice": True,
                "description": "Request with voice intent"
            },
            {
                "message": "Что такое тактическая игра?",
                "expect_voice": False,
                "description": "Regular text request"
            },
            {
                "message": "Скажи мне о правилах безопасности.",
                "expect_voice": True,
                "description": "Another voice intent request"
            }
        ]
        
        results = []
        
        for i, test_case in enumerate(test_messages):
            logger.info(f"   Testing message {i+1}: {test_case['description']}")
            
            payload = {
                "message": test_case["message"],
                "chat_id": 70000 + i,
                "metadata": {
                    "test_case": test_case["description"],
                    "expect_voice": test_case["expect_voice"]
                }
            }
            
            try:
                async with self.session.post(chat_url, json=payload) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        
                        has_text_response = "response" in data
                        has_voice_response = "voice_response" in data
                        voice_url = data.get("voice_response", {}).get("audio_url")
                        
                        # Проверяем соответствие ожиданиям
                        voice_expectation_met = (
                            test_case["expect_voice"] == has_voice_response
                        )
                        
                        # Тестируем скачивание голосового ответа
                        voice_download_test = None
                        if voice_url:
                            voice_download_test = await self.test_audio_download(voice_url)
                        
                        result = {
                            "message": test_case["message"],
                            "expected_voice": test_case["expect_voice"],
                            "has_text_response": has_text_response,
                            "has_voice_response": has_voice_response,
                            "voice_expectation_met": voice_expectation_met,
                            "voice_url": voice_url,
                            "voice_download_test": voice_download_test,
                            "status": "passed" if voice_expectation_met else "failed"
                        }
                        
                    else:
                        result = {
                            "message": test_case["message"],
                            "status": "failed",
                            "error": f"HTTP {resp.status}"
                        }
                
                results.append(result)
                
                # Пауза между запросами
                await asyncio.sleep(1)
                
            except Exception as e:
                results.append({
                    "message": test_case["message"],
                    "status": "failed",
                    "error": str(e)
                })
        
        # Анализируем общие результаты
        total_tests = len(results)
        passed_tests = sum(1 for r in results if r.get("status") == "passed")
        
        return {
            "test": "agent_voice_chat",
            "agent_id": agent_id,
            "total_tests": total_tests,
            "passed_tests": passed_tests,
            "success_rate": (passed_tests / total_tests) * 100,
            "detailed_results": results,
            "overall_status": "passed" if passed_tests == total_tests else "failed"
        }
    
    async def test_redis_integration(self) -> Dict[str, Any]:
        """Тестирование интеграции с Redis"""
        logger.info("🔴 Testing Redis integration...")
        
        try:
            redis_url = f"{self.base_url}/api/v1/redis/test"
            
            test_data = {
                "key": f"test_key_{uuid.uuid4().hex[:8]}",
                "value": "test_value_for_integration",
                "ttl": 300
            }
            
            async with self.session.post(redis_url, json=test_data) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    
                    return {
                        "test": "redis_integration",
                        "status": "passed",
                        "redis_available": True,
                        "set_operation": data.get("set_success", False),
                        "get_operation": data.get("get_success", False),
                        "delete_operation": data.get("delete_success", False)
                    }
                else:
                    return {
                        "test": "redis_integration",
                        "status": "failed",
                        "error": f"HTTP {resp.status}"
                    }
                    
        except Exception as e:
            return {
                "test": "redis_integration",
                "status": "failed",
                "error": str(e)
            }


async def run_integration_test_suite():
    """Запуск полного набора интеграционных тестов"""
    print("🔌 Voice Workflow Integration Testing Suite")
    print("=" * 55)
    
    agent_id = "agent_airsoft_0faa9616"
    
    async with VoiceIntegrationTester() as tester:
        results = {
            "start_time": datetime.now().isoformat(),
            "agent_id": agent_id,
            "tests": {}
        }
        
        # 1. Проверка health endpoints
        print("\n🏥 Testing service health...")
        health_test = await tester.test_health_endpoints()
        results["tests"]["health"] = health_test
        
        # 2. Тестирование API конфигурации агента
        print("\n⚙️ Testing agent configuration API...")
        config_test = await tester.test_agent_configuration_api(agent_id)
        results["tests"]["configuration"] = config_test
        
        # 3. Тестирование TTS синтеза
        print("\n🎵 Testing TTS synthesis...")
        tts_test = await tester.test_voice_tts_synthesis()
        results["tests"]["tts_synthesis"] = tts_test
        
        # 4. Тестирование полного голосового чата
        print("\n💬 Testing full voice chat...")
        chat_test = await tester.test_agent_voice_chat(agent_id)
        results["tests"]["voice_chat"] = chat_test
        
        # 5. Тестирование Redis интеграции
        print("\n🔴 Testing Redis integration...")
        redis_test = await tester.test_redis_integration()
        results["tests"]["redis"] = redis_test
        
        results["end_time"] = datetime.now().isoformat()
        
        # Выводим результаты
        print("\n📊 INTEGRATION TEST RESULTS:")
        print("-" * 40)
        
        for test_name, test_result in results["tests"].items():
            status_emoji = "✅" if test_result.get("status") == "passed" or test_result.get("overall_status") == "passed" else "❌"
            status = test_result.get("status", test_result.get("overall_status", "unknown"))
            print(f"{status_emoji} {test_name}: {status}")
            
            # Дополнительная информация для некоторых тестов
            if test_name == "health":
                healthy_services = sum(1 for r in test_result["results"].values() if r.get("status") == "healthy")
                total_services = len(test_result["results"])
                print(f"   Services healthy: {healthy_services}/{total_services}")
            
            elif test_name == "voice_chat":
                success_rate = test_result.get("success_rate", 0)
                print(f"   Success rate: {success_rate:.1f}%")
            
            elif test_name == "tts_synthesis":
                if test_result.get("status") == "passed":
                    download_status = test_result.get("download_test", {}).get("status", "unknown")
                    file_size = test_result.get("download_test", {}).get("file_size", 0)
                    print(f"   Audio download: {download_status}, Size: {file_size} bytes")
        
        # Сохраняем результаты
        results_file = "voice_integration_test_results.json"
        with open(results_file, "w", encoding="utf-8") as f:
            json.dump(results, f, indent=2, ensure_ascii=False, default=str)
        
        print(f"\n💾 Integration test results saved to: {results_file}")
        
        return results


if __name__ == "__main__":
    asyncio.run(run_integration_test_suite())
