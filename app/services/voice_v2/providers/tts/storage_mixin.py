"""
TTS Storage Mixin для Voice_v2 - Устранение дублирования кода

Применяет принципы DRY (Don't Repeat Yourself) и SOLID:
- Single Responsibility: Только операции с хранением аудио файлов
- Open/Closed: Расширяемый через параметры, закрытый для модификации
- Interface Segregation: Минимальный интерфейс для storage операций
- Dependency Inversion: Зависит от абстракций (MinioFileManager)

Устраняет дублирование:
- MinIO инициализация и конфигурация
- Логика загрузки файлов
- Генерация presigned URLs
- Обработка ошибок storage операций
- Metadata формирование
"""

import hashlib
import time
import logging
from dataclasses import dataclass
from typing import Dict, Any, Optional

from ...infrastructure.minio_manager import MinioFileManager
from ...core.config import get_config
from ...core.exceptions import AudioProcessingError

logger = logging.getLogger(__name__)


@dataclass
class AudioUploadParams:
    """Data class for audio upload parameters to reduce argument count."""
    audio_data: bytes
    provider_name: str
    agent_id: str = "default"
    user_id: str = "default"
    voice: str = "default"
    language: str = "en-US"
    audio_format: str = "mp3"
    additional_metadata: Optional[Dict[str, Any]] = None
    filename_prefix: Optional[str] = None


class TTSStorageMixin:
    """
    Mixin для TTS провайдеров для устранения дублирования кода хранения.

    Обеспечивает:
    - Единообразную логику загрузки в MinIO
    - Стандартизированное формирование metadata
    - Consistent error handling
    - Configurable storage parameters
    """

    # Public interface methods
    async def upload_audio_file(self, params: AudioUploadParams) -> str:
        """
        Public interface for audio file upload.

        Args:
            params: AudioUploadParams containing all upload parameters

        Returns:
            Presigned URL for audio file access

        Raises:
            AudioProcessingError: If upload fails
        """
        return await self._upload_audio_to_storage(params)

    async def get_storage_health(self) -> bool:
        """
        Public method to check storage health.

        Returns:
            True if storage is healthy, False otherwise
        """
        try:
            minio_manager = await self._create_minio_manager()
            return await minio_manager.health_check()
        except AudioProcessingError as e:
            logger.warning("Storage health check failed: %s", e)
            return False

    def get_supported_content_types(self) -> Dict[str, str]:
        """
        Public method to get supported audio content types.

        Returns:
            Dictionary mapping audio formats to MIME types
        """
        return {
            "mp3": "audio/mpeg",
            "wav": "audio/wav",
            "ogg": "audio/ogg",
            "oggopus": "audio/ogg",
            "opus": "audio/opus",
            "flac": "audio/flac",
            "aac": "audio/aac"
        }

    # Private implementation methods
    async def _upload_audio_to_storage(self, params: AudioUploadParams) -> str:
        """
        Unified audio storage upload for all TTS providers.

        Orchestrates the complete upload process by delegating to smaller methods.
        """
        try:
            minio_manager = await self._create_minio_manager()
            upload_params = self._prepare_upload_parameters(params)

            await self._execute_upload(minio_manager, params.audio_data, upload_params)
            return await self._generate_access_url(minio_manager, upload_params["object_key"])

        except AudioProcessingError:
            raise
        except Exception as e:
            logger.error("Audio storage upload failed: %s", e)
            raise AudioProcessingError(f"Storage upload failed: {e}") from e

    async def _create_minio_manager(self) -> MinioFileManager:
        """Create and initialize MinIO manager."""
        voice_config = get_config()
        storage_config = voice_config.file_storage

        minio_manager = MinioFileManager(
            endpoint=storage_config.minio_endpoint or "127.0.0.1:9000",
            access_key=storage_config.minio_access_key or "minioadmin",
            secret_key=storage_config.minio_secret_key or "minioadmin",
            bucket_name=storage_config.bucket_name or "voice-files",
            secure=storage_config.minio_secure or False
        )

        await minio_manager.initialize()
        return minio_manager

    def _prepare_upload_parameters(self, params: AudioUploadParams) -> Dict[str, Any]:
        """Prepare all parameters needed for upload."""
        filename = self._generate_unique_filename(
            provider_name=params.provider_name,
            audio_format=params.audio_format,
            prefix=params.filename_prefix
        )

        object_key = f"tts/{params.provider_name}/{params.agent_id}/{params.user_id}/{filename}"
        content_type = self._get_content_type(params.audio_format)

        metadata = self._build_metadata(
            provider_name=params.provider_name,
            agent_id=params.agent_id,
            user_id=params.user_id,
            voice=params.voice,
            language=params.language,
            audio_format=params.audio_format,
            additional_metadata=params.additional_metadata
        )

        return {
            "filename": filename,
            "object_key": object_key,
            "content_type": content_type,
            "metadata": metadata
        }

    async def _execute_upload(
        self,
        minio_manager: MinioFileManager,
        audio_data: bytes,
        upload_params: Dict[str, Any]
    ) -> None:
        """Execute the actual file upload to MinIO."""
        await minio_manager.upload_file(
            file_data=audio_data,
            object_key=upload_params["object_key"],
            content_type=upload_params["content_type"],
            metadata=upload_params["metadata"]
        )

    async def _generate_access_url(
        self,
        minio_manager: MinioFileManager,
        object_key: str,
        expires_hours: int = 24
    ) -> str:
        """Generate presigned URL for file access."""
        return await minio_manager.generate_presigned_url(
            object_key=object_key,
            expires_hours=expires_hours
        )

    def _generate_unique_filename(
        self,
        provider_name: str,
        audio_format: str,
        prefix: Optional[str] = None,
        text_for_hash: Optional[str] = None
    ) -> str:
        """
        Generate unique filename for audio file.

        Args:
            provider_name: Provider identifier
            audio_format: File extension
            prefix: Optional prefix for filename
            text_for_hash: Optional text to include in hash (for consistency)

        Returns:
            Unique filename string
        """
        timestamp = int(time.time() * 1000)  # milliseconds for uniqueness

        # Use text hash if provided, otherwise timestamp hash
        if text_for_hash:
            text_hash = hashlib.sha256(text_for_hash.encode()).hexdigest()[:8]
            unique_part = f"{text_hash}_{timestamp}"
        else:
            unique_part = str(timestamp)

        if prefix:
            return f"{prefix}_{provider_name}_{unique_part}.{audio_format}"

        return f"tts_{provider_name}_{unique_part}.{audio_format}"

    def _get_content_type(self, audio_format: str) -> str:
        """
        Get MIME content type for audio format.

        Args:
            audio_format: Audio file extension

        Returns:
            MIME content type string
        """
        content_type_mapping = {
            "mp3": "audio/mpeg",
            "wav": "audio/wav",
            "ogg": "audio/ogg",
            "oggopus": "audio/ogg",
            "opus": "audio/opus",
            "flac": "audio/flac",
            "aac": "audio/aac"
        }
        return content_type_mapping.get(audio_format.lower(), "audio/mpeg")

    def _build_metadata(
        self,
        provider_name: str,
        agent_id: str,
        user_id: str,
        voice: str,
        language: str,
        audio_format: str,
        additional_metadata: Optional[Dict[str, Any]] = None
    ) -> Dict[str, str]:
        """
        Build standardized metadata for audio file.

        Args:
            provider_name: Provider identifier
            agent_id: Agent identifier
            user_id: User identifier
            voice: Voice name used
            language: Language code used
            audio_format: Audio format
            additional_metadata: Provider-specific metadata

        Returns:
            Metadata dictionary
        """
        metadata = {
            "provider": provider_name,
            "agent_id": agent_id,
            "user_id": user_id,
            "voice": voice,
            "language": language,
            "format": audio_format,
            "created_at": time.strftime("%Y-%m-%d %H:%M:%S")
        }

        # Add provider-specific metadata
        if additional_metadata:
            # Convert all values to strings for MinIO compatibility
            for key, value in additional_metadata.items():
                metadata[f"ext_{key}"] = str(value)

        return metadata

    def _estimate_audio_duration(
        self,
        text: str,
        speaking_rate: float = 1.0,
        words_per_minute: float = 150.0
    ) -> float:
        """
        Estimate audio duration from text.

        Args:
            text: Text to be synthesized
            speaking_rate: Speaking rate multiplier
            words_per_minute: Base words per minute rate

        Returns:
            Estimated duration in seconds
        """
        if not text:
            return 0.0

        # Count words (more accurate than character counting)
        words = len(text.split())
        base_duration = (words / words_per_minute) * 60  # Convert to seconds

        # Adjust for speaking rate
        return base_duration / speaking_rate if speaking_rate > 0 else base_duration
