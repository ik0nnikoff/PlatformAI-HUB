# Сервис Управления Агентами

Этот проект предоставляет сервис для управления и взаимодействия с настраиваемыми ИИ-агентами.

## Возможности

*   Создание, настройка и управление несколькими агентами через API.
*   Запуск/остановка процессов агентов.
*   Взаимодействие с агентами в реальном времени через WebSocket.
*   Интеграция с ботами Telegram.
*   Хранение конфигураций агентов в PostgreSQL.
*   Использование Redis для управления статусами процессов и обмена сообщениями (pub/sub).
*   Автоматическая остановка неактивных процессов агентов.
*   Динамические инструменты API на основе конфигурации агента.
*   Адаптивный RAG с переписыванием запросов.
*   Развертывание с использованием Docker.

## Структура Проекта

```
experiments/
├── agent_manager/           # Сервис FastAPI для управления агентами
│   ├── api/                 # Конечные точки API
│   │   ├── __init__.py
│   │   ├── agents.py        # Эндпоинты для управления агентами (CRUD, start, stop)
│   │   └── websocket.py     # Эндпоинт WebSocket для взаимодействия с агентами
│   ├── alembic/             # Скрипты миграции базы данных Alembic
│   │   ├── versions/        # Файлы версий миграций
│   │   ├── env.py           # Конфигурация среды Alembic
│   │   └── script.py.mako   # Шаблон скрипта миграции
│   ├── __init__.py
│   ├── config.py            # Загрузка конфигурации (из .env)
│   ├── crud.py              # Операции CRUD с базой данных
│   ├── db.py                # Настройка базы данных (SQLAlchemy, async session)
│   ├── main.py              # Точка входа приложения FastAPI
│   ├── models.py            # Модели Pydantic и SQLAlchemy для manager'а
│   ├── process_manager.py   # Логика запуска/остановки процессов агентов и интеграций
│   └── redis_client.py      # Настройка соединения с Redis
│   └── alembic.ini          # Конфигурация Alembic
├── agent_runner/            # Код для отдельного процесса агента (LangGraph)
│   ├── __init__.py
│   ├── graph_factory.py     # Фабрика для создания графа LangGraph (узлы, ребра, компиляция)
│   ├── models.py            # Модели Pydantic для runner'а (AgentState)
│   ├── runner.py            # Основной скрипт для запуска экземпляра агента
│   └── tools.py             # Определение и конфигурация инструментов Langchain/LangGraph
├── integrations/            # Код для внешних интеграций
│   ├── __init__.py
│   └── telegram_bot.py      # Скрипт интеграции с ботом Telegram
├── docker-PAI/              # Файлы Docker Compose для зависимостей (Redis, Postgres, etc.)
│   ├── docker-compose.deps.yml
│   └── docker.env           # Переменные окружения для docker-compose.deps.yml
├── .env                     # Переменные окружения (ключи API, URL) - !! НЕ КОММИТИТЬ !!
├── docker-compose.yml       # Docker Compose для запуска agent-manager и зависимостей
├── Dockerfile.manager       # Dockerfile для сборки образа agent-manager
├── Makefile                 # Команды Make для общих задач
├── pyproject.toml           # Метаданные проекта и зависимости (используя uv/pip)
└── README.md                # Этот файл
```

## Настройка

1.  **Клонируйте репозиторий:**
    ```bash
    git clone <repository_url>
    cd experiments
    ```

2.  **Создайте и активируйте виртуальное окружение:**
    ```bash
    python -m venv .venv
    source .venv/bin/activate
    # Или в Windows: .venv\Scripts\activate
    ```

3.  **Установите зависимости с помощью `uv`:**
    ```bash
    pip install uv # Установите uv, если его нет
    uv pip sync pyproject.toml
    ```

4.  **Настройте переменные окружения:**
    *   Скопируйте `.env.example` в `.env` (если пример существует) или создайте `.env`.
    *   Заполните необходимые переменные:
        *   `OPENAI_API_KEY`: Ваш ключ API OpenAI.
        *   `TAVILY_API_KEY`: Ваш ключ API Tavily Search (если используется веб-поиск).
        *   `TELEGRAM_BOT_TOKEN`: Токен вашего бота Telegram (если используется интеграция с Telegram).
        *   `REDIS_URL`: URL вашего экземпляра Redis (например, `redis://localhost:6379`). По умолчанию localhost, если не установлено.
        *   `DATABASE_URL`: Строка подключения PostgreSQL (например, `postgresql+asyncpg://user:password@host:port/dbname`). **Обязательно**.
        *   `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB`: Обязательны при запуске с Docker Compose (должны соответствовать `DATABASE_URL`).
        *   `AGENT_INACTIVITY_TIMEOUT`: Таймаут в секундах для автоматической остановки агентов (по умолчанию: 1800).
        *   `AGENT_INACTIVITY_CHECK_INTERVAL`: Как часто проверять неактивных агентов (по умолчанию: 300).
        *   `QDRANT_URL`, `QDRANT_COLLECTION`: Детали подключения Qdrant (если используется база знаний).
        *   `LANGSMITH_API_KEY`, `LANGCHAIN_PROJECT`: Необязательно, для трассировки LangSmith.
    *   **Примечание:** Файл `.env` имеет решающее значение для предоставления секретов и данных подключения приложению, как при локальном запуске, так и с Docker Compose.

5.  **Настройте базу данных PostgreSQL:**
    *   Убедитесь, что у вас запущен сервер PostgreSQL.
    *   Создайте базу данных, соответствующую `dbname` в вашем `DATABASE_URL`.
    *   Примените миграции базы данных с помощью Alembic:
        ```bash
        # Активируйте окружение
        source .venv/bin/activate
        # Перейдите в каталог agent_manager
        cd agent_manager
        # Примените последние миграции
        alembic upgrade head
        # Вернитесь в корень проекта
        cd ..
        ```
        *Примечание: Если вы изменяете модели SQLAlchemy в `agent_manager/models.py`, сначала сгенерируйте новую миграцию:*
        ```bash
        # cd agent_manager
        # alembic revision --autogenerate -m "Описание изменений"
        # alembic upgrade head
        # cd ..
        ```

## Запуск Сервиса

Вы можете запустить сервис и его зависимости (PostgreSQL, Redis) локально с помощью `make manager` или с использованием Docker Compose.

### Вариант 1: Локальный запуск (с использованием Makefile)

1.  **Запустите зависимости:**
    *   Убедитесь, что Redis запущен (например, `redis-server`).
    *   Убедитесь, что PostgreSQL запущен, база данных создана и миграции применены (см. раздел Настройка).
    *   Убедитесь, что Qdrant запущен (если используется база знаний).
    *   *(Необязательно)* Запустите другие сервисы, такие как MinIO, если они требуются конкретным инструментам агента.

2.  **Запустите сервис Agent Manager:**
    Используйте цель Makefile:
    ```bash
    make manager
    ```
    Или запустите напрямую с uvicorn:
    ```bash
    uvicorn agent_manager.main:app --reload --port 8000 --host 0.0.0.0
    ```
    Документация API будет доступна по адресу `http://localhost:8000/docs`.

### Вариант 2: Запуск с Docker Compose

Этот метод запускает сервис Agent Manager, PostgreSQL и Redis в контейнерах Docker.

1.  **Предварительные требования:**
    *   Установлены Docker и Docker Compose.

2.  **Настройте переменные окружения:**
    *   Убедитесь, что у вас есть файл `.env` в корне проекта.
    *   Убедитесь, что в `.env` установлены следующие переменные, так как они используются `docker-compose.yml`:
        *   `POSTGRES_USER`: Имя пользователя для базы данных PostgreSQL.
        *   `POSTGRES_PASSWORD`: Пароль для базы данных PostgreSQL.
        *   `POSTGRES_DB`: Имя базы данных PostgreSQL.
        *   Другие переменные, такие как `OPENAI_API_KEY`, `TAVILY_API_KEY` и т. д., также необходимы контейнеру `agent-manager`.

3.  **Соберите и запустите контейнеры:**
    ```bash
    docker compose up --build -d
    ```
    *   `--build`: Заставляет Docker пересобрать образ `agent-manager`, если код или `Dockerfile.manager` изменились.
    *   `-d`: Запускает контейнеры в фоновом режиме (detached mode).

4.  **Примените миграции базы данных (при первом запуске или после изменения моделей):**
    Поскольку база данных работает внутри Docker, вам необходимо выполнить миграции Alembic внутри контейнера `agent-manager`:
    ```bash
    docker compose exec agent-manager alembic upgrade head
    ```
    *(Примечание: Если вы изменяете модели SQLAlchemy, сначала сгенерируйте миграции локально (`cd agent_manager && alembic revision --autogenerate ...`), прежде чем пересобирать образ и запускать `upgrade head` внутри контейнера).*

5.  **Доступ к сервису:**
    *   API будет доступен по адресу `http://localhost:8000`.
    *   Документация API будет доступна по адресу `http://localhost:8000/docs`.

6.  **Просмотр логов:**
    ```bash
    docker compose logs -f agent-manager # Просмотр логов сервиса manager
    docker compose logs -f db           # Просмотр логов postgres
    docker compose logs -f redis        # Просмотр логов redis
    ```

7.  **Остановка сервисов:**
    ```bash
    docker compose down
    ```
    Чтобы остановить и удалить тома (удаление данных базы данных и Redis):
    ```bash
    docker compose down -v
    ```

**Важное примечание о процессах Agent Runner с Docker:**

Предоставленный `docker-compose.yml` запускает только `agent-manager`, `redis` и `db`. Отдельные процессы `agent_runner` по-прежнему запускаются сервисом `agent-manager` при использовании конечной точки API `/start`. `agent-manager` (работающий внутри своего контейнера Docker) выполняет команду `python -m agent_runner.runner ...` в среде *своего* контейнера. Это работает, потому что код runner и зависимости включены в образ `agent-manager` через `COPY . /app`.

Если бы вам потребовалось запускать процессы `agent_runner` в отдельных выделенных контейнерах (например, для изоляции ресурсов или разных зависимостей), вам потребовалась бы более сложная настройка, включающая Docker-in-Docker или систему оркестрации контейнеров, такую как Kubernetes, чтобы позволить `agent-manager` запускать новые контейнеры `agent-runner`.

## Конечные точки API

Сервис предоставляет следующие конечные точки API с префиксом `/agents`:

*   **Создать Агента:** `POST /agents`
    *   Тело запроса: Конфигурация агента (см. `AgentConfigInput` в `agent_manager/models.py`).
*   **Список Агентов:** `GET /agents`
*   **Получить Детали Агента:** `GET /agents/{agent_id}`
*   **Удалить Агента:** `DELETE /agents/{agent_id}` (Сначала останавливает агента)
*   **Получить Статус Агента:** `GET /agents/{agent_id}/status`
*   **Запустить Агента:** `POST /agents/{agent_id}/start`
*   **Остановить Агента:** `POST /agents/{agent_id}/stop` (`?force=true` для SIGKILL)

*   **Получить Статус Интеграции:** `GET /agents/{agent_id}/integrations/{integration_type}/status`
    *   `integration_type` может быть `telegram`.
*   **Запустить Интеграцию:** `POST /agents/{agent_id}/integrations/{integration_type}/start`
    *   Запускает соответствующий процесс интеграции (например, бота Telegram).
*   **Остановить Интеграцию:** `POST /agents/{agent_id}/integrations/{integration_type}/stop` (`?force=true` для SIGKILL)
    *   Останавливает процесс интеграции.

*Внутренняя конечная точка (используется agent runner):*

*   **Получить "сырую" конфигурацию агента:** `GET /agents/{agent_id}/config`

## Взаимодействие с Агентами (WebSocket)

Подключитесь к конечной точке WebSocket: `ws://localhost:8000/ws/agents/{agent_id}`

*   **Отправка сообщений:** Отправляйте JSON-сообщения следующей структуры:
    ```json
    {
      "type": "message",
      "content": "Ваше сообщение агенту"
      // Опционально: "user_data": {"first_name": "...", "is_authenticated": true, ...}
      // Опционально: "channel": "web" | "telegram" | ...
    }
    ```
*   **Получение сообщений:** Получайте JSON-сообщения:
    *   Ответы агента: `{"type": "message", "content": "Ответ агента", "message_object": {...}}`
    *   Обновления статуса: `{"type": "status", "message": "Агент запускается..."}`
    *   Ошибки: `{"type": "error", "message": "Детали ошибки"}`

### Пример Клиентского Компонента Next.js

Вот базовый пример того, как вы можете реализовать компонент чата WebSocket в приложении Next.js с использованием TypeScript:

```typescript
// components/AgentChat.tsx
'use client';

import React, { useState, useEffect, useRef, useCallback } from 'react';

interface Message {
  type: 'message' | 'status' | 'error' | 'user'; // Добавлен тип 'user' для отображения сообщений пользователя
  content: string;
}

interface AgentChatProps {
  agentId: string;
  wsUrlBase?: string; // Необязательный базовый URL для WebSocket
}

const AgentChat: React.FC<AgentChatProps> = ({ agentId, wsUrlBase = 'ws://localhost:8000' }) => {
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState('');
  const [isConnected, setIsConnected] = useState(false);
  const ws = useRef<WebSocket | null>(null);
  const messagesEndRef = useRef<HTMLDivElement | null>(null); // Ref для прокрутки

  const connectWebSocket = useCallback(() => {
    if (ws.current && ws.current.readyState === WebSocket.OPEN) {
      console.log('WebSocket уже открыт');
      return;
    }

    const wsUrl = `${wsUrlBase}/ws/agents/${agentId}`;
    console.log(`Подключение к WebSocket: ${wsUrl}`);
    ws.current = new WebSocket(wsUrl);

    ws.current.onopen = () => {
      console.log('WebSocket подключен');
      setIsConnected(true);
      setMessages((prev) => [...prev, { type: 'status', content: 'Подключено к агенту.' }]);
    };

    ws.current.onmessage = (event) => {
      console.log('Сообщение от сервера:', event.data);
      try {
        const messageData = JSON.parse(event.data);
        // Убедимся, что messageData имеет type и content
        if (messageData.type && typeof messageData.content !== 'undefined') {
           setMessages((prev) => [...prev, { type: messageData.type, content: messageData.content }]);
        } else if (messageData.type === 'message' && typeof messageData.response !== 'undefined') {
           // Обработка ответа от runner.py
           setMessages((prev) => [...prev, { type: 'message', content: messageData.response }]);
        } else {
           console.warn("Получено сообщение неожиданного формата:", messageData);
           setMessages((prev) => [...prev, { type: 'error', content: `Получены неожиданные данные: ${event.data}` }]);
        }
      } catch (error) {
        console.error('Не удалось разобрать сообщение:', error);
        setMessages((prev) => [...prev, { type: 'error', content: `Получены не-JSON данные: ${event.data}` }]);
      }
    };

    ws.current.onerror = (error) => {
      console.error('Ошибка WebSocket:', error);
      setIsConnected(false);
      setMessages((prev) => [...prev, { type: 'error', content: 'Произошла ошибка WebSocket.' }]);
    };

    ws.current.onclose = (event) => {
      console.log('WebSocket отключен:', event.reason, event.code);
      setIsConnected(false);
      setMessages((prev) => [...prev, { type: 'status', content: `Отключено: ${event.reason || 'Соединение закрыто'}` }]);
      // Необязательно: Реализуйте логику переподключения здесь
    };
  }, [agentId, wsUrlBase]);

  useEffect(() => {
    connectWebSocket();
    // Функция очистки для закрытия WebSocket при размонтировании компонента
    return () => {
      ws.current?.close();
      console.log('Соединение WebSocket закрыто при размонтировании компонента.');
    };
  }, [connectWebSocket]); // Переподключаемся, если изменился agentId или wsUrlBase

  // Прокрутка вниз при обновлении сообщений
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages]);

  const sendMessage = () => {
    if (input.trim() && ws.current && ws.current.readyState === WebSocket.OPEN) {
      const messageToSend = { type: 'message', content: input }; // Структура для отправки через WebSocket manager'а
      console.log('Отправка сообщения:', messageToSend);
      ws.current.send(JSON.stringify(messageToSend));
      // Немедленно добавляем сообщение пользователя в чат
      setMessages((prev) => [...prev, { type: 'user', content: input }]);
      setInput('');
    } else if (!isConnected) {
       setMessages((prev) => [...prev, { type: 'error', content: 'Нет подключения. Пожалуйста, подождите или попробуйте переподключиться.' }]);
    }
  };

  const handleInputChange = (event: React.ChangeEvent<HTMLInputElement>) => {
    setInput(event.target.value);
  };

  const handleKeyPress = (event: React.KeyboardEvent<HTMLInputElement>) => {
    if (event.key === 'Enter') {
      sendMessage();
    }
  };

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '400px', border: '1px solid #ccc', padding: '10px' }}>
      <div style={{ flexGrow: 1, overflowY: 'auto', marginBottom: '10px', borderBottom: '1px solid #eee' }}>
        {messages.map((msg, index) => (
          <div key={index} style={{ marginBottom: '5px', padding: '5px', borderRadius: '5px', background: msg.type === 'user' ? '#e1f5fe' : (msg.type === 'error' ? '#ffcdd2' : (msg.type === 'status' ? '#f0f0f0' : '#fff')) }}>
            <strong>{msg.type === 'user' ? 'Вы' : (msg.type === 'message' ? 'Агент' : msg.type.toUpperCase())}:</strong> {msg.content}
          </div>
        ))}
        {/* Элемент для прокрутки */}
        <div ref={messagesEndRef} />
      </div>
      <div style={{ display: 'flex' }}>
        <input
          type="text"
          value={input}
          onChange={handleInputChange}
          onKeyPress={handleKeyPress}
          disabled={!isConnected}
          style={{ flexGrow: 1, marginRight: '5px', padding: '8px' }}
          placeholder={isConnected ? "Введите ваше сообщение..." : "Подключение..."}
        />
        <button onClick={sendMessage} disabled={!isConnected || !input.trim()} style={{ padding: '8px 15px' }}>
          Отправить
        </button>
      </div>
       {!isConnected && (
         <button onClick={connectWebSocket} style={{ marginTop: '10px', padding: '8px 15px' }}>
           Переподключиться
         </button>
       )}
    </div>
  );
};

export default AgentChat;

```
**Использование:**

```typescript
// На вашей странице Next.js (например, app/agents/[agentId]/page.tsx)
import AgentChat from '@/components/AgentChat'; // При необходимости скорректируйте путь

export default function AgentPage({ params }: { params: { agentId: string } }) {
  const { agentId } = params;

  return (
    <div>
      <h1>Чат с агентом: {agentId}</h1>
      <AgentChat agentId={agentId} />
      {/* Вы можете передать wsUrlBase из переменных окружения, если необходимо */}
      {/* <AgentChat agentId={agentId} wsUrlBase={process.env.NEXT_PUBLIC_WS_URL} /> */}
    </div>
  );
}
```

## Запуск Агента Вручную (для Тестирования)

**Примечание:** Эти команды запускают агента *вне* Docker. Убедитесь, что зависимости (например, Redis) доступны с вашей хост-машины (например, `redis://localhost:6379`). Если вы запускаете manager через Docker Compose, убедитесь, что порты Redis и PostgreSQL открыты для хоста в `docker-compose.yml`, если вам нужно, чтобы внешние инструменты или ручные runner'ы подключались напрямую.

```bash
# Пример: make run_agent AGENT_ID=agent_my_agent_1234
make run_agent AGENT_ID={ваш_agent_id}
```
Или напрямую:
```bash
python -m agent_runner.runner \
    --agent-id {ваш_agent_id} \
    --config-url http://localhost:8000/agents/{ваш_agent_id}/config \
    --redis-url redis://localhost:6379
```

## Запуск Интеграции Вручную (для Тестирования)

**Примечание:** Как и в случае с ручным запуском агентов, эти команды выполняются *вне* Docker. Убедитесь, что зависимости доступны.

*   **Бот Telegram:**
    ```bash
    # Пример: make run_telegram_bot AGENT_ID=agent_my_agent_1234
    make run_telegram_bot AGENT_ID={ваш_agent_id}
    ```
    Или напрямую:
    ```bash
    python -m integrations.telegram_bot --agent-id {ваш_agent_id}
    ```

## Будущие Улучшения (Идеи)

*   **Аутентификация и Авторизация:** Реализовать учетные записи пользователей и разрешения для управления агентами.
*   **Хранение Истории Чатов:** Сохранять историю разговоров в базе данных PostgreSQL.
*   **Улучшенный Фронтенд:** Разработать более функциональный веб-интерфейс чата.
*   **Изоляция Agent Runner:** Исследовать запуск agent runner'ов в отдельных контейнерах Docker для лучшего управления ресурсами (требуется Docker-in-Docker или оркестрация).
*   **UI для Конфигурации:** Создать веб-интерфейс для управления конфигурациями агентов.
*   **Мониторинг и Оповещения:** Интегрировать мониторинг состояния процессов агентов и работоспособности сервиса.
*   **Продвинутые Схемы Инструментов:** Динамически генерировать схемы Pydantic для входных данных инструментов API.
*   **Больше Интеграций:** Добавить поддержку других платформ, таких как Slack, Discord и т. д.
*   **Тестирование:** Добавить комплексные модульные и интеграционные тесты.
