import logging
import json
import asyncio
from typing import Dict, List, Optional, Set
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Depends, status as fastapi_status
from starlette.websockets import WebSocketState
from sqlalchemy.ext.asyncio import AsyncSession
import redis.asyncio as redis # Keep this for type hints and client usage
# --- ИЗМЕНЕНИЕ: Добавляем импорт базового redis для исключений ---
import redis as redis_base # Import base redis for exceptions
# --- КОНЕЦ ИЗМЕНЕНИЯ ---

from ..redis_client import get_redis
from ..db import get_db
from .. import crud

logger = logging.getLogger(__name__)
router = APIRouter()

# --- Constants ---
AGENT_START_TIMEOUT = 20 # Seconds to wait for agent to become 'running'
AGENT_STATUS_POLL_INTERVAL = 1 # Seconds between status checks

# --- ИЗМЕНЕННЫЙ ConnectionManager ---
class ConnectionManager:
    """Управляет активными WebSocket соединениями и центральными слушателями Redis."""
    def __init__(self):
        # agent_id -> thread_id -> list of websockets
        self.active_connections: Dict[str, Dict[str, List[WebSocket]]] = {}
        # agent_id -> listener task
        self.listener_tasks: Dict[str, asyncio.Task] = {}
        # Зависимости для слушателя (передаются при создании)
        self._redis_dependency: Optional[callable] = None
        self._manager_instance: Optional['ConnectionManager'] = self # Ссылка на себя

    def set_dependencies(self, redis_dependency: callable):
        """Устанавливает зависимости, необходимые для запуска слушателей."""
        self._redis_dependency = redis_dependency

    async def connect(self, websocket: WebSocket, agent_id: str, thread_id: str):
        """Регистрирует новое WebSocket соединение и запускает слушатель Redis, если необходимо."""
        if agent_id not in self.active_connections:
            self.active_connections[agent_id] = {}
        if thread_id not in self.active_connections[agent_id]:
            self.active_connections[agent_id][thread_id] = []
        self.active_connections[agent_id][thread_id].append(websocket)
        logger.info(f"WebSocket connected for agent {agent_id}, thread {thread_id}. Total threads for agent: {len(self.active_connections[agent_id])}")

        # Запускаем центральный слушатель для agent_id, если он еще не запущен
        if agent_id not in self.listener_tasks or self.listener_tasks[agent_id].done():
            if self._redis_dependency and self._manager_instance:
                logger.info(f"Starting central Redis listener task for agent {agent_id}")
                try:
                    # Получаем Redis клиент через зависимость
                    r = await self._redis_dependency().__anext__() # Получаем клиент из генератора
                    self.listener_tasks[agent_id] = asyncio.create_task(
                        central_redis_listener(agent_id, r, self._manager_instance)
                    )
                except StopAsyncIteration:
                     logger.error(f"Failed to get Redis client from dependency for agent {agent_id} listener.")
                except Exception as e:
                     logger.error(f"Error starting central listener for agent {agent_id}: {e}", exc_info=True)
            else:
                logger.error(f"Cannot start listener for {agent_id}: Dependencies not set in ConnectionManager.")

    def disconnect(self, websocket: WebSocket, agent_id: str, thread_id: str):
        """Отключает WebSocket и останавливает слушатель Redis, если соединений больше нет."""
        if agent_id in self.active_connections and thread_id in self.active_connections[agent_id]:
            try:
                self.active_connections[agent_id][thread_id].remove(websocket)
                if not self.active_connections[agent_id][thread_id]: # Если список для thread_id пуст
                    del self.active_connections[agent_id][thread_id]
                    logger.info(f"Thread {thread_id} closed for agent {agent_id}.")
                if not self.active_connections[agent_id]: # Если для agent_id больше нет thread_id
                    del self.active_connections[agent_id]
                    logger.info(f"Agent {agent_id} has no more active threads.")
                    # Останавливаем центральный слушатель, если он существует и работает
                    if agent_id in self.listener_tasks and not self.listener_tasks[agent_id].done():
                        logger.info(f"Stopping central Redis listener task for agent {agent_id}")
                        self.listener_tasks[agent_id].cancel()
                        # Удаляем задачу из словаря (можно добавить обработку ожидания завершения)
                        del self.listener_tasks[agent_id]

                logger.info(f"WebSocket disconnected for agent {agent_id}, thread {thread_id}.")

            except ValueError:
                 logger.warning(f"WebSocket not found in list during disconnect for agent {agent_id}, thread {thread_id}.")
            except Exception as e:
                 logger.error(f"Error during WebSocket disconnect cleanup for {agent_id}/{thread_id}: {e}", exc_info=True)
        else:
            logger.warning(f"Attempted to disconnect WebSocket for agent {agent_id}, thread {thread_id}, but not found.")

    def get_sockets_for_thread(self, agent_id: str, thread_id: str) -> List[WebSocket]:
        """Возвращает список WebSocket соединений для данного agent_id и thread_id."""
        return self.active_connections.get(agent_id, {}).get(thread_id, [])

    async def send_to_thread(self, agent_id: str, thread_id: str, message: str):
        """Отправляет сообщение всем WebSocket клиентам, подписанным на данный thread_id."""
        sockets_to_send = self.get_sockets_for_thread(agent_id, thread_id)
        if not sockets_to_send:
            # logger.debug(f"No active WebSocket connections found for agent {agent_id}, thread {thread_id} to send message.")
            return

        disconnected_sockets = []
        for connection in sockets_to_send:
            try:
                await connection.send_text(message)
            except WebSocketDisconnect:
                logger.warning(f"WebSocket for agent {agent_id}, thread {thread_id} disconnected during send.")
                disconnected_sockets.append(connection)
            except Exception as e:
                logger.error(f"Error sending message to WebSocket for agent {agent_id}, thread {thread_id}: {e}")
                disconnected_sockets.append(connection) # Удаляем сокет при ошибке отправки

        # Удаляем отключенные сокеты после итерации
        for socket in disconnected_sockets:
            # Мы не можем вызвать disconnect напрямую здесь, так как не знаем thread_id изначального подключения сокета
            # Вместо этого, просто удалим его из текущего списка (если он там есть)
            # disconnect будет вызван из основного websocket_endpoint при возникновении WebSocketDisconnect
            if agent_id in self.active_connections and thread_id in self.active_connections[agent_id]:
                 if socket in self.active_connections[agent_id][thread_id]:
                     self.active_connections[agent_id][thread_id].remove(socket)
                     # Логика очистки пустых списков/словарей остается в disconnect


# --- Глобальный экземпляр менеджера ---
manager = ConnectionManager()
# Устанавливаем зависимости после создания manager
manager.set_dependencies(redis_dependency=get_redis)


# --- НОВЫЙ Центральный слушатель Redis ---
async def central_redis_listener(agent_id: str, r: redis.Redis, conn_manager: ConnectionManager):
    """
    Центральный слушатель для agent_id. Получает сообщения из Redis output
    и отправляет их соответствующим WebSocket клиентам через ConnectionManager.
    """
    output_channel = f"agent:{agent_id}:output"
    pubsub = None
    expected_channels = ["websocket", "dashboard", "web"] # Каналы для отправки в WebSocket

    logger.info(f"[Central Listener {agent_id}] Subscribing to {output_channel}")
    try:
        pubsub = r.pubsub()
        await pubsub.subscribe(output_channel)

        while True:
            message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
            if message and message.get("type") == "message":
                raw_data = message["data"]
                # logger.debug(f"[Central Listener {agent_id}] Received raw message: {raw_data[:100]}...")
                try:
                    data = json.loads(raw_data)
                    response_channel = data.get("channel")
                    response_thread_id = data.get("thread_id")

                    if response_channel in expected_channels and response_thread_id:
                        # logger.debug(f"[Central Listener {agent_id}] Forwarding message to thread {response_thread_id}")
                        await conn_manager.send_to_thread(agent_id, response_thread_id, raw_data)
                    # else:
                        # logger.debug(f"[Central Listener {agent_id}] Ignoring message (channel: {response_channel}, thread: {response_thread_id})")

                except json.JSONDecodeError:
                    logger.error(f"[Central Listener {agent_id}] Failed to decode JSON: {raw_data}")
                except Exception as e:
                    logger.error(f"[Central Listener {agent_id}] Error processing message: {e}", exc_info=True)

            await asyncio.sleep(0.05) # Небольшая пауза

    # --- ИЗМЕНЕНИЕ: Используем redis_base.exceptions ---
    except redis_base.exceptions.ConnectionError as e:
    # --- КОНЕЦ ИЗМЕНЕНИЯ ---
        logger.error(f"[Central Listener {agent_id}] Redis connection error: {e}")
        # Попытка отправить сообщение об ошибке всем подключенным клиентам этого агента? Сложно.
    except asyncio.CancelledError:
        logger.info(f"[Central Listener {agent_id}] Task cancelled.")
    except Exception as e:
        logger.error(f"[Central Listener {agent_id}] Unexpected error: {e}", exc_info=True)
    finally:
        logger.info(f"[Central Listener {agent_id}] Cleaning up...")
        if pubsub:
            try:
                await pubsub.unsubscribe(output_channel)
                if hasattr(pubsub, 'aclose'): await pubsub.aclose()
                else: await pubsub.close()
                logger.info(f"[Central Listener {agent_id}] Unsubscribed from {output_channel}")
            except Exception as clean_e:
                logger.error(f"[Central Listener {agent_id}] Error closing pubsub: {clean_e}")
        # Убедимся, что задача удалена из менеджера при неожиданном завершении
        if agent_id in conn_manager.listener_tasks and conn_manager.listener_tasks[agent_id].done():
             del conn_manager.listener_tasks[agent_id]
             logger.info(f"[Central Listener {agent_id}] Task removed from manager due to completion/error.")


# --- УДАЛЕН redis_websocket_listener ---

# --- ОБНОВЛЕННЫЙ websocket_endpoint ---
@router.websocket("/ws/agents/{agent_id}")
async def websocket_endpoint(
    websocket: WebSocket,
    agent_id: str,
    r: redis.Redis = Depends(get_redis),
    db: AsyncSession = Depends(get_db) # Добавляем зависимость БД
):
    """
    Эндпоинт WebSocket для взаимодействия с агентом.
    Регистрирует соединение в ConnectionManager и передает сообщения
    между клиентом и каналом ввода/вывода агента в Redis.
    """
    # --- Проверка существования агента ---
    db_agent = await crud.db_get_agent_config(db, agent_id)
    if not db_agent:
        logger.warning(f"WebSocket connection attempt for non-existent agent (DB check): {agent_id}")
        await websocket.accept() # Принимаем, чтобы отправить ошибку
        await websocket.send_text(json.dumps({"type": "error", "content": f"Agent '{agent_id}' not found."}))
        await websocket.close(code=fastapi_status.WS_1008_POLICY_VIOLATION)
        return
    # --- Конец проверки ---

    # Принимаем соединение только после проверки агента
    await websocket.accept()
    logger.info(f"WebSocket connection accepted for agent {agent_id}")

    initial_thread_id: Optional[str] = None
    manager_connected = False # Флаг, что соединение зарегистрировано в менеджере

    try:
        while True:
            data = await websocket.receive_text()
            # logger.debug(f"Received raw data via WebSocket for agent {agent_id}: {data[:100]}...")

            # Парсим данные и извлекаем thread_id
            try:
                payload = json.loads(data)
                received_thread_id = payload.get("thread_id")
                # Ищем 'message' или 'content'
                message_to_agent = payload.get("message", payload.get("content"))
                received_channel = payload.get("channel", "websocket") # Канал по умолчанию

                if not received_thread_id:
                    await websocket.send_text(json.dumps({"type": "error", "content": "Missing 'thread_id' in message."}))
                    continue
                if message_to_agent is None: # Проверяем, что сообщение есть
                    await websocket.send_text(json.dumps({"type": "error", "content": "Missing 'message' or 'content' field in message."}))
                    continue

            except json.JSONDecodeError:
                await websocket.send_text(json.dumps({"type": "error", "content": "Invalid JSON received."}))
                continue
            except Exception as parse_err:
                 logger.error(f"Error parsing WebSocket message for {agent_id}: {parse_err}", exc_info=True)
                 await websocket.send_text(json.dumps({"type": "error", "content": "Error parsing message."}))
                 continue

            # --- Подключение к менеджеру при первом сообщении ---
            if not manager_connected:
                initial_thread_id = received_thread_id
                # Сохраняем thread_id на объекте websocket для использования в finally
                setattr(websocket, "thread_id", initial_thread_id)
                await manager.connect(websocket, agent_id, initial_thread_id)
                manager_connected = True
                logger.info(f"WebSocket registered with manager for agent {agent_id}, thread {initial_thread_id}")

            # --- Проверка смены thread_id (опционально) ---
            elif received_thread_id != initial_thread_id:
                logger.warning(f"WebSocket for agent {agent_id} sent message with different thread_id ({received_thread_id}). Expected {initial_thread_id}. Ignoring.")
                await websocket.send_text(json.dumps({"type": "error", "content": f"Thread ID mismatch. Connection bound to {initial_thread_id}."}))
                continue

            # --- Публикация в Redis Input ---
            input_channel = f"agent:{agent_id}:input"
            agent_payload = {
                "message": message_to_agent,
                "thread_id": received_thread_id,
                "channel": received_channel,
                "user_data": payload.get("user_data", {})
            }
            try:
                await r.publish(input_channel, json.dumps(agent_payload))
                # logger.debug(f"Published message to {input_channel}")
            # --- ИЗМЕНЕНИЕ: Используем redis_base.exceptions ---
            except redis_base.exceptions.ConnectionError as e:
            # --- КОНЕЦ ИЗМЕНЕНИЯ ---
                 logger.error(f"Redis connection error publishing WS message to {input_channel}: {e}")
                 # --- ИЗМЕНЕНИЕ: Добавляем проверку состояния перед отправкой ---
                 if websocket.client_state == WebSocketState.CONNECTED:
                     await websocket.send_text(json.dumps({"type": "error", "content": "Failed to send message to agent (Redis connection)"}))
                 # --- КОНЕЦ ИЗМЕНЕНИЯ ---
            except Exception as e:
                 logger.error(f"Error publishing WS message to {input_channel}: {e}", exc_info=True)
                 # --- ИЗМЕНЕНИЕ: Добавляем проверку состояния перед отправкой ---
                 if websocket.client_state == WebSocketState.CONNECTED:
                     await websocket.send_text(json.dumps({"type": "error", "content": "Failed to send message to agent"}))
                 # --- КОНЕЦ ИЗМЕНЕНИЯ ---

    except WebSocketDisconnect:
        logger.info(f"WebSocket client for agent {agent_id} (thread: {initial_thread_id or 'unknown'}) disconnected.")
    except Exception as e:
        logger.error(f"Unexpected error in WebSocket endpoint for agent {agent_id} (thread: {initial_thread_id or 'unknown'}): {e}", exc_info=True)
        # Попытка закрыть соединение, если оно еще открыто
        if websocket.client_state == WebSocketState.CONNECTED:
             await websocket.close(code=fastapi_status.WS_1011_INTERNAL_ERROR)
    finally:
        logger.info(f"Cleaning up WebSocket connection for agent {agent_id} (thread: {initial_thread_id or 'unknown'})")
        # Отключаем от менеджера, только если были успешно подключены
        if manager_connected and initial_thread_id:
            # Используем thread_id, сохраненный на объекте websocket
            thread_id_to_disconnect = getattr(websocket, "thread_id", None)
            if thread_id_to_disconnect:
                 manager.disconnect(websocket, agent_id, thread_id_to_disconnect)
            else: # На всякий случай, если атрибут не установился
                 manager.disconnect(websocket, agent_id, initial_thread_id)

