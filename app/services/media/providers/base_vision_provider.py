"""
Базовый абстрактный класс для провайдеров Vision API
"""

import logging
from abc import ABC, abstractmethod
from typing import List, Optional
from dataclasses import dataclass

from app.core.config import settings


@dataclass
class VisionAnalysisResult:
    """Результат анализа изображения"""
    analysis: str
    provider_name: str
    success: bool
    error_message: Optional[str] = None
    processing_time_seconds: Optional[float] = None


class BaseVisionProvider(ABC):
    """
    Абстрактный базовый класс для всех провайдеров Vision API
    
    Каждый провайдер должен:
    1. Валидировать свои credentials при инициализации
    2. Реализовать метод analyze_images для анализа изображений
    3. Обеспечить graceful error handling
    """
    
    def __init__(self, provider_name: str):
        self.provider_name = provider_name
        self.logger = logging.getLogger(f"vision_provider_{provider_name}")
        self._validate_credentials()
        self.logger.info(f"Initialized {provider_name} vision provider")
    
    @abstractmethod
    def _validate_credentials(self) -> None:
        """
        Валидация credentials для провайдера
        
        Raises:
            ValueError: Если требуемые credentials недоступны
        """
        pass
    
    @abstractmethod
    async def analyze_images(
        self, 
        image_urls: List[str], 
        prompt: str = "Describe what you see in these images"
    ) -> VisionAnalysisResult:
        """
        Анализ изображений с помощью Vision API
        
        Args:
            image_urls: Список URL изображений для анализа
            prompt: Промпт для анализа (по умолчанию общее описание)
            
        Returns:
            VisionAnalysisResult: Результат анализа
        """
        pass
    
    def _create_error_result(self, error_message: str) -> VisionAnalysisResult:
        """Создание результата с ошибкой"""
        self.logger.error(f"{self.provider_name} error: {error_message}")
        return VisionAnalysisResult(
            analysis="",
            provider_name=self.provider_name,
            success=False,
            error_message=error_message
        )
    
    def _create_success_result(
        self, 
        analysis: str, 
        processing_time: Optional[float] = None
    ) -> VisionAnalysisResult:
        """Создание успешного результата"""
        self.logger.info(f"{self.provider_name} analysis completed successfully")
        if processing_time:
            self.logger.debug(f"Processing time: {processing_time:.2f}s")
            
        return VisionAnalysisResult(
            analysis=analysis,
            provider_name=self.provider_name,
            success=True,
            processing_time_seconds=processing_time
        )
    
    def is_available(self) -> bool:
        """
        Проверка доступности провайдера
        
        Returns:
            bool: True если провайдер доступен для использования
        """
        try:
            self._validate_credentials()
            return True
        except Exception as e:
            self.logger.warning(f"{self.provider_name} provider unavailable: {e}")
            return False


class VisionProviderError(Exception):
    """Базовое исключение для провайдеров Vision API"""
    
    def __init__(self, provider_name: str, message: str, original_error: Optional[Exception] = None):
        self.provider_name = provider_name
        self.original_error = original_error
        super().__init__(f"{provider_name}: {message}")


class CredentialsNotFoundError(VisionProviderError):
    """Исключение для отсутствующих credentials"""
    pass


class VisionAPIError(VisionProviderError):
    """Исключение для ошибок Vision API"""
    pass


class ImageProcessingError(VisionProviderError):
    """Исключение для ошибок обработки изображений"""
    pass
