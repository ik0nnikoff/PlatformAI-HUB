import logging
import json
import asyncio
from typing import Dict, List
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Depends, status as fastapi_status
import redis.asyncio as redis

from ..redis_client import get_redis
from .. import process_manager
from ..models import AgentStatus

logger = logging.getLogger(__name__)
router = APIRouter()

# --- Constants ---
AGENT_START_TIMEOUT = 20 # Seconds to wait for agent to become 'running'
AGENT_STATUS_POLL_INTERVAL = 1 # Seconds between status checks

class ConnectionManager:
    """Управляет активными WebSocket соединениями для каждого агента."""
    def __init__(self):
        # agent_id -> list of websockets
        self.active_connections: Dict[str, List[WebSocket]] = {}

    async def connect(self, websocket: WebSocket, agent_id: str):
        await websocket.accept()
        if agent_id not in self.active_connections:
            self.active_connections[agent_id] = []
        self.active_connections[agent_id].append(websocket)
        logger.info(f"WebSocket connected for agent {agent_id}. Total connections: {len(self.active_connections[agent_id])}")

    def disconnect(self, websocket: WebSocket, agent_id: str):
        if agent_id in self.active_connections:
            self.active_connections[agent_id].remove(websocket)
            if not self.active_connections[agent_id]: # Если список пуст
                del self.active_connections[agent_id]
            logger.info(f"WebSocket disconnected for agent {agent_id}.")
        else:
            logger.warning(f"Attempted to disconnect WebSocket for agent {agent_id}, but no active connections found.")

    async def broadcast_to_agent(self, agent_id: str, message: str):
        """Отправляет сообщение всем подключенным клиентам для данного агента."""
        if agent_id in self.active_connections:
            disconnected_sockets = []
            for connection in self.active_connections[agent_id]:
                try:
                    await connection.send_text(message)
                except WebSocketDisconnect:
                    logger.warning(f"WebSocket for agent {agent_id} disconnected during broadcast.")
                    disconnected_sockets.append(connection)
                except Exception as e:
                    logger.error(f"Error sending message to WebSocket for agent {agent_id}: {e}")
                    disconnected_sockets.append(connection) # Удаляем сокет при ошибке отправки

            # Удаляем отключенные сокеты после итерации
            for socket in disconnected_sockets:
                self.disconnect(socket, agent_id)

manager = ConnectionManager()

async def redis_websocket_listener(websocket: WebSocket, agent_id: str, r: redis.Redis):
    """Прослушивает канал Redis и пересылает сообщения в WebSocket."""
    output_channel = f"agent:{agent_id}:output"
    pubsub = None
    try:
        pubsub = r.pubsub()
        await pubsub.subscribe(output_channel)
        logger.info(f"WebSocket listener subscribed to Redis channel: {output_channel}")

        while True:
            # Проверяем соединение WebSocket перед ожиданием сообщения Redis
            try:
                # Быстрая проверка, жив ли сокет (может вызвать исключение при разрыве)
                await websocket.send_text("") # Отправляем пустую строку как пинг
                await asyncio.sleep(0.01) # Небольшая пауза
            except WebSocketDisconnect:
                 logger.info(f"WebSocket for {agent_id} disconnected (detected by ping). Stopping listener.")
                 break
            except Exception as ws_err:
                 logger.error(f"WebSocket error for {agent_id} during ping: {ws_err}. Stopping listener.")
                 break

            message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
            if message and message.get("type") == "message":
                logger.debug(f"Received message from Redis {output_channel} for WebSocket")
                try:
                    # Просто пересылаем необработанные данные JSON
                    await manager.broadcast_to_agent(agent_id, message["data"])
                except json.JSONDecodeError:
                    logger.error(f"Failed to decode JSON from {output_channel} for WebSocket: {message['data']}")
                except Exception as e:
                    logger.error(f"Error processing/sending message from {output_channel} via WebSocket: {e}", exc_info=True)

            await asyncio.sleep(0.1) # Небольшая пауза, чтобы не загружать ЦП

    except redis.exceptions.ConnectionError as e:
        logger.error(f"Redis connection error in WebSocket listener for {agent_id}: {e}")
        # Попытка отправить сообщение об ошибке клиенту
        try:
            await websocket.send_text(json.dumps({"error": "Redis connection error"}))
        except Exception: pass
    except asyncio.CancelledError:
        logger.info(f"Redis WebSocket listener task cancelled for {agent_id}.")
    except Exception as e:
        logger.error(f"Unexpected error in Redis WebSocket listener for {agent_id}: {e}", exc_info=True)
        try:
            await websocket.send_text(json.dumps({"error": "Internal listener error"}))
        except Exception: pass
    finally:
        logger.info(f"Cleaning up Redis WebSocket listener for {agent_id}")
        if pubsub:
            try:
                await pubsub.unsubscribe(output_channel)
                await pubsub.close()
                logger.info(f"Unsubscribed from Redis channel: {output_channel}")
            except Exception as clean_e:
                logger.error(f"Error closing Redis pubsub for WebSocket listener {agent_id}: {clean_e}")
        manager.disconnect(websocket, agent_id)


@router.websocket("/ws/agents/{agent_id}")
async def websocket_endpoint(websocket: WebSocket, agent_id: str, r: redis.Redis = Depends(get_redis)):
    """
    Эндпоинт WebSocket для получения обновлений от агента.
    Подключается к каналу вывода агента в Redis и пересылает сообщения.
    Также принимает сообщения от клиента и публикует их в канал ввода агента.
    """
    # Проверяем, существует ли конфигурация агента
    if not await r.exists(f"agent_config:{agent_id}"):
        logger.warning(f"WebSocket connection attempt for non-existent agent: {agent_id}")
        await websocket.close(code=fastapi_status.WS_1008_POLICY_VIOLATION)
        return

    await manager.connect(websocket, agent_id)
    listener_task = asyncio.create_task(redis_websocket_listener(websocket, agent_id, r))

    try:
        while True:
            data = await websocket.receive_text()
            logger.info(f"Received message via WebSocket for agent {agent_id}: {data[:100]}...")
            # Публикуем полученное сообщение в канал ввода агента
            input_channel = f"agent:{agent_id}:input"
            try:
                # Предполагаем, что клиент отправляет JSON, совместимый с ожидаемым форматом runner'а
                # TODO: Добавить валидацию входящего JSON
                await r.publish(input_channel, data)
            except redis.exceptions.ConnectionError as e:
                 logger.error(f"Redis connection error publishing WS message to {input_channel}: {e}")
                 await websocket.send_text(json.dumps({"error": "Failed to send message to agent (Redis connection)"}))
            except Exception as e:
                 logger.error(f"Error publishing WS message to {input_channel}: {e}", exc_info=True)
                 await websocket.send_text(json.dumps({"error": "Failed to send message to agent"}))

    except WebSocketDisconnect:
        logger.info(f"WebSocket client for agent {agent_id} disconnected.")
    except Exception as e:
        logger.error(f"Unexpected error in WebSocket endpoint for agent {agent_id}: {e}", exc_info=True)
    finally:
        logger.info(f"Cleaning up WebSocket connection for agent {agent_id}")
        listener_task.cancel() # Отменяем задачу прослушивания Redis
        try:
            await listener_task # Ожидаем завершения задачи
        except asyncio.CancelledError:
            pass # Ожидаемое исключение
        manager.disconnect(websocket, agent_id) # Удаляем соединение из менеджера

