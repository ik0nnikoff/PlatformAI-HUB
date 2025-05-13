import asyncio
import json
import logging
from typing import Dict, List, Optional, Callable, Awaitable

from fastapi import WebSocket
import redis.asyncio as redis
import redis as redis_base # For exceptions

logger = logging.getLogger(__name__)

class ConnectionManager:
    def __init__(self):
        # agent_id -> thread_id -> list of WebSockets
        self.active_connections: Dict[str, Dict[str, List[WebSocket]]] = {}
        # agent_id -> asyncio.Task (for the central Redis listener)
        self.listener_tasks: Dict[str, asyncio.Task] = {}
        # agent_id -> redis.Redis client (associated with the listener)
        self.redis_clients_for_listeners: Dict[str, redis.Redis] = {}

    async def connect(self, websocket: WebSocket, agent_id: str, thread_id: str, r: redis.Redis, listener_factory: Callable[[str, redis.Redis, "ConnectionManager"], Awaitable[None]]):
        await websocket.accept()
        if agent_id not in self.active_connections:
            self.active_connections[agent_id] = {}
        if thread_id not in self.active_connections[agent_id]:
            self.active_connections[agent_id][thread_id] = []
        
        self.active_connections[agent_id][thread_id].append(websocket)
        logger.info(f"WebSocket connected: agent {agent_id}, thread {thread_id}. Total for thread: {len(self.active_connections[agent_id][thread_id])}")

        if agent_id not in self.listener_tasks or self.listener_tasks[agent_id].done():
            logger.info(f"No active listener for agent {agent_id}. Starting new one.")
            # Сохраняем клиент Redis, который будет использоваться слушателем
            self.redis_clients_for_listeners[agent_id] = r 
            # Передаем 'self' (ConnectionManager instance) в listener_factory
            task = asyncio.create_task(listener_factory(agent_id, r, self))
            self.listener_tasks[agent_id] = task
            logger.info(f"Central Redis listener task created for agent {agent_id}")
        else:
            logger.info(f"Listener already running for agent {agent_id}")


    async def disconnect(self, websocket: WebSocket, agent_id: str, thread_id: str):
        if agent_id in self.active_connections and thread_id in self.active_connections[agent_id]:
            try:
                self.active_connections[agent_id][thread_id].remove(websocket)
                logger.info(f"WebSocket disconnected: agent {agent_id}, thread {thread_id}. Remaining for thread: {len(self.active_connections[agent_id][thread_id])}")
                if not self.active_connections[agent_id][thread_id]:
                    del self.active_connections[agent_id][thread_id]
                    logger.info(f"Thread {thread_id} for agent {agent_id} has no more connections.")
                
                if not self.active_connections[agent_id]:
                    del self.active_connections[agent_id]
                    logger.info(f"Agent {agent_id} has no more active threads. Stopping listener.")
                    await self._stop_listener_for_agent(agent_id)

            except ValueError:
                logger.warning(f"WebSocket not found in active connections for agent {agent_id}, thread {thread_id} during disconnect.")
        else:
            logger.warning(f"Agent {agent_id} or thread {thread_id} not found in active_connections during disconnect.")

    async def _stop_listener_for_agent(self, agent_id: str):
        if agent_id in self.listener_tasks:
            task = self.listener_tasks.pop(agent_id)
            if not task.done():
                task.cancel()
                try:
                    await task # Wait for the task to acknowledge cancellation
                except asyncio.CancelledError:
                    logger.info(f"Central Redis listener for agent {agent_id} cancelled successfully.")
                except Exception as e:
                    logger.error(f"Error during listener task cancellation for agent {agent_id}: {e}", exc_info=True)
            else:
                logger.info(f"Listener task for agent {agent_id} was already done.")
            
            # Удаляем связанный клиент Redis
            if agent_id in self.redis_clients_for_listeners:
                del self.redis_clients_for_listeners[agent_id]
        else:
            logger.info(f"No listener task found to stop for agent {agent_id}.")

    async def send_to_thread(self, agent_id: str, thread_id: str, message: str):
        if agent_id in self.active_connections and thread_id in self.active_connections[agent_id]:
            disconnected_sockets = []
            for connection in self.active_connections[agent_id][thread_id]:
                try:
                    await connection.send_text(message)
                except Exception as e: # WebSocketException or ConnectionClosed etc.
                    logger.warning(f"Failed to send message to a WebSocket for agent {agent_id}, thread {thread_id}. Marking for removal. Error: {e}")
                    disconnected_sockets.append(connection)
            
            # Удаляем "мертвые" сокеты
            for sock in disconnected_sockets:
                await self.disconnect(sock, agent_id, thread_id) # disconnect handles removal and listener stop if needed
        else:
            logger.debug(f"No active WebSocket connections for agent {agent_id}, thread {thread_id} to send message.")

    async def broadcast_to_agent(self, agent_id: str, message: str):
        if agent_id in self.active_connections:
            for thread_id in list(self.active_connections[agent_id].keys()): # Iterate over a copy of keys
                await self.send_to_thread(agent_id, thread_id, message)

    async def notify_listener_stopped(self, agent_id: str):
        """
        Called by the listener itself if it stops unexpectedly or finishes.
        Ensures resources are cleaned up if the manager didn't initiate the stop.
        """
        logger.info(f"Listener for agent {agent_id} reported it has stopped.")
        if agent_id in self.listener_tasks:
            # Task might already be removed if _stop_listener_for_agent was called
            # but this ensures cleanup if listener stops on its own.
            self.listener_tasks.pop(agent_id, None)
        if agent_id in self.redis_clients_for_listeners:
            del self.redis_clients_for_listeners[agent_id]
        
        # Если слушатель остановился, а соединения еще есть, это может быть проблемой.
        # Однако, основная логика остановки слушателя - отсутствие соединений.
        # Если слушатель упал, новые соединения создадут нового слушателя.


async def central_redis_listener(agent_id: str, r: redis.Redis, conn_manager: ConnectionManager):
    output_channel = f"agent:{agent_id}:output"
    pubsub = None
    # Каналы, сообщения из которых предназначены для WebSocket клиентов
    expected_channels_for_websocket = ["websocket", "dashboard", "web"] 

    logger.info(f"[Central Listener {agent_id}] Subscribing to {output_channel}")
    try:
        pubsub = r.pubsub()
        await pubsub.subscribe(output_channel)

        while True:
            # Проверка на отмену задачи извне (например, ConnectionManager останавливает слушатель)
            await asyncio.sleep(0.001) # Дает возможность циклу событий обработать отмену

            message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
            if message and message.get("type") == "message":
                raw_data = message["data"]
                try:
                    data = json.loads(raw_data)
                    response_channel = data.get("channel")
                    response_thread_id = data.get("thread_id")

                    if response_channel in expected_channels_for_websocket and response_thread_id:
                        await conn_manager.send_to_thread(agent_id, response_thread_id, raw_data.decode('utf-8') if isinstance(raw_data, bytes) else raw_data)
                    
                except json.JSONDecodeError:
                    logger.error(f"[Central Listener {agent_id}] Failed to decode JSON: {raw_data}")
                except Exception as e:
                    logger.error(f"[Central Listener {agent_id}] Error processing message: {e}", exc_info=True)
            
            # Если нет активных соединений для этого agent_id, слушатель должен завершиться.
            # ConnectionManager._stop_listener_for_agent отменит эту задачу.
            # Дополнительная проверка здесь может быть избыточной, если cancellation работает надежно.
            if not conn_manager.active_connections.get(agent_id):
                logger.info(f"[Central Listener {agent_id}] No active connections found. Stopping listener.")
                break


    except redis_base.exceptions.ConnectionError as e:
        logger.error(f"[Central Listener {agent_id}] Redis connection error: {e}")
    except asyncio.CancelledError:
        logger.info(f"[Central Listener {agent_id}] Task cancelled.")
    except Exception as e:
        logger.error(f"[Central Listener {agent_id}] Unexpected error: {e}", exc_info=True)
    finally:
        logger.info(f"[Central Listener {agent_id}] Cleaning up...")
        if pubsub:
            try:
                await pubsub.unsubscribe(output_channel)
                if hasattr(pubsub, 'aclose'):
                    await pubsub.aclose()
                else:
                    await pubsub.close() 
                logger.info(f"[Central Listener {agent_id}] Unsubscribed from {output_channel} and closed pubsub.")
            except Exception as clean_e:
                logger.error(f"[Central Listener {agent_id}] Error closing pubsub: {clean_e}")
        
        # Уведомляем ConnectionManager, что слушатель завершился
        await conn_manager.notify_listener_stopped(agent_id)
