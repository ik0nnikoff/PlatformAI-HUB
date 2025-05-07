# Интеграция SSE и отправки сообщений

## SSE (Server-Sent Events)

SSE используется для получения сообщений от агента в реальном времени. Клиент подключается к SSE-эндпоинту и получает потоковые данные.

### Эндпоинт для SSE

**URL**: `GET /agents/{agent_id}/sse`

**Параметры запроса**:
- `agent_id` (path): Уникальный идентификатор агента.
- `thread_id` (query): Уникальный идентификатор треда, по которому фильтруются сообщения.

**Пример запроса**:
```http
GET /agents/agent_123/sse?thread_id=thread_456 HTTP/1.1
Host: localhost:8000
Accept: text/event-stream
```

**Пример ответа**:
```plaintext
data: {"agent_id": "agent_123", "thread_id": "thread_456", "message": "Пример сообщения от агента"}
```

**Пример подключения на клиенте (JavaScript)**:
```javascript
const agentId = "agent_123";
const threadId = "thread_456";

const eventSource = new EventSource(`/agents/${agentId}/sse?thread_id=${threadId}`);

eventSource.onmessage = (event) => {
    const data = JSON.parse(event.data);
    console.log("Новое сообщение от агента:", data.message);
};

eventSource.onerror = (error) => {
    console.error("Ошибка SSE:", error);
};
```

---

## Отправка сообщений агенту

Для отправки сообщений агенту используется REST API.

### Эндпоинт для отправки сообщений

**URL**: `POST /agents/{agent_id}/messages`

**Тело запроса**:
- `thread_id` (string): Уникальный идентификатор треда.
- `message` (string): Сообщение, которое нужно отправить агенту.

**Пример запроса**:
```http
POST /agents/agent_123/messages HTTP/1.1
Host: localhost:8000
Content-Type: application/json

{
    "thread_id": "thread_456",
    "message": "Привет, агент!"
}
```

**Пример ответа**:
```json
{
    "status": "success",
    "message": "Message sent to agent."
}
```

**Пример отправки на клиенте (JavaScript)**:
```javascript
const sendMessage = async (agentId, threadId, message) => {
    const response = await fetch(`/agents/${agentId}/messages`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ thread_id: threadId, message }),
    });

    if (response.ok) {
        const result = await response.json();
        console.log("Сообщение отправлено:", result);
    } else {
        console.error("Ошибка при отправке сообщения:", response.statusText);
    }
};

sendMessage("agent_123", "thread_456", "Привет, агент!");
```

---

## Примечания

1. **Переподключение SSE**:
   - Если соединение SSE разрывается, клиенту нужно реализовать механизм переподключения.
   - Например, можно использовать `setTimeout` для повторного подключения через несколько секунд.

2. **Формат данных**:
   - SSE отправляет данные в формате `text/event-stream`, где каждое сообщение начинается с `data:` и заканчивается двумя переводами строки (`\n\n`).
   - Сообщения от агента передаются в формате JSON.

3. **Ограничения SSE**:
   - SSE поддерживает только одностороннюю передачу данных от сервера к клиенту.
   - Для отправки данных от клиента к серверу используется REST API.

4. **Безопасность**:
   - Убедитесь, что доступ к эндпоинтам ограничен авторизованными пользователями.
   - Используйте HTTPS для защиты данных в сети.
