"""
Voice_v2 Common Utilities Module.

Предоставляет общие вспомогательные функции и утилиты для voice_v2 системы.
Следует принципам SRP и высокой производительности из Phase 1.3.

Architecture:
- HashGenerator: Hash generation utilities
- FileUtilities: File system operations
- ValidationHelpers: Validation utilities
- ErrorHandlingHelpers: Centralized error handling
"""

import asyncio
import hashlib
import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Union
import logging

logger = logging.getLogger(__name__)


class HashGenerator:
    """
    Генератор хешей для caching и идентификации.
    
    Single Responsibility: Только генерация консистентных хешей.
    """
    
    @staticmethod
    def generate_audio_hash(audio_data: bytes) -> str:
        """Генерирует MD5 hash для audio data."""
        return hashlib.sha256(audio_data).hexdigest()
    
    @staticmethod
    def generate_text_hash(text: str) -> str:
        """Генерирует MD5 hash для text content."""
        return hashlib.sha256(text.encode('utf-8')).hexdigest()
    
    @staticmethod
    def generate_cache_key(*components: str, separator: str = ":") -> str:
        """Генерирует составной cache key из компонентов."""
        return separator.join(str(component) for component in components)


class FileUtilities:
    """
    Utilities для работы с файлами.
    
    Single Responsibility: Только file system operations.
    """
    
    @staticmethod
    def ensure_directory(path: Union[str, Path]) -> Path:
        """Обеспечивает существование директории."""
        directory = Path(path)
        directory.mkdir(parents=True, exist_ok=True)
        return directory
    
    @staticmethod
    def get_file_size_mb(file_path: Union[str, Path]) -> float:
        """Получает размер файла в мегабайтах."""
        path = Path(file_path)
        if not path.exists():
            return 0.0
        
        size_bytes = path.stat().st_size
        return size_bytes / (1024 * 1024)
    
    @staticmethod
    async def read_file_async(file_path: Union[str, Path]) -> bytes:
        """Асинхронное чтение файла."""
        def _read_file():
            with open(file_path, 'rb') as f:
                return f.read()
        
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, _read_file)


class ValidationHelpers:
    """
    Validation utilities с performance optimization.
    
    Single Responsibility: Только validation operations.
    """
    
    @staticmethod
    def validate_audio_size(file_size_mb: float, max_size_mb: float = 25.0) -> bool:
        """Валидирует размер аудио файла."""
        return 0 < file_size_mb <= max_size_mb
    
    @staticmethod
    def validate_language_code(language: str) -> bool:
        """Валидирует код языка."""
        if not language or not isinstance(language, str):
            return False
        
        supported_languages = {
            'auto', 'en', 'ru', 'es', 'fr', 'de', 'it', 'pt', 'zh', 'ja', 'ko'
        }
        
        return language.lower() in supported_languages
    
    @staticmethod
    def validate_provider_config(config: Dict[str, Any]) -> bool:
        """Валидирует конфигурацию провайдера."""
        required_fields = ['provider', 'priority', 'enabled']
        
        for field in required_fields:
            if field not in config:
                return False
        
        if not isinstance(config['provider'], str):
            return False
        if not isinstance(config['priority'], int) or config['priority'] < 1:
            return False
        if not isinstance(config['enabled'], bool):
            return False
        
        return True


class ErrorHandlingHelpers:
    """
    Centralized error handling utilities.
    
    Single Responsibility: Только error handling и logging.
    """
    
    @staticmethod
    def log_provider_error(
        provider_name: str,
        operation: str,
        error: Exception,
        context: Optional[Dict[str, Any]] = None
    ) -> None:
        """Логирует ошибку провайдера с контекстом."""
        context_str = ""
        if context:
            try:
                context_str = f" | Context: {json.dumps(context, default=str)}"
            except Exception:
                context_str = f" | Context: {str(context)}"
        
        logger.error(
            f"Provider error: {provider_name} | Operation: {operation} | "
            f"Error: {str(error)}{context_str}",
            exc_info=True
        )
    
    @staticmethod
    def create_fallback_error(
        operation: str,
        attempted_providers: List[str],
        last_error: Exception
    ) -> Exception:
        """Создает ошибку fallback при неудаче всех провайдеров."""
        providers_str = ", ".join(attempted_providers)
        message = (
            f"All providers failed for operation '{operation}'. "
            f"Attempted providers: {providers_str}. "
            f"Last error: {str(last_error)}"
        )
        
        return RuntimeError(message)


# Convenience utility functions

def sanitize_filename(filename: str) -> str:
    """Sanitizes filename для безопасного использования."""
    unsafe_chars = '<>:"/\\|?*'
    for char in unsafe_chars:
        filename = filename.replace(char, '_')
    
    if len(filename) > 255:
        name, ext = filename.rsplit('.', 1) if '.' in filename else (filename, '')
        max_name_len = 255 - len(ext) - 1 if ext else 255
        filename = name[:max_name_len] + ('.' + ext if ext else '')
    
    return filename


def format_bytes(size_bytes: int) -> str:
    """Форматирует размер в байтах в человеко-читаемый формат."""
    for unit in ['B', 'KB', 'MB', 'GB']:
        if size_bytes < 1024:
            return f"{size_bytes:.1f} {unit}"
        size_bytes /= 1024
    return f"{size_bytes:.1f} TB"
