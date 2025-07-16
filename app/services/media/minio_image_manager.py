"""
Менеджер для работы с MinIO для хранения изображений пользователей
Расширяет функциональность существующего MinIO manager
"""

import asyncio
import logging
import uuid
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any
from io import BytesIO

from minio import Minio
from minio.error import S3Error
from urllib3.exceptions import MaxRetryError

from app.core.config import settings
from app.services.media.image_settings import image_settings, ImageValidationResult


class MinIOImageManager:
    """
    Менеджер для работы с MinIO/S3 хранилищем изображений пользователей
    """

    def __init__(self, logger: Optional[logging.Logger] = None):
        self.logger = logger or logging.getLogger("minio_image_manager")
        self.client: Optional[Minio] = None
        self.bucket_name = settings.MINIO_USER_FILES_BUCKET
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
            self.logger.info(f"MinIO image manager initialized. Bucket: {self.bucket_name}")

        except Exception as e:
            self.logger.error(f"Failed to initialize MinIO image manager: {e}", exc_info=True)
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

    async def upload_user_image(self,
                               image_data: bytes,
                               agent_id: str,
                               user_id: str,
                               original_filename: Optional[str] = None,
                               metadata: Optional[Dict[str, Any]] = None) -> str:
        """
        Загрузка изображения пользователя в MinIO
        
        Args:
            image_data: Байты изображения
            agent_id: ID агента
            user_id: ID пользователя  
            original_filename: Исходное имя файла (опционально)
            metadata: Дополнительные метаданные
            
        Returns:
            str: Object key загруженного файла
            
        Raises:
            ValueError: Если изображение не прошло валидацию
            RuntimeError: Если MinIO недоступен
        """
        if not self._initialized:
            await self.initialize()
        
        # Валидация изображения
        validation_result = image_settings.validate_image_data(image_data, original_filename)
        if not validation_result.is_valid:
            raise ValueError(f"Image validation failed: {validation_result.error_message}")
        
        try:
            # Генерация имени файла и пути
            filename = image_settings.generate_filename(user_id, agent_id, validation_result.detected_format)
            object_key = image_settings.generate_object_path(user_id, agent_id, filename)
            
            # Подготовка метаданных
            file_metadata = {
                'user-id': user_id,
                'agent-id': agent_id,
                'upload-timestamp': datetime.utcnow().isoformat(),
                'file-size-mb': str(validation_result.file_size_mb),
                'image-format': validation_result.detected_format,
                'image-dimensions': f"{validation_result.dimensions[0]}x{validation_result.dimensions[1]}"
            }
            
            if original_filename:
                file_metadata['original-filename'] = original_filename
                
            if metadata:
                file_metadata.update(metadata)
            
            # Определение MIME типа
            content_type = image_settings.get_mime_type(validation_result.detected_format)
            
            # Загрузка в MinIO
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(
                None,
                self.client.put_object,
                self.bucket_name,
                object_key,
                BytesIO(image_data),
                len(image_data),
                content_type,
                file_metadata
            )
            
            self.logger.info(f"Successfully uploaded image: {object_key}")
            self.logger.debug(f"Image metadata: {file_metadata}")
            
            return object_key
            
        except Exception as e:
            self.logger.error(f"Failed to upload image: {e}", exc_info=True)
            raise RuntimeError(f"Image upload failed: {str(e)}")

    async def upload_user_images(self,
                                images_data: List[bytes],
                                agent_id: str,
                                user_id: str,
                                original_filenames: Optional[List[str]] = None,
                                metadata: Optional[Dict[str, Any]] = None) -> List[str]:
        """
        Загрузка нескольких изображений пользователя
        
        Args:
            images_data: Список байтов изображений
            agent_id: ID агента
            user_id: ID пользователя
            original_filenames: Список исходных имён файлов (опционально)
            metadata: Общие метаданные для всех файлов
            
        Returns:
            List[str]: Список object keys загруженных файлов
        """
        # Валидация списка изображений
        is_valid, errors = image_settings.validate_images_list(images_data)
        if not is_valid:
            raise ValueError(f"Images validation failed: {'; '.join(errors)}")
        
        object_keys = []
        
        for i, image_data in enumerate(images_data):
            original_filename = None
            if original_filenames and i < len(original_filenames):
                original_filename = original_filenames[i]
            
            # Добавляем индекс в метаданные для группы изображений
            image_metadata = {'batch-index': str(i), 'batch-size': str(len(images_data))}
            if metadata:
                image_metadata.update(metadata)
            
            try:
                object_key = await self.upload_user_image(
                    image_data=image_data,
                    agent_id=agent_id,
                    user_id=user_id,
                    original_filename=original_filename,
                    metadata=image_metadata
                )
                object_keys.append(object_key)
                
            except Exception as e:
                self.logger.error(f"Failed to upload image {i+1}/{len(images_data)}: {e}")
                # Если одно изображение не загрузилось, удаляем уже загруженные
                if object_keys:
                    await self._cleanup_uploaded_files(object_keys)
                raise RuntimeError(f"Batch upload failed at image {i+1}: {str(e)}")
        
        self.logger.info(f"Successfully uploaded {len(object_keys)} images for user {user_id}")
        return object_keys

    async def get_presigned_url(self,
                               object_key: str,
                               expires_hours: int = 1) -> str:
        """
        Получение presigned URL для доступа к изображению
        
        Args:
            object_key: Ключ объекта в MinIO
            expires_hours: Время жизни URL в часах (по умолчанию 1 час)
            
        Returns:
            str: Presigned URL
        """
        if not self._initialized:
            await self.initialize()
        
        try:
            loop = asyncio.get_event_loop()
            expires = timedelta(hours=expires_hours)
            
            presigned_url = await loop.run_in_executor(
                None,
                self.client.presigned_get_object,
                self.bucket_name,
                object_key,
                expires
            )
            
            self.logger.debug(f"Generated presigned URL for {object_key}, expires in {expires_hours}h")
            return presigned_url
            
        except Exception as e:
            self.logger.error(f"Failed to generate presigned URL for {object_key}: {e}")
            raise RuntimeError(f"Presigned URL generation failed: {str(e)}")

    async def get_presigned_urls(self,
                                object_keys: List[str],
                                expires_hours: int = 1) -> List[str]:
        """
        Получение presigned URLs для списка изображений
        
        Args:
            object_keys: Список ключей объектов
            expires_hours: Время жизни URLs в часах
            
        Returns:
            List[str]: Список presigned URLs
        """
        presigned_urls = []
        
        for object_key in object_keys:
            try:
                url = await self.get_presigned_url(object_key, expires_hours)
                presigned_urls.append(url)
            except Exception as e:
                self.logger.error(f"Failed to get presigned URL for {object_key}: {e}")
                # Возвращаем пустой URL для неудачных запросов
                presigned_urls.append("")
        
        return presigned_urls

    async def _cleanup_uploaded_files(self, object_keys: List[str]) -> None:
        """Удаление загруженных файлов в случае ошибки"""
        if not object_keys:
            return
            
        self.logger.warning(f"Cleaning up {len(object_keys)} uploaded files due to error")
        
        for object_key in object_keys:
            try:
                loop = asyncio.get_event_loop()
                await loop.run_in_executor(
                    None,
                    self.client.remove_object,
                    self.bucket_name,
                    object_key
                )
                self.logger.debug(f"Cleaned up file: {object_key}")
            except Exception as e:
                self.logger.error(f"Failed to cleanup file {object_key}: {e}")

    async def delete_user_image(self, object_key: str) -> bool:
        """
        Удаление изображения пользователя
        
        Args:
            object_key: Ключ объекта для удаления
            
        Returns:
            bool: True если удаление успешно
        """
        if not self._initialized:
            await self.initialize()
            
        try:
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(
                None,
                self.client.remove_object,
                self.bucket_name,
                object_key
            )
            
            self.logger.info(f"Successfully deleted image: {object_key}")
            return True
            
        except Exception as e:
            self.logger.error(f"Failed to delete image {object_key}: {e}")
            return False

    def is_initialized(self) -> bool:
        """Проверка инициализации менеджера"""
        return self._initialized
