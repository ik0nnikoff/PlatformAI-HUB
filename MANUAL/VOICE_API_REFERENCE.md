# 🔌 Voice Services API Reference

## 📋 API Endpoints для тестирования и диагностики голосовых сервисов

### Base URL: `/api/v1/voice`

---

## 🔍 Диагностические endpoints

### 1. Health Check голосовых сервисов
```http
GET /api/v1/voice/health
```

**Response:**
```json
{
  "status": "healthy",
  "orchestrator_initialized": true,
  "minio_health": {
    "status": "healthy",
    "bucket_exists": true,
    "connection": "ok"
  },
  "stt_services": {
    "openai": "healthy",
    "google": "healthy", 
    "yandex": "healthy"
  },
  "tts_services": {
    "openai": "healthy",
    "google": "healthy",
    "yandex": "healthy"
  },
  "total_rate_limiters": 3
}
```

### 2. Проверка конфигурации агента
```http
POST /api/v1/voice/validate-config
Content-Type: application/json

{
  "agent_id": "agent_123",
  "voice_settings": {
    "enabled": true,
    "providers": [...]
  }
}
```

**Response:**
```json
{
  "valid": true,
  "errors": [],
  "warnings": ["При режиме keywords рекомендуется указать ключевые слова"],
  "supported_providers": ["openai", "yandex"],
  "missing_credentials": ["google"]
}
```

---

## 🎤 STT (Speech-to-Text) endpoints

### 1. Прямая STT обработка
```http
POST /api/v1/voice/stt/process
Content-Type: multipart/form-data

{
  "audio_file": <binary_audio_data>,
  "agent_id": "agent_123",
  "provider": "openai|google|yandex", // optional
  "language": "ru-RU" // optional
}
```

**Response:**
```json
{
  "success": true,
  "text": "Распознанный текст из аудио",
  "confidence": 0.95,
  "provider_used": "yandex",
  "processing_time": 1.23,
  "file_info": {
    "file_id": "uuid",
    "duration": 5.2,
    "format": "mp3",
    "size_bytes": 51200
  },
  "cached": false
}
```

### 2. Тестирование STT провайдера
```http
POST /api/v1/voice/stt/test/{provider}
Content-Type: multipart/form-data

{
  "audio_file": <binary_audio_data>,
  "language": "ru-RU"
}
```

---

## 🔊 TTS (Text-to-Speech) endpoints

### 1. Прямой TTS синтез
```http
POST /api/v1/voice/tts/synthesize
Content-Type: application/json

{
  "text": "Текст для синтеза",
  "agent_id": "agent_123",
  "provider": "openai|google|yandex", // optional
  "voice": "nova", // optional
  "language": "ru-RU" // optional
}
```

**Response:**
```json
{
  "success": true,
  "audio_url": "https://minio.domain.com/bucket/audio_file.mp3",
  "provider_used": "openai",
  "processing_time": 2.45,
  "file_info": {
    "file_id": "uuid",
    "duration": 3.1,
    "format": "mp3",
    "size_bytes": 31200
  }
}
```

### 2. Тестирование TTS провайдера
```http
POST /api/v1/voice/tts/test/{provider}
Content-Type: application/json

{
  "text": "Тестовая фраза",
  "voice": "nova",
  "language": "ru-RU"
}
```

---

## 🎯 Intent Detection endpoints

### 1. Проверка намерения озвучивания
```http
POST /api/v1/voice/intent/detect
Content-Type: application/json

{
  "text": "Скажи мне ответ голосом",
  "agent_id": "agent_123"
}
```

**Response:**
```json
{
  "should_voice": true,
  "detected_keywords": ["скажи", "голосом"],
  "confidence": 0.9,
  "mode": "keywords"
}
```

---

## ⚙️ Configuration endpoints

### 1. Получение настроек агента
```http
GET /api/v1/voice/agent/{agent_id}/settings
```

**Response:**
```json
{
  "agent_id": "agent_123",
  "voice_settings": {
    "enabled": true,
    "intent_detection_mode": "keywords",
    "providers": [...],
    "cache_enabled": true
  },
  "initialized_providers": ["openai", "yandex"],
  "active_rate_limiters": 1
}
```

### 2. Инициализация сервисов для агента
```http
POST /api/v1/voice/agent/{agent_id}/initialize
Content-Type: application/json

{
  "voice_settings": {
    "enabled": true,
    "providers": [...]
  }
}
```

---

## 📊 Metrics & Analytics endpoints

### 1. Статистика использования
```http
GET /api/v1/voice/metrics/{agent_id}
```

**Response:**
```json
{
  "agent_id": "agent_123",
  "period": "24h",
  "stt_requests": 145,
  "tts_requests": 89,
  "success_rate": {
    "stt": 0.967,
    "tts": 0.943
  },
  "provider_usage": {
    "openai": {"stt": 50, "tts": 30},
    "yandex": {"stt": 95, "tts": 59}
  },
  "cache_hit_rate": 0.23,
  "avg_processing_time": {
    "stt": 1.2,
    "tts": 2.1
  }
}
```

### 2. Rate limiting статус
```http
GET /api/v1/voice/rate-limit/{agent_id}/{user_id}
```

**Response:**
```json
{
  "agent_id": "agent_123",
  "user_id": "user_456",
  "current_requests": 3,
  "limit": 15,
  "window": "1 minute",
  "remaining": 12,
  "reset_time": "2025-01-15T10:30:00Z"
}
```

---

## 🗂️ File Management endpoints

### 1. Список аудиофайлов агента
```http
GET /api/v1/voice/files/{agent_id}?limit=50&offset=0
```

**Response:**
```json
{
  "files": [
    {
      "file_id": "uuid",
      "original_filename": "voice_123.ogg",
      "type": "voice_input|tts_output",
      "size_bytes": 51200,
      "duration": 5.2,
      "created_at": "2025-01-15T10:30:00Z",
      "download_url": "https://..."
    }
  ],
  "total": 324,
  "has_more": true
}
```

### 2. Информация о файле
```http
GET /api/v1/voice/file/{file_id}
```

### 3. Скачивание файла
```http
GET /api/v1/voice/file/{file_id}/download
```

---

## 🧹 Maintenance endpoints

### 1. Очистка кэша
```http
DELETE /api/v1/voice/cache/{agent_id}
```

### 2. Очистка старых файлов
```http
DELETE /api/v1/voice/files/cleanup
Content-Type: application/json

{
  "older_than_days": 7,
  "agent_ids": ["agent_123"] // optional
}
```

---

## 🚨 Error Handling

### Стандартные коды ошибок:

| Код | Описание |
|-----|----------|
| 400 | Некорректные параметры запроса |
| 401 | Не авторизован |
| 403 | Превышен rate limit |
| 404 | Агент/файл не найден |
| 413 | Файл слишком большой |
| 415 | Неподдерживаемый формат аудио |
| 422 | Ошибка валидации конфигурации |
| 500 | Внутренняя ошибка сервера |
| 502 | Ошибка внешнего провайдера |
| 503 | Сервис временно недоступен |

### Пример ошибки:
```json
{
  "error": "VOICE_CONFIG_INVALID",
  "message": "Конфигурация голосовых настроек содержит ошибки",
  "details": {
    "errors": [
      "Приоритеты провайдеров должны быть уникальными",
      "Размер файла должен быть от 1 до 100 МБ"
    ]
  },
  "timestamp": "2025-01-15T10:30:00Z",
  "request_id": "req_123456"
}
```

---

## 🧪 Testing utilities

### 1. Тестовый аудиофайл
```http
GET /api/v1/voice/test/audio
```
Возвращает короткий тестовый аудиофайл для проверки STT

### 2. Эхо тест (STT + TTS)
```http
POST /api/v1/voice/test/echo
Content-Type: multipart/form-data

{
  "audio_file": <binary_audio_data>,
  "agent_id": "agent_123"
}
```

**Response:**
```json
{
  "original_audio": {
    "recognized_text": "Привет, как дела?",
    "stt_provider": "yandex",
    "stt_time": 1.2
  },
  "synthesized_audio": {
    "audio_url": "https://...",
    "tts_provider": "yandex", 
    "tts_time": 2.1
  },
  "total_time": 3.3
}
```

---

## 📚 Использование с cURL

### Примеры команд:

```bash
# Health check
curl -X GET http://localhost:8000/api/v1/voice/health

# STT обработка
curl -X POST http://localhost:8000/api/v1/voice/stt/process \
  -H "Content-Type: multipart/form-data" \
  -F "audio_file=@voice.ogg" \
  -F "agent_id=agent_123"

# TTS синтез
curl -X POST http://localhost:8000/api/v1/voice/tts/synthesize \
  -H "Content-Type: application/json" \
  -d '{"text": "Привет!", "agent_id": "agent_123"}'

# Проверка намерения
curl -X POST http://localhost:8000/api/v1/voice/intent/detect \
  -H "Content-Type: application/json" \
  -d '{"text": "Скажи мне ответ", "agent_id": "agent_123"}'
```

---

## 🔐 Авторизация

Все endpoints требуют авторизации через заголовок:
```http
Authorization: Bearer <your_token>
```

Или через query parameter:
```http
?api_key=<your_api_key>
```

---

**Версия API**: v1  
**Дата**: 15 января 2025  
**Документация**: Voice Services API
