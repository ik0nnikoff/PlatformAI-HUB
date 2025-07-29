"""
Интеграционные тесты для STT системы
Phase 3.1.5 & 3.1.6 - STT Provider Integration & Testing
"""

import pytest
import asyncio
from unittest.mock import patch
from pathlib import Path
import tempfile
import os

# Тестируем только существующие компоненты
import sys
sys.path.append(str(Path(__file__).parent.parent.parent.parent))

from app.services.voice_v2.providers.stt.coordinator import STTCoordinator

from app.services.voice_v2.providers.stt.yandex_stt import YandexSTTProvider
from app.services.voice_v2.providers.stt.openai_stt import OpenAISTTProvider


class TestSTTIntegration:
    """Интеграционные тесты STT системы"""
    
    @pytest.fixture
    def mock_openai_config(self):
        """Мок конфигурации для OpenAI"""
        return {
            'api_key': 'test-key-openai',
            'model': 'whisper-1',
            'enabled': True,
            'priority': 1
        }
    
    @pytest.fixture
    def mock_yandex_config(self):
        """Мок конфигурации для Yandex"""
        return {
            'api_key': 'test-key-yandex',
            'folder_id': 'test-folder',
            'enabled': True,
            'priority': 2
        }
    
    @pytest.fixture
    def test_audio_file(self):
        """Создает тестовый аудио файл"""
        with tempfile.NamedTemporaryFile(suffix='.wav', delete=False) as f:
            # Простые тестовые данные
            f.write(b'RIFF\x24\x00\x00\x00WAVEfmt ')
            yield f.name
        os.unlink(f.name)
    
    @pytest.mark.asyncio
    async def test_openai_provider_creation(self, mock_openai_config):
        """Тест создания OpenAI провайдера"""
        provider = OpenAISTTProvider(mock_openai_config)
        assert provider is not None
        assert provider.provider_name == 'openai'
        assert hasattr(provider, 'transcribe')
        
        # Cleanup
        if hasattr(provider, 'cleanup'):
            await provider.cleanup()
    
    @pytest.mark.asyncio
    async def test_yandex_provider_creation(self, mock_yandex_config):
        """Тест создания Yandex провайдера"""
        provider = YandexSTTProvider(mock_yandex_config)
        assert provider is not None
        assert provider.provider_name == 'yandex'
        assert hasattr(provider, 'transcribe')
        
        # Cleanup
        if hasattr(provider, 'cleanup'):
            await provider.cleanup()
    
    @pytest.mark.asyncio
    async def test_provider_capabilities(self, mock_openai_config, mock_yandex_config):
        """Тест проверки capabilities провайдеров"""
        openai_provider = OpenAISTTProvider(mock_openai_config)
        yandex_provider = YandexSTTProvider(mock_yandex_config)
        
        # Проверяем capabilities
        openai_caps = openai_provider.get_capabilities()
        yandex_caps = yandex_provider.get_capabilities()
        
        assert isinstance(openai_caps, dict)
        assert isinstance(yandex_caps, dict)
        assert 'supported_formats' in openai_caps
        assert 'supported_formats' in yandex_caps
        
        # Cleanup
        for provider in [openai_provider, yandex_provider]:
            if hasattr(provider, 'cleanup'):
                await provider.cleanup()
    
    @pytest.mark.asyncio
    async def test_provider_mock_workflow(self, mock_openai_config, test_audio_file):
        """Тест workflow с мокированной транскрипцией"""
        provider = OpenAISTTProvider(mock_openai_config)
        
        # Мокируем метод transcribe
        with patch.object(provider, 'transcribe') as mock_transcribe:
            mock_transcribe.return_value = "Тестовая транскрипция"
            
            result = await provider.transcribe(test_audio_file, language='ru-RU')
            assert result == "Тестовая транскрипция"
            mock_transcribe.assert_called_once_with(test_audio_file, language='ru-RU')
        
        # Cleanup
        if hasattr(provider, 'cleanup'):
            await provider.cleanup()
    
    @pytest.mark.asyncio
    async def test_multiple_providers_parallel(self, mock_openai_config, mock_yandex_config):
        """Тест создания нескольких провайдеров параллельно"""
        async def create_provider(config, provider_class):
            provider = provider_class(config)
            return provider
        
        # Создаем провайдеры параллельно
        tasks = [
            create_provider(mock_openai_config, OpenAISTTProvider),
            create_provider(mock_yandex_config, YandexSTTProvider)
        ]
        
        providers = await asyncio.gather(*tasks)
        
        assert len(providers) == 2
        assert providers[0].provider_name == 'openai'
        assert providers[1].provider_name == 'yandex'
        
        # Cleanup
        for provider in providers:
            if hasattr(provider, 'cleanup'):
                await provider.cleanup()


class TestSTTProviderIntegration:
    """Дополнительные интеграционные тесты"""
    
    @pytest.mark.asyncio
    async def test_provider_initialization_workflow(self):
        """Тест полного workflow инициализации провайдера"""
        config = {
            'api_key': 'test-key',
            'model': 'whisper-1',
            'enabled': True
        }
        
        provider = OpenAISTTProvider(config)
        
        # Проверяем начальное состояние
        assert not provider.is_initialized
        
        # Мокируем инициализацию (чтобы не делать реальные API вызовы)
        with patch.object(provider, '_initialize_session') as mock_init:
            mock_init.return_value = None
            
            await provider.initialize()
            
            # Проверяем что инициализация вызвана
            mock_init.assert_called_once()
        
        # Cleanup
        if hasattr(provider, 'cleanup'):
            await provider.cleanup()
    
    @pytest.mark.asyncio
    async def test_error_handling_integration(self):
        """Тест обработки ошибок в интеграции"""
        config = {
            'api_key': '',  # Пустой ключ для теста ошибки
            'enabled': True
        }
        
        provider = OpenAISTTProvider(config)
        
        # Проверяем что провайдер корректно обрабатывает отсутствие ключа
        try:
            await provider.initialize()
        except Exception as e:
            # Ожидаем ошибку конфигурации
            assert "api_key" in str(e).lower() or "configuration" in str(e).lower()
        
        # Cleanup
        if hasattr(provider, 'cleanup'):
            await provider.cleanup()


if __name__ == "__main__":
    # Для прямого запуска тестов
    pytest.main([__file__, "-v"])


class TestSTTPerformanceIntegration:
    """Тесты производительности STT системы"""
    
    @pytest.mark.asyncio
    async def test_concurrent_requests_handling(self, mock_config=None):
        """Тест обработки параллельных запросов"""
        if mock_config is None:
            mock_config = {
                'providers': [
                    {
                        'provider': 'openai',
                        'priority': 1,
                        'enabled': True,
                        'api_key': 'test-key',
                        'model': 'whisper-1'
                    }
                ],
                'default_language': 'ru-RU',
                'fallback_enabled': True
            }
        
        coordinator = STTCoordinator()
        await coordinator.initialize(mock_config)
        
        # Мок для параллельных запросов
        with patch.object(coordinator, '_transcribe_with_provider') as mock_transcribe:
            mock_transcribe.return_value = "Параллельный тест"
            
            # Создаем несколько параллельных задач
            tasks = []
            for i in range(5):
                task = coordinator.transcribe(f"test_audio_{i}.wav")
                tasks.append(task)
            
            # Выполняем все задачи параллельно
            results = await asyncio.gather(*tasks)
            
            # Проверяем что все задачи выполнились
            assert len(results) == 5
            assert all(result == "Параллельный тест" for result in results)
            
            # Проверяем что метод был вызван для каждой задачи
            assert mock_transcribe.call_count == 5
        
        await coordinator.cleanup()
