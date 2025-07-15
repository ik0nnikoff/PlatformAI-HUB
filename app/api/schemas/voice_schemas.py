"""
Схемы Pydantic для голосовых сервисов (STT/TTS)
"""

from enum import Enum
from typing import Optional, Dict, Any, List
from pydantic import BaseModel, Field, validator


class VoiceProvider(str, Enum):
    """Провайдеры голосовых сервисов"""
    OPENAI = "openai"
    GOOGLE = "google"
    YANDEX = "yandex"


class STTModel(str, Enum):
    """Модели для Speech-to-Text"""
    # OpenAI models
    WHISPER_1 = "whisper-1"
    
    # Google models
    GOOGLE_LATEST_LONG = "latest_long"
    GOOGLE_LATEST_SHORT = "latest_short"
    GOOGLE_COMMAND_AND_SEARCH = "command_and_search"
    
    # Yandex models
    YANDEX_GENERAL = "general"
    YANDEX_GENERAL_RC = "general:rc"
    YANDEX_GENERAL_DEPRECATED = "general:deprecated"


class TTSModel(str, Enum):
    """Модели для Text-to-Speech"""
    # OpenAI models
    TTS_1 = "tts-1"
    TTS_1_HD = "tts-1-hd"
    
    # Google models
    GOOGLE_STANDARD = "standard"
    GOOGLE_WAVENET = "wavenet"
    GOOGLE_NEURAL2 = "neural2"
    GOOGLE_POLYGLOT = "polyglot"
    GOOGLE_STUDIO = "studio"
    GOOGLE_JOURNEY = "journey"
    
    # Yandex models
    YANDEX_JANE = "jane"
    YANDEX_OKSANA = "oksana"
    YANDEX_OMAZH = "omazh"
    YANDEX_ZAHAR = "zahar"
    YANDEX_ERMIL = "ermil"


class OpenAIVoice(str, Enum):
    """Голоса OpenAI"""
    ALLOY = "alloy"
    ECHO = "echo"
    FABLE = "fable"
    ONYX = "onyx"
    NOVA = "nova"
    SHIMMER = "shimmer"


class AudioFormat(str, Enum):
    """Форматы аудиофайлов"""
    MP3 = "mp3"
    OPUS = "opus"
    AAC = "aac"
    FLAC = "flac"
    WAV = "wav"
    PCM = "pcm"
    OGG = "ogg"


class IntentDetectionMode(str, Enum):
    """Режимы определения намерения пользователя"""
    KEYWORDS = "keywords"  # По ключевым словам
    ALWAYS = "always"      # Всегда обрабатывать голос
    DISABLED = "disabled"  # Отключено


class STTConfig(BaseModel):
    """Настройки Speech-to-Text для провайдера"""
    enabled: bool = Field(default=True, description="Включен ли STT для этого провайдера")
    model: STTModel = Field(description="Модель для распознавания речи")
    language: str = Field(default="ru-RU", description="Язык распознавания (RFC 5646)")
    max_duration: int = Field(default=60, description="Максимальная длительность аудио в секундах")
    enable_word_time_offsets: bool = Field(default=False, description="Включить временные метки слов")
    enable_automatic_punctuation: bool = Field(default=True, description="Автоматическая пунктуация")
    enable_profanity_filter: bool = Field(default=False, description="Фильтр нецензурной лексики")
    audio_channel_count: int = Field(default=1, description="Количество аудиоканалов")
    sample_rate_hertz: int = Field(default=16000, description="Частота дискретизации в Гц")
    custom_params: Dict[str, Any] = Field(default_factory=dict, description="Дополнительные параметры провайдера")

    @validator('max_duration')
    def validate_max_duration(cls, v):
        if v <= 0 or v > 600:  # 10 minutes max
            raise ValueError('max_duration должен быть от 1 до 600 секунд')
        return v

    @validator('sample_rate_hertz')
    def validate_sample_rate(cls, v):
        valid_rates = [8000, 16000, 22050, 44100, 48000]
        if v not in valid_rates:
            raise ValueError(f'sample_rate_hertz должен быть одним из: {valid_rates}')
        return v


class TTSConfig(BaseModel):
    """Настройки Text-to-Speech для провайдера"""
    enabled: bool = Field(default=True, description="Включен ли TTS для этого провайдера")
    model: TTSModel = Field(description="Модель для синтеза речи")
    voice: str = Field(description="Голос для синтеза")
    language: str = Field(default="ru-RU", description="Язык синтеза")
    speed: float = Field(default=1.0, description="Скорость речи (0.25-4.0)")
    pitch: float = Field(default=0.0, description="Высота тона (-20.0 до 20.0)")
    volume_gain_db: float = Field(default=0.0, description="Усиление громкости в дБ")
    audio_format: AudioFormat = Field(default=AudioFormat.MP3, description="Формат выходного аудио")
    sample_rate: int = Field(default=22050, description="Частота дискретизации")
    custom_params: Dict[str, Any] = Field(default_factory=dict, description="Дополнительные параметры провайдера")

    @validator('speed')
    def validate_speed(cls, v):
        if v < 0.25 or v > 4.0:
            raise ValueError('speed должен быть от 0.25 до 4.0')
        return v

    @validator('pitch')
    def validate_pitch(cls, v):
        if v < -20.0 or v > 20.0:
            raise ValueError('pitch должен быть от -20.0 до 20.0')
        return v


class VoiceProviderConfig(BaseModel):
    """Настройки для конкретного провайдера голосовых сервисов"""
    provider: VoiceProvider = Field(description="Тип провайдера")
    priority: int = Field(default=1, description="Приоритет провайдера (чем меньше, тем выше)")
    stt_config: Optional[STTConfig] = Field(default=None, description="Настройки STT")
    tts_config: Optional[TTSConfig] = Field(default=None, description="Настройки TTS")
    fallback_enabled: bool = Field(default=True, description="Использовать как fallback")
    custom_settings: Dict[str, Any] = Field(default_factory=dict, description="Пользовательские настройки провайдера")


class VoiceSettings(BaseModel):
    """Общие настройки голосовых функций агента"""
    enabled: bool = Field(default=False, description="Включены ли голосовые функции")
    intent_detection_mode: IntentDetectionMode = Field(
        default=IntentDetectionMode.KEYWORDS,
        description="Режим определения намерения пользователя"
    )
    intent_keywords: List[str] = Field(
        default_factory=lambda: ["голос", "скажи", "произнеси", "озвучь"],
        description="Ключевые слова для определения намерения"
    )
    auto_stt: bool = Field(default=True, description="Автоматически обрабатывать голосовые сообщения")
    auto_tts_on_keywords: bool = Field(default=True, description="Автоматически озвучивать ответы при наличии ключевых слов")
    max_file_size_mb: int = Field(default=25, description="Максимальный размер аудиофайла в МБ")
    cache_enabled: bool = Field(default=True, description="Включить кэширование результатов")
    cache_ttl_hours: int = Field(default=24, description="TTL кэша в часах")
    rate_limit_per_minute: int = Field(default=10, description="Лимит запросов в минуту")
    providers: List[VoiceProviderConfig] = Field(
        default_factory=list,
        description="Список настроенных провайдеров"
    )

    @validator('max_file_size_mb')
    def validate_max_file_size(cls, v):
        if v <= 0 or v > 100:
            raise ValueError('max_file_size_mb должен быть от 1 до 100 МБ')
        return v

    @validator('cache_ttl_hours')
    def validate_cache_ttl(cls, v):
        if v < 1 or v > 168:  # 1 week max
            raise ValueError('cache_ttl_hours должен быть от 1 до 168 часов')
        return v

    @validator('rate_limit_per_minute')
    def validate_rate_limit(cls, v):
        if v <= 0 or v > 100:
            raise ValueError('rate_limit_per_minute должен быть от 1 до 100')
        return v

    def get_stt_providers(self) -> List[VoiceProviderConfig]:
        """Получить провайдеров STT отсортированных по приоритету"""
        stt_providers = [p for p in self.providers if p.stt_config and p.stt_config.enabled]
        return sorted(stt_providers, key=lambda x: x.priority)

    def get_tts_providers(self) -> List[VoiceProviderConfig]:
        """Получить провайдеров TTS отсортированных по приоритету"""
        tts_providers = [p for p in self.providers if p.tts_config and p.tts_config.enabled]
        return sorted(tts_providers, key=lambda x: x.priority)

    def should_process_voice_intent(self, text: str) -> bool:
        """Определить, нужно ли обрабатывать голосовое намерение в тексте"""
        if self.intent_detection_mode == IntentDetectionMode.ALWAYS:
            return True
        elif self.intent_detection_mode == IntentDetectionMode.DISABLED:
            return False
        elif self.intent_detection_mode == IntentDetectionMode.KEYWORDS:
            text_lower = text.lower()
            return any(keyword.lower() in text_lower for keyword in self.intent_keywords)
        return False


class VoiceProcessingResult(BaseModel):
    """Результат обработки голосового сообщения"""
    success: bool = Field(description="Успешность обработки")
    text: Optional[str] = Field(default=None, description="Распознанный текст")
    audio_url: Optional[str] = Field(default=None, description="URL аудиофайла")
    audio_data: Optional[bytes] = Field(default=None, description="Бинарные аудиоданные")
    provider_used: Optional[VoiceProvider] = Field(default=None, description="Использованный провайдер")
    processing_time: float = Field(default=0.0, description="Время обработки в секундах")
    error_message: Optional[str] = Field(default=None, description="Сообщение об ошибке")
    metadata: Dict[str, Any] = Field(default_factory=dict, description="Дополнительные метаданные")


class VoiceFileInfo(BaseModel):
    """Информация о голосовом файле"""
    file_id: str = Field(description="Уникальный идентификатор файла")
    original_filename: str = Field(description="Оригинальное имя файла")
    mime_type: str = Field(description="MIME тип файла")
    size_bytes: int = Field(description="Размер файла в байтах")
    duration_seconds: Optional[float] = Field(default=None, description="Длительность в секундах")
    format: Optional[AudioFormat] = Field(default=None, description="Формат аудио")
    sample_rate: Optional[int] = Field(default=None, description="Частота дискретизации")
    channels: Optional[int] = Field(default=None, description="Количество каналов")
    created_at: str = Field(description="Время создания (ISO format)")
    minio_bucket: str = Field(description="S3/MinIO bucket")
    minio_key: str = Field(description="S3/MinIO ключ объекта")


# Примеры настроек по умолчанию для разных провайдеров
DEFAULT_OPENAI_CONFIG = VoiceProviderConfig(
    provider=VoiceProvider.OPENAI,
    priority=1,
    stt_config=STTConfig(
        model=STTModel.WHISPER_1,
        language="ru",
        max_duration=120,
    ),
    tts_config=TTSConfig(
        model=TTSModel.TTS_1,
        voice=OpenAIVoice.ALLOY.value,
        language="ru",
        speed=1.0,
        audio_format=AudioFormat.MP3,
    )
)

DEFAULT_GOOGLE_CONFIG = VoiceProviderConfig(
    provider=VoiceProvider.GOOGLE,
    priority=2,
    stt_config=STTConfig(
        model=STTModel.GOOGLE_LATEST_SHORT,
        language="ru-RU",
        max_duration=60,
        enable_automatic_punctuation=True,
    ),
    tts_config=TTSConfig(
        model=TTSModel.GOOGLE_NEURAL2,
        voice="ru-RU-Wavenet-A",
        language="ru-RU",
        audio_format=AudioFormat.MP3,
    )
)

DEFAULT_YANDEX_CONFIG = VoiceProviderConfig(
    provider=VoiceProvider.YANDEX,
    priority=3,
    stt_config=STTConfig(
        model=STTModel.YANDEX_GENERAL,
        language="ru-RU",
        max_duration=60,
    ),
    tts_config=TTSConfig(
        model=TTSModel.YANDEX_JANE,
        voice="jane",
        language="ru-RU",
        audio_format=AudioFormat.OGG,
    )
)
