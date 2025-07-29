"""
Comprehensive test suite для voice_v2 utils/audio.py.

Покрывает все функции AudioProcessor с акцентом на:
- Определение и валидация форматов
- Конвертация между форматами
- Performance testing
- Error handling
- Edge cases

100% code coverage требуется для production.
"""

import pytest
import io
import time
from unittest.mock import Mock, patch

from app.services.voice_v2.utils.audio import (
    AudioProcessor,
    AudioFormat,
    AudioLimits,
    AudioMimeTypes,
    PYDUB_AVAILABLE
)


# ==================== FIXTURES ====================

@pytest.fixture
def audio_processor():
    """Базовый AudioProcessor для тестов."""
    return AudioProcessor()


@pytest.fixture
def logger_mock():
    """Mock logger для тестирования логирования."""
    return Mock()


@pytest.fixture
def audio_processor_with_mock_logger(logger_mock):
    """AudioProcessor с mock logger."""
    return AudioProcessor(logger=logger_mock)


# Тестовые аудиоданные (минимальные валидные заголовки)
@pytest.fixture
def mp3_header():
    """Минимальный MP3 заголовок."""
    return b'ID3\x03\x00\x00\x00\x00\x00\x00\xff\xfb\x90\x00'


@pytest.fixture
def wav_header():
    """Минимальный WAV заголовок."""
    return b'RIFF\x24\x00\x00\x00WAVE'


@pytest.fixture
def ogg_header():
    """Минимальный OGG заголовок."""
    return b'OggS\x00\x02\x00\x00\x00\x00\x00\x00\x00\x00'


@pytest.fixture
def flac_header():
    """Минимальный FLAC заголовок."""
    return b'fLaC\x00\x00\x00\x22' + b'\x00' * 20  # Достаточно данных для определения


@pytest.fixture
def opus_header():
    """Минимальный OPUS заголовок."""
    return b'OggSOpusHead\x01\x02\x38\x01\x80\xbb\x00\x00'


@pytest.fixture
def large_audio_data():
    """Большой аудиофайл для тестирования лимитов."""
    # 30MB данных
    return b'RIFF\x24\x00\x00\x00WAVE' + b'\x00' * (30 * 1024 * 1024)


# ==================== ТЕСТЫ КОНСТРУКТОРА ====================

class TestAudioProcessorInit:
    """Тесты инициализации AudioProcessor."""

    def test_init_without_logger(self):
        """Тест инициализации без логгера."""
        processor = AudioProcessor()
        assert processor.logger is not None
        assert processor.logger.name == "app.services.voice_v2.utils.audio"

    def test_init_with_logger(self, logger_mock):
        """Тест инициализации с пользовательским логгером."""
        processor = AudioProcessor(logger=logger_mock)
        assert processor.logger is logger_mock

    def test_validate_dependencies_pydub_available(self, audio_processor_with_mock_logger):
        """Тест валидации зависимостей когда pydub доступен."""
        with patch('app.services.voice_v2.utils.audio.PYDUB_AVAILABLE', True):
            processor = AudioProcessor(logger=audio_processor_with_mock_logger.logger)
            # Не должно быть warning'ов если pydub доступен
            audio_processor_with_mock_logger.logger.warning.assert_not_called()

    def test_validate_dependencies_pydub_unavailable(self, audio_processor_with_mock_logger):
        """Тест валидации зависимостей когда pydub недоступен."""
        with patch('app.services.voice_v2.utils.audio.PYDUB_AVAILABLE', False):
            processor = AudioProcessor(logger=audio_processor_with_mock_logger.logger)
            # Должен быть warning о недоступности pydub
            audio_processor_with_mock_logger.logger.warning.assert_called_once()


# ==================== ТЕСТЫ ОПРЕДЕЛЕНИЯ ФОРМАТА ====================

class TestAudioFormatDetection:
    """Тесты определения аудиоформатов."""

    def test_detect_mp3_by_id3_header(self, audio_processor, mp3_header):
        """Тест определения MP3 по ID3 заголовку."""
        result = audio_processor.detect_format(mp3_header)
        assert result == AudioFormat.MP3

    def test_detect_mp3_by_mpeg_frame_header(self, audio_processor):
        """Тест определения MP3 по MPEG frame заголовку."""
        mpeg_headers = [
            b'\xff\xfb' + b'\x00' * 10,  # MPEG Layer 3
            b'\xff\xf3' + b'\x00' * 10,  # MPEG Layer 3
            b'\xff\xf2' + b'\x00' * 10   # MPEG Layer 2
        ]

        for header in mpeg_headers:
            result = audio_processor.detect_format(header)
            assert result == AudioFormat.MP3

    def test_detect_wav_format(self, audio_processor, wav_header):
        """Тест определения WAV формата."""
        result = audio_processor.detect_format(wav_header)
        assert result == AudioFormat.WAV

    def test_detect_ogg_format(self, audio_processor, ogg_header):
        """Тест определения OGG формата."""
        result = audio_processor.detect_format(ogg_header)
        assert result == AudioFormat.OGG

    def test_detect_flac_format(self, audio_processor, flac_header):
        """Тест определения FLAC формата."""
        result = audio_processor.detect_format(flac_header)
        assert result == AudioFormat.FLAC

    def test_detect_opus_format(self, audio_processor, opus_header):
        """Тест определения OPUS формата."""
        result = audio_processor.detect_format(opus_header)
        assert result == AudioFormat.OPUS

    def test_detect_aac_format(self, audio_processor):
        """Тест определения AAC формата."""
        aac_headers = [
            b'\xff\xf1' + b'\x00' * 10,  # AAC ADTS
            b'\xff\xf9' + b'\x00' * 10   # AAC ADTS
        ]

        for header in aac_headers:
            result = audio_processor.detect_format(header)
            assert result == AudioFormat.AAC

    def test_detect_format_by_filename_fallback(self, audio_processor):
        """Тест fallback на определение по расширению файла."""
        unknown_data = b'\x00\x01\x02\x03' * 10

        test_cases = [
            ("audio.mp3", AudioFormat.MP3),
            ("audio.wav", AudioFormat.WAV),
            ("audio.wave", AudioFormat.WAV),
            ("audio.ogg", AudioFormat.OGG),
            ("audio.opus", AudioFormat.OPUS),
            ("audio.flac", AudioFormat.FLAC),
            ("audio.aac", AudioFormat.AAC),
            ("audio.m4a", AudioFormat.AAC),
            ("audio.pcm", AudioFormat.PCM),
        ]

        for filename, expected_format in test_cases:
            result = audio_processor.detect_format(unknown_data, filename)
            assert result == expected_format

    def test_detect_format_unknown_extension(self, audio_processor):
        """Тест обработки неизвестного расширения."""
        unknown_data = b'\x00\x01\x02\x03' * 10
        result = audio_processor.detect_format(unknown_data, "audio.unknown")
        assert result == AudioFormat.WAV  # По умолчанию WAV

    def test_detect_format_no_filename(self, audio_processor):
        """Тест определения формата без имени файла."""
        unknown_data = b'\x00\x01\x02\x03' * 10
        result = audio_processor.detect_format(unknown_data)
        assert result == AudioFormat.WAV  # По умолчанию WAV

    def test_detect_format_empty_data(self, audio_processor):
        """Тест обработки пустых данных."""
        with pytest.raises(ValueError, match="Пустые аудиоданные"):
            audio_processor.detect_format(b"")

    def test_detect_format_short_data(self, audio_processor):
        """Тест обработки слишком коротких данных."""
        short_data = b'\x00\x01'  # Меньше 12 байт
        result = audio_processor.detect_format(short_data, "audio.mp3")
        assert result == AudioFormat.MP3  # Должен использовать filename fallback


# ==================== ТЕСТЫ ВАЛИДАЦИИ ====================

class TestAudioValidation:
    """Тесты валидации аудиофайлов."""

    def test_validate_empty_data(self, audio_processor):
        """Тест валидации пустых данных."""
        result = audio_processor.validate_audio(b"")
        assert not result.is_valid
        assert "Пустые аудиоданные" in result.error_message

    def test_validate_file_size_limit(self, audio_processor, large_audio_data):
        """Тест валидации лимита размера файла."""
        result = audio_processor.validate_audio(large_audio_data, max_size_mb=10)
        assert not result.is_valid
        assert "слишком большой" in result.error_message
        assert result.size_bytes == len(large_audio_data)

    def test_validate_successful_basic(self, audio_processor, wav_header):
        """Тест успешной базовой валидации."""
        result = audio_processor.validate_audio(wav_header)
        assert result.is_valid
        assert result.format == AudioFormat.WAV
        assert result.size_bytes == len(wav_header)
        assert result.error_message is None

    @pytest.mark.skipif(not PYDUB_AVAILABLE, reason="pydub не доступен")
    def test_validate_with_pydub_metadata(self, audio_processor):
        """Тест извлечения метаданных через pydub."""
        # Создаем минимальный WAV файл для pydub
        with patch('app.services.voice_v2.utils.audio.AudioSegment') as mock_audio_segment:
            mock_segment = Mock()
            mock_segment.__len__ = Mock(return_value=5000)  # 5 секунд в миллисекундах
            mock_segment.frame_rate = 44100
            mock_segment.channels = 2
            mock_audio_segment.from_file.return_value = mock_segment

            result = audio_processor.validate_audio(b'RIFF\x24\x00\x00\x00WAVE')

            assert result.is_valid
            assert result.duration_seconds == 5.0
            assert result.sample_rate == 44100
            assert result.channels == 2

    def test_validate_duration_too_long(self, audio_processor):
        """Тест валидации слишком длинного аудио."""
        with patch('app.services.voice_v2.utils.audio.AudioSegment') as mock_audio_segment:
            mock_segment = Mock()
            mock_segment.__len__ = Mock(return_value=700000)  # 700 секунд в миллисекундах
            mock_audio_segment.from_file.return_value = mock_segment

            result = audio_processor.validate_audio(
                b'RIFF\x24\x00\x00\x00WAVE',
                max_duration_seconds=600
            )

            assert not result.is_valid
            assert "слишком длинное" in result.error_message

    def test_validate_duration_too_short(self, audio_processor):
        """Тест валидации слишком короткого аудио."""
        with patch('app.services.voice_v2.utils.audio.AudioSegment') as mock_audio_segment:
            mock_segment = Mock()
            mock_segment.__len__ = Mock(return_value=50)  # 0.05 секунд
            mock_audio_segment.from_file.return_value = mock_segment

            result = audio_processor.validate_audio(b'RIFF\x24\x00\x00\x00WAVE')

            assert not result.is_valid
            assert "слишком короткое" in result.error_message

    def test_validate_pydub_error_handling(self, audio_processor_with_mock_logger):
        """Тест обработки ошибок pydub."""
        with patch('app.services.voice_v2.utils.audio.AudioSegment') as mock_audio_segment:
            mock_audio_segment.from_file.side_effect = Exception("pydub error")

            result = audio_processor_with_mock_logger.validate_audio(b'RIFF\x24\x00\x00\x00WAVE')

            assert result.is_valid  # Базовая валидация должна пройти
            audio_processor_with_mock_logger.logger.warning.assert_called_once()

    def test_validate_general_exception(self, audio_processor_with_mock_logger):
        """Тест обработки общих исключений."""
        with patch.object(audio_processor_with_mock_logger, 'detect_format', side_effect=Exception("test error")):
            result = audio_processor_with_mock_logger.validate_audio(b'some data')

            assert not result.is_valid
            assert "Ошибка валидации" in result.error_message
            audio_processor_with_mock_logger.logger.error.assert_called_once()


# ==================== ТЕСТЫ КОНВЕРТАЦИИ ====================

class TestAudioConversion:
    """Тесты конвертации аудио."""

    @pytest.mark.asyncio
    async def test_convert_without_pydub(self, audio_processor):
        """Тест конвертации без pydub."""
        with patch('app.services.voice_v2.utils.audio.PYDUB_AVAILABLE', False):
            processor = AudioProcessor()
            result = await processor.convert_audio(
                b'test data',
                AudioFormat.MP3
            )

            assert not result.success
            assert "pydub недоступен" in result.error_message

    @pytest.mark.asyncio
    async def test_convert_same_format_no_params(self, audio_processor, wav_header):
        """Тест конвертации в тот же формат без параметров."""
        with patch.object(audio_processor, 'detect_format', return_value=AudioFormat.WAV):
            result = await audio_processor.convert_audio(
                wav_header,
                AudioFormat.WAV
            )

            assert result.success
            assert result.audio_data == wav_header
            assert result.output_format == AudioFormat.WAV
            assert result.conversion_time_ms == 0

    @pytest.mark.asyncio
    @pytest.mark.skipif(not PYDUB_AVAILABLE, reason="pydub не доступен")
    async def test_convert_successful_conversion(self, audio_processor):
        """Тест успешной конвертации."""
        test_audio_data = b'RIFF\x24\x00\x00\x00WAVE'
        converted_data = b'converted audio data'

        with patch.object(audio_processor, 'detect_format', return_value=AudioFormat.WAV):
            with patch.object(audio_processor, '_perform_conversion', return_value=converted_data):
                result = await audio_processor.convert_audio(
                    test_audio_data,
                    AudioFormat.MP3
                )

                assert result.success
                assert result.audio_data == converted_data
                assert result.output_format == AudioFormat.MP3
                assert result.original_size == len(test_audio_data)
                assert result.converted_size == len(converted_data)
                assert result.conversion_time_ms > 0

    @pytest.mark.asyncio
    async def test_convert_with_parameters(self, audio_processor):
        """Тест конвертации с параметрами."""
        test_audio_data = b'test data'

        with patch.object(audio_processor, '_perform_conversion', return_value=b'converted') as mock_convert:
            result = await audio_processor.convert_audio(
                test_audio_data,
                AudioFormat.MP3,
                source_format=AudioFormat.WAV,
                sample_rate=44100,
                channels=1
            )

            mock_convert.assert_called_once_with(
                test_audio_data,
                AudioFormat.WAV,
                AudioFormat.MP3,
                44100,
                1
            )

    @pytest.mark.asyncio
    async def test_convert_error_handling(self, audio_processor_with_mock_logger):
        """Тест обработки ошибок конвертации."""
        with patch.object(audio_processor_with_mock_logger, '_perform_conversion', side_effect=Exception("conversion error")):
            result = await audio_processor_with_mock_logger.convert_audio(
                b'test data',
                AudioFormat.MP3
            )

            assert not result.success
            assert "Ошибка конвертации" in result.error_message
            assert result.conversion_time_ms > 0
            audio_processor_with_mock_logger.logger.error.assert_called_once()


# ==================== ТЕСТЫ СИНХРОННОЙ КОНВЕРТАЦИИ ====================

class TestSyncConversion:
    """Тесты синхронной конвертации."""

    @pytest.mark.skipif(not PYDUB_AVAILABLE, reason="pydub не доступен")
    def test_perform_conversion_basic(self, audio_processor):
        """Тест базовой синхронной конвертации."""
        test_data = b'audio data'

        with patch('app.services.voice_v2.utils.audio.AudioSegment') as mock_audio_segment:
            mock_segment = Mock()
            mock_audio_segment.from_file.return_value = mock_segment

            # Mock для export
            export_buffer = io.BytesIO()
            export_buffer.write(b'converted data')
            export_buffer.seek(0)

            with patch('io.BytesIO', return_value=export_buffer):
                result = audio_processor._perform_conversion(
                    test_data,
                    AudioFormat.WAV,
                    AudioFormat.MP3,
                    None,
                    None
                )

                assert isinstance(result, bytes)
                mock_audio_segment.from_file.assert_called_once()
                mock_segment.export.assert_called_once()

    @pytest.mark.skipif(not PYDUB_AVAILABLE, reason="pydub не доступен")
    def test_perform_conversion_with_sample_rate(self, audio_processor):
        """Тест конвертации с изменением частоты дискретизации."""
        with patch('app.services.voice_v2.utils.audio.AudioSegment') as mock_audio_segment:
            mock_segment = Mock()
            mock_audio_segment.from_file.return_value = mock_segment

            with patch('io.BytesIO'):
                audio_processor._perform_conversion(
                    b'test',
                    AudioFormat.WAV,
                    AudioFormat.MP3,
                    44100,
                    None
                )

                mock_segment.set_frame_rate.assert_called_once_with(44100)

    @pytest.mark.skipif(not PYDUB_AVAILABLE, reason="pydub не доступен")
    def test_perform_conversion_with_channels(self, audio_processor):
        """Тест конвертации с изменением количества каналов."""
        with patch('app.services.voice_v2.utils.audio.AudioSegment') as mock_audio_segment:
            mock_segment = Mock()
            mock_audio_segment.from_file.return_value = mock_segment

            with patch('io.BytesIO'):
                # Тест mono
                audio_processor._perform_conversion(
                    b'test',
                    AudioFormat.WAV,
                    AudioFormat.MP3,
                    None,
                    1
                )
                mock_segment.set_channels.assert_called_with(1)

                # Тест stereo
                audio_processor._perform_conversion(
                    b'test',
                    AudioFormat.WAV,
                    AudioFormat.MP3,
                    None,
                    2
                )
                mock_segment.set_channels.assert_called_with(2)

    def test_get_export_parameters(self, audio_processor):
        """Тест получения параметров экспорта."""
        # MP3
        params = audio_processor._get_export_parameters(AudioFormat.MP3)
        assert 'bitrate' in params
        assert params['bitrate'] == '128k'

        # WAV
        params = audio_processor._get_export_parameters(AudioFormat.WAV)
        assert 'parameters' in params
        assert '-acodec' in params['parameters']

        # OGG
        params = audio_processor._get_export_parameters(AudioFormat.OGG)
        assert 'codec' in params
        assert params['codec'] == 'libvorbis'

        # FLAC
        params = audio_processor._get_export_parameters(AudioFormat.FLAC)
        assert 'parameters' in params
        assert '-compression_level' in params['parameters']

        # Неизвестный формат
        params = audio_processor._get_export_parameters(AudioFormat.AAC)
        assert params == {}


# ==================== ТЕСТЫ УТИЛИТАРНЫХ МЕТОДОВ ====================

class TestUtilityMethods:
    """Тесты утилитарных методов."""

    def test_calculate_audio_hash(self):
        """Тест вычисления хеша аудио."""
        test_data = b'test audio data'
        hash1 = AudioProcessor.calculate_audio_hash(test_data)
        hash2 = AudioProcessor.calculate_audio_hash(test_data)

        assert hash1 == hash2  # Одинаковые данные = одинаковый хеш
        assert len(hash1) == 64  # SHA-256 хеш

        # Разные данные = разные хеши
        hash3 = AudioProcessor.calculate_audio_hash(b'different data')
        assert hash1 != hash3

    def test_get_mime_type(self):
        """Тест получения MIME типов."""
        test_cases = [
            (AudioFormat.MP3, "audio/mpeg"),
            (AudioFormat.WAV, "audio/wav"),
            (AudioFormat.OGG, "audio/ogg"),
            (AudioFormat.OPUS, "audio/opus"),
            (AudioFormat.FLAC, "audio/flac"),
            (AudioFormat.AAC, "audio/aac"),
            (AudioFormat.PCM, "audio/pcm"),
        ]

        for audio_format, expected_mime in test_cases:
            result = AudioProcessor.get_mime_type(audio_format)
            assert result == expected_mime

    def test_is_format_supported(self):
        """Тест проверки поддержки форматов."""
        supported_formats = [
            AudioFormat.MP3,
            AudioFormat.WAV,
            AudioFormat.OGG,
            AudioFormat.OPUS,
            AudioFormat.FLAC,
            AudioFormat.AAC,
            AudioFormat.PCM
        ]

        for audio_format in supported_formats:
            assert AudioProcessor.is_format_supported(audio_format)


# ==================== ТЕСТЫ ПРОИЗВОДИТЕЛЬНОСТИ ====================

class TestPerformance:
    """Тесты производительности."""

    def test_format_detection_performance(self, audio_processor):
        """Тест производительности определения формата."""
        test_data = b'ID3\x03\x00\x00\x00\x00\x00\x00\xff\xfb\x90\x00' * 1000

        start_time = time.time()
        for _ in range(1000):
            audio_processor.detect_format(test_data)
        end_time = time.time()

        # Должно быть быстрее 1 секунды для 1000 операций
        assert (end_time - start_time) < 1.0

    def test_hash_calculation_performance(self):
        """Тест производительности вычисления хеша."""
        test_data = b'test audio data' * 10000  # ~150KB

        start_time = time.time()
        for _ in range(100):
            AudioProcessor.calculate_audio_hash(test_data)
        end_time = time.time()

        # Должно быть быстрее 1 секунды для 100 операций
        assert (end_time - start_time) < 1.0

    @pytest.mark.asyncio
    async def test_conversion_timeout(self, audio_processor):
        """Тест таймаута конвертации."""
        with patch.object(audio_processor, '_perform_conversion', side_effect=lambda *args: time.sleep(5)):
            start_time = time.time()

            result = await audio_processor.convert_audio(
                b'test data',
                AudioFormat.MP3
            )

            end_time = time.time()
            # Должно завершиться с ошибкой, но не висеть
            assert not result.success
            assert (end_time - start_time) < 10  # Максимум 10 секунд


# ==================== ТЕСТЫ ИНТЕГРАЦИИ ====================

class TestIntegration:
    """Интеграционные тесты."""

    @pytest.mark.asyncio
    async def test_full_workflow_validation_and_conversion(self, audio_processor):
        """Тест полного workflow: валидация + конвертация."""
        test_data = b'RIFF\x24\x00\x00\x00WAVE' + b'\x00' * 1000

        # 1. Валидация
        validation_result = audio_processor.validate_audio(test_data, max_size_mb=1)
        assert validation_result.is_valid

        # 2. Конвертация (если pydub доступен)
        if PYDUB_AVAILABLE:
            with patch.object(audio_processor, '_perform_conversion', return_value=b'converted'):
                conversion_result = await audio_processor.convert_audio(
                    test_data,
                    AudioFormat.MP3
                )
                assert conversion_result.success

    def test_all_supported_formats_mime_types(self):
        """Тест всех поддерживаемых форматов и их MIME типов."""
        for audio_format in AudioFormat:
            if AudioProcessor.is_format_supported(audio_format):
                mime_type = AudioProcessor.get_mime_type(audio_format)
                assert mime_type.startswith("audio/")

    def test_constants_consistency(self):
        """Тест консистентности констант."""
        # Проверяем что все форматы в AudioMimeTypes.MAP поддерживаются
        for audio_format in AudioMimeTypes.MAP:
            assert AudioProcessor.is_format_supported(audio_format)

        # Проверяем разумные лимиты
        assert AudioLimits.MAX_FILE_SIZE_MB > 0
        assert AudioLimits.MAX_DURATION_SECONDS > AudioLimits.MIN_DURATION_SECONDS
        assert AudioLimits.DEFAULT_SAMPLE_RATE > 0
        assert AudioLimits.DEFAULT_CHANNELS in [1, 2]


# ==================== ФИКСТУРЫ ДЛЯ ПРОИЗВОДИТЕЛЬНОСТИ ====================

# Note: Benchmark тесты требуют pytest-benchmark пакет
# Можно установить: pip install pytest-benchmark

if __name__ == "__main__":
    pytest.main([__file__, "-v", "--cov=app.services.voice_v2.utils.audio", "--cov-report=term-missing"])
