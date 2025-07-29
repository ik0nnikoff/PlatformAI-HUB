"""
Voice_v2 Validation Functions Module.

Provides focused validation functions for voice_v2 system.
Follows SRP and Interface Segregation principles from Phase 1.2.2.

Validation categories:
- Audio validation: Format, size, duration validation
- Provider validation: Configuration validation
- Configuration validation: Settings validation
- Data validation: Input sanitization, type checking
"""

import re
from pathlib import Path
from typing import Any, Dict, List, Tuple, Union
import logging

logger = logging.getLogger(__name__)


class AudioValidator:
    """Audio content validation utilities. SRP: Only audio validation."""

    SUPPORTED_FORMATS = {'mp3', 'wav', 'ogg', 'flac', 'opus', 'aac', 'm4a'}
    MAX_FILE_SIZE_MB = 25.0
    MAX_DURATION_SECONDS = 120

    @classmethod
    def validate_audio_format(cls, file_path: Union[str, Path]) -> bool:
        """Validates audio format by extension."""
        if not file_path:
            return False
        extension = Path(file_path).suffix.lower().lstrip('.')
        return extension in cls.SUPPORTED_FORMATS

    @classmethod
    def validate_audio_size(cls, size_bytes: int) -> bool:
        """Validates audio file size."""
        if size_bytes <= 0:
            return False
        size_mb = size_bytes / (1024 * 1024)
        return size_mb <= cls.MAX_FILE_SIZE_MB

    @classmethod
    def validate_audio_duration(cls, duration_seconds: float) -> bool:
        """Validates audio duration."""
        return 0 < duration_seconds <= cls.MAX_DURATION_SECONDS


class ProviderValidator:
    """Provider configuration validation. SRP: Only provider validation."""

    SUPPORTED_PROVIDERS = {'openai', 'google', 'yandex'}

    @classmethod
    def validate_provider_name(cls, provider_name: str) -> bool:
        """Validates provider name."""
        if not provider_name or not isinstance(provider_name, str):
            return False
        return provider_name.lower() in cls.SUPPORTED_PROVIDERS

    @classmethod
    def validate_provider_config(cls, config: Dict[str, Any]) -> Tuple[bool, List[str]]:
        """Validates provider configuration."""
        errors = []

        # Required fields
        for field in ['provider', 'priority', 'enabled']:
            if field not in config:
                errors.append(f"Missing required field: {field}")

        if errors:
            return False, errors

        # Validate provider name
        if not cls.validate_provider_name(config['provider']):
            errors.append(f"Invalid provider: {config['provider']}")

        # Validate priority
        if not isinstance(config['priority'], int) or config['priority'] < 1:
            errors.append(f"Invalid priority: {config['priority']}")

        # Validate enabled flag
        if not isinstance(config['enabled'], bool):
            errors.append(f"Invalid enabled flag: {config['enabled']}")

        return len(errors) == 0, errors


class ConfigurationValidator:
    """System configuration validation. SRP: Only config validation."""

    LANGUAGE_PATTERN = re.compile(r'^[a-z]{2}(-[A-Z]{2})?$|^auto$')

    @classmethod
    def validate_language_code(cls, language: str) -> bool:
        """Validates language code (ISO 639-1 or 'auto')."""
        if not language or not isinstance(language, str):
            return False
        return bool(cls.LANGUAGE_PATTERN.match(language))

    @classmethod
    def validate_fallback_config(cls, fallback_config: Dict[str, Any]) -> Tuple[bool, List[str]]:
        """Validates fallback configuration."""
        errors = []

        max_retries = fallback_config.get('max_retries', 3)
        if not isinstance(max_retries, int) or max_retries < 0:
            errors.append(f"Invalid max_retries: {max_retries}")

        retry_delay = fallback_config.get('retry_delay', 1.0)
        if not isinstance(retry_delay, (int, float)) or retry_delay < 0:
            errors.append(f"Invalid retry_delay: {retry_delay}")

        return len(errors) == 0, errors

    @classmethod
    def validate_cache_config(cls, cache_config: Dict[str, Any]) -> Tuple[bool, List[str]]:
        """Validates cache configuration."""
        errors = []

        for key, default_ttl in [('stt_ttl', 86400), ('tts_ttl', 86400)]:
            ttl = cache_config.get(key, default_ttl)
            if not isinstance(ttl, int) or ttl < 0:
                errors.append(f"Invalid {key}: {ttl}")

        return len(errors) == 0, errors


class DataValidator:
    """Input data validation. SRP: Only data validation."""

    MAX_TEXT_LENGTH = 4000
    MIN_TEXT_LENGTH = 1
    MAX_FILENAME_LENGTH = 255
    FILENAME_FORBIDDEN_CHARS = r'[<>:"/\\|?*\x00-\x1f]'

    @classmethod
    def validate_text_input(cls, text: str) -> Tuple[bool, List[str]]:
        """Validates text for TTS."""
        errors = []

        if not text or not isinstance(text, str):
            errors.append("Text must be a non-empty string")
            return False, errors

        text = text.strip()

        if len(text) < cls.MIN_TEXT_LENGTH:
            errors.append(f"Text too short: {len(text)}")

        if len(text) > cls.MAX_TEXT_LENGTH:
            errors.append(f"Text too long: {len(text)}")

        return len(errors) == 0, errors

    @classmethod
    def validate_filename(cls, filename: str) -> Tuple[bool, List[str]]:
        """Validates filename."""
        errors = []

        if not filename or not isinstance(filename, str):
            errors.append("Filename must be a non-empty string")
            return False, errors

        if len(filename) > cls.MAX_FILENAME_LENGTH:
            errors.append("Filename too long")

        if re.search(cls.FILENAME_FORBIDDEN_CHARS, filename):
            errors.append("Filename contains forbidden characters")

        return len(errors) == 0, errors

    @classmethod
    def sanitize_text(cls, text: str) -> str:
        """Sanitizes text for safe usage."""
        if not text or not isinstance(text, str):
            return ""

        # Remove control characters and normalize whitespace
        text = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]', '', text)
        text = re.sub(r'\s+', ' ', text).strip()

        # Truncate if too long
        if len(text) > cls.MAX_TEXT_LENGTH:
            text = text[:cls.MAX_TEXT_LENGTH].rstrip()

        return text
