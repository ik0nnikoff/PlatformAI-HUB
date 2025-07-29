"""
Voice_v2 Utilities Package.

Предоставляет набор утилит для voice_v2 системы:
- audio.py: Обработка аудио файлов (детекция формата, валидация, конвертация)
- helpers.py: Общие вспомогательные функции и утилиты
- performance.py: Performance measurements и optimization utilities
- validators.py: Валидация данных и конфигураций (будет добавлено)

Архитектура следует принципам SOLID и высокой производительности.
"""

# Audio processing utilities
from .audio import (
    AudioProcessor,
    AudioMetadata,
    ConversionResult
)

# Common utilities and helpers
from .helpers import (
    HashGenerator,
    FileUtilities,
    ValidationHelpers,
    ErrorHandlingHelpers,
    sanitize_filename,
    format_bytes
)

# Performance utilities
from .performance import (
    PerformanceTimer,
    MetricsHelpers,
    time_async_operation
)

__all__ = [
    # Audio utilities
    "AudioProcessor",
    "AudioMetadata", 
    "ConversionResult",
    
    # Helper utilities
    "HashGenerator",
    "FileUtilities",
    "ValidationHelpers",
    "ErrorHandlingHelpers",
    "sanitize_filename",
    "format_bytes",
    
    # Performance utilities
    "PerformanceTimer",
    "MetricsHelpers",
    "time_async_operation"
]

# Removed duplicate imports to fix reimport warning

__all__ = [
    "AudioProcessor",
    "AudioMetadata", 
    "ConversionResult",
    "AudioLimits",
    "AudioMimeTypes",
    "PYDUB_AVAILABLE"
]
