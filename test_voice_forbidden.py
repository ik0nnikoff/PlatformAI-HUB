"""
Тест для проверки обработки исключения VOICE_MESSAGES_FORBIDDEN
"""

import asyncio
import logging
from unittest.mock import AsyncMock, patch, MagicMock
from aiogram.exceptions import TelegramBadRequest

# Настройка логирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("voice_forbidden_test")


class VoiceForbiddenTester:
    """Тестер для проверки обработки запрета голосовых сообщений"""
    
    def __init__(self):
        self.test_results = {}
    
    async def test_voice_forbidden_fallback(self):
        """Тест fallback'а на текст при запрете голосовых сообщений"""
        logger.info("🚫 Testing VOICE_MESSAGES_FORBIDDEN fallback...")
        
        try:
            # Импортируем после настройки логирования
            from app.integrations.telegram.telegram_bot import TelegramIntegrationBot
            
            # Создаем mock объекты
            mock_bot = AsyncMock()
            mock_redis_client = AsyncMock()
            mock_status_updater = AsyncMock()
            
            # Настраиваем mock для send_voice чтобы вызывать исключение
            mock_bot.send_voice.side_effect = TelegramBadRequest(
                method="sendVoice",
                message="Bad Request: VOICE_MESSAGES_FORBIDDEN"
            )
            
            # Создаем экземпляр TelegramBot с mock'ами
            bot = TelegramIntegrationBot(
                agent_id="test_agent",
                bot_token="test_token",
                redis_client=mock_redis_client,
                status_updater=mock_status_updater
            )
            
            # Заменяем реальный bot на mock
            bot.bot = mock_bot
            
            # Создаем тестовое сообщение с голосовым ответом
            test_payload = {
                "response": "Тестовый ответ агента",
                "audio_url": "http://localhost:9000/voice-files/test.mp3"
            }
            
            # Симулируем обработку pubsub сообщения
            import json
            test_message_data = json.dumps({
                "chat_id": "123456789",
                "payload": test_payload
            }).encode('utf-8')
            
            # Создаем mock message для pubsub
            mock_message = MagicMock()
            mock_message.data = test_message_data
            
            # Патчим aiohttp.ClientSession для mock'а скачивания аудио
            with patch('aiohttp.ClientSession') as mock_session:
                mock_response = AsyncMock()
                mock_response.status = 200
                mock_response.read.return_value = b"mock_audio_data"
                
                mock_session.return_value.__aenter__.return_value.get.return_value.__aenter__.return_value = mock_response
                
                # Вызываем метод обработки сообщения
                await bot._handle_pubsub_message(mock_message)
            
            # Проверяем результаты
            voice_call_made = mock_bot.send_voice.called
            text_call_made = mock_bot.send_message.called
            
            return {
                "test": "voice_forbidden_fallback",
                "status": "passed" if voice_call_made and text_call_made else "failed",
                "voice_attempted": voice_call_made,
                "text_sent_as_fallback": text_call_made,
                "voice_call_count": mock_bot.send_voice.call_count,
                "text_call_count": mock_bot.send_message.call_count
            }
            
        except Exception as e:
            logger.error(f"❌ Voice forbidden test failed: {e}")
            return {
                "test": "voice_forbidden_fallback",
                "status": "failed",
                "error": str(e)
            }
    
    async def test_voice_success_no_text_duplication(self):
        """Тест что при успешной отправке голоса текст не дублируется"""
        logger.info("✅ Testing voice success without text duplication...")
        
        try:
            from app.integrations.telegram.telegram_bot import TelegramIntegrationBot
            
            # Создаем mock объекты
            mock_bot = AsyncMock()
            mock_redis_client = AsyncMock()
            mock_status_updater = AsyncMock()
            
            # Настраиваем mock для успешной отправки голоса
            mock_bot.send_voice.return_value = {"message_id": 123}
            
            # Создаем экземпляр TelegramBot с mock'ами
            bot = TelegramIntegrationBot(
                agent_id="test_agent",
                bot_token="test_token",
                redis_client=mock_redis_client,
                status_updater=mock_status_updater
            )
            
            bot.bot = mock_bot
            
            # Тестовое сообщение с голосовым ответом
            test_payload = {
                "response": "Тестовый ответ агента",
                "audio_url": "http://localhost:9000/voice-files/test.mp3"
            }
            
            import json
            test_message_data = json.dumps({
                "chat_id": "123456789",
                "payload": test_payload
            }).encode('utf-8')
            
            mock_message = MagicMock()
            mock_message.data = test_message_data
            
            # Патчим aiohttp для mock'а скачивания
            with patch('aiohttp.ClientSession') as mock_session:
                mock_response = AsyncMock()
                mock_response.status = 200
                mock_response.read.return_value = b"mock_audio_data"
                
                mock_session.return_value.__aenter__.return_value.get.return_value.__aenter__.return_value = mock_response
                
                await bot._handle_pubsub_message(mock_message)
            
            # Проверяем что голос отправлен, а текст НЕ отправлен
            voice_call_made = mock_bot.send_voice.called
            text_call_made = mock_bot.send_message.called
            
            return {
                "test": "voice_success_no_duplication",
                "status": "passed" if voice_call_made and not text_call_made else "failed",
                "voice_sent": voice_call_made,
                "text_not_sent": not text_call_made,
                "voice_call_count": mock_bot.send_voice.call_count,
                "text_call_count": mock_bot.send_message.call_count
            }
            
        except Exception as e:
            logger.error(f"❌ Voice success test failed: {e}")
            return {
                "test": "voice_success_no_duplication", 
                "status": "failed",
                "error": str(e)
            }
    
    async def run_all_tests(self):
        """Запуск всех тестов"""
        logger.info("🎭 Running Voice Forbidden Exception Tests...")
        
        results = {
            "start_time": asyncio.get_event_loop().time(),
            "tests": {}
        }
        
        # 1. Тест fallback'а при запрете голосовых сообщений
        forbidden_test = await self.test_voice_forbidden_fallback()
        results["tests"]["voice_forbidden_fallback"] = forbidden_test
        
        # 2. Тест успешной отправки голоса без дублирования текста
        success_test = await self.test_voice_success_no_text_duplication()
        results["tests"]["voice_success_no_duplication"] = success_test
        
        results["end_time"] = asyncio.get_event_loop().time()
        results["total_duration"] = results["end_time"] - results["start_time"]
        
        # Подсчет статистики
        passed_tests = sum(1 for test in results["tests"].values() if test.get("status") == "passed")
        total_tests = len(results["tests"])
        
        results["summary"] = {
            "total_tests": total_tests,
            "passed_tests": passed_tests,
            "failed_tests": total_tests - passed_tests,
            "success_rate": f"{(passed_tests/total_tests)*100:.1f}%"
        }
        
        return results


async def main():
    """Главная функция"""
    print("🚫 Voice Messages Forbidden Exception Test")
    print("=" * 45)
    
    tester = VoiceForbiddenTester()
    results = await tester.run_all_tests()
    
    # Выводим результаты
    print("\n📊 TEST RESULTS:")
    print("-" * 30)
    
    for test_name, test_result in results["tests"].items():
        status_emoji = "✅" if test_result.get("status") == "passed" else "❌"
        print(f"{status_emoji} {test_name}: {test_result.get('status', 'unknown')}")
        
        if test_result.get("status") == "failed":
            print(f"   Error: {test_result.get('error', 'Unknown error')}")
        else:
            # Дополнительная информация для успешных тестов
            if test_name == "voice_forbidden_fallback":
                print(f"   Voice attempted: {test_result.get('voice_attempted', False)}")
                print(f"   Text fallback: {test_result.get('text_sent_as_fallback', False)}")
            elif test_name == "voice_success_no_duplication":
                print(f"   Voice sent: {test_result.get('voice_sent', False)}")
                print(f"   Text NOT sent: {test_result.get('text_not_sent', False)}")
    
    print(f"\n📈 SUMMARY:")
    summary = results["summary"]
    print(f"   Total Tests: {summary['total_tests']}")
    print(f"   Passed: {summary['passed_tests']}")
    print(f"   Failed: {summary['failed_tests']}")
    print(f"   Success Rate: {summary['success_rate']}")
    print(f"   Duration: {results['total_duration']:.2f}s")
    
    # Сохраняем результаты
    import json
    results_file = "voice_forbidden_test_results.json"
    with open(results_file, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False, default=str)
    
    print(f"\n💾 Results saved to: {results_file}")
    
    return results


if __name__ == "__main__":
    asyncio.run(main())
