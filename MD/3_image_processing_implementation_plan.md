# План реализации Image Processing для PlatformAI-HUB

## Архитектурное решение

**Выбранный подход**: Вариант 2 - Direct URL passing
- Изображения загружаются в MinIO bucket "user-files"
- AgentRunner передаёт presigned URLs в LangGraph агенты
- Мультипровайдерная поддержка Vision API (OpenAI → Google → Claude)

## Детальный план реализации

### Этап 1: Базовая инфраструктура

#### 1.1 Environment Variables (.env)
```bash
# Image Processing Settings
IMAGE_MAX_FILE_SIZE_MB=10
IMAGE_MAX_FILES_COUNT=5
IMAGE_SUPPORTED_FORMATS=jpg,jpeg,png,webp,gif

# Vision API Providers (priority order)
IMAGE_VISION_PROVIDERS=openai,google,claude

# OpenAI Vision API
OPENAI_API_KEY=sk-...                       # Required for OpenAI provider
IMAGE_OPENAI_MODEL=gpt-4o

# Google Vision API
GOOGLE_APPLICATION_CREDENTIALS=/path/to/credentials.json  # Required for Google provider
GOOGLE_CLOUD_PROJECT_ID=your-project-id     # Required for Google provider

# Claude Vision API
ANTHROPIC_API_KEY=sk-ant-...                 # Required for Claude provider
IMAGE_CLAUDE_MODEL=claude-3-sonnet-20240229

# MinIO for user files
MINIO_USER_FILES_BUCKET=user-files
```

#### 1.2 Config Settings (app/core/config.py)
```python
class Settings:
    # Image processing
    IMAGE_MAX_FILE_SIZE_MB: int = int(os.getenv("IMAGE_MAX_FILE_SIZE_MB", "10"))
    IMAGE_MAX_FILES_COUNT: int = int(os.getenv("IMAGE_MAX_FILES_COUNT", "5"))
    IMAGE_SUPPORTED_FORMATS: List[str] = os.getenv("IMAGE_SUPPORTED_FORMATS", "jpg,jpeg,png,webp,gif").split(",")
    MINIO_USER_FILES_BUCKET: str = os.getenv("MINIO_USER_FILES_BUCKET", "user-files")
    
    # Vision API settings
    IMAGE_VISION_PROVIDERS: List[str] = os.getenv("IMAGE_VISION_PROVIDERS", "openai,google,claude").split(",")
    
    # OpenAI Vision API
    OPENAI_API_KEY: Optional[str] = os.getenv("OPENAI_API_KEY")
    IMAGE_OPENAI_MODEL: str = os.getenv("IMAGE_OPENAI_MODEL", "gpt-4o")
    
    # Google Vision API  
    GOOGLE_APPLICATION_CREDENTIALS: Optional[str] = os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
    GOOGLE_CLOUD_PROJECT_ID: Optional[str] = os.getenv("GOOGLE_CLOUD_PROJECT_ID")
    
    # Claude Vision API
    ANTHROPIC_API_KEY: Optional[str] = os.getenv("ANTHROPIC_API_KEY")
    IMAGE_CLAUDE_MODEL: str = os.getenv("IMAGE_CLAUDE_MODEL", "claude-3-sonnet-20240229")
    
    def get_available_vision_providers(self) -> List[str]:
        """Return list of vision providers that have required API keys"""
        available_providers = []
        
        # Check OpenAI
        if self.OPENAI_API_KEY:
            available_providers.append("openai")
            
        # Check Google
        if self.GOOGLE_APPLICATION_CREDENTIALS and self.GOOGLE_CLOUD_PROJECT_ID:
            available_providers.append("google")
            
        # Check Claude
        if self.ANTHROPIC_API_KEY:
            available_providers.append("claude")
            
        return available_providers
```

#### 1.3 MinIO Bucket Setup
- Создать bucket "user-files" для пользовательских изображений
- Настроить структуру папок: `user-files/agent_{agent_id}/user_{user_id}/год/месяц/день/час/`

### Этап 2: ImageOrchestrator и Vision Providers

#### 2.1 ImageOrchestrator (app/services/media/image_orchestrator.py)
```python
class ImageOrchestrator:
    def __init__(self, settings: ImageSettings, minio_manager: MinIOManager):
        self.settings = settings
        self.minio_manager = minio_manager
        self.providers = self._init_providers()
    
    def _init_providers(self) -> List[VisionProvider]:
        """Initialize only providers that have required API keys"""
        providers = []
        available_providers = self.settings.get_available_vision_providers()
        
        logger.info(f"Available vision providers: {available_providers}")
        
        for provider_name in self.settings.IMAGE_VISION_PROVIDERS:
            if provider_name not in available_providers:
                logger.warning(f"Skipping {provider_name} provider: missing API credentials")
                continue
                
            if provider_name == "openai":
                providers.append(OpenAIVisionProvider(self.settings))
            elif provider_name == "google":
                providers.append(GoogleVisionProvider(self.settings))
            elif provider_name == "claude":
                providers.append(ClaudeVisionProvider(self.settings))
        
        if not providers:
            raise ValueError("No vision providers available. Please configure API keys.")
            
        logger.info(f"Initialized {len(providers)} vision providers")
        return providers
    
    async def process_images(self, image_files: List[bytes], user_id: str, agent_id: str) -> List[str]:
        """Upload images to MinIO, return presigned URLs"""
        
    async def analyze_images(self, image_urls: List[str], prompt: str) -> str:
        """Multi-provider fallback for image analysis"""
        
    def _validate_image(self, image_data: bytes) -> bool:
        """Validate image format and size"""
```

#### 2.2 Vision API Providers

**Base Vision Provider** (app/services/media/providers/base_vision_provider.py):
```python
from abc import ABC, abstractmethod
from typing import List

class BaseVisionProvider(ABC):
    def __init__(self, settings):
        self.settings = settings
        self._validate_credentials()
    
    @abstractmethod
    def _validate_credentials(self):
        """Validate that required credentials are available"""
        pass
    
    @abstractmethod
    async def analyze_images(self, image_urls: List[str], prompt: str) -> str:
        """Analyze images and return description"""
        pass
```

**OpenAI Vision Provider** (app/services/media/providers/openai_vision_provider.py):
```python
class OpenAIVisionProvider(BaseVisionProvider):
    def _validate_credentials(self):
        if not self.settings.OPENAI_API_KEY:
            raise ValueError("OPENAI_API_KEY is required for OpenAI Vision provider")
    
    async def analyze_images(self, image_urls: List[str], prompt: str) -> str:
        # OpenAI GPT-4V API integration using self.settings.OPENAI_API_KEY
```

**Google Vision Provider** (app/services/media/providers/google_vision_provider.py):
```python
class GoogleVisionProvider(BaseVisionProvider):
    def _validate_credentials(self):
        if not self.settings.GOOGLE_APPLICATION_CREDENTIALS:
            raise ValueError("GOOGLE_APPLICATION_CREDENTIALS is required for Google Vision provider")
        if not self.settings.GOOGLE_CLOUD_PROJECT_ID:
            raise ValueError("GOOGLE_CLOUD_PROJECT_ID is required for Google Vision provider")
    
    async def analyze_images(self, image_urls: List[str], prompt: str) -> str:
        # Google Vision API integration using credentials
```

**Claude Vision Provider** (app/services/media/providers/claude_vision_provider.py):
```python
class ClaudeVisionProvider(BaseVisionProvider):
    def _validate_credentials(self):
        if not self.settings.ANTHROPIC_API_KEY:
            raise ValueError("ANTHROPIC_API_KEY is required for Claude Vision provider")
    
    async def analyze_images(self, image_urls: List[str], prompt: str) -> str:
        # Anthropic Claude Vision integration using self.settings.ANTHROPIC_API_KEY
```

### Этап 3: Интеграционные изменения

#### 3.1 Telegram Integration (app/integrations/telegram/telegram_bot.py)

**Новые handlers:**
```python
@router.message(F.photo)
async def handle_photo_message(message: Message):
    """Handle single photo messages"""
    await _handle_photo_message(message)

@router.message(F.media_group_id.as_("media_group_id"))
async def handle_media_group(message: Message, media_group_id: str):
    """Handle multiple photos sent as MediaGroup"""
    await _handle_media_group_message(message, media_group_id)

async def _handle_photo_message(message: Message):
    # Get largest photo: message.photo[-1]
    # Download via bot.download()
    # Process via ImageOrchestrator
    # Send to agent via Redis

async def _handle_media_group_message(message: Message, media_group_id: str):
    # Collect all photos in MediaGroup
    # Process multiple images
    # Send batch to agent
```

#### 3.2 WhatsApp Integration (app/integrations/whatsapp/whatsapp_bot.py)

**Message type handling:**
```python
async def _handle_received_message(self, data: dict):
    message_type = data.get("type", "")
    
    # ... existing handlers ...
    elif message_type == "image":
        await self._handle_image_message(data)

async def _handle_image_message(self, data: dict):
    # Download via wppconnect API: POST /download-media
    # Extract image data and metadata
    # Process via ImageOrchestrator
    # Send to agent via Redis
```

**WppConnect API calls:**
```python
# Download media
async def _download_whatsapp_media(self, message_id: str) -> bytes:
    response = await self.session.post(
        f"{self.base_url}/download-media",
        json={"messageId": message_id}
    )
    return response.content
```

#### 3.3 AgentRunner Updates (app/agent_runner/agent_runner.py)

**Redis message payload:**
```python
async def _handle_pubsub_message(self, channel: str, message: dict):
    if channel.endswith(":input"):
        # Extract all data types
        text = message.get("text", "")
        audio_url = message.get("audio_url")
        image_urls = message.get("image_urls", [])  # NEW FIELD
        
        # Pass to LangGraph agent
        agent_input = {
            "text": text,
            "audio_url": audio_url,
            "image_urls": image_urls,
            "user_info": user_info
        }
```

### Этап 4: LangGraph Agent Integration

#### 4.1 Agent State Updates (app/agent_runner/langgraph/models.py)
```python
class AgentState(TypedDict):
    # ... existing fields ...
    image_urls: List[str]
    image_analysis: Optional[str]
```

#### 4.2 Vision Tools (app/agent_runner/langgraph/tools.py)
```python
@tool
async def analyze_images(image_urls: List[str], prompt: str = "Describe what you see") -> str:
    """Analyze images using vision API providers"""
    image_orchestrator = get_image_orchestrator()
    return await image_orchestrator.analyze_images(image_urls, prompt)

@tool
async def describe_image_content(image_urls: List[str]) -> str:
    """Get detailed description of image contents"""
    return await analyze_images(image_urls, "Provide a detailed description of what's in these images")
```

#### 4.3 Processing Nodes Updates
```python
async def process_message_node(state: AgentState) -> dict:
    result = {}
    
    # Process images if present
    if state.get("image_urls"):
        image_analysis = await analyze_images(
            state["image_urls"], 
            "Analyze these images in context of the conversation"
        )
        result["image_analysis"] = image_analysis
    
    return result
```

### Этап 5: API Endpoints (опционально)

#### 5.1 Image Analysis API (app/api/routers/image_api.py)
```python
@router.post("/agents/{agent_id}/analyze-images")
async def analyze_images_endpoint(
    agent_id: str,
    request: ImageAnalysisRequest,
    image_orchestrator: ImageOrchestrator = Depends(get_image_orchestrator)
):
    """Direct API endpoint for image analysis"""
```

### Этап 6: Тестирование

#### 6.1 Unit Tests
- `tests/test_image_orchestrator.py`
- `tests/test_vision_providers.py`
- `tests/test_image_validation.py`

#### 6.2 Integration Tests  
- `tests/test_telegram_image_integration.py`
- `tests/test_whatsapp_image_integration.py`
- `tests/test_agent_image_processing.py`

#### 6.3 End-to-End Tests
- Реальные сценарии с отправкой изображений через ботов
- Проверка передачи в агенты и получения анализа

## Структура файлов

```
app/
├── services/
│   └── media/
│       ├── __init__.py
│       ├── image_orchestrator.py
│       ├── image_settings.py
│       └── providers/
│           ├── __init__.py
│           ├── base_vision_provider.py
│           ├── openai_vision_provider.py
│           ├── google_vision_provider.py
│           └── claude_vision_provider.py
├── integrations/
│   ├── telegram/
│   │   └── telegram_bot.py  # обновлённый
│   └── whatsapp/
│       └── whatsapp_bot.py  # обновлённый
├── agent_runner/
│   ├── agent_runner.py      # обновлённый
│   └── langgraph/
│       ├── models.py        # обновлённый
│       └── tools.py         # обновлённый
└── api/
    └── routers/
        └── image_api.py     # новый (опционально)
```

## Redis Message Format

```json
{
    "text": "Что на этих изображениях?",
    "image_urls": [
        "https://minio.example.com/user-files/agent_123/user_456/image1_uuid.jpg?presigned_params",
        "https://minio.example.com/user-files/agent_123/user_456/image2_uuid.jpg?presigned_params"
    ],
    "user_info": {
        "user_id": "456",
        "thread_id": "789",
        "platform": "telegram"
    },
    "timestamp": "2024-01-15T10:30:00Z"
}
```

## Приоритеты реализации

1. **Высокий приоритет**: ImageOrchestrator + OpenAI Vision
2. **Средний приоритет**: Telegram/WhatsApp integration
3. **Низкий приоритет**: Google/Claude fallback providers

## Ожидаемые результаты

После реализации пользователи смогут:
1. Отправлять изображения в Telegram/WhatsApp ботов
2. Получать анализ изображений от LangGraph агентов
3. Использовать изображения в контексте диалога
4. Обрабатывать множественные изображения за раз

## Технические ограничения

- Максимальный размер файла: 10MB (configurable)
- Максимальное количество изображений: 5 за сообщение (configurable)
- Поддерживаемые форматы: JPG, PNG, WEBP, GIF
- TTL presigned URLs: 1 час для безопасности
