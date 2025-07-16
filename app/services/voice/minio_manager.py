"""
Менеджер для работы с MinIO для хранения аудиофайлов
"""

import asyncio
import logging
import uuid
from datetime import datetime, timedelta
from typing import Optional, Tuple, Dict, Any, List
from io import BytesIO

from minio import Minio
from minio.error import S3Error
from urllib3.exceptions import MaxRetryError

from app.core.config import settings
from app.api.schemas.voice_schemas import VoiceFileInfo, AudioFormat


class MinioFileManager:
    """
    Менеджер для работы с MinIO/S3 хранилищем аудиофайлов
    """

    def __init__(self, logger: Optional[logging.Logger] = None):
        self.logger = logger or logging.getLogger("minio_file_manager")
        self.client: Optional[Minio] = None
        self.bucket_name = settings.MINIO_VOICE_BUCKET_NAME
        self._initialized = False

    async def initialize(self) -> None:
        """Инициализация подключения к MinIO"""
        try:
            # Создаем клиент MinIO
            self.client = Minio(
                endpoint=settings.MINIO_ENDPOINT,
                access_key=settings.MINIO_ACCESS_KEY,
                secret_key=settings.MINIO_SECRET_KEY,
                secure=settings.MINIO_SECURE
            )

            # Проверяем подключение и создаем bucket если нужно
            await self._ensure_bucket_exists()
            self._initialized = True
            self.logger.info(f"MinIO client initialized. Bucket: {self.bucket_name}")

        except Exception as e:
            self.logger.error(f"Failed to initialize MinIO client: {e}", exc_info=True)
            raise

    async def _ensure_bucket_exists(self) -> None:
        """Убедиться что bucket существует, создать если нет"""
        if not self.client:
            raise RuntimeError("MinIO client not initialized")

        try:
            # Выполняем операции с MinIO в отдельном потоке
            loop = asyncio.get_event_loop()
            
            bucket_exists = await loop.run_in_executor(
                None, self.client.bucket_exists, self.bucket_name
            )
            
            if not bucket_exists:
                await loop.run_in_executor(
                    None, self.client.make_bucket, self.bucket_name
                )
                self.logger.info(f"Created bucket: {self.bucket_name}")
            else:
                self.logger.debug(f"Bucket already exists: {self.bucket_name}")

        except Exception as e:
            self.logger.error(f"Error ensuring bucket exists: {e}", exc_info=True)
            raise

    def _generate_object_key(self, agent_id: str, user_id: str, file_type: str = "voice") -> str:
        """
        Генерация ключа объекта в S3/MinIO
        
        Args:
            agent_id: ID агента
            user_id: ID пользователя
            file_type: Тип файла (voice, tts)
            
        Returns:
            Строка-ключ для S3/MinIO
        """
        timestamp = datetime.utcnow().strftime("%Y/%m/%d/%H")
        unique_id = str(uuid.uuid4())
        return f"{file_type}/{agent_id}/{user_id}/{timestamp}/{unique_id}"

    async def upload_audio_file(self, 
                               audio_data: bytes,
                               agent_id: str,
                               user_id: str,
                               original_filename: str = "audio",
                               mime_type: str = "audio/mpeg",
                               audio_format: Optional[AudioFormat] = None,
                               metadata: Optional[Dict[str, Any]] = None) -> VoiceFileInfo:
        """
        Загрузка аудиофайла в MinIO
        
        Args:
            audio_data: Бинарные данные аудиофайла
            agent_id: ID агента
            user_id: ID пользователя  
            original_filename: Оригинальное имя файла
            mime_type: MIME тип файла
            audio_format: Формат аудио
            metadata: Дополнительные метаданные
            
        Returns:
            VoiceFileInfo: Информация о загруженном файле
        """
        if not self._initialized or not self.client:
            raise RuntimeError("MinIO client not initialized")

        try:
            # Генерируем уникальный ключ
            object_key = self._generate_object_key(agent_id, user_id)
            
            # Добавляем расширение к ключу если есть
            if audio_format:
                object_key += f".{audio_format.value}"
            elif "." in original_filename:
                ext = original_filename.split(".")[-1]
                object_key += f".{ext}"

            # Подготавливаем метаданные
            upload_metadata = {
                "agent-id": agent_id,
                "user-id": user_id,
                "original-filename": original_filename,
                "upload-time": datetime.utcnow().isoformat(),
            }
            
            if metadata:
                upload_metadata.update({f"custom-{k}": str(v) for k, v in metadata.items()})

            # Загружаем файл
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(
                None,
                self.client.put_object,
                self.bucket_name,
                object_key,
                BytesIO(audio_data),
                len(audio_data),
                mime_type,
                upload_metadata
            )

            # Создаем информацию о файле
            file_info = VoiceFileInfo(
                file_id=str(uuid.uuid4()),
                original_filename=original_filename,
                mime_type=mime_type,
                size_bytes=len(audio_data),
                format=audio_format,
                created_at=datetime.utcnow().isoformat(),
                minio_bucket=self.bucket_name,
                minio_key=object_key
            )

            # Пытаемся получить длительность аудио
            try:
                from app.services.voice.base import AudioFileProcessor
                file_info.duration_seconds = await AudioFileProcessor.get_audio_duration(audio_data)
            except Exception as e:
                self.logger.warning(f"Failed to get audio duration: {e}")

            self.logger.info(f"Uploaded audio file: {object_key} ({len(audio_data)} bytes)")
            return file_info

        except Exception as e:
            self.logger.error(f"Error uploading audio file: {e}", exc_info=True)
            raise

    async def download_audio_file(self, file_info: VoiceFileInfo) -> bytes:
        """
        Скачивание аудиофайла из MinIO
        
        Args:
            file_info: Информация о файле
            
        Returns:
            Бинарные данные файла
        """
        if not self._initialized or not self.client:
            raise RuntimeError("MinIO client not initialized")

        try:
            loop = asyncio.get_event_loop()
            response = await loop.run_in_executor(
                None,
                self.client.get_object,
                file_info.minio_bucket,
                file_info.minio_key
            )
            
            # Читаем все данные
            audio_data = response.read()
            response.close()
            response.release_conn()
            
            self.logger.debug(f"Downloaded audio file: {file_info.minio_key} ({len(audio_data)} bytes)")
            return audio_data

        except Exception as e:
            self.logger.error(f"Error downloading audio file {file_info.minio_key}: {e}", exc_info=True)
            raise

    async def delete_audio_file(self, file_info: VoiceFileInfo) -> bool:
        """
        Удаление аудиофайла из MinIO
        
        Args:
            file_info: Информация о файле
            
        Returns:
            True если файл успешно удален
        """
        if not self._initialized or not self.client:
            raise RuntimeError("MinIO client not initialized")

        try:
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(
                None,
                self.client.remove_object,
                file_info.minio_bucket,
                file_info.minio_key
            )
            
            self.logger.info(f"Deleted audio file: {file_info.minio_key}")
            return True

        except Exception as e:
            self.logger.error(f"Error deleting audio file {file_info.minio_key}: {e}", exc_info=True)
            return False

    async def get_file_url(self, 
                          file_info: VoiceFileInfo, 
                          expiry_hours: int = 1) -> str:
        """
        Получение временной ссылки на файл
        
        Args:
            file_info: Информация о файле
            expiry_hours: Время жизни ссылки в часах
            
        Returns:
            Временная URL для доступа к файлу
        """
        if not self._initialized or not self.client:
            raise RuntimeError("MinIO client not initialized")

        try:
            loop = asyncio.get_event_loop()
            expiry = timedelta(hours=expiry_hours)
            
            url = await loop.run_in_executor(
                None,
                self.client.presigned_get_object,
                file_info.minio_bucket,
                file_info.minio_key,
                expiry
            )
            
            self.logger.debug(f"Generated presigned URL for {file_info.minio_key}")
            return url

        except Exception as e:
            self.logger.error(f"Error generating presigned URL for {file_info.minio_key}: {e}", exc_info=True)
            raise

    async def list_user_files(self, 
                             agent_id: str, 
                             user_id: str,
                             limit: int = 100) -> List[str]:
        """
        Получение списка файлов пользователя
        
        Args:
            agent_id: ID агента
            user_id: ID пользователя
            limit: Максимальное количество файлов
            
        Returns:
            Список ключей объектов
        """
        if not self._initialized or not self.client:
            raise RuntimeError("MinIO client not initialized")

        try:
            prefix = f"voice/{agent_id}/{user_id}/"
            loop = asyncio.get_event_loop()
            
            objects = await loop.run_in_executor(
                None,
                lambda: list(self.client.list_objects(
                    self.bucket_name, 
                    prefix=prefix,
                    recursive=True
                ))
            )
            
            file_keys = [obj.object_name for obj in objects[:limit]]
            self.logger.debug(f"Found {len(file_keys)} files for user {user_id}")
            return file_keys

        except Exception as e:
            self.logger.error(f"Error listing user files: {e}", exc_info=True)
            return []

    async def cleanup_old_files(self, days_old: int = 7) -> int:
        """
        Очистка старых файлов
        
        Args:
            days_old: Возраст файлов в днях для удаления
            
        Returns:
            Количество удаленных файлов
        """
        if not self._initialized or not self.client:
            raise RuntimeError("MinIO client not initialized")

        try:
            cutoff_date = datetime.utcnow() - timedelta(days=days_old)
            loop = asyncio.get_event_loop()
            
            # Получаем все объекты
            objects = await loop.run_in_executor(
                None,
                lambda: list(self.client.list_objects(self.bucket_name, recursive=True))
            )
            
            deleted_count = 0
            for obj in objects:
                if obj.last_modified < cutoff_date:
                    try:
                        await loop.run_in_executor(
                            None,
                            self.client.remove_object,
                            self.bucket_name,
                            obj.object_name
                        )
                        deleted_count += 1
                    except Exception as e:
                        self.logger.warning(f"Failed to delete old file {obj.object_name}: {e}")

            self.logger.info(f"Cleaned up {deleted_count} old files")
            return deleted_count

        except Exception as e:
            self.logger.error(f"Error during cleanup: {e}", exc_info=True)
            return 0

    async def health_check(self) -> bool:
        """
        Проверка здоровья подключения к MinIO
        
        Returns:
            True если подключение работает
        """
        if not self._initialized or not self.client:
            return False

        try:
            loop = asyncio.get_event_loop()
            bucket_exists = await loop.run_in_executor(
                None, self.client.bucket_exists, self.bucket_name
            )
            return bucket_exists
        except Exception as e:
            self.logger.error(f"MinIO health check failed: {e}")
            return False

    async def get_storage_stats(self) -> Dict[str, Any]:
        """
        Получение статистики использования хранилища
        
        Returns:
            Словарь со статистикой
        """
        if not self._initialized or not self.client:
            raise RuntimeError("MinIO client not initialized")

        try:
            loop = asyncio.get_event_loop()
            
            # Получаем все объекты и считаем статистику
            objects = await loop.run_in_executor(
                None,
                lambda: list(self.client.list_objects(self.bucket_name, recursive=True))
            )
            
            total_files = len(objects)
            total_size = sum(obj.size for obj in objects)
            
            # Группируем по типам файлов
            file_types = {}
            for obj in objects:
                if obj.object_name.startswith("voice/"):
                    ext = obj.object_name.split(".")[-1] if "." in obj.object_name else "unknown"
                    file_types[ext] = file_types.get(ext, 0) + 1

            return {
                "total_files": total_files,
                "total_size_bytes": total_size,
                "total_size_mb": round(total_size / (1024 * 1024), 2),
                "file_types": file_types,
                "bucket_name": self.bucket_name
            }

        except Exception as e:
            self.logger.error(f"Error getting storage stats: {e}", exc_info=True)
            return {}

    async def cleanup(self) -> None:
        """Очистка ресурсов"""
        # MinIO client не требует явного закрытия соединений
        self._initialized = False
        self.logger.info("MinIO file manager cleaned up")
