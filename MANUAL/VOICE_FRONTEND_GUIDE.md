# 🎛️ Voice Settings Configuration Guide для Frontend разработчика

## 📖 Краткое руководство по настройке голосовых функций агента

### 🎯 Основная задача
Создать UI форму для настройки голосовых функций агента, которая генерирует правильную JSON конфигурацию по пути `config.simple.settings.voice_settings`.

---

## 🔧 Структура данных

### TypeScript интерфейсы
```typescript
// Основная структура настроек
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

// Провайдер голосовых сервисов
interface VoiceProvider {
  provider: 'openai' | 'google' | 'yandex';
  priority: number;                 // 1-10 (чем меньше, тем выше приоритет)
  fallback_enabled: boolean;
  stt_config?: STTConfig;
  tts_config?: TTSConfig;
  custom_settings?: Record<string, any>;
}

// STT конфигурация
interface STTConfig {
  enabled: boolean;
  model: string;                    // Зависит от провайдера
  language: string;                 // Код языка
  max_duration: number;             // 1-600 секунд
  enable_automatic_punctuation?: boolean;
  enable_profanity_filter?: boolean;
  sample_rate_hertz?: number;       // 8000, 16000, 22050, 44100, 48000
  custom_params?: Record<string, any>;
}

// TTS конфигурация
interface TTSConfig {
  enabled: boolean;
  model: string;                    // Зависит от провайдера
  voice: string;                    // Имя голоса
  language: string;                 // Код языка
  speed: number;                    // 0.25-4.0
  pitch?: number;                   // -20.0 до 20.0 (только Yandex)
  volume_gain_db?: number;          // Усиление громкости
  audio_format: string;             // mp3, opus, aac, flac
  sample_rate: number;              // Частота дискретизации
  custom_params?: Record<string, any>;
}
```

---

## ✅ Валидация данных

### Основная валидация
```typescript
function validateVoiceSettings(settings: VoiceSettings): ValidationResult {
  const errors: string[] = [];
  const warnings: string[] = [];
  
  // Проверка основных полей
  if (settings.max_file_size_mb < 1 || settings.max_file_size_mb > 100) {
    errors.push('Размер файла должен быть от 1 до 100 МБ');
  }
  
  if (settings.cache_ttl_hours < 1 || settings.cache_ttl_hours > 168) {
    errors.push('TTL кэша должен быть от 1 до 168 часов (неделя)');
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
  
  // Проверка ключевых слов при keywords режиме
  if (settings.intent_detection_mode === 'keywords' && 
      settings.intent_keywords.length === 0) {
    warnings.push('При режиме "keywords" рекомендуется указать ключевые слова');
  }
  
  // Проверка STT/TTS включенности
  const hasSTT = settings.providers.some(p => p.stt_config?.enabled);
  const hasTTS = settings.providers.some(p => p.tts_config?.enabled);
  
  if (!hasSTT && !hasTTS) {
    errors.push('Хотя бы один провайдер должен иметь включенные STT или TTS');
  }
  
  return { valid: errors.length === 0, errors, warnings };
}

interface ValidationResult {
  valid: boolean;
  errors: string[];
  warnings: string[];
}
```

### Валидация провайдера
```typescript
function validateProvider(provider: VoiceProvider): ValidationResult {
  const errors: string[] = [];
  
  // Проверка приоритета
  if (provider.priority < 1 || provider.priority > 10) {
    errors.push('Приоритет должен быть от 1 до 10');
  }
  
  // Проверка STT конфигурации
  if (provider.stt_config?.enabled) {
    const sttErrors = validateSTTConfig(provider.stt_config, provider.provider);
    errors.push(...sttErrors);
  }
  
  // Проверка TTS конфигурации
  if (provider.tts_config?.enabled) {
    const ttsErrors = validateTTSConfig(provider.tts_config, provider.provider);
    errors.push(...ttsErrors);
  }
  
  // Хотя бы одна функция должна быть включена
  if (!provider.stt_config?.enabled && !provider.tts_config?.enabled) {
    errors.push('Хотя бы STT или TTS должны быть включены для провайдера');
  }
  
  return { valid: errors.length === 0, errors, warnings: [] };
}
```

---

## 📋 Справочники для UI

### 1. Модели по провайдерам

#### OpenAI
```typescript
const OPENAI_STT_MODELS = ['whisper-1'];

const OPENAI_TTS_MODELS = ['tts-1', 'tts-1-hd'];

const OPENAI_VOICES = [
  'alloy', 'echo', 'fable', 'onyx', 'nova', 'shimmer'
];

const OPENAI_LANGUAGES = [
  { code: 'ru', name: 'Русский' },
  { code: 'en', name: 'English' },
  { code: 'de', name: 'Deutsch' },
  { code: 'fr', name: 'Français' },
  { code: 'es', name: 'Español' }
];
```

#### Google Cloud
```typescript
const GOOGLE_STT_MODELS = [
  'latest_short',     // Для коротких аудио
  'latest_long',      // Для длинных аудио  
  'command_and_search' // Для команд и поиска
];

const GOOGLE_TTS_MODELS = [
  'standard',    // Стандартный
  'wavenet',     // WaveNet (высокое качество)
  'neural2'      // Neural2 (новейший)
];

const GOOGLE_VOICES_RU = [
  'ru-RU-Standard-A', 'ru-RU-Standard-B', 'ru-RU-Standard-C', 'ru-RU-Standard-D',
  'ru-RU-Wavenet-A', 'ru-RU-Wavenet-B', 'ru-RU-Wavenet-C', 'ru-RU-Wavenet-D'
];

const GOOGLE_LANGUAGES = [
  { code: 'ru-RU', name: 'Русский (Россия)' },
  { code: 'en-US', name: 'English (US)' },
  { code: 'en-GB', name: 'English (UK)' },
  { code: 'de-DE', name: 'Deutsch (Deutschland)' }
];
```

#### Yandex SpeechKit
```typescript
const YANDEX_STT_MODELS = [
  'general',              // Общая модель
  'general:rc',          // Release candidate
  'general:deprecated'    // Устаревшая
];

const YANDEX_TTS_VOICES = [
  'jane',    // Женский голос
  'oksana',  // Женский голос
  'alyss',   // Женский голос  
  'omazh',   // Женский голос
  'zahar',   // Мужской голос
  'ermil'    // Мужской голос
];

const YANDEX_LANGUAGES = [
  { code: 'ru-RU', name: 'Русский' },
  { code: 'en-US', name: 'English' },
  { code: 'tr-TR', name: 'Türkçe' }
];
```

### 2. Настройки по умолчанию
```typescript
const DEFAULT_VOICE_SETTINGS: VoiceSettings = {
  enabled: true,
  intent_detection_mode: 'keywords',
  intent_keywords: [
    'голос', 'скажи', 'произнеси', 'озвучь',
    'расскажи голосом', 'ответь голосом', 'прочитай вслух'
  ],
  auto_stt: true,
  auto_tts_on_keywords: true,
  max_file_size_mb: 25,
  cache_enabled: true,
  cache_ttl_hours: 24,
  rate_limit_per_minute: 15,
  providers: []
};

const DEFAULT_PROVIDER_CONFIGS = {
  openai: {
    provider: 'openai' as const,
    priority: 1,
    fallback_enabled: true,
    stt_config: {
      enabled: true,
      model: 'whisper-1',
      language: 'ru',
      max_duration: 120,
      enable_automatic_punctuation: true
    },
    tts_config: {
      enabled: true,
      model: 'tts-1',
      voice: 'nova',
      language: 'ru',
      speed: 1.0,
      audio_format: 'mp3',
      sample_rate: 22050
    }
  },
  
  yandex: {
    provider: 'yandex' as const,
    priority: 1,
    fallback_enabled: true,
    stt_config: {
      enabled: true,
      model: 'general',
      language: 'ru-RU',
      max_duration: 60,
      enable_automatic_punctuation: true,
      sample_rate_hertz: 16000
    },
    tts_config: {
      enabled: true,
      model: 'jane',
      voice: 'jane',
      language: 'ru-RU',
      speed: 1.0,
      audio_format: 'mp3',
      sample_rate: 22050
    }
  }
};
```

---

## 🎨 React компоненты

### 1. Основной компонент настроек
```tsx
import React, { useState } from 'react';

interface VoiceSettingsFormProps {
  initialSettings?: VoiceSettings;
  onSave: (settings: VoiceSettings) => void;
  onCancel: () => void;
}

export function VoiceSettingsForm({ 
  initialSettings, 
  onSave, 
  onCancel 
}: VoiceSettingsFormProps) {
  const [settings, setSettings] = useState<VoiceSettings>(
    initialSettings || DEFAULT_VOICE_SETTINGS
  );
  const [validation, setValidation] = useState<ValidationResult>({ 
    valid: true, 
    errors: [], 
    warnings: [] 
  });

  const handleSave = () => {
    const result = validateVoiceSettings(settings);
    setValidation(result);
    
    if (result.valid) {
      onSave(settings);
    }
  };

  const updateSettings = (updates: Partial<VoiceSettings>) => {
    setSettings(prev => ({ ...prev, ...updates }));
  };

  return (
    <div className="voice-settings-form">
      <h2>Настройки голосовых функций</h2>
      
      {/* Основные настройки */}
      <div className="section">
        <h3>Основные настройки</h3>
        
        <label className="switch">
          <input
            type="checkbox"
            checked={settings.enabled}
            onChange={e => updateSettings({ enabled: e.target.checked })}
          />
          Включить голосовые функции
        </label>

        <div className="field">
          <label>Режим определения намерений:</label>
          <select
            value={settings.intent_detection_mode}
            onChange={e => updateSettings({ 
              intent_detection_mode: e.target.value as any 
            })}
          >
            <option value="keywords">По ключевым словам</option>
            <option value="always">Всегда озвучивать</option>
            <option value="disabled">Отключено</option>
          </select>
        </div>

        {settings.intent_detection_mode === 'keywords' && (
          <KeywordsEditor
            keywords={settings.intent_keywords}
            onChange={keywords => updateSettings({ intent_keywords: keywords })}
          />
        )}
      </div>

      {/* Ограничения */}
      <div className="section">
        <h3>Ограничения</h3>
        
        <div className="field">
          <label>Максимальный размер файла (МБ):</label>
          <input
            type="number"
            min="1"
            max="100"
            value={settings.max_file_size_mb}
            onChange={e => updateSettings({ 
              max_file_size_mb: parseInt(e.target.value) 
            })}
          />
        </div>

        <div className="field">
          <label>Лимит запросов в минуту:</label>
          <input
            type="number"
            min="1"
            max="100"
            value={settings.rate_limit_per_minute}
            onChange={e => updateSettings({ 
              rate_limit_per_minute: parseInt(e.target.value) 
            })}
          />
        </div>
      </div>

      {/* Провайдеры */}
      <div className="section">
        <h3>Провайдеры</h3>
        <ProvidersManager
          providers={settings.providers}
          onChange={providers => updateSettings({ providers })}
        />
      </div>

      {/* Ошибки валидации */}
      {validation.errors.length > 0 && (
        <div className="validation-errors">
          <h4>Ошибки:</h4>
          <ul>
            {validation.errors.map((error, i) => (
              <li key={i} className="error">{error}</li>
            ))}
          </ul>
        </div>
      )}

      {/* Предупреждения */}
      {validation.warnings.length > 0 && (
        <div className="validation-warnings">
          <h4>Предупреждения:</h4>
          <ul>
            {validation.warnings.map((warning, i) => (
              <li key={i} className="warning">{warning}</li>
            ))}
          </ul>
        </div>
      )}

      {/* Кнопки */}
      <div className="actions">
        <button onClick={handleSave} className="save-btn">
          Сохранить
        </button>
        <button onClick={onCancel} className="cancel-btn">
          Отменить
        </button>
      </div>
    </div>
  );
}
```

### 2. Компонент управления провайдерами
```tsx
interface ProvidersManagerProps {
  providers: VoiceProvider[];
  onChange: (providers: VoiceProvider[]) => void;
}

function ProvidersManager({ providers, onChange }: ProvidersManagerProps) {
  const addProvider = (type: 'openai' | 'google' | 'yandex') => {
    const defaultConfig = DEFAULT_PROVIDER_CONFIGS[type];
    const newPriority = Math.max(...providers.map(p => p.priority), 0) + 1;
    
    onChange([
      ...providers,
      { ...defaultConfig, priority: newPriority }
    ]);
  };

  const updateProvider = (index: number, updates: Partial<VoiceProvider>) => {
    const newProviders = [...providers];
    newProviders[index] = { ...newProviders[index], ...updates };
    onChange(newProviders);
  };

  const removeProvider = (index: number) => {
    onChange(providers.filter((_, i) => i !== index));
  };

  return (
    <div className="providers-manager">
      {/* Список провайдеров */}
      {providers.map((provider, index) => (
        <ProviderConfig
          key={index}
          provider={provider}
          onChange={updates => updateProvider(index, updates)}
          onRemove={() => removeProvider(index)}
        />
      ))}

      {/* Кнопки добавления */}
      <div className="add-provider-buttons">
        <button onClick={() => addProvider('openai')}>
          + OpenAI
        </button>
        <button onClick={() => addProvider('google')}>
          + Google Cloud
        </button>
        <button onClick={() => addProvider('yandex')}>
          + Yandex SpeechKit
        </button>
      </div>
    </div>
  );
}
```

### 3. Компонент конфигурации провайдера
```tsx
interface ProviderConfigProps {
  provider: VoiceProvider;
  onChange: (updates: Partial<VoiceProvider>) => void;
  onRemove: () => void;
}

function ProviderConfig({ provider, onChange, onRemove }: ProviderConfigProps) {
  const updateSTT = (updates: Partial<STTConfig>) => {
    onChange({
      stt_config: { ...provider.stt_config!, ...updates }
    });
  };

  const updateTTS = (updates: Partial<TTSConfig>) => {
    onChange({
      tts_config: { ...provider.tts_config!, ...updates }
    });
  };

  return (
    <div className="provider-config">
      <div className="provider-header">
        <h4>{provider.provider.toUpperCase()} Провайдер</h4>
        <button onClick={onRemove} className="remove-btn">×</button>
      </div>

      <div className="provider-basic">
        <div className="field">
          <label>Приоритет:</label>
          <input
            type="number"
            min="1"
            max="10"
            value={provider.priority}
            onChange={e => onChange({ 
              priority: parseInt(e.target.value) 
            })}
          />
        </div>

        <label className="switch">
          <input
            type="checkbox"
            checked={provider.fallback_enabled}
            onChange={e => onChange({ 
              fallback_enabled: e.target.checked 
            })}
          />
          Использовать как fallback
        </label>
      </div>

      {/* STT Настройки */}
      <div className="stt-section">
        <label className="switch">
          <input
            type="checkbox"
            checked={provider.stt_config?.enabled || false}
            onChange={e => {
              if (e.target.checked) {
                onChange({
                  stt_config: DEFAULT_PROVIDER_CONFIGS[provider.provider].stt_config
                });
              } else {
                onChange({ stt_config: undefined });
              }
            }}
          />
          Включить STT (Speech-to-Text)
        </label>

        {provider.stt_config?.enabled && (
          <STTConfigForm
            config={provider.stt_config}
            provider={provider.provider}
            onChange={updateSTT}
          />
        )}
      </div>

      {/* TTS Настройки */}
      <div className="tts-section">
        <label className="switch">
          <input
            type="checkbox"
            checked={provider.tts_config?.enabled || false}
            onChange={e => {
              if (e.target.checked) {
                onChange({
                  tts_config: DEFAULT_PROVIDER_CONFIGS[provider.provider].tts_config
                });
              } else {
                onChange({ tts_config: undefined });
              }
            }}
          />
          Включить TTS (Text-to-Speech)
        </label>

        {provider.tts_config?.enabled && (
          <TTSConfigForm
            config={provider.tts_config}
            provider={provider.provider}
            onChange={updateTTS}
          />
        )}
      </div>
    </div>
  );
}
```

---

## 🏁 Финальная JSON структура

### Функция генерации конфигурации агента
```typescript
function generateAgentConfig(voiceSettings: VoiceSettings): any {
  return {
    config: {
      simple: {
        settings: {
          voice_settings: voiceSettings
          // Здесь могут быть другие настройки агента
        }
      }
    }
  };
}

// Пример использования
const agentConfig = generateAgentConfig(settings);
console.log(JSON.stringify(agentConfig, null, 2));
```

### Пример результата
```json
{
  "config": {
    "simple": {
      "settings": {
        "voice_settings": {
          "enabled": true,
          "intent_detection_mode": "keywords",
          "intent_keywords": ["голос", "скажи", "произнеси"],
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
                "audio_format": "mp3",
                "sample_rate": 22050
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

## 🎨 CSS стили (пример)
```css
.voice-settings-form {
  max-width: 800px;
  margin: 0 auto;
  padding: 20px;
}

.section {
  margin-bottom: 30px;
  padding: 20px;
  border: 1px solid #e0e0e0;
  border-radius: 8px;
}

.field {
  margin-bottom: 15px;
}

.field label {
  display: block;
  margin-bottom: 5px;
  font-weight: 500;
}

.field input,
.field select {
  width: 100%;
  padding: 8px 12px;
  border: 1px solid #ddd;
  border-radius: 4px;
}

.switch {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 15px;
}

.provider-config {
  border: 1px solid #ddd;
  border-radius: 8px;
  padding: 20px;
  margin-bottom: 20px;
}

.provider-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 20px;
}

.validation-errors,
.validation-warnings {
  margin-top: 20px;
  padding: 15px;
  border-radius: 4px;
}

.validation-errors {
  background-color: #ffebee;
  border-left: 4px solid #f44336;
}

.validation-warnings {
  background-color: #fff3e0;
  border-left: 4px solid #ff9800;
}

.actions {
  display: flex;
  gap: 10px;
  justify-content: flex-end;
  margin-top: 30px;
}

.save-btn {
  background-color: #4caf50;
  color: white;
  padding: 10px 20px;
  border: none;
  border-radius: 4px;
  cursor: pointer;
}

.cancel-btn {
  background-color: #757575;
  color: white;
  padding: 10px 20px;
  border: none;
  border-radius: 4px;
  cursor: pointer;
}
```

---

## ✅ Чек-лист для разработчика

### Обязательные проверки:
- [ ] Путь конфигурации: `config.simple.settings.voice_settings`
- [ ] Валидация всех числовых полей (диапазоны)
- [ ] Уникальность приоритетов провайдеров
- [ ] Хотя бы один провайдер настроен
- [ ] Валидация модели/голоса для каждого провайдера
- [ ] Обработка ошибок при сохранении
- [ ] Предварительный просмотр JSON

### Рекомендации:
- [ ] Автосохранение в localStorage
- [ ] Импорт/экспорт конфигурации
- [ ] Предустановленные шаблоны
- [ ] Подсказки и описания полей
- [ ] Тестирование конфигурации перед сохранением

**Готово!** Теперь у вас есть все необходимое для создания UI настройки голосовых функций агента. 🎉
