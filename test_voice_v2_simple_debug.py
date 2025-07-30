#!/usr/bin/env python3
"""
Простой отладочный тест для Voice V2 системы - Phase 4.6.4
Цель: Выявить проблемы с провайдерами в production режиме
"""

import asyncio
import time
import sys
import os
from pathlib import Path

# Add project root to Python path
project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))

from app.services.voice_v2.core.orchestrator import VoiceServiceOrchestrator


async def test_tts_single_operation():
    """Простой тест одной TTS операции с детальной отладкой."""
    print("🔍 Начинаем простой тест TTS операции...")
    
    try:
        # Создаем оркестратор
        print("  📝 Создание VoiceServiceOrchestrator...")
        
        # Импортируем фабрику для оркестратора
        from app.services.voice_v2.providers.factory.factory import EnhancedVoiceProviderFactory
        factory = EnhancedVoiceProviderFactory()
        
        orchestrator = VoiceServiceOrchestrator(enhanced_factory=factory)
        
        # Инициализация
        print("  🚀 Инициализация оркестратора...")
        await orchestrator.initialize()
        
        # Проверяем провайдеры через менеджеры
        print("  📊 Проверяем доступные провайдеры через менеджеры...")
        
        # Доступ к менеджерам оркестратора
        provider_manager = getattr(orchestrator, '_provider_manager', None)
        tts_manager = getattr(orchestrator, '_tts_manager', None)
        
        if provider_manager:
            print(f"    - Provider Manager найден: {provider_manager.__class__.__name__}")
        else:
            print(f"    - Provider Manager не найден")
            
        if tts_manager:
            print(f"    - TTS Manager найден: {tts_manager.__class__.__name__}")
        else:
            print(f"    - TTS Manager не найден")
        
        # Тестируем TTS
        test_text = "Привет, это тест голосового синтеза"
        print(f"  🔊 Тестируем TTS с текстом: '{test_text}'")
        
        start_time = time.time()
        
        # Импортируем схемы
        from app.services.voice_v2.core.schemas import TTSRequest
        
        # Создаем запрос
        tts_request = TTSRequest(
            text=test_text,
            language="ru",
            voice="alloy",
            speed=1.0
        )
        
        print(f"  ⚙️ Запрос TTS: {tts_request}")
        
        result = await orchestrator.synthesize_speech(tts_request)
        
        duration = time.time() - start_time
        
        if result and hasattr(result, 'audio_data') and result.audio_data:
            audio_size = len(result.audio_data)
            provider_used = getattr(result, 'provider_used', 'unknown')
            print(f"  ✅ TTS УСПЕШНО!")
            print(f"     Длительность: {duration:.3f}s")
            print(f"     Размер аудио: {audio_size} bytes")
            print(f"     Провайдер: {provider_used}")
            return True
        else:
            print(f"  ❌ TTS НЕУДАЧНО - нет аудио данных")
            print(f"     result: {result}")
            if result:
                print(f"     result.audio_data: {getattr(result, 'audio_data', 'НЕТ АТРИБУТА')}")
            return False
            
    except Exception as e:
        print(f"  💥 ИСКЛЮЧЕНИЕ: {str(e)}")
        import traceback
        traceback.print_exc()
        return False
    finally:
        # Очистка
        try:
            if 'orchestrator' in locals():
                await orchestrator.cleanup()
                print("  🧹 Очистка завершена")
        except Exception as cleanup_e:
            print(f"  ⚠️ Ошибка очистки: {cleanup_e}")


async def test_provider_availability():
    """Тест доступности провайдеров."""
    print("🔍 Проверяем доступность провайдеров...")
    
    try:
        # Проверим импорты
        print("  📦 Проверяем импорты...")
        
        from app.services.voice_v2.providers.tts.openai_tts import OpenAITTSProvider
        from app.services.voice_v2.providers.tts.yandex_tts import YandexTTSProvider
        print("    ✅ Импорты TTS провайдеров успешны")
        
        from app.services.voice_v2.providers.factory.factory import EnhancedVoiceProviderFactory
        print("    ✅ Импорт фабрики успешен")
        
        # Создаем фабрику
        print("  🏭 Создание фабрики...")
        factory = EnhancedVoiceProviderFactory()
        
        # ВАЖНО: Инициализируем фабрику!
        print("  🚀 Инициализация фабрики...")
        await factory.initialize()
        
        # Получаем доступные провайдеры
        print("  🔧 Получение доступных провайдеров...")
        available_providers = factory.get_available_providers()
        
        print(f"  📊 Доступно провайдеров: {len(available_providers)}")
        for provider_info in available_providers:
            print(f"    - {provider_info.name}: Категория={provider_info.category.value}, Тип={provider_info.provider_type.value}")
        
        # Попробуем создать TTS провайдер
        if available_providers:
            from app.services.voice_v2.providers.factory.types import ProviderCategory
            tts_providers = [p for p in available_providers if p.category == ProviderCategory.TTS]
            if tts_providers:
                print(f"  🎤 Создаем TTS провайдер: {tts_providers[0].name}")
                try:
                    tts_provider = await factory.create_tts_provider(tts_providers[0].provider_type.value)
                    if tts_provider:
                        print(f"    ✅ TTS провайдер создан: {tts_provider.__class__.__name__}")
                    else:
                        print(f"    ❌ TTS провайдер не создан")
                except Exception as e:
                    print(f"    💥 Ошибка создания TTS: {e}")
            else:
                print("  ❌ Нет TTS провайдеров")
        
        return len(available_providers) > 0
        
    except Exception as e:
        print(f"  💥 ОШИБКА: {str(e)}")
        import traceback
        traceback.print_exc()
        return False


async def test_environment():
    """Тест переменных окружения."""
    print("🔍 Проверяем переменные окружения...")
    
    env_vars = [
        "OPENAI_API_KEY",
        "YANDEX_API_KEY", 
        "YANDEX_FOLDER_ID"
    ]
    
    for var in env_vars:
        value = os.getenv(var)
        if value:
            print(f"  ✅ {var}: {'*' * min(len(value), 8)}... (длина: {len(value)})")
        else:
            print(f"  ❌ {var}: НЕ УСТАНОВЛЕН")


async def main():
    """Главная функция отладки."""
    print("=" * 60)
    print("🧪 ОТЛАДОЧНЫЙ ТЕСТ VOICE V2 - PHASE 4.6.4")
    print("=" * 60)
    
    # Тест переменных окружения
    await test_environment()
    print()
    
    # Тест провайдеров
    providers_ok = await test_provider_availability()
    print()
    
    if not providers_ok:
        print("❌ КРИТИЧЕСКАЯ ОШИБКА: Провайдеры недоступны")
        return
    
    # Тест TTS операции
    tts_ok = await test_tts_single_operation()
    print()
    
    # Итоговый результат
    print("=" * 60)
    if tts_ok:
        print("🎉 ОТЛАДКА ЗАВЕРШЕНА УСПЕШНО!")
        print("✅ TTS операция работает корректно")
    else:
        print("💥 ОТЛАДКА ВЫЯВИЛА ПРОБЛЕМЫ!")
        print("❌ TTS операция не работает")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
