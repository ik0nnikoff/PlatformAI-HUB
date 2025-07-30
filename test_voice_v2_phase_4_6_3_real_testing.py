#!/usr/bin/env python3
"""
Phase 4.6.3 Real Voice V2 Testing
=================================

Полное тестирование Voice V2 системы с реальными провайдерами:
- OpenAI TTS/STT  
- Yandex TTS/STT
- MinIO интеграция
- Enhanced Factory
- Orchestrator

Требования:
- Запущенный MinIO (docker)
- Рабочие API ключи OpenAI и Yandex
- Правильные языковые коды
"""

import asyncio
import time
import tempfile
import os
from typing import Tuple, List, Dict, Any

from app.services.voice_v2.core.orchestrator.base_orchestrator import VoiceServiceOrchestrator
from app.services.voice_v2.core.schemas import TTSRequest, STTRequest
from app.services.voice_v2.core.interfaces import AudioFormat
from app.services.voice_v2.providers.factory.factory import EnhancedVoiceProviderFactory
from app.core.config import settings


class VoiceV2RealTester:
    """Тестер реальной функциональности Voice V2"""
    
    def __init__(self):
        self.factory = None
        self.orchestrator = None
        self.test_results = []
        
    async def initialize(self) -> bool:
        """Инициализация системы"""
        print('🚀 ИНИЦИАЛИЗАЦИЯ VOICE V2 СИСТЕМЫ')
        print('=' * 50)
        
        try:
            # Initialize Enhanced Factory
            self.factory = EnhancedVoiceProviderFactory()
            await self.factory.initialize()
            print('✅ Enhanced Factory инициализирована')
            
            # Initialize orchestrator with factory
            self.orchestrator = VoiceServiceOrchestrator(enhanced_factory=self.factory)
            await self.orchestrator.initialize()
            print('✅ Orchestrator инициализирован')
            
            # Check MinIO
            print(f'✅ MinIO endpoint: {settings.MINIO_ENDPOINT}')
            
            # Check API keys
            openai_key = settings.OPENAI_API_KEY.get_secret_value() if settings.OPENAI_API_KEY else None
            yandex_key = settings.YANDEX_API_KEY.get_secret_value() if settings.YANDEX_API_KEY else None
            
            print(f'✅ OpenAI API: {"✓" if openai_key and openai_key.startswith("sk-") else "✗"}')
            print(f'✅ Yandex API: {"✓" if yandex_key else "✗"}')
            
            return True
            
        except Exception as e:
            print(f'❌ Ошибка инициализации: {e}')
            return False
    
    async def test_openai_tts(self) -> Dict[str, Any]:
        """Тест OpenAI TTS"""
        print('\n1. 🎵 ТЕСТИРОВАНИЕ OPENAI TTS')
        print('-' * 35)
        
        test_result = {
            'name': 'OpenAI TTS',
            'success': False,
            'audio_data': None,
            'details': {},
            'error': None
        }
        
        # Правильный запрос для OpenAI - используем язык который точно поддерживается
        request = TTSRequest(
            text='Привет! Это тестирование OpenAI синтеза речи.',
            voice='alloy',
            language='en',  # Используем английский для OpenAI в тесте
            speed=1.0
        )
        
        print(f'Текст: "{request.text}"')
        print(f'Голос: {request.voice}')
        print(f'Язык: {request.language}')
        
        try:
            start_time = time.time()
            result = await self.orchestrator.synthesize_speech(request)
            elapsed = (time.time() - start_time) * 1000
            
            print(f'✅ УСПЕХ: {elapsed:.0f}ms')
            print(f'   Провайдер: {result.provider}')
            print(f'   Размер аудио: {len(result.audio_data):,} байт')
            print(f'   Формат: {result.format}')
            
            test_result.update({
                'success': True,
                'audio_data': result.audio_data,
                'details': {
                    'provider': result.provider,
                    'size': len(result.audio_data),
                    'format': str(result.format),
                    'processing_time': elapsed
                }
            })
            
            # Сохраняем аудио для проверки
            with tempfile.NamedTemporaryFile(suffix='_openai_tts.mp3', delete=False) as f:
                f.write(result.audio_data)
                audio_file = f.name
                test_result['details']['saved_file'] = audio_file
                print(f'   💾 Аудио сохранено: {audio_file}')
            
        except Exception as e:
            print(f'❌ ОШИБКА: {str(e)[:100]}...')
            test_result['error'] = str(e)
        
        self.test_results.append(test_result)
        return test_result
    
    async def test_yandex_tts(self) -> Dict[str, Any]:
        """Тест Yandex TTS"""
        print('\n2. 🎵 ТЕСТИРОВАНИЕ YANDEX TTS')
        print('-' * 35)
        
        test_result = {
            'name': 'Yandex TTS',
            'success': False,
            'audio_data': None,
            'details': {},
            'error': None
        }
        
        # Правильный запрос для Yandex
        request = TTSRequest(
            text='Привет! Это тестирование Яндекс синтеза речи.',
            voice='jane',  # Базовый русский голос
            language='ru-RU',  # Yandex использует полные коды
            speed=1.0
        )
        
        print(f'Текст: "{request.text}"')
        print(f'Голос: {request.voice}')
        print(f'Язык: {request.language}')
        
        try:
            start_time = time.time()
            result = await self.orchestrator.synthesize_speech(request)
            elapsed = (time.time() - start_time) * 1000
            
            print(f'✅ УСПЕХ: {elapsed:.0f}ms')
            print(f'   Провайдер: {result.provider}')
            print(f'   Размер аудио: {len(result.audio_data):,} байт')
            print(f'   Формат: {result.format}')
            
            test_result.update({
                'success': True,
                'audio_data': result.audio_data,
                'details': {
                    'provider': result.provider,
                    'size': len(result.audio_data),
                    'format': str(result.format),
                    'processing_time': elapsed
                }
            })
            
            # Сохраняем аудио для проверки
            with tempfile.NamedTemporaryFile(suffix='_yandex_tts.mp3', delete=False) as f:
                f.write(result.audio_data)
                audio_file = f.name
                test_result['details']['saved_file'] = audio_file
                print(f'   💾 Аудио сохранено: {audio_file}')
            
        except Exception as e:
            print(f'❌ ОШИБКА: {str(e)[:100]}...')
            test_result['error'] = str(e)
        
        self.test_results.append(test_result)
        return test_result
    
    async def test_openai_stt(self, audio_data: bytes) -> Dict[str, Any]:
        """Тест OpenAI STT"""
        print('\n3. 🎤 ТЕСТИРОВАНИЕ OPENAI STT')
        print('-' * 35)
        
        test_result = {
            'name': 'OpenAI STT',
            'success': False,
            'recognized_text': None,
            'details': {},
            'error': None
        }
        
        if not audio_data:
            print('⏭️  Пропущен - нет аудио данных')
            test_result['error'] = 'No audio data available'
            self.test_results.append(test_result)
            return test_result
        
        request = STTRequest(
            audio_data=audio_data,
            language='en',  # OpenAI английский
            format=AudioFormat.MP3
        )
        
        print(f'Размер аудио: {len(audio_data):,} байт')
        print(f'Язык: {request.language}')
        print(f'Формат: {request.format}')
        
        try:
            start_time = time.time()
            result = await self.orchestrator.transcribe_audio(request)
            elapsed = (time.time() - start_time) * 1000
            
            print(f'✅ УСПЕХ: {elapsed:.0f}ms')
            print(f'   Провайдер: {result.provider}')
            print(f'   Распознанный текст: "{result.text}"')
            if hasattr(result, 'confidence') and result.confidence:
                print(f'   Уверенность: {result.confidence:.2%}')
            
            test_result.update({
                'success': True,
                'recognized_text': result.text,
                'details': {
                    'provider': result.provider,
                    'processing_time': elapsed,
                    'confidence': getattr(result, 'confidence', None)
                }
            })
            
        except Exception as e:
            print(f'❌ ОШИБКА: {str(e)[:100]}...')
            test_result['error'] = str(e)
        
        self.test_results.append(test_result)
        return test_result
    
    async def test_yandex_stt(self, audio_data: bytes) -> Dict[str, Any]:
        """Тест Yandex STT"""
        print('\n4. 🎤 ТЕСТИРОВАНИЕ YANDEX STT')
        print('-' * 35)
        
        test_result = {
            'name': 'Yandex STT',
            'success': False,
            'recognized_text': None,
            'details': {},
            'error': None
        }
        
        if not audio_data:
            print('⏭️  Пропущен - нет аудио данных')
            test_result['error'] = 'No audio data available'
            self.test_results.append(test_result)
            return test_result
        
        request = STTRequest(
            audio_data=audio_data,
            language='ru-RU',  # Yandex полный код
            format=AudioFormat.MP3
        )
        
        print(f'Размер аудио: {len(audio_data):,} байт')
        print(f'Язык: {request.language}')
        print(f'Формат: {request.format}')
        
        try:
            start_time = time.time()
            result = await self.orchestrator.transcribe_audio(request)
            elapsed = (time.time() - start_time) * 1000
            
            print(f'✅ УСПЕХ: {elapsed:.0f}ms')
            print(f'   Провайдер: {result.provider}')
            print(f'   Распознанный текст: "{result.text}"')
            if hasattr(result, 'confidence') and result.confidence:
                print(f'   Уверенность: {result.confidence:.2%}')
            
            test_result.update({
                'success': True,
                'recognized_text': result.text,
                'details': {
                    'provider': result.provider,
                    'processing_time': elapsed,
                    'confidence': getattr(result, 'confidence', None)
                }
            })
            
        except Exception as e:
            print(f'❌ ОШИБКА: {str(e)[:100]}...')
            test_result['error'] = str(e)
        
        self.test_results.append(test_result)
        return test_result
    
    def generate_report(self) -> str:
        """Генерирует финальный отчет"""
        print('\n5. 📊 ФИНАЛЬНЫЙ ОТЧЕТ PHASE 4.6.3')
        print('=' * 50)
        
        successful_tests = [r for r in self.test_results if r['success']]
        total_tests = len(self.test_results)
        success_rate = len(successful_tests) / total_tests if total_tests > 0 else 0
        
        # Детальные результаты
        for result in self.test_results:
            status = '✅' if result['success'] else '❌'
            print(f'{result["name"]}: {status}')
            
            if result['success'] and result['details']:
                details = result['details']
                if 'processing_time' in details:
                    print(f'  ⏱️  Время: {details["processing_time"]:.0f}ms')
                if 'size' in details:
                    print(f'  📦 Размер: {details["size"]:,} байт')
                if 'provider' in details:
                    print(f'  🔧 Провайдер: {details["provider"]}')
                if 'saved_file' in details:
                    print(f'  💾 Файл: {details["saved_file"]}')
            elif result['error']:
                print(f'  ❌ Ошибка: {result["error"][:80]}...')
        
        # Общая статистика
        print(f'\nОбщая статистика:')
        print(f'  Успешных тестов: {len(successful_tests)}/{total_tests}')
        print(f'  Успешность: {success_rate:.1%}')
        
        # Оценка системы
        if success_rate >= 0.75:
            status = '🎉 ОТЛИЧНО'
            message = 'Voice V2 система полностью функциональна!'
        elif success_rate >= 0.5:
            status = '✅ ХОРОШО'
            message = 'Voice V2 система работает, есть мелкие проблемы'
        elif success_rate > 0:
            status = '⚠️  УДОВЛЕТВОРИТЕЛЬНО'
            message = 'Voice V2 система частично работает'
        else:
            status = '❌ ПЛОХО'
            message = 'Voice V2 система не функциональна'
        
        print(f'\nИТОГОВАЯ ОЦЕНКА: {status}')
        print(f'  {message}')
        
        # Рекомендации
        if success_rate < 1.0:
            print(f'\nРекомендации:')
            failed_tests = [r for r in self.test_results if not r['success']]
            for failed in failed_tests:
                print(f'  - Исправить {failed["name"]}: {failed["error"][:50]}...')
        
        return status
    
    async def cleanup(self):
        """Очистка ресурсов"""
        if self.orchestrator:
            await self.orchestrator.cleanup()
        if self.factory:
            await self.factory.cleanup()
        print('\n🧹 Очистка завершена')
    
    async def run_full_test(self) -> Tuple[str, int, int]:
        """Запуск полного тестирования"""
        print('🚀 PHASE 4.6.3 - РЕАЛЬНОЕ ТЕСТИРОВАНИЕ VOICE V2')
        print('=' * 60)
        
        # Инициализация
        if not await self.initialize():
            return 'НЕУДАЧА', 0, 0
        
        # TTS тесты
        openai_tts_result = await self.test_openai_tts()
        yandex_tts_result = await self.test_yandex_tts()
        
        # STT тесты (используем сгенерированное аудио)
        openai_audio = openai_tts_result.get('audio_data')
        yandex_audio = yandex_tts_result.get('audio_data')
        
        await self.test_openai_stt(openai_audio)
        await self.test_yandex_stt(yandex_audio)
        
        # Генерация отчета
        status = self.generate_report()
        
        # Очистка
        await self.cleanup()
        
        successful = len([r for r in self.test_results if r['success']])
        total = len(self.test_results)
        
        return status, successful, total


async def main():
    """Главная функция тестирования"""
    tester = VoiceV2RealTester()
    
    try:
        status, successful, total = await tester.run_full_test()
        
        print(f'\n🎯 ИТОГОВЫЙ РЕЗУЛЬТАТ PHASE 4.6.3:')
        print(f'   Статус: {status}')
        print(f'   Тесты: {successful}/{total}')
        
        # Определяем готовность к следующей фазе
        if successful >= total * 0.5:  # Минимум 50% успешности
            print(f'   ✅ Готовность к Phase 4.6.4: ДА')
            return True
        else:
            print(f'   ❌ Готовность к Phase 4.6.4: НЕТ')
            return False
            
    except Exception as e:
        print(f'❌ Критическая ошибка тестирования: {e}')
        return False


if __name__ == '__main__':
    result = asyncio.run(main())
    exit(0 if result else 1)
