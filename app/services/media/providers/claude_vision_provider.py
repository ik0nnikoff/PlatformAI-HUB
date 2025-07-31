"""
Claude Vision Provider - провайдер для анализа изображений через Anthropic Claude API
"""

import time
from typing import List
import base64
import httpx

from anthropic import AsyncAnthropic

from app.core.config import settings
from app.services.media.providers.base_vision_provider import (
    BaseVisionProvider,
    VisionAnalysisResult,
    CredentialsNotFoundError,
    VisionAPIError
)


class ClaudeVisionProvider(BaseVisionProvider):
    """
    Провайдер для анализа изображений через Anthropic Claude API
    """
    
    def __init__(self):
        super().__init__("claude")
        
        # Инициализация клиента Anthropic
        self.client = AsyncAnthropic(
            api_key=settings.ANTHROPIC_API_KEY.get_secret_value()
        )
        
        # Модель для анализа изображений
        self.model = settings.IMAGE_CLAUDE_MODEL
        
        # Настройки API
        self.max_tokens = 1000
        self.timeout_seconds = 30
        
        self.logger.info(f"Claude Vision Provider initialized with model: {self.model}")
    
    def _validate_credentials(self) -> None:
        """Валидация Anthropic API ключа"""
        if not settings.ANTHROPIC_API_KEY:
            raise CredentialsNotFoundError(
                "claude",
                "ANTHROPIC_API_KEY environment variable is required"
            )
        
        api_key = settings.ANTHROPIC_API_KEY.get_secret_value()
        if not api_key.startswith("sk-ant-"):
            raise CredentialsNotFoundError(
                "claude",
                "Invalid ANTHROPIC_API_KEY format (should start with 'sk-ant-')"
            )
    
    async def analyze_images(
        self, 
        image_urls: List[str], 
        prompt: str = "Describe what you see in these images"
    ) -> VisionAnalysisResult:
        """
        Анализ изображений через Anthropic Claude API
        
        Args:
            image_urls: Список URL изображений
            prompt: Промпт для анализа
            
        Returns:
            VisionAnalysisResult: Результат анализа
        """
        start_time = time.time()
        
        try:
            self.logger.debug(f"Analyzing {len(image_urls)} images with Claude")
            
            # Подготовка сообщений для API
            messages = await self._prepare_messages(image_urls, prompt)
            
            # Вызов Claude API
            response = await self._call_claude_api(messages)
            
            # Извлечение результата
            analysis_text = response.content[0].text
            
            if not analysis_text:
                return self._create_error_result("Empty response from Claude API")
            
            processing_time = time.time() - start_time
            
            self.logger.info(f"Claude analysis completed in {processing_time:.2f}s")
            self.logger.debug(f"Analysis result length: {len(analysis_text)} characters")
            
            return self._create_success_result(analysis_text, processing_time)
            
        except Exception as e:
            error_message = f"Claude API error: {str(e)}"
            self.logger.error(error_message, exc_info=True)
            return self._create_error_result(error_message)
    
    async def _prepare_messages(self, image_urls: List[str], prompt: str) -> List[dict]:
        """
        Подготовка сообщений для Claude API
        
        Args:
            image_urls: URLs изображений
            prompt: Промпт пользователя
            
        Returns:
            List[dict]: Форматированные сообщения для API
        """
        # Базовое содержимое с промптом
        content = [
            {
                "type": "text",
                "text": prompt
            }
        ]
        
        # Добавляем изображения
        for i, url in enumerate(image_urls):
            try:
                # Claude требует изображения в base64 формате
                image_data = await self._download_image_as_base64(url)
                
                # Определяем MIME тип из расширения URL
                mime_type = self._get_mime_type_from_url(url)
                
                content.append({
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": mime_type,
                        "data": image_data
                    }
                })
                
                # Добавляем нумерацию для множественных изображений
                if len(image_urls) > 1:
                    content.append({
                        "type": "text",
                        "text": f"[Image {i+1} of {len(image_urls)}]"
                    })
                    
            except Exception as e:
                self.logger.error(f"Failed to process image {i+1}: {e}")
                content.append({
                    "type": "text",
                    "text": f"[Image {i+1}: Failed to load]"
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
    
    async def _download_image_as_base64(self, url: str) -> str:
        """
        Скачивание изображения и конвертация в base64
        
        Args:
            url: URL изображения
            
        Returns:
            str: Base64 encoded изображение
            
        Raises:
            VisionAPIError: При ошибке скачивания
        """
        try:
            async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                response = await client.get(url)
                response.raise_for_status()
                
                # Конвертация в base64
                image_base64 = base64.b64encode(response.content).decode('utf-8')
                
                self.logger.debug(f"Successfully downloaded image: {len(response.content)} bytes")
                return image_base64
                
        except httpx.TimeoutException as e:
            raise VisionAPIError("claude", f"Image download timeout: {url}", e)
        except httpx.HTTPStatusError as e:
            raise VisionAPIError("claude", f"Failed to download image {url}: HTTP {e.response.status_code}", e)
        except Exception as e:
            raise VisionAPIError("claude", f"Image download error for {url}: {str(e)}", e)
    
    def _get_mime_type_from_url(self, url: str) -> str:
        """
        Определение MIME типа из URL
        
        Args:
            url: URL изображения
            
        Returns:
            str: MIME тип
        """
        url_lower = url.lower()
        
        if '.png' in url_lower:
            return 'image/png'
        elif '.jpg' in url_lower or '.jpeg' in url_lower:
            return 'image/jpeg'
        elif '.gif' in url_lower:
            return 'image/gif'
        elif '.webp' in url_lower:
            return 'image/webp'
        else:
            # По умолчанию JPEG
            return 'image/jpeg'
    
    async def _call_claude_api(self, messages: List[dict]) -> any:
        """
        Вызов Claude API с обработкой ошибок
        
        Args:
            messages: Подготовленные сообщения
            
        Returns:
            Claude response object
            
        Raises:
            VisionAPIError: При ошибках API
        """
        try:
            response = await self.client.messages.create(
                model=self.model,
                messages=messages,
                max_tokens=self.max_tokens,
                timeout=self.timeout_seconds
            )
            
            return response
            
        except Exception as e:
            # Обработка различных типов ошибок Anthropic
            error_str = str(e).lower()
            
            if "authentication" in error_str or "api_key" in error_str:
                raise VisionAPIError("claude", "Invalid API key", e)
            elif "rate_limit" in error_str or "too_many_requests" in error_str:
                raise VisionAPIError("claude", "Rate limit exceeded", e)
            elif "insufficient_quota" in error_str or "credits" in error_str:
                raise VisionAPIError("claude", "Insufficient quota/credits", e)
            elif "timeout" in error_str:
                raise VisionAPIError("claude", f"Request timeout after {self.timeout_seconds}s", e)
            elif "invalid_request" in error_str:
                raise VisionAPIError("claude", f"Invalid request: {str(e)}", e)
            elif "model_not_found" in error_str:
                raise VisionAPIError("claude", f"Model {self.model} not found", e)
            else:
                raise VisionAPIError("claude", f"Unexpected error: {str(e)}", e)
    
    async def test_connection(self) -> bool:
        """
        Тестирование подключения к Claude API
        
        Returns:
            bool: True если подключение работает
        """
        try:
            # Простой тест с текстовым запросом
            await self.client.messages.create(
                model=self.model,
                messages=[{"role": "user", "content": "Test connection"}],
                max_tokens=5,
                timeout=10
            )
            
            self.logger.info("Claude connection test successful")
            return True
            
        except Exception as e:
            self.logger.error(f"Claude connection test failed: {e}")
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
            "max_images_per_request": 5,   # Claude поддерживает до 5 изображений
            "max_image_size_mb": 5,        # Максимальный размер изображения
            "max_total_size_mb": 25,       # Максимальный общий размер
            "supported_formats": self.get_supported_formats(),
            "max_tokens": self.max_tokens,
            "requires_base64": True        # Claude требует base64 encoding
        }
