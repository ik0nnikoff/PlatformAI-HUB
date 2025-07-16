# Система переключения режимов обработки изображений

## Обзор

PlatformAI-HUB поддерживает два режима передачи изображений в Vision API:

- **URL режим** (`url`) - для production окружений с публично доступным MinIO
- **Binary режим** (`binary`) - для development окружений с localhost MinIO

## Настройка

Добавьте в `.env` файл:

```bash
# Режим обработки изображений: "url" или "binary"
IMAGE_VISION_MODE=binary  # для development (localhost MinIO)
# IMAGE_VISION_MODE=url   # для production (публично доступный MinIO)
```

## Режимы работы

### Binary режим (по умолчанию)
- **Когда использовать**: Development, localhost MinIO
- **Как работает**: 
  - Загружает изображения по URL в память
  - Кодирует в base64
  - Передает binary данные в Vision API
- **Преимущества**: Работает с недоступными извне URL (127.0.0.1:9000)
- **Недостатки**: Больше потребление памяти и трафика

### URL режим
- **Когда использовать**: Production, публично доступный MinIO
- **Как работает**:
  - Передает URL напрямую в Vision API
  - API самостоятельно загружает изображения
- **Преимущества**: Меньше потребление ресурсов сервера
- **Недостатки**: Требует публично доступный MinIO

## Архитектура

### AgentRunner
В `app/agent_runner/agent_runner.py`:
- Проверяет `settings.IMAGE_VISION_MODE`
- **URL режим**: Создает multimodal сообщения с image_url для прямой передачи в LLM
- **Binary режим**: Создает текстовые инструкции для использования Vision tools

### Vision Tools
В `app/agent_runner/langgraph/tools.py`:
- `analyze_images` - основной tool для анализа изображений
- `_analyze_images_url_mode()` - передает URL напрямую в Vision providers
- `_analyze_images_binary_mode()` - загружает и кодирует изображения в base64

### Vision Providers
В `app/services/media/providers/`:
- Поддерживают оба режима передачи данных
- Автоматический fallback между провайдерами при ошибках
- OpenAI, Claude, Google Vision API

## Тестирование

### Проверка текущего режима
```python
from app.core.config import settings
print(f"Current mode: {settings.IMAGE_VISION_MODE}")
```

### Тест binary режима
```bash
cd /path/to/project
export IMAGE_VISION_MODE=binary
python -c "from app.agent_runner.langgraph.tools import analyze_images; print('Binary mode ready')"
```

### Тест URL режима
```bash
cd /path/to/project
export IMAGE_VISION_MODE=url
python -c "from app.agent_runner.langgraph.tools import analyze_images; print('URL mode ready')"
```

## Deployment

### Development
```bash
# .env.development
IMAGE_VISION_MODE=binary
MINIO_ENDPOINT=127.0.0.1:9000
```

### Production
```bash
# .env.production
IMAGE_VISION_MODE=url
MINIO_ENDPOINT=your-public-minio.domain.com:9000
```

## Troubleshooting

### Ошибки в URL режиме
- **"Error while downloading"** - MinIO недоступен для внешних API
- **Решение**: Переключитесь на binary режим или настройте публичный доступ к MinIO

### Ошибки в Binary режиме
- **"Failed to download image"** - Неверный URL или недоступное изображение
- **Решение**: Проверьте доступность MinIO сервера локально

### Проверка подключения к MinIO
```bash
curl http://127.0.0.1:9000/minio/health/live
```

## Мониторинг

Логи содержат информацию о текущем режиме:
```
INFO: Image processing mode: binary. Message type: text
INFO: Using binary mode - analyzing images via tools
```

или

```
INFO: Image processing mode: url. Message type: multimodal  
INFO: Using URL mode - creating multimodal message with 2 images
```
