"""
OpenAI Vision Provider - провайдер для анализа изображений через GPT-4V
"""

import time
from typing import List

import httpx
from openai import AsyncOpenAI

from app.core.config import settings
from app.services.media.providers.base_vision_provider import (
    BaseVisionProvider,
    VisionAnalysisResult,
    CredentialsNotFoundError,
    VisionAPIError
)


class OpenAIVisionProvider(BaseVisionProvider):
    """
    Провайдер для анализа изображений через OpenAI GPT-4V API
    """
    
    def __init__(self):
        super().__init__("openai")
        
        # Инициализация клиента OpenAI
        self.client = AsyncOpenAI(
            api_key=settings.OPENAI_API_KEY.get_secret_value()
        )
        
        # Модель для анализа изображений
        self.model = settings.IMAGE_OPENAI_MODEL
        
        # Настройки API
        self.max_tokens = 1000
        self.temperature = 0.1
        self.timeout_seconds = 30
        
        self.logger.info(f"OpenAI Vision Provider initialized with model: {self.model}")
    
    def _validate_credentials(self) -> None:
        """Валидация OpenAI API ключа"""
        if not settings.OPENAI_API_KEY:
            raise CredentialsNotFoundError(
                "openai", 
                "OPENAI_API_KEY environment variable is required"
            )
        
        api_key = settings.OPENAI_API_KEY.get_secret_value()
        if not api_key.startswith("sk-"):
            raise CredentialsNotFoundError(
                "openai",
                "Invalid OPENAI_API_KEY format (should start with 'sk-')"
            )
    
    async def analyze_images(
        self, 
        image_urls: List[str], 
        prompt: str = "Describe what you see in these images"
    ) -> VisionAnalysisResult:
        """
        Анализ изображений через OpenAI GPT-4V
        
        Args:
            image_urls: Список URL изображений
            prompt: Промпт для анализа
            
        Returns:
            VisionAnalysisResult: Результат анализа
        """
        start_time = time.time()
        
        try:
            self.logger.debug(f"Analyzing {len(image_urls)} images with OpenAI GPT-4V")
            
            # Подготовка сообщений для API
            messages = self._prepare_messages(image_urls, prompt)
            
            # Вызов OpenAI API
            response = await self._call_openai_api(messages)
            
            # Извлечение результата
            analysis_text = response.choices[0].message.content
            
            if not analysis_text:
                return self._create_error_result("Empty response from OpenAI API")
            
            processing_time = time.time() - start_time
            
            self.logger.info(f"OpenAI analysis completed in {processing_time:.2f}s")
            self.logger.debug(f"Analysis result length: {len(analysis_text)} characters")
            
            return self._create_success_result(analysis_text, processing_time)
            
        except Exception as e:
            error_message = f"OpenAI API error: {str(e)}"
            self.logger.error(error_message, exc_info=True)
            return self._create_error_result(error_message)
    
    def _prepare_messages(self, image_urls: List[str], prompt: str) -> List[dict]:
        """
        Подготовка сообщений для OpenAI API
        
        Args:
            image_urls: URLs изображений
            prompt: Промпт пользователя
            
        Returns:
            List[dict]: Форматированные сообщения для API
        """
        # Базовое сообщение с промптом
        content = [
            {
                "type": "text",
                "text": prompt
            }
        ]
        
        # Добавляем изображения
        for i, url in enumerate(image_urls):
            content.append({
                "type": "image_url",
                "image_url": {
                    "url": url,
                    "detail": "high"  # Высокое качество анализа
                }
            })
            
            # Добавляем нумерацию для множественных изображений
            if len(image_urls) > 1:
                content.append({
                    "type": "text", 
                    "text": f"[Image {i+1} of {len(image_urls)}]"
                })
        
        # Добавляем инструкции для лучшего анализа
        if len(image_urls) > 1:
            content.append({
                "type": "text",
                "text": "Please analyze all images together and provide a comprehensive description."
            })
        
        messages = [
            {
                "role": "user",
                "content": content
            }
        ]
        
        return messages
    
    async def _call_openai_api(self, messages: List[dict]) -> any:
        """
        Вызов OpenAI API с обработкой ошибок
        
        Args:
            messages: Подготовленные сообщения
            
        Returns:
            OpenAI response object
            
        Raises:
            VisionAPIError: При ошибках API
        """
        try:
            response = await self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                max_tokens=self.max_tokens,
                temperature=self.temperature,
                timeout=self.timeout_seconds
            )
            
            return response
            
        except httpx.TimeoutException as e:
            raise VisionAPIError("openai", f"Request timeout after {self.timeout_seconds}s", e)
            
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 401:
                raise VisionAPIError("openai", "Invalid API key", e)
            elif e.response.status_code == 429:
                raise VisionAPIError("openai", "Rate limit exceeded", e)
            elif e.response.status_code == 400:
                # Часто это означает проблему с изображениями
                error_detail = e.response.text if hasattr(e.response, 'text') else str(e)
                raise VisionAPIError("openai", f"Bad request (possibly invalid image): {error_detail}", e)
            else:
                raise VisionAPIError("openai", f"HTTP {e.response.status_code}: {e.response.text}", e)
                
        except Exception as e:
            # Общие ошибки OpenAI SDK
            if "insufficient_quota" in str(e):
                raise VisionAPIError("openai", "Insufficient quota/credits", e)
            elif "model_not_found" in str(e):
                raise VisionAPIError("openai", f"Model {self.model} not found", e)
            elif "invalid_request_error" in str(e):
                raise VisionAPIError("openai", f"Invalid request: {str(e)}", e)
            else:
                raise VisionAPIError("openai", f"Unexpected error: {str(e)}", e)
    
    async def test_connection(self) -> bool:
        """
        Тестирование подключения к OpenAI API
        
        Returns:
            bool: True если подключение работает
        """
        try:
            # Простой тест с текстовым запросом
            await self.client.chat.completions.create(
                model="gpt-3.5-turbo",  # Используем более дешёвую модель для теста
                messages=[{"role": "user", "content": "Test connection"}],
                max_tokens=5,
                timeout=10
            )
            
            self.logger.info("OpenAI connection test successful")
            return True
            
        except Exception as e:
            self.logger.error(f"OpenAI connection test failed: {e}")
            return False
    
    def get_supported_formats(self) -> List[str]:
        """
        Получение поддерживаемых форматов изображений
        
        Returns:
            List[str]: Поддерживаемые форматы
        """
        return ["png", "jpeg", "jpg", "gif", "webp"]
    
    def get_limits(self) -> dict:
        """
        Получение лимитов провайдера
        
        Returns:
            dict: Информация о лимитах
        """
        return {
            "max_images_per_request": 10,  # OpenAI поддерживает до 10 изображений
            "max_image_size_mb": 20,       # Максимальный размер изображения
            "max_total_size_mb": 100,      # Максимальный общий размер
            "supported_formats": self.get_supported_formats(),
            "max_tokens": self.max_tokens
        }
