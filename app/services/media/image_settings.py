"""
Настройки и валидация для обработки изображений
"""

import logging
from typing import List, Tuple, Optional
from dataclasses import dataclass

from PIL import Image
from io import BytesIO

from app.core.config import settings


@dataclass
class ImageValidationResult:
    """Результат валидации изображения"""
    is_valid: bool
    error_message: Optional[str] = None
    detected_format: Optional[str] = None
    file_size_mb: Optional[float] = None
    dimensions: Optional[Tuple[int, int]] = None


class ImageSettings:
    """
    Класс для управления настройками обработки изображений
    """
    
    def __init__(self):
        self.logger = logging.getLogger("image_settings")
        
        # Загружаем настройки из config
        self.max_file_size_mb = settings.IMAGE_MAX_FILE_SIZE_MB
        self.max_files_count = settings.IMAGE_MAX_FILES_COUNT
        self.supported_formats = [fmt.lower().strip() for fmt in settings.IMAGE_SUPPORTED_FORMATS]
        
        # MIME типы для поддерживаемых форматов
        self.format_to_mime = {
            'jpg': 'image/jpeg',
            'jpeg': 'image/jpeg',
            'png': 'image/png',
            'webp': 'image/webp',
            'gif': 'image/gif'
        }
        
        # Максимальные размеры изображения (пиксели)
        self.max_width = 4096
        self.max_height = 4096
        
        self.logger.info(f"Image settings initialized: "
                        f"max_size={self.max_file_size_mb}MB, "
                        f"max_files={self.max_files_count}, "
                        f"formats={self.supported_formats}")
    
    def validate_image_data(self, image_data: bytes, filename: Optional[str] = None) -> ImageValidationResult:
        """
        Валидация данных изображения
        
        Args:
            image_data: Байты изображения
            filename: Имя файла (опционально)
            
        Returns:
            ImageValidationResult: Результат валидации
        """
        try:
            # Проверка размера файла
            file_size_mb = len(image_data) / (1024 * 1024)
            if file_size_mb > self.max_file_size_mb:
                return ImageValidationResult(
                    is_valid=False,
                    error_message=f"File size {file_size_mb:.2f}MB exceeds limit of {self.max_file_size_mb}MB",
                    file_size_mb=file_size_mb
                )
            
            # Попытка открыть изображение с помощью Pillow
            try:
                with Image.open(BytesIO(image_data)) as img:
                    detected_format = img.format.lower() if img.format else None
                    dimensions = img.size
                    
                    # Проверка формата
                    if detected_format not in self.supported_formats:
                        return ImageValidationResult(
                            is_valid=False,
                            error_message=f"Format '{detected_format}' not supported. Allowed: {self.supported_formats}",
                            detected_format=detected_format,
                            file_size_mb=file_size_mb,
                            dimensions=dimensions
                        )
                    
                    # Проверка размеров
                    width, height = dimensions
                    if width > self.max_width or height > self.max_height:
                        return ImageValidationResult(
                            is_valid=False,
                            error_message=f"Image dimensions {width}x{height} exceed limit of {self.max_width}x{self.max_height}",
                            detected_format=detected_format,
                            file_size_mb=file_size_mb,
                            dimensions=dimensions
                        )
                    
                    # Все проверки пройдены
                    return ImageValidationResult(
                        is_valid=True,
                        detected_format=detected_format,
                        file_size_mb=file_size_mb,
                        dimensions=dimensions
                    )
                    
            except Exception as e:
                return ImageValidationResult(
                    is_valid=False,
                    error_message=f"Cannot open image: {str(e)}",
                    file_size_mb=file_size_mb
                )
                
        except Exception as e:
            self.logger.error(f"Image validation error: {e}", exc_info=True)
            return ImageValidationResult(
                is_valid=False,
                error_message=f"Validation error: {str(e)}"
            )
    
    def validate_images_list(self, images_data: List[bytes]) -> Tuple[bool, List[str]]:
        """
        Валидация списка изображений
        
        Args:
            images_data: Список байтов изображений
            
        Returns:
            Tuple[bool, List[str]]: (все_валидны, список_ошибок)
        """
        if len(images_data) > self.max_files_count:
            return False, [f"Too many files: {len(images_data)}. Maximum allowed: {self.max_files_count}"]
        
        errors = []
        for i, image_data in enumerate(images_data):
            result = self.validate_image_data(image_data)
            if not result.is_valid:
                errors.append(f"Image {i+1}: {result.error_message}")
        
        return len(errors) == 0, errors
    
    def get_mime_type(self, format_name: str) -> str:
        """
        Получение MIME типа по формату
        
        Args:
            format_name: Название формата (jpg, png, etc.)
            
        Returns:
            str: MIME тип
        """
        return self.format_to_mime.get(format_name.lower(), 'application/octet-stream')
    
    def generate_filename(self, user_id: str, agent_id: str, original_format: str) -> str:
        """
        Генерация имени файла для изображения
        
        Args:
            user_id: ID пользователя
            agent_id: ID агента
            original_format: Исходный формат изображения
            
        Returns:
            str: Сгенерированное имя файла
        """
        import uuid
        from datetime import datetime
        
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        unique_id = str(uuid.uuid4())[:8]
        
        # Приводим формат к стандартному виду
        if original_format.lower() == 'jpeg':
            original_format = 'jpg'
        
        return f"img_{timestamp}_{unique_id}.{original_format.lower()}"
    
    def generate_object_path(self, user_id: str, agent_id: str, filename: str) -> str:
        """
        Генерация пути объекта в MinIO bucket
        
        Args:
            user_id: ID пользователя
            agent_id: ID агента
            filename: Имя файла
            
        Returns:
            str: Путь объекта в формате agent_X/user_Y/YYYY/MM/DD/HH/filename
        """
        from datetime import datetime
        
        now = datetime.now()
        path = f"agent_{agent_id}/user_{user_id}/{now.year:04d}/{now.month:02d}/{now.day:02d}/{now.hour:02d}/{filename}"
        
        return path


# Создаём глобальный экземпляр настроек
image_settings = ImageSettings()
