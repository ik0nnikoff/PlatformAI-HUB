"""
ImageOrchestrator - центральный координатор для обработки изображений
Управляет провайдерами Vision API и MinIO storage
"""

import logging
import time
from typing import List, Optional, Dict, Any

from app.core.config import settings
from app.services.media.providers.base_vision_provider import (
    BaseVisionProvider, 
    VisionAnalysisResult
)
from app.services.media.minio_image_manager import MinIOImageManager


class ImageOrchestrator:
    """
    Центральный координатор для обработки изображений
    
    Функциональность:
    - Управление провайдерами Vision API с fallback
    - Загрузка изображений в MinIO
    - Валидация и обработка изображений
    - Анализ изображений через Vision API
    """
    
    def __init__(self):
        self.logger = logging.getLogger("image_orchestrator")
        self.minio_manager = MinIOImageManager(self.logger)
        self.providers: List[BaseVisionProvider] = []
        self._initialized = False
    
    async def initialize(self) -> None:
        """Инициализация ImageOrchestrator"""
        try:
            # Инициализация MinIO manager
            await self.minio_manager.initialize()
            
            # Инициализация провайдеров Vision API
            self.providers = await self._init_vision_providers()
            
            self._initialized = True
            self.logger.info(f"ImageOrchestrator initialized with {len(self.providers)} vision providers")
            
        except Exception as e:
            self.logger.error(f"Failed to initialize ImageOrchestrator: {e}", exc_info=True)
            raise
    
    async def _init_vision_providers(self) -> List[BaseVisionProvider]:
        """
        Инициализация провайдеров Vision API с проверкой доступности
        
        Returns:
            List[BaseVisionProvider]: Список доступных провайдеров
        """
        providers = []
        available_providers = settings.get_available_vision_providers()
        
        self.logger.info(f"Available vision providers based on credentials: {available_providers}")
        
        # Импортируем провайдеры динамически, чтобы избежать ошибок если credentials отсутствуют
        for provider_name in settings.IMAGE_VISION_PROVIDERS:
            if provider_name not in available_providers:
                self.logger.warning(f"Skipping {provider_name} provider: missing API credentials")
                continue
            
            try:
                if provider_name == "openai":
                    from app.services.media.providers.openai_vision_provider import OpenAIVisionProvider
                    provider = OpenAIVisionProvider()
                    providers.append(provider)
                    
                elif provider_name == "google":
                    from app.services.media.providers.google_vision_provider import GoogleVisionProvider
                    provider = GoogleVisionProvider()
                    providers.append(provider)
                    
                elif provider_name == "claude":
                    from app.services.media.providers.claude_vision_provider import ClaudeVisionProvider
                    provider = ClaudeVisionProvider()
                    providers.append(provider)
                    
                self.logger.info(f"Successfully initialized {provider_name} vision provider")
                
            except Exception as e:
                self.logger.error(f"Failed to initialize {provider_name} provider: {e}")
                continue
        
        if not providers:
            raise ValueError("No vision providers available. Please configure API keys for at least one provider.")
        
        self.logger.info(f"Initialized {len(providers)} vision providers: {[p.provider_name for p in providers]}")
        return providers
    
    async def process_images(self,
                           images_data: List[bytes],
                           agent_id: str,
                           user_id: str,
                           original_filenames: Optional[List[str]] = None,
                           metadata: Optional[Dict[str, Any]] = None) -> List[str]:
        """
        Обработка и загрузка изображений в MinIO
        
        Args:
            images_data: Список байтов изображений
            agent_id: ID агента
            user_id: ID пользователя
            original_filenames: Исходные имена файлов (опционально)
            metadata: Дополнительные метаданные
            
        Returns:
            List[str]: Список presigned URLs загруженных изображений
            
        Raises:
            ValueError: Если изображения не прошли валидацию
            RuntimeError: Если загрузка не удалась
        """
        if not self._initialized:
            await self.initialize()
        
        self.logger.info(f"Processing {len(images_data)} images for user {user_id}, agent {agent_id}")
        
        try:
            # Загрузка изображений в MinIO
            object_keys = await self.minio_manager.upload_user_images(
                images_data=images_data,
                agent_id=agent_id,
                user_id=user_id,
                original_filenames=original_filenames,
                metadata=metadata
            )
            
            # Генерация presigned URLs
            presigned_urls = await self.minio_manager.get_presigned_urls(
                object_keys=object_keys,
                expires_hours=1  # URLs действительны 1 час
            )
            
            self.logger.info(f"Successfully processed {len(presigned_urls)} images")
            return presigned_urls
            
        except Exception as e:
            self.logger.error(f"Failed to process images: {e}", exc_info=True)
            raise
    
    async def analyze_images(self,
                           image_urls: List[str],
                           prompt: str = "Describe what you see in these images") -> VisionAnalysisResult:
        """
        Анализ изображений с помощью Vision API с fallback между провайдерами
        
        Args:
            image_urls: Список URLs изображений для анализа
            prompt: Промпт для анализа
            
        Returns:
            VisionAnalysisResult: Результат анализа
        """
        if not self._initialized:
            await self.initialize()
        
        if not image_urls:
            return VisionAnalysisResult(
                analysis="",
                provider_name="none",
                success=False,
                error_message="No image URLs provided"
            )
        
        self.logger.info(f"Analyzing {len(image_urls)} images with prompt: '{prompt[:50]}...'")
        
        # Пробуем провайдеров по порядку приоритета
        last_error = None
        
        for provider in self.providers:
            try:
                start_time = time.time()
                
                self.logger.debug(f"Trying {provider.provider_name} provider...")
                result = await provider.analyze_images(image_urls, prompt)
                
                if result.success:
                    processing_time = time.time() - start_time
                    result.processing_time_seconds = processing_time
                    
                    self.logger.info(f"Successfully analyzed images using {provider.provider_name} "
                                   f"in {processing_time:.2f}s")
                    return result
                else:
                    self.logger.warning(f"{provider.provider_name} provider failed: {result.error_message}")
                    last_error = result.error_message
                    
            except Exception as e:
                self.logger.error(f"{provider.provider_name} provider error: {e}")
                last_error = str(e)
                continue
        
        # Все провайдеры не сработали
        error_message = f"All vision providers failed. Last error: {last_error}"
        self.logger.error(error_message)
        
        return VisionAnalysisResult(
            analysis="",
            provider_name="fallback_failed",
            success=False,
            error_message=error_message
        )
    
    async def process_and_analyze_images(self,
                                       images_data: List[bytes],
                                       agent_id: str,
                                       user_id: str,
                                       analysis_prompt: str = "Describe what you see in these images",
                                       original_filenames: Optional[List[str]] = None,
                                       metadata: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """
        Полный цикл: загрузка изображений в MinIO и их анализ
        
        Args:
            images_data: Список байтов изображений
            agent_id: ID агента
            user_id: ID пользователя
            analysis_prompt: Промпт для анализа
            original_filenames: Исходные имена файлов
            metadata: Дополнительные метаданные
            
        Returns:
            Dict[str, Any]: Результат с URLs и анализом
        """
        try:
            # Загрузка и получение URLs
            image_urls = await self.process_images(
                images_data=images_data,
                agent_id=agent_id,
                user_id=user_id,
                original_filenames=original_filenames,
                metadata=metadata
            )
            
            # Анализ изображений
            analysis_result = await self.analyze_images(image_urls, analysis_prompt)
            
            result = {
                "image_urls": image_urls,
                "analysis": {
                    "success": analysis_result.success,
                    "analysis_text": analysis_result.analysis,
                    "provider_used": analysis_result.provider_name,
                    "error_message": analysis_result.error_message,
                    "processing_time_seconds": analysis_result.processing_time_seconds
                }
            }
            
            self.logger.info(f"Completed full image processing cycle for user {user_id}")
            return result
            
        except Exception as e:
            self.logger.error(f"Failed to process and analyze images: {e}", exc_info=True)
            raise
    
    async def upload_user_image(self,
                               agent_id: str,
                               user_id: str,
                               image_data: bytes,
                               original_filename: Optional[str] = None,
                               metadata: Optional[Dict[str, Any]] = None) -> str:
        """
        Загрузка изображения пользователя через MinIO менеджер
        
        Args:
            agent_id: ID агента
            user_id: ID пользователя
            image_data: Байты изображения
            original_filename: Исходное имя файла (опционально)
            metadata: Дополнительные метаданные
            
        Returns:
            str: Presigned URL загруженного изображения
        """
        if not self._initialized:
            await self.initialize()
            
        # Загружаем изображение и получаем object_key
        object_key = await self.minio_manager.upload_user_image(
            image_data=image_data,
            agent_id=agent_id,
            user_id=user_id,
            original_filename=original_filename,
            metadata=metadata
        )
        
        # Генерируем presigned URL
        presigned_url = await self.minio_manager.get_presigned_url(
            object_key=object_key,
            expires_hours=1  # URL действителен 1 час
        )
        
        return presigned_url

    def get_available_providers(self) -> List[str]:
        """Получение списка доступных провайдеров"""
        return [provider.provider_name for provider in self.providers]
    
    def is_initialized(self) -> bool:
        """Проверка инициализации"""
        return self._initialized
    
    async def health_check(self) -> Dict[str, Any]:
        """
        Проверка состояния ImageOrchestrator
        
        Returns:
            Dict[str, Any]: Статус всех компонентов
        """
        health_status = {
            "initialized": self._initialized,
            "minio_initialized": self.minio_manager.is_initialized(),
            "available_providers": len(self.providers),
            "provider_names": [p.provider_name for p in self.providers],
            "provider_health": {}
        }
        
        # Проверяем доступность каждого провайдера
        for provider in self.providers:
            try:
                health_status["provider_health"][provider.provider_name] = provider.is_available()
            except Exception as e:
                health_status["provider_health"][provider.provider_name] = False
                self.logger.error(f"Health check failed for {provider.provider_name}: {e}")
        
        return health_status


# Глобальный экземпляр ImageOrchestrator
image_orchestrator = ImageOrchestrator()
