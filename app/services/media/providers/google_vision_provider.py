"""
Google Vision Provider - провайдер для анализа изображений через Google Cloud Vision API
"""

import asyncio
import time
from typing import List, Optional

from google.cloud import vision
from google.oauth2 import service_account

from app.core.config import settings
from app.services.media.providers.base_vision_provider import (
    BaseVisionProvider,
    VisionAnalysisResult,
    CredentialsNotFoundError,
    VisionAPIError
)


class GoogleVisionProvider(BaseVisionProvider):
    """
    Провайдер для анализа изображений через Google Cloud Vision API
    """
    
    def __init__(self):
        super().__init__("google")
        
        # Инициализация клиента Google Vision
        self.client = None
        self._initialize_client()
        
        # Настройки анализа
        self.max_results = 10
        self.timeout_seconds = 30
        
        self.logger.info("Google Vision Provider initialized")
    
    def _validate_credentials(self) -> None:
        """Валидация Google Cloud credentials"""
        if not settings.GOOGLE_APPLICATION_CREDENTIALS:
            raise CredentialsNotFoundError(
                "google",
                "GOOGLE_APPLICATION_CREDENTIALS environment variable is required"
            )
        
        if not settings.GOOGLE_CLOUD_PROJECT_ID:
            raise CredentialsNotFoundError(
                "google",
                "GOOGLE_CLOUD_PROJECT_ID environment variable is required"
            )
        
        # Проверка существования файла credentials
        import os
        credentials_path = settings.GOOGLE_APPLICATION_CREDENTIALS
        if not os.path.exists(credentials_path):
            raise CredentialsNotFoundError(
                "google",
                f"Credentials file not found: {credentials_path}"
            )
    
    def _initialize_client(self) -> None:
        """Инициализация Google Vision клиента"""
        try:
            # Создание клиента с явными credentials
            credentials = service_account.Credentials.from_service_account_file(
                settings.GOOGLE_APPLICATION_CREDENTIALS
            )
            
            self.client = vision.ImageAnnotatorClient(credentials=credentials)
            self.logger.debug("Google Vision client initialized successfully")
            
        except Exception as e:
            self.logger.error(f"Failed to initialize Google Vision client: {e}")
            raise CredentialsNotFoundError("google", f"Failed to initialize client: {str(e)}")
    
    async def analyze_images(
        self, 
        image_urls: List[str], 
        prompt: str = "Describe what you see in these images"
    ) -> VisionAnalysisResult:
        """
        Анализ изображений через Google Cloud Vision API
        
        Args:
            image_urls: Список URL изображений
            prompt: Промпт для анализа (для совместимости, Google Vision не использует промпты)
            
        Returns:
            VisionAnalysisResult: Результат анализа
        """
        start_time = time.time()
        
        try:
            self.logger.debug(f"Analyzing {len(image_urls)} images with Google Vision API")
            
            # Google Vision API анализирует изображения по одному
            all_descriptions = []
            
            for i, image_url in enumerate(image_urls):
                description = await self._analyze_single_image(image_url)
                if description:
                    if len(image_urls) > 1:
                        all_descriptions.append(f"Image {i+1}: {description}")
                    else:
                        all_descriptions.append(description)
            
            if not all_descriptions:
                return self._create_error_result("No descriptions generated for any images")
            
            # Объединяем описания
            combined_description = self._combine_descriptions(all_descriptions, prompt)
            
            processing_time = time.time() - start_time
            
            self.logger.info(f"Google Vision analysis completed in {processing_time:.2f}s")
            self.logger.debug(f"Analysis result length: {len(combined_description)} characters")
            
            return self._create_success_result(combined_description, processing_time)
            
        except Exception as e:
            error_message = f"Google Vision API error: {str(e)}"
            self.logger.error(error_message, exc_info=True)
            return self._create_error_result(error_message)
    
    async def _analyze_single_image(self, image_url: str) -> Optional[str]:
        """
        Анализ одного изображения через Google Vision API
        
        Args:
            image_url: URL изображения
            
        Returns:
            Optional[str]: Описание изображения или None при ошибке
        """
        try:
            # Создание объекта изображения
            image = vision.Image()
            image.source.image_uri = image_url
            
            # Выполнение анализа в отдельном потоке (Google SDK синхронный)
            loop = asyncio.get_event_loop()
            
            # Получаем различные типы аннотаций
            response = await loop.run_in_executor(
                None,
                self._get_image_annotations,
                image
            )
            
            # Объединяем результаты разных типов анализа
            description_parts = []
            
            # Обнаружение объектов
            if response.label_annotations:
                labels = [label.description for label in response.label_annotations[:5]]
                description_parts.append(f"Objects detected: {', '.join(labels)}")
            
            # Обнаружение текста
            if response.text_annotations:
                text_content = response.text_annotations[0].description.strip()
                if text_content:
                    # Ограничиваем длину текста
                    if len(text_content) > 200:
                        text_content = text_content[:200] + "..."
                    description_parts.append(f"Text found: {text_content}")
            
            # Обнаружение лиц
            if response.face_annotations:
                face_count = len(response.face_annotations)
                description_parts.append(f"Faces detected: {face_count}")
            
            # Обнаружение ориентиров
            if response.landmark_annotations:
                landmarks = [landmark.description for landmark in response.landmark_annotations[:3]]
                description_parts.append(f"Landmarks: {', '.join(landmarks)}")
            
            if not description_parts:
                return "Image content could not be analyzed"
            
            return ". ".join(description_parts)
            
        except Exception as e:
            self.logger.error(f"Failed to analyze single image {image_url}: {e}")
            return None
    
    def _get_image_annotations(self, image: vision.Image) -> any:
        """
        Получение аннотаций изображения (синхронный метод для executor)
        
        Args:
            image: Объект изображения Google Vision
            
        Returns:
            Response object с аннотациями
        """
        # Запрашиваем разные типы анализа
        features = [
            vision.Feature(type_=vision.Feature.Type.LABEL_DETECTION, max_results=self.max_results),
            vision.Feature(type_=vision.Feature.Type.TEXT_DETECTION, max_results=1),
            vision.Feature(type_=vision.Feature.Type.FACE_DETECTION, max_results=self.max_results),
            vision.Feature(type_=vision.Feature.Type.LANDMARK_DETECTION, max_results=5),
        ]
        
        request = vision.AnnotateImageRequest(image=image, features=features)
        
        response = self.client.annotate_image(request=request)
        
        # Проверка на ошибки
        if response.error.message:
            raise VisionAPIError("google", f"Vision API error: {response.error.message}")
        
        return response
    
    def _combine_descriptions(self, descriptions: List[str], original_prompt: str) -> str:
        """
        Объединение описаний с учётом исходного промпта
        
        Args:
            descriptions: Список описаний изображений
            original_prompt: Исходный промпт пользователя
            
        Returns:
            str: Объединённое описание
        """
        if len(descriptions) == 1:
            combined = descriptions[0]
        else:
            combined = "\n\n".join(descriptions)
        
        # Добавляем контекст если промпт специфичный
        if original_prompt and original_prompt.lower() not in [
            "describe what you see in these images",
            "describe what you see",
            "what do you see"
        ]:
            combined = f"Based on the request '{original_prompt}':\n\n{combined}"
        
        return combined
    
    async def test_connection(self) -> bool:
        """
        Тестирование подключения к Google Vision API
        
        Returns:
            bool: True если подключение работает
        """
        try:
            # Создаём тестовое изображение (белый пиксель в base64)
            test_image_content = b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\tpHYs\x00\x00\x0b\x13\x00\x00\x0b\x13\x01\x00\x9a\x9c\x18\x00\x00\x00\x0cIDATx\x9cc```\x00\x00\x00\x04\x00\x01\'\x18\x05Q\x00\x00\x00\x00IEND\xaeB`\x82'
            
            image = vision.Image(content=test_image_content)
            features = [vision.Feature(type_=vision.Feature.Type.LABEL_DETECTION, max_results=1)]
            request = vision.AnnotateImageRequest(image=image, features=features)
            
            loop = asyncio.get_event_loop()
            response = await loop.run_in_executor(
                None,
                self.client.annotate_image,
                request
            )

            # Используем response для проверки ошибок
            if hasattr(response, "error") and response.error.message:
                self.logger.error(f"Google Vision test image error: {response.error.message}")
                return False

            self.logger.info("Google Vision connection test successful")
            return True

        except Exception as e:
            self.logger.error(f"Google Vision connection test failed: {e}")
            return False
    
    def get_supported_formats(self) -> List[str]:
        """
        Получение поддерживаемых форматов изображений
        
        Returns:
            List[str]: Поддерживаемые форматы
        """
        return ["png", "jpeg", "jpg", "gif", "webp", "bmp", "tiff"]
    
    def get_limits(self) -> dict:
        """
        Получение лимитов провайдера
        
        Returns:
            dict: Информация о лимитах
        """
        return {
            "max_images_per_request": 1,   # Google Vision обрабатывает по одному изображению
            "max_image_size_mb": 20,       # Максимальный размер изображения
            "supported_formats": self.get_supported_formats(),
            "features": [
                "label_detection",
                "text_detection", 
                "face_detection",
                "landmark_detection"
            ]
        }
