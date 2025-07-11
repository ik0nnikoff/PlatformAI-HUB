# PlatformAI-HUB Architecture Notes

## Ключевые моменты архитектуры для разработки

### 1. Архитектура процессов

- **Главное приложение**: FastAPI сервер (port 8001) с API управления
- **Агенты**: Каждый агент = отдельный процесс (`AgentRunner`)
- **Интеграции**: Каждая интеграция = отдельный процесс (Telegram, WebSocket)
- **Воркеры**: Фоновые задачи для обработки очередей

### 2. Система обмена сообщениями (Redis Pub/Sub)

#### Каналы:
- `agent:{agent_id}:input` - входящие сообщения для агента
- `agent:{agent_id}:output` - исходящие сообщения от агента

#### Очереди:
- `history_queue` - сохранение истории чатов
- `token_usage_queue` - учет использования токенов

#### Статусы:
- `agent_status:{agent_id}` - состояние агента
- `integration_status:{agent_id}:{type}` - состояние интеграции

### 3. Поток данных

```
Пользователь -> Интеграция -> Redis Input -> AgentRunner -> LangGraph -> Redis Output -> Интеграция -> Пользователь
```

#### Структура входящего сообщения:
```json
{
  "text": "текст сообщения",
  "chat_id": "thread_id",
  "platform_user_id": "user_id", 
  "user_data": {},
  "channel": "telegram"
}
```

#### Структура исходящего сообщения:
```json
{
  "chat_id": "thread_id",
  "response": "ответ агента",
  "channel": "telegram"
}
```

### 4. Управление процессами

#### ProcessManager (`app/services/process_manager.py`):
- Запуск/остановка/перезапуск процессов
- Мониторинг через Redis статусы
- Graceful shutdown с SIGTERM -> SIGKILL

#### Статусы процессов:
- `running` - работает
- `stopped` - остановлен
- `starting` - запускается
- `error` - ошибка

### 5. Конфигурация агентов

#### Структура конфигурации в БД:
```json
{
  "simple": {
    "settings": {
      "system_prompt": "Ты помощник...",
      "model_id": "gpt-4",
      "provider": "openai",
      "temperature": 0.7,
      "integrations": [
        {
          "type": "telegram",
          "settings": {
            "enabled": true,
            "bot_token": "token"
          }
        }
      ]
    }
  }
}
```

### 6. База данных (PostgreSQL)

#### Ключевые таблицы:
- `agent_configs` - конфигурации агентов
- `chat_messages` - история сообщений
- `users` - пользователи
- `agent_user_authorizations` - авторизации
- `token_usage_logs` - логи токенов

### 7. LangGraph Integration

#### Создание агента:
```python
# app/agent_runner/langgraph/factory.py
def create_agent_app(agent_config, agent_id, logger):
    # Создает граф с узлами и инструментами
    # Возвращает приложение для astream
```

#### Структура состояния графа:
```python
{
  "messages": [HumanMessage, AIMessage],
  "user_data": {},
  "channel": "telegram",
  "question": "текст",
  "documents": [],
  "token_usage_events": [TokenUsageData]
}
```

### 8. Жизненный цикл компонентов

#### Наследование:
- `ServiceComponentBase` - базовый класс для всех компонентов
- `AgentRunner` - управление агентом
- `TelegramIntegrationBot` - Telegram интеграция
- `QueueWorker` - фоновые воркеры

#### Методы жизненного цикла:
- `setup()` - инициализация
- `run()` - основной цикл
- `cleanup()` - очистка ресурсов

### 9. Мониторинг и восстановление

#### Автоматическое восстановление:
- Перезапуск при сбоях (MAX_RESTART_ATTEMPTS)
- Мониторинг неактивности (AGENT_INACTIVITY_TIMEOUT)
- Heartbeat обновления (AGENT_RUNNER_HEARTBEAT_INTERVAL)

#### Воркеры:
- `HistorySaverWorker` - сохраняет историю в БД
- `TokenUsageWorker` - учитывает токены
- `InactivityMonitorWorker` - мониторит активность

### 10. API Endpoints

#### Агенты:
- `POST /api/v1/agents` - создание
- `GET /api/v1/agents/{agent_id}` - получение
- `POST /api/v1/agents/{agent_id}/start` - запуск
- `POST /api/v1/agents/{agent_id}/stop` - остановка

#### Интеграции:
- `POST /api/v1/integrations/{agent_id}/{integration_type}/start`
- `POST /api/v1/integrations/{agent_id}/{integration_type}/stop`

### 11. Переменные окружения

#### Основные настройки:
- `REDIS_URL` - подключение к Redis
- `DATABASE_URL` - подключение к PostgreSQL
- `OPENAI_API_KEY` - ключ OpenAI
- `OPENROUTER_API_KEY` - ключ OpenRouter
- `MANAGER_PORT` - порт главного сервера

#### Мониторинг:
- `AGENT_INACTIVITY_TIMEOUT` - таймаут неактивности
- `PROCESS_CHECK_INTERVAL` - интервал проверки процессов
- `MAX_RESTART_ATTEMPTS` - максимум попыток перезапуска

### 12. Команды разработки

```bash
# Запуск основного сервера
make run

# Миграции БД
make migrate
make makemigrations m="описание"

# Тестирование агента
make run_agent AGENT_ID=test_agent

# Остановка агента
make stop_agent AGENT_ID=test_agent
```

### 13. Особенности реализации

#### Redis клиенты:
- Каждый компонент имеет собственный Redis клиент
- Наследование от `RedisClientManager`
- Автоматическое переподключение

#### Обработка ошибок:
- Логирование всех ошибок
- Graceful degradation
- Retry механизмы

#### Конфигурация:
- Централизованная в `app/core/config.py`
- Использование `.env` файлов
- Валидация через Pydantic

### 14. Точки расширения

#### Новые интеграции:
1. Создать класс наследник от `ServiceComponentBase`
2. Реализовать методы `setup()`, `run()`, `cleanup()`
3. Добавить в `IntegrationType` enum
4. Обновить `ProcessManager` для поддержки нового типа

#### Новые инструменты:
1. Добавить в `app/agent_runner/langgraph/tools.py`
2. Зарегистрировать в `configure_tools()`
3. Обновить конфигурацию агента

#### Новые воркеры:
1. Наследоваться от `QueueWorker`
2. Реализовать `process_message()`
3. Добавить в `lifespan.py`

### 15. Важные моменты безопасности

- Авторизация через `agent_user_authorizations`
- Валидация токенов интеграций
- Изоляция процессов
- Логирование всех операций

---

**Дата обновления**: 10 июля 2025 г.
**Версия**: 0.1.0

---

## AI Agent Development Instructions

### Системные инструкции для AI-агента разработки PlatformAI-HUB

При работе с данным проектом учитывать следующие ключевые принципы:

#### 1. Архитектурные принципы
- **Изоляция процессов**: Каждый агент и интеграция работают в отдельных процессах
- **Асинхронность**: Все операции должны быть асинхронными (async/await)
- **Redis-центричность**: Все межпроцессное взаимодействие через Redis Pub/Sub
- **Наследование от базовых классов**: Обязательно использовать `ServiceComponentBase`, `QueueWorker`

#### 2. Структура кода
- **Конфигурация**: Все настройки в `app/core/config.py` + `.env`
- **Миграции**: Изменения БД только через Alembic
- **Логирование**: Обязательное логирование всех операций
- **Типизация**: Использовать типы Python и Pydantic схемы

#### 3. Правила разработки
- **Новые интеграции**: Наследоваться от `ServiceComponentBase`, добавить в `IntegrationType`
- **Новые инструменты**: Добавить в `langgraph/tools.py`, зарегистрировать в `configure_tools()`
- **Новые воркеры**: Наследоваться от `QueueWorker`, добавить в `lifespan.py`
- **API endpoints**: Группировать по функциональности, использовать зависимости FastAPI

#### 4. Обязательные проверки при модификации
- **Статусы процессов**: Обновлять в Redis при изменении состояния
- **Очистка ресурсов**: Реализовать методы `cleanup()` для всех компонентов
- **Обработка ошибок**: Graceful degradation, retry механизмы
- **Миграции**: Проверить совместимость с существующими данными

#### 5. Потоки данных - НЕ НАРУШАТЬ
```
Интеграция -> Redis Input -> AgentRunner -> LangGraph -> Redis Output -> Интеграция
```

#### 6. Ключевые файлы (изменять осторожно)
- `app/services/process_manager.py` - управление процессами
- `app/core/lifespan.py` - жизненный цикл приложения
- `app/agent_runner/agent_runner.py` - ядро агента
- `app/db/alchemy_models.py` - схема БД

#### 7. Тестирование изменений
- Запуск: `make run`
- Тест агента: `make run_agent AGENT_ID=test`
- Миграции: `make migrate`
- Проверка логов: `tail -f logs/app.log`

#### 8. Безопасность
- Всегда проверять авторизацию через `agent_user_authorizations`
- Валидировать входные данные
- Не логировать секретные данные (токены, ключи)
- Использовать `SecretStr` для конфиденциальных данных

#### 9. Производительность
- Мониторить использование памяти процессами
- Не блокировать основные потоки
- Использовать пулы соединений для БД и Redis
- Оптимизировать SQL запросы

#### 10. Совместимость
- Поддерживать обратную совместимость API
- Версионировать изменения схемы БД
- Учитывать существующие конфигурации агентов
- Тестировать с различными провайдерами LLM
