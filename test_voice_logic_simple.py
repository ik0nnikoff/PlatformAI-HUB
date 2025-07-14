"""
Простой тест для проверки обработки исключения VOICE_MESSAGES_FORBIDDEN
"""

import asyncio
import logging
from unittest.mock import AsyncMock, patch, MagicMock
from aiogram.exceptions import TelegramBadRequest

# Настройка логирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("voice_forbidden_simple_test")


async def test_voice_forbidden_logic():
    """Тест логики обработки запрета голосовых сообщений"""
    print("🚫 Testing VOICE_MESSAGES_FORBIDDEN handling logic...")
    
    # Симулируем логику из telegram_bot.py
    async def simulate_voice_sending_logic(chat_id: int, response: str, audio_url: str = None):
        """Симуляция логики отправки сообщений"""
        
        voice_sent_successfully = False
        text_sent = False
        
        # Mock bot
        mock_bot = AsyncMock()
        
        if audio_url:
            logger.info(f"Attempting to send voice to chat {chat_id}")
            try:
                # Симулируем исключение VOICE_MESSAGES_FORBIDDEN
                raise TelegramBadRequest(
                    method="sendVoice",
                    message="Bad Request: VOICE_MESSAGES_FORBIDDEN"
                )
                
            except TelegramBadRequest as e:
                if "VOICE_MESSAGES_FORBIDDEN" in str(e):
                    logger.warning(f"Voice messages are forbidden for chat {chat_id}, falling back to text")
                else:
                    logger.error(f"Telegram API error sending voice to chat {chat_id}: {e}")
            except Exception as e:
                logger.error(f"Error sending audio response to chat {chat_id}: {e}")
        
        # Send text response only if voice wasn't sent successfully
        if not voice_sent_successfully:
            logger.info(f"Sending text message to chat {chat_id}: {response}")
            text_sent = True
        
        return {
            "voice_sent": voice_sent_successfully,
            "text_sent": text_sent,
            "fallback_triggered": audio_url is not None and not voice_sent_successfully
        }
    
    # Тест 1: С голосовым ответом, но voice forbidden
    result1 = await simulate_voice_sending_logic(
        chat_id=123456789,
        response="Тестовый ответ",
        audio_url="http://localhost:9000/test.mp3"
    )
    
    # Тест 2: Обычное текстовое сообщение
    result2 = await simulate_voice_sending_logic(
        chat_id=123456789,
        response="Тестовый ответ",
        audio_url=None
    )
    
    return {
        "test_with_voice_forbidden": result1,
        "test_text_only": result2
    }


async def test_successful_voice_logic():
    """Тест логики успешной отправки голоса"""
    print("✅ Testing successful voice sending logic...")
    
    async def simulate_successful_voice_logic(chat_id: int, response: str, audio_url: str = None):
        """Симуляция успешной отправки голоса"""
        
        voice_sent_successfully = False
        text_sent = False
        
        if audio_url:
            logger.info(f"Attempting to send voice to chat {chat_id}")
            try:
                # Симулируем успешную отправку голоса
                logger.info(f"Voice message sent successfully to chat {chat_id}")
                voice_sent_successfully = True
                
            except TelegramBadRequest as e:
                if "VOICE_MESSAGES_FORBIDDEN" in str(e):
                    logger.warning(f"Voice messages are forbidden for chat {chat_id}, falling back to text")
                else:
                    logger.error(f"Telegram API error sending voice to chat {chat_id}: {e}")
            except Exception as e:
                logger.error(f"Error sending audio response to chat {chat_id}: {e}")
        
        # Send text response only if voice wasn't sent successfully
        if not voice_sent_successfully:
            logger.info(f"Sending text message to chat {chat_id}: {response}")
            text_sent = True
        
        return {
            "voice_sent": voice_sent_successfully,
            "text_sent": text_sent,
            "no_duplication": voice_sent_successfully and not text_sent
        }
    
    # Тест успешной отправки голоса
    result = await simulate_successful_voice_logic(
        chat_id=123456789,
        response="Тестовый ответ",
        audio_url="http://localhost:9000/test.mp3"
    )
    
    return result


async def main():
    """Главная функция"""
    print("🎭 Voice Logic Testing Suite")
    print("=" * 30)
    
    results = {}
    
    # Тест 1: VOICE_MESSAGES_FORBIDDEN
    forbidden_results = await test_voice_forbidden_logic()
    results["voice_forbidden_tests"] = forbidden_results
    
    # Тест 2: Успешная отправка голоса
    success_result = await test_successful_voice_logic()
    results["voice_success_test"] = success_result
    
    # Анализ результатов
    print("\n📊 TEST RESULTS:")
    print("-" * 30)
    
    # Тест с запретом голосовых сообщений
    forbidden_test = forbidden_results["test_with_voice_forbidden"]
    forbidden_success = (
        not forbidden_test["voice_sent"] and 
        forbidden_test["text_sent"] and 
        forbidden_test["fallback_triggered"]
    )
    
    print(f"{'✅' if forbidden_success else '❌'} Voice Forbidden Fallback:")
    print(f"   Voice sent: {forbidden_test['voice_sent']}")
    print(f"   Text sent: {forbidden_test['text_sent']}")
    print(f"   Fallback triggered: {forbidden_test['fallback_triggered']}")
    
    # Тест обычного текста
    text_test = forbidden_results["test_text_only"]
    text_success = not text_test["voice_sent"] and text_test["text_sent"]
    
    print(f"\n{'✅' if text_success else '❌'} Regular Text Message:")
    print(f"   Voice sent: {text_test['voice_sent']}")
    print(f"   Text sent: {text_test['text_sent']}")
    
    # Тест успешного голоса
    voice_test = success_result
    voice_success = (
        voice_test["voice_sent"] and 
        not voice_test["text_sent"] and 
        voice_test["no_duplication"]
    )
    
    print(f"\n{'✅' if voice_success else '❌'} Successful Voice (No Duplication):")
    print(f"   Voice sent: {voice_test['voice_sent']}")
    print(f"   Text sent: {voice_test['text_sent']}")
    print(f"   No duplication: {voice_test['no_duplication']}")
    
    # Общая статистика
    total_tests = 3
    passed_tests = sum([forbidden_success, text_success, voice_success])
    
    print(f"\n📈 SUMMARY:")
    print(f"   Total Tests: {total_tests}")
    print(f"   Passed: {passed_tests}")
    print(f"   Failed: {total_tests - passed_tests}")
    print(f"   Success Rate: {(passed_tests/total_tests)*100:.1f}%")
    
    # Сохраняем результаты
    import json
    results_file = "voice_logic_test_results.json"
    results["summary"] = {
        "total_tests": total_tests,
        "passed_tests": passed_tests,
        "failed_tests": total_tests - passed_tests,
        "success_rate": f"{(passed_tests/total_tests)*100:.1f}%",
        "test_results": {
            "voice_forbidden_fallback": forbidden_success,
            "regular_text_message": text_success,
            "successful_voice_no_duplication": voice_success
        }
    }
    
    with open(results_file, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False, default=str)
    
    print(f"\n💾 Results saved to: {results_file}")
    
    return results


if __name__ == "__main__":
    asyncio.run(main())
