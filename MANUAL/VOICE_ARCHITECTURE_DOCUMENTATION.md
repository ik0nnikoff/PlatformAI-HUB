# 🎤 Архитектурная документация Voice Services - PlatformAI-HUB

## 📋 Оглавление
1. [Архитектура системы](#архитектура-системы)
2. [Компоненты системы](#компоненты-системы)
3. [Потоки данных](#потоки-данных)
4. [Конфигурация агента](#конфигурация-агента)
5. [Провайдеры голосовых сервисов](#провайдеры-голосовых-сервисов)
6. [Настройка намерений](#настройка-намерений)
7. [Fallback система](#fallback-система)
8. [Кэширование и производительность](#кэширование-и-производительность)
9. [Интеграция с фронтендом](#интеграция-с-фронтендом)
10. [Примеры конфигураций](#примеры-конфигураций)
11. [Troubleshooting](#troubleshooting)

---

## 🏗️ Архитектура системы

### Общая схема
```
┌─────────────────┐    ┌─────────────────┐    ┌─────────────────┐
│   Telegram Bot  │    │   Voice         │    │   Agent         │
│   Integration   │◄──►│   Orchestrator  │◄──►│   Runner        │
└─────────────────┘    └─────────────────┘    └─────────────────┘
         │                       │                       │
         │                       │                       │
         ▼                       ▼                       ▼
┌─────────────────┐    ┌─────────────────┐    ┌─────────────────┐
│   Audio Input   │    │   STT/TTS       │    │   Text          │
│   (Voice Msgs)  │    │   Services      │    │   Processing    │
└─────────────────┘    └─────────────────┘    └─────────────────┘
         │                       │                       │
         │                       │                       │
         ▼                       ▼                       ▼
┌─────────────────┐    ┌─────────────────┐    ┌─────────────────┐
│   MinIO         │    │   Redis Cache   │    │   Voice         │
│   File Storage  │    │   & Rate Limit  │    │   Response      │
└─────────────────┘    └─────────────────┘    └─────────────────┘
```

### Ключевые принципы
- **Многопровайдерность**: Поддержка OpenAI, Google, Yandex
- **Fallback логика**: Автоматическое переключение между провайдерами
- **Кэширование**: Redis для STT результатов и rate limiting
- **Намерения**: Детекция голосовых намерений в тексте пользователя
- **Файловое хранение**: MinIO для аудиофайлов
- **Асинхронность**: Полностью асинхронная обработка

---

## 🔧 Компоненты системы

### 1. VoiceServiceOrchestrator
**Путь**: `app/services/voice/voice_orchestrator.py`

**Основные функции**:
- Координация всех голосовых сервисов
- Инициализация провайдеров STT/TTS
- Управление fallback логикой
- Кэширование результатов
- Rate limiting

**Ключевые методы**:
```python
async def process_voice_message()      # STT обработка
async def synthesize_response()        # TTS синтез
async def initialize_voice_services_for_agent()  # Инициализация для агента
```

### 2. STT (Speech-to-Text) Сервисы
**Пути**: 
- `app/services/voice/stt/openai_stt.py` - OpenAI Whisper
- `app/services/voice/stt/google_stt.py` - Google Cloud Speech
- `app/services/voice/stt/yandex_stt.py` - Yandex SpeechKit

**Функции**: Преобразование аудио в текст

### 3. TTS (Text-to-Speech) Сервисы
**Пути**:
- `app/services/voice/tts/openai_tts.py` - OpenAI TTS
- `app/services/voice/tts/google_tts.py` - Google Cloud TTS
- `app/services/voice/tts/yandex_tts.py` - Yandex SpeechKit

**Функции**: Синтез речи из текста

### 4. MinioFileManager
**Путь**: `app/services/voice/minio_manager.py`

**Функции**: 
- Хранение аудиофайлов
- Генерация presigned URLs
- Управление lifecycle файлов

### 5. Telegram Integration
**Путь**: `app/integrations/telegram/telegram_bot.py`

**Функции**:
- Обработка голосовых сообщений от пользователей
- Отправка голосовых ответов
- Fallback на текст при VOICE_MESSAGES_FORBIDDEN

---

## 📊 Потоки данных

### STT Поток (Голосовое сообщение → Текст)
```
Голосовое сообщение пользователя
        ↓
Telegram Bot получает аудио
        ↓
Загрузка в MinIO
        ↓
Проверка кэша Redis (по хэшу файла)
        ↓
STT обработка (с fallback по провайдерам)
        ↓
Кэширование результата
        ↓
Отправка текста агенту
        ↓
Обработка агентом
        ↓
Текстовый ответ агента
```

### TTS Поток (Текст → Голосовое сообщение)
```
Текстовый ответ от агента
        ↓
Детекция намерения озвучивания (keywords)
        ↓
TTS синтез (с fallback по провайдерам)
        ↓
Загрузка аудио в MinIO
        ↓
Получение presigned URL
        ↓
Отправка голосового сообщения пользователю
        ↓
Fallback на текст при ошибке отправки
```

---

## ⚙️ Конфигурация агента

### Структура JSON конфигурации агента

Голосовые настройки должны находиться по пути: **`config.simple.settings.voice_settings`**

```json
{
  "config": {
    "simple": {
      "settings": {
        "voice_settings": {
          // ОСНОВНЫЕ НАСТРОЙКИ
          "enabled": true,                    // Включить голосовые функции
          "intent_detection_mode": "keywords", // "keywords", "always", "disabled"
          "auto_stt": true,                   // Автоматическая STT обработка
          "auto_tts_on_keywords": true,       // Автоматическая TTS при намерениях
          
          // ОГРАНИЧЕНИЯ
          "max_file_size_mb": 25,             // Макс размер аудиофайла
          "rate_limit_per_minute": 15,        // Лимит запросов в минуту
          
          // КЭШИРОВАНИЕ
          "cache_enabled": true,              // Включить кэш STT результатов
          "cache_ttl_hours": 24,              // TTL кэша в часах
          
          // КЛЮЧЕВЫЕ СЛОВА ДЛЯ НАМЕРЕНИЙ
          "intent_keywords": [
            "голос",
            "скажи", 
            "произнеси",
            "озвучь",
            "расскажи голосом",
            "ответь голосом",
            "прочитай вслух"
          ],
          
          // ПРОВАЙДЕРЫ (по приоритету)
          "providers": [
            // Конфигурации провайдеров (см. ниже)
          ]
        }
      }
    }
  }
}
```

### Поля конфигурации

| Поле | Тип | Обязательно | Описание |
|------|-----|-------------|----------|
| `enabled` | boolean | Да | Включает/выключает голосовые функции |
| `intent_detection_mode` | string | Да | Режим детекции намерений: `"keywords"`, `"always"`, `"disabled"` |
| `intent_keywords` | string[] | Нет | Ключевые слова для детекции намерений (при mode="keywords") |
| `auto_stt` | boolean | Нет | Автоматически обрабатывать голосовые сообщения (по умолчанию: true) |
| `auto_tts_on_keywords` | boolean | Нет | Автоматически озвучивать при обнаружении намерений (по умолчанию: true) |
| `max_file_size_mb` | number | Нет | Максимальный размер аудиофайла в МБ (1-100, по умолчанию: 25) |
| `cache_enabled` | boolean | Нет | Включить кэширование STT результатов (по умолчанию: true) |
| `cache_ttl_hours` | number | Нет | TTL кэша в часах (1-168, по умолчанию: 24) |
| `rate_limit_per_minute` | number | Нет | Лимит запросов в минуту (1-100, по умолчанию: 10) |
| `providers` | object[] | Да | Список провайдеров голосовых сервисов |

---

## 🌐 Провайдеры голосовых сервисов

### Структура провайдера
```json
{
  "provider": "openai|google|yandex",
  "priority": 1,                      // Чем меньше, тем выше приоритет
  "fallback_enabled": true,           // Использовать как fallback
  "stt_config": { /* STT настройки */ },
  "tts_config": { /* TTS настройки */ },
  "custom_settings": { /* Дополнительные настройки */ }
}
```

### 1. OpenAI Провайдер

#### STT Конфигурация (Whisper)
```json
{
  "stt_config": {
    "enabled": true,
    "model": "whisper-1",               // Модель Whisper
    "language": "ru",                   // Язык (ISO 639-1)
    "max_duration": 120,                // Макс длительность в секундах
    "enable_automatic_punctuation": true,
    "custom_params": {
      "model": "whisper-1",
      "language": "ru"
    }
  }
}
```

#### TTS Конфигурация
```json
{
  "tts_config": {
    "enabled": true,
    "model": "tts-1",                   // tts-1 или tts-1-hd
    "voice": "nova",                    // alloy, echo, fable, onyx, nova, shimmer
    "language": "ru",
    "speed": 1.0,                       // 0.25-4.0
    "audio_format": "mp3",              // mp3, opus, aac, flac
    "sample_rate": 22050,
    "custom_params": {
      "model": "tts-1",
      "voice": "nova",
      "speed": 1.0
    }
  }
}
```

### 2. Google Cloud Провайдер

#### STT Конфигурация (Cloud Speech)
```json
{
  "stt_config": {
    "enabled": true,
    "model": "latest_short",            // latest_short, latest_long, command_and_search
    "language": "ru-RU",                // BCP-47 language tag
    "max_duration": 60,
    "sample_rate_hertz": 16000,         // 8000, 16000, 22050, 44100, 48000
    "enable_automatic_punctuation": true,
    "custom_params": {
      "model": "latest_short",
      "languageCode": "ru-RU"
    }
  }
}
```

#### TTS Конфигурация
```json
{
  "tts_config": {
    "enabled": true,
    "model": "wavenet",                 // standard, wavenet, neural2
    "voice": "ru-RU-Wavenet-A",         // Голос для языка
    "language": "ru-RU",
    "speed": 1.0,
    "audio_format": "mp3",
    "sample_rate": 22050,
    "custom_params": {
      "voiceName": "ru-RU-Wavenet-A",
      "audioEncoding": "MP3"
    }
  }
}
```

### 3. Yandex SpeechKit Провайдер

#### STT Конфигурация
```json
{
  "stt_config": {
    "enabled": true,
    "model": "general",                 // general, general:rc, general:deprecated
    "language": "ru-RU",
    "max_duration": 60,
    "sample_rate_hertz": 16000,
    "enable_automatic_punctuation": true,
    "enable_profanity_filter": false,
    "custom_params": {
      "sampleRateHertz": 16000,
      "languageCode": "ru-RU",
      "model": "general"
    }
  }
}
```

#### TTS Конфигурация
```json
{
  "tts_config": {
    "enabled": true,
    "model": "jane",                    // jane, oksana, alyss, omazh
    "voice": "jane",
    "language": "ru-RU",
    "speed": 1.0,
    "pitch": 0.0,                       // -20.0 до 20.0
    "volume_gain_db": 0.0,
    "audio_format": "mp3",
    "sample_rate": 22050,
    "custom_params": {
      "speed": 1.0
    }
  }
}
```

---

## 🎯 Настройка намерений

### Режимы детекции намерений

| Режим | Описание | Использование |
|-------|----------|---------------|
| `"keywords"` | По ключевым словам в тексте пользователя | Рекомендуется для большинства случаев |
| `"always"` | Всегда озвучивать ответы | Для полностью голосовых ботов |
| `"disabled"` | Никогда не озвучивать | Только STT, без TTS |

### Ключевые слова по умолчанию
```json
[
  "голос",
  "скажи", 
  "произнеси",
  "озвучь",
  "расскажи голосом",
  "ответь голосом", 
  "прочитай вслух"
]
```

### Кастомные ключевые слова
Можно добавить свои ключевые слова для специфических доменов:
```json
{
  "intent_keywords": [
    // Стандартные
    "голос", "скажи", "произнеси", "озвучь",
    
    // Для медицинского бота
    "диагноз голосом", "результаты вслух",
    
    // Для образовательного бота
    "объясни голосом", "расскажи урок",
    
    // Для развлекательного бота
    "спой", "расскажи анекдот", "озвучь историю"
  ]
}
```

---

## 🔄 Fallback система

### Приоритеты провайдеров
Система автоматически переключается между провайдерами по приоритету:

1. **Priority 1** (самый высокий) - основной провайдер
2. **Priority 2** - первый fallback
3. **Priority 3** - второй fallback
4. И так далее...

### Рекомендуемая настройка приоритетов

```json
{
  "providers": [
    {
      "provider": "yandex",      // Лучше для русского языка
      "priority": 1,
      "fallback_enabled": true
    },
    {
      "provider": "openai",      // Универсальный fallback
      "priority": 2, 
      "fallback_enabled": true
    },
    {
      "provider": "google",      // Дополнительный fallback
      "priority": 3,
      "fallback_enabled": true
    }
  ]
}
```

### Логика fallback
1. Попытка с **Priority 1** провайдером
2. При ошибке - переключение на **Priority 2**
3. При ошибке - переключение на **Priority 3**
4. Если все провайдеры неудачны - возврат ошибки

---

## ⚡ Кэширование и производительность

### STT Кэширование
- **Ключ кэша**: MD5 хэш от (размер_файла + mime_type + провайдер)
- **TTL**: Настраивается через `cache_ttl_hours` (по умолчанию 24 часа)
- **Хранилище**: Redis

### Rate Limiting
- **Лимит**: Настраивается через `rate_limit_per_minute`
- **Период**: 60 секунд
- **Применение**: На пользователя + агента
- **Хранилище**: Redis

### Оптимизация производительности
- **Параллельная обработка**: Инициализация провайдеров
- **Lazy loading**: Провайдеры инициализируются по требованию
- **Credential проверка**: Пропуск провайдеров без credentials
- **Асинхронные операции**: Все I/O операции неблокирующие

---

## 💻 Интеграция с фронтендом

### Для фронтенд разработчика

#### 1. Структура данных для UI формы
```typescript
interface VoiceSettings {
  enabled: boolean;
  intent_detection_mode: 'keywords' | 'always' | 'disabled';
  intent_keywords: string[];
  auto_stt: boolean;
  auto_tts_on_keywords: boolean;
  max_file_size_mb: number;         // 1-100
  cache_enabled: boolean;
  cache_ttl_hours: number;          // 1-168
  rate_limit_per_minute: number;    // 1-100
  providers: VoiceProvider[];
}

interface VoiceProvider {
  provider: 'openai' | 'google' | 'yandex';
  priority: number;                 // 1-10
  fallback_enabled: boolean;
  stt_config?: STTConfig;
  tts_config?: TTSConfig;
  custom_settings?: Record<string, any>;
}
```

#### 2. Валидация на фронтенде
```typescript
function validateVoiceSettings(settings: VoiceSettings): string[] {
  const errors: string[] = [];
  
  // Основные проверки
  if (settings.max_file_size_mb < 1 || settings.max_file_size_mb > 100) {
    errors.push('Размер файла должен быть от 1 до 100 МБ');
  }
  
  if (settings.cache_ttl_hours < 1 || settings.cache_ttl_hours > 168) {
    errors.push('TTL кэша должен быть от 1 до 168 часов');
  }
  
  if (settings.rate_limit_per_minute < 1 || settings.rate_limit_per_minute > 100) {
    errors.push('Rate limit должен быть от 1 до 100 запросов в минуту');
  }
  
  // Проверка провайдеров
  if (!settings.providers || settings.providers.length === 0) {
    errors.push('Должен быть настроен хотя бы один провайдер');
  }
  
  // Проверка уникальности приоритетов
  const priorities = settings.providers.map(p => p.priority);
  if (new Set(priorities).size !== priorities.length) {
    errors.push('Приоритеты провайдеров должны быть уникальными');
  }
  
  return errors;
}
```

#### 3. Компонент React для настройки провайдера
```tsx
interface ProviderConfigProps {
  provider: VoiceProvider;
  onChange: (provider: VoiceProvider) => void;
}

function ProviderConfig({ provider, onChange }: ProviderConfigProps) {
  const updateSTTConfig = (config: Partial<STTConfig>) => {
    onChange({
      ...provider,
      stt_config: { ...provider.stt_config, ...config }
    });
  };
  
  return (
    <div className="provider-config">
      <h3>{provider.provider.toUpperCase()} Провайдер</h3>
      
      <label>
        Приоритет:
        <input 
          type="number" 
          min="1" 
          max="10"
          value={provider.priority}
          onChange={e => onChange({
            ...provider, 
            priority: parseInt(e.target.value)
          })}
        />
      </label>
      
      {/* STT настройки */}
      {provider.stt_config && (
        <div className="stt-config">
          <h4>STT Настройки</h4>
          
          <label>
            Модель:
            <select 
              value={provider.stt_config.model}
              onChange={e => updateSTTConfig({ model: e.target.value })}
            >
              {getSTTModelsForProvider(provider.provider).map(model => (
                <option key={model} value={model}>{model}</option>
              ))}
            </select>
          </label>
          
          <label>
            Язык:
            <input 
              value={provider.stt_config.language}
              onChange={e => updateSTTConfig({ language: e.target.value })}
            />
          </label>
        </div>
      )}
    </div>
  );
}
```

---

## 📝 Примеры конфигураций

### 1. Минимальная конфигурация (только OpenAI)
```json
{
  "config": {
    "simple": {
      "settings": {
        "voice_settings": {
          "enabled": true,
          "intent_detection_mode": "keywords",
          "intent_keywords": ["голос", "скажи", "произнеси"],
          "providers": [
            {
              "provider": "openai",
              "priority": 1,
              "fallback_enabled": false,
              "stt_config": {
                "enabled": true,
                "model": "whisper-1",
                "language": "ru"
              },
              "tts_config": {
                "enabled": true,
                "model": "tts-1",
                "voice": "nova"
              }
            }
          ]
        }
      }
    }
  }
}
```

### 2. Полная конфигурация с тремя провайдерами
```json
{
  "config": {
    "simple": {
      "settings": {
        "voice_settings": {
          "enabled": true,
          "intent_detection_mode": "keywords",
          "intent_keywords": [
            "голос", "скажи", "произнеси", "озвучь",
            "расскажи голосом", "ответь голосом", "прочитай вслух"
          ],
          "auto_stt": true,
          "auto_tts_on_keywords": true,
          "max_file_size_mb": 25,
          "cache_enabled": true,
          "cache_ttl_hours": 24,
          "rate_limit_per_minute": 15,
          
          "providers": [
            {
              "provider": "yandex",
              "priority": 1,
              "fallback_enabled": true,
              "stt_config": {
                "enabled": true,
                "model": "general",
                "language": "ru-RU",
                "max_duration": 60,
                "enable_automatic_punctuation": true
              },
              "tts_config": {
                "enabled": true,
                "model": "jane",
                "voice": "jane",
                "language": "ru-RU",
                "speed": 1.0,
                "audio_format": "mp3"
              },
              "custom_settings": {
                "description": "Основной провайдер для русского языка",
                "priority_reason": "Лучшее качество для русской речи"
              }
            },
            {
              "provider": "openai",
              "priority": 2,
              "fallback_enabled": true,
              "stt_config": {
                "enabled": true,
                "model": "whisper-1",
                "language": "ru",
                "max_duration": 120
              },
              "tts_config": {
                "enabled": true,
                "model": "tts-1",
                "voice": "nova",
                "speed": 1.0,
                "audio_format": "mp3"
              },
              "custom_settings": {
                "description": "Универсальный fallback провайдер",
                "priority_reason": "Надежный резервный вариант"
              }
            },
            {
              "provider": "google",
              "priority": 3,
              "fallback_enabled": true,
              "stt_config": {
                "enabled": true,
                "model": "latest_short",
                "language": "ru-RU",
                "max_duration": 60,
                "sample_rate_hertz": 16000
              },
              "tts_config": {
                "enabled": true,
                "model": "wavenet",
                "voice": "ru-RU-Wavenet-A",
                "language": "ru-RU",
                "audio_format": "mp3"
              },
              "custom_settings": {
                "description": "Дополнительный fallback",
                "priority_reason": "Резервный провайдер для особых случаев"
              }
            }
          ]
        }
      }
    }
  }
}
```

### 3. Конфигурация только для STT (без TTS)
```json
{
  "config": {
    "simple": {
      "settings": {
        "voice_settings": {
          "enabled": true,
          "intent_detection_mode": "disabled",
          "auto_stt": true,
          "auto_tts_on_keywords": false,
          
          "providers": [
            {
              "provider": "openai",
              "priority": 1,
              "stt_config": {
                "enabled": true,
                "model": "whisper-1",
                "language": "ru"
              }
              // Без tts_config - только STT
            }
          ]
        }
      }
    }
  }
}
```

### 4. Конфигурация для многоязычного бота
```json
{
  "config": {
    "simple": {
      "settings": {
        "voice_settings": {
          "enabled": true,
          "intent_detection_mode": "keywords",
          "intent_keywords": [
            // Русские
            "голос", "скажи", "произнеси",
            // Английские  
            "voice", "say", "speak",
            // Другие языки...
          ],
          
          "providers": [
            {
              "provider": "openai",
              "priority": 1,
              "stt_config": {
                "enabled": true,
                "model": "whisper-1",
                "language": "auto"         // Автоопределение языка
              },
              "tts_config": {
                "enabled": true,
                "model": "tts-1",
                "voice": "nova"
              }
            }
          ]
        }
      }
    }
  }
}
```

---

## 🔧 Troubleshooting

### Частые проблемы и решения

#### 1. "Voice services not initialized"
**Причина**: Отсутствуют credentials для всех провайдеров
**Решение**: 
- Проверить переменные окружения: `OPENAI_API_KEY`, `GOOGLE_APPLICATION_CREDENTIALS`, `YANDEX_API_KEY`
- Убедиться что хотя бы один провайдер имеет валидные credentials

#### 2. "STT service не инициализирован"
**Причина**: Ошибка инициализации конкретного провайдера
**Решение**:
- Проверить логи для деталей ошибки
- Проверить credentials для провайдера
- Попробовать другой провайдер

#### 3. "Voice settings not found"
**Причина**: Неправильный путь в конфигурации агента
**Решение**: 
- Убедиться что настройки находятся по пути `config.simple.settings.voice_settings`
- Проверить валидность JSON структуры

#### 4. "Превышен лимит запросов"
**Причина**: Rate limiting сработал
**Решение**:
- Увеличить `rate_limit_per_minute`
- Проверить логику кэширования

#### 5. "VOICE_MESSAGES_FORBIDDEN"
**Причина**: Пользователь отключил голосовые сообщения в Telegram
**Решение**: 
- Система автоматически fallback на текст
- Информировать пользователя о необходимости включить голосовые сообщения

### Логирование и мониторинг

#### Ключевые логи для отладки:
```
INFO - Voice service orchestrator initialized
INFO - Successfully initialized {provider} STT service  
INFO - Voice services initialized for agent {agent_id}
INFO - STT successful with provider {provider}
INFO - TTS successful with provider {provider}
WARNING - Voice processing failed: {error}
ERROR - Failed to initialize voice services: {error}
```

#### Метрики для мониторинга:
- Время обработки STT/TTS
- Процент успешных запросов по провайдерам
- Использование кэша (hit rate)
- Rate limiting срабатывания

---

## 📚 Дополнительные ресурсы

### Переменные окружения
```bash
# OpenAI
OPENAI_API_KEY=sk-...

# Google Cloud  
GOOGLE_APPLICATION_CREDENTIALS=/path/to/credentials.json
GOOGLE_CLOUD_PROJECT_ID=your-project-id

# Yandex Cloud
YANDEX_API_KEY=your-api-key
YANDEX_IAM_TOKEN=your-iam-token  
YANDEX_FOLDER_ID=your-folder-id

# MinIO для файлов
MINIO_ENDPOINT=localhost:9000
MINIO_ACCESS_KEY=minioadmin
MINIO_SECRET_KEY=minioadmin
MINIO_VOICE_BUCKET_NAME=voice-files
```

### Связанные файлы
- `app/api/schemas/voice_schemas.py` - Pydantic схемы
- `app/core/config.py` - Конфигурация приложения
- `app/services/voice/base.py` - Базовые классы
- `app/services/voice/intent_utils.py` - Утилиты намерений

---

**Версия документации**: 1.0  
**Дата**: 15 января 2025  
**Автор**: Voice Services Team
