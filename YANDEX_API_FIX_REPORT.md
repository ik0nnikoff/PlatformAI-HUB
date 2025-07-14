# ОТЧЕТ О ПРОБЛЕМЕ YANDEX API И ИСПРАВЛЕНИЯХ

## Дата: 2024-12-19
## Статус: 🔧 ИСПРАВЛЕНИЯ ВНЕСЕНЫ, ТРЕБУЕТСЯ ОБНОВЛЕНИЕ КОНФИГУРАЦИИ

---

## 🔍 АНАЛИЗ ПРОБЛЕМЫ

### Исходная ошибка из логов:
```
2025-07-14 19:17:09,512 - ERROR - AGENT:agent_airsoft_0faa9616 - Yandex TTS synthesis failed: 
Yandex TTS API error 401: {"error_code":"UNAUTHORIZED","error_message":"rpc error: code = Unauthenticated desc = Unknown api key '************ (FF6C4961)'"}
```

### Найденная причина:
1. **Неправильное получение API ключа** - использовался `settings.YANDEX_API_KEY` вместо `.get_secret_value()`
2. **Неправильный Folder ID** - в конфигурации `aje4vtb0ecrp0glbscsr`, а API ключ требует `b1gukhoek8a45sqv67v4`

---

## 🔧 ВНЕСЕННЫЕ ИСПРАВЛЕНИЯ

### 1. Yandex TTS Service - Исправление получения API ключа

**Файл:** `app/services/voice/tts/yandex_tts.py`

**До:**
```python
def __init__(self, config: TTSConfig, logger: Optional[logging.Logger] = None):
    super().__init__(VoiceProvider.YANDEX, config, logger)
    self.api_key = settings.YANDEX_API_KEY  # ❌ Неправильно
    self.folder_id = settings.YANDEX_FOLDER_ID
```

**После:**
```python
def __init__(self, config: TTSConfig, logger: Optional[logging.Logger] = None):
    super().__init__(VoiceProvider.YANDEX, config, logger)
    self.api_key = settings.YANDEX_API_KEY.get_secret_value() if settings.YANDEX_API_KEY else None  # ✅ Правильно
    self.folder_id = settings.YANDEX_FOLDER_ID
```

### 2. Voice Orchestrator - Исправление проверки креденшалов

**Файл:** `app/services/voice/voice_orchestrator.py`

**До:**
```python
elif provider == VoiceProvider.YANDEX:
    return bool(settings.YANDEX_API_KEY or settings.YANDEX_IAM_TOKEN)  # ❌ Неправильно
```

**После:**
```python
elif provider == VoiceProvider.YANDEX:
    api_key = settings.YANDEX_API_KEY.get_secret_value() if settings.YANDEX_API_KEY else None
    iam_token = settings.YANDEX_IAM_TOKEN.get_secret_value() if settings.YANDEX_IAM_TOKEN else None
    return bool(api_key or iam_token)  # ✅ Правильно
```

---

## ✅ РЕЗУЛЬТАТЫ ТЕСТИРОВАНИЯ

### Тест 1: API Key исправление
```
🔑 Тестируем исправление Yandex API key...
✅ YANDEX_API_KEY настроен в settings
✅ get_secret_value() работает, длина ключа: 40
✅ API ключ корректный (начинается с: AQVNxP9WBz...)
✅ YandexTTSService.api_key установлен корректно
```

### Тест 2: Проверка валидности API
```
🔍 Проверяем валидность Yandex API ключа...
📊 Статус ответа: 401
❌ Ошибка авторизации (401):
{"error_code":"UNAUTHORIZED","error_message":"rpc error: code = InvalidArgument desc = Specified folder ID 'aje4vtb0ecrp0glbscsr' does not match with service account folder ID 'b1gukhoek8a45sqv67v4'"}
```

**Вывод:** Код исправлен корректно, но требуется обновление конфигурации.

---

## 🚨 ТРЕБУЕТСЯ ДЕЙСТВИЕ

### Обновить переменную окружения:

**В файле `.env` изменить:**
```env
# Было:
YANDEX_FOLDER_ID=aje4vtb0ecrp0glbscsr

# Должно быть:
YANDEX_FOLDER_ID=b1gukhoek8a45sqv67v4
```

### Альтернативный вариант:
Создать новый API ключ для существующего folder ID `aje4vtb0ecrp0glbscsr` в Yandex Cloud Console.

---

## 📊 СТАТУС ИСПРАВЛЕНИЙ

| Компонент | Статус | Описание |
|-----------|---------|----------|
| YandexTTSService API key | ✅ Исправлено | Использует get_secret_value() |
| VoiceOrchestrator credentials check | ✅ Исправлено | Правильная проверка креденшалов |
| Folder ID конфигурация | ❌ Требует действия | Несоответствие API ключу |

---

## 🔍 ДЕТАЛИ ПРОБЛЕМЫ

### Что означает ошибка:
```
"Specified folder ID 'aje4vtb0ecrp0glbscsr' does not match with service account folder ID 'b1gukhoek8a45sqv67v4'"
```

Это означает, что:
1. API ключ создан для сервисного аккаунта в folder `b1gukhoek8a45sqv67v4`
2. В конфигурации указан folder `aje4vtb0ecrp0glbscsr`
3. Yandex Cloud не разрешает использовать API ключ из одного folder для другого

### Решение:
- **Вариант 1:** Обновить `YANDEX_FOLDER_ID` на `b1gukhoek8a45sqv67v4`
- **Вариант 2:** Создать новый API ключ для folder `aje4vtb0ecrp0glbscsr`

---

## 🎯 РЕКОМЕНДАЦИИ

1. **Немедленно:** Обновить `YANDEX_FOLDER_ID` в `.env`
2. **Проверить:** Перезапустить сервисы после изменения
3. **Тестировать:** Запустить голосовые функции
4. **Мониторинг:** Следить за логами Yandex TTS

### Команда для быстрого исправления:
```bash
# В файле .env
sed -i '' 's/YANDEX_FOLDER_ID=aje4vtb0ecrp0glbscsr/YANDEX_FOLDER_ID=b1gukhoek8a45sqv67v4/' .env
```

---

## 🏁 ЗАКЛЮЧЕНИЕ

### ✅ Исправлено в коде:
- Правильное получение API ключа через `.get_secret_value()`
- Корректная проверка креденшалов в VoiceOrchestrator
- Унификация с другими провайдерами (OpenAI, Google)

### ❗ Требует действия:
- **YANDEX_FOLDER_ID** должен быть изменен на `b1gukhoek8a45sqv67v4`

### 🎉 После исправления:
- Yandex TTS будет работать корректно
- Ошибки 401 исчезнут
- Голосовые функции будут полностью функциональны

---

**Приоритет:** 🔥 ВЫСОКИЙ - требует немедленного действия  
**Время исправления:** ~2 минуты  
**Влияние:** Полное восстановление функций Yandex TTS

---

**Подготовлено:** AI Assistant  
**Дата:** 2024-12-19  
**Версия:** Yandex API Fix v1.0
