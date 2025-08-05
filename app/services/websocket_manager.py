"""
WebSocket Manager для управления соединениями и Redis слушателями.

Модуль содержит менеджер WebSocket соединений с централизованными
Redis слушателями для агентов.
"""

import asyncio
import json
import logging
from dataclasses import dataclass
from typing import Dict, List, Callable, Awaitable

from fastapi import WebSocket
import redis.asyncio as redis
import redis as redis_base

logger = logging.getLogger(__name__)


@dataclass
class ConnectionConfig:
    """Конфигурация для подключения WebSocket."""

    websocket: WebSocket
    agent_id: str
    thread_id: str
    redis_client: redis.Redis
    listener_factory: Callable[[str, redis.Redis, "ConnectionManager"], Awaitable[None]]


class ConnectionManager:
    """Менеджер WebSocket соединений с централизованными Redis слушателями."""

    def __init__(self):
        """Инициализировать ConnectionManager."""
        # agent_id -> thread_id -> list of WebSockets
        self.active_connections: Dict[str, Dict[str, List[WebSocket]]] = {}
        # agent_id -> asyncio.Task (for the central Redis listener)
        self.listener_tasks: Dict[str, asyncio.Task] = {}
        # agent_id -> redis.Redis client (associated with the listener)
        self.redis_clients_for_listeners: Dict[str, redis.Redis] = {}

    async def connect(self, config: ConnectionConfig):
        """
        Подключить WebSocket клиента.

        Args:
            config: Конфигурация подключения
        """
        await config.websocket.accept()
        self._add_connection_to_pool(config.websocket, config.agent_id, config.thread_id)
        logger.info(
            "WebSocket connected: agent %s, thread %s. Total for thread: %s",
            config.agent_id,
            config.thread_id,
            len(self.active_connections[config.agent_id][config.thread_id]),
        )

        await self._ensure_listener_running(
            config.agent_id, config.redis_client, config.listener_factory
        )

    def _add_connection_to_pool(self, websocket: WebSocket, agent_id: str, thread_id: str):
        """Добавить соединение в пул активных соединений."""
        if agent_id not in self.active_connections:
            self.active_connections[agent_id] = {}
        if thread_id not in self.active_connections[agent_id]:
            self.active_connections[agent_id][thread_id] = []

        self.active_connections[agent_id][thread_id].append(websocket)

    async def _ensure_listener_running(
        self,
        agent_id: str,
        r: redis.Redis,
        listener_factory: Callable[[str, redis.Redis, "ConnectionManager"], Awaitable[None]],
    ):
        """Убедиться, что слушатель Redis запущен для агента."""
        if agent_id not in self.listener_tasks or self.listener_tasks[agent_id].done():
            logger.info("No active listener for agent %s. Starting new one.", agent_id)
            # Сохраняем клиент Redis, который будет использоваться слушателем
            self.redis_clients_for_listeners[agent_id] = r
            # Передаем 'self' (ConnectionManager instance) в listener_factory
            task = asyncio.create_task(listener_factory(agent_id, r, self))
            self.listener_tasks[agent_id] = task
            logger.info("Central Redis listener task created for agent %s", agent_id)
        else:
            logger.info("Listener already running for agent %s", agent_id)

    async def disconnect(self, websocket: WebSocket, agent_id: str, thread_id: str):
        """
        Отключить WebSocket клиента.

        Args:
            websocket: WebSocket соединение
            agent_id: Идентификатор агента
            thread_id: Идентификатор потока
        """
        if not self._is_connection_valid(agent_id, thread_id):
            logger.warning(
                "Agent %s or thread %s not found in active_connections during disconnect.",
                agent_id,
                thread_id,
            )
            return

        await self._remove_connection_from_pool(websocket, agent_id, thread_id)

    def _is_connection_valid(self, agent_id: str, thread_id: str) -> bool:
        """Проверить, что соединение существует в пуле."""
        return (
            agent_id in self.active_connections and thread_id in self.active_connections[agent_id]
        )

    async def _remove_connection_from_pool(
        self, websocket: WebSocket, agent_id: str, thread_id: str
    ):
        """Удалить соединение из пула и очистить ресурсы при необходимости."""
        try:
            self.active_connections[agent_id][thread_id].remove(websocket)
            remaining_count = len(self.active_connections[agent_id][thread_id])
            logger.info(
                "WebSocket disconnected: agent %s, thread %s. Remaining for thread: %s",
                agent_id,
                thread_id,
                remaining_count,
            )

            await self._cleanup_empty_pools(agent_id, thread_id)

        except ValueError:
            logger.warning(
                "WebSocket not found in active connections for agent %s, thread %s during disconnect.",
                agent_id,
                thread_id,
            )

    async def _cleanup_empty_pools(self, agent_id: str, thread_id: str):
        """Очистить пустые пулы соединений и остановить слушатель при необходимости."""
        if not self.active_connections[agent_id][thread_id]:
            del self.active_connections[agent_id][thread_id]
            logger.info("Thread %s for agent %s has no more connections.", thread_id, agent_id)

        if not self.active_connections[agent_id]:
            del self.active_connections[agent_id]
            logger.info("Agent %s has no more active threads. Stopping listener.", agent_id)
            await self._stop_listener_for_agent(agent_id)

    async def _stop_listener_for_agent(self, agent_id: str):
        """
        Остановить слушатель Redis для агента.

        Args:
            agent_id: Идентификатор агента
        """
        if agent_id not in self.listener_tasks:
            logger.info("No listener task found to stop for agent %s.", agent_id)
            return

        task = self.listener_tasks.pop(agent_id)
        await self._cancel_listener_task(task, agent_id)
        self._cleanup_redis_client(agent_id)

    async def _cancel_listener_task(self, task: asyncio.Task, agent_id: str):
        """Отменить задачу слушателя."""
        if not task.done():
            task.cancel()
            try:
                await task  # Wait for the task to acknowledge cancellation
            except asyncio.CancelledError:
                logger.info("Central Redis listener for agent %s cancelled successfully.", agent_id)
            except (OSError, ConnectionError, redis_base.exceptions.RedisError) as e:
                logger.error(
                    "Error during listener task cancellation for agent %s: %s",
                    agent_id,
                    e,
                    exc_info=True,
                )
        else:
            logger.info("Listener task for agent %s was already done.", agent_id)

    def _cleanup_redis_client(self, agent_id: str):
        """Очистить Redis клиент для агента."""
        if agent_id in self.redis_clients_for_listeners:
            del self.redis_clients_for_listeners[agent_id]

    async def send_to_thread(self, agent_id: str, thread_id: str, message: str):
        """
        Отправить сообщение всем соединениям в потоке.

        Args:
            agent_id: Идентификатор агента
            thread_id: Идентификатор потока
            message: Сообщение для отправки
        """
        if not self._has_active_connections(agent_id, thread_id):
            logger.debug(
                "No active WebSocket connections for agent %s, thread %s to send message.",
                agent_id,
                thread_id,
            )
            return

        await self._broadcast_to_connections(agent_id, thread_id, message)

    def _has_active_connections(self, agent_id: str, thread_id: str) -> bool:
        """Проверить наличие активных соединений для агента и потока."""
        return (
            agent_id in self.active_connections and thread_id in self.active_connections[agent_id]
        )

    async def _broadcast_to_connections(self, agent_id: str, thread_id: str, message: str):
        """Отправить сообщение всем соединениям и обработать отключенные."""
        disconnected_sockets = []
        for connection in self.active_connections[agent_id][thread_id]:
            try:
                await connection.send_text(message)
            except (OSError, ConnectionError) as e:  # WebSocket connection errors
                logger.warning(
                    "Failed to send message to a WebSocket for agent %s, thread %s. "
                    "Marking for removal. Error: %s",
                    agent_id,
                    thread_id,
                    e,
                )
                disconnected_sockets.append(connection)

        # Удаляем "мертвые" сокеты
        for sock in disconnected_sockets:
            await self.disconnect(sock, agent_id, thread_id)

    async def broadcast_to_agent(self, agent_id: str, message: str):
        """
        Отправить сообщение всем потокам агента.

        Args:
            agent_id: Идентификатор агента
            message: Сообщение для отправки
        """
        if agent_id in self.active_connections:
            for thread_id in list(self.active_connections[agent_id].keys()):
                await self.send_to_thread(agent_id, thread_id, message)

    async def notify_listener_stopped(self, agent_id: str):
        """
        Уведомить о том, что слушатель остановился.

        Вызывается самим слушателем, если он останавливается неожиданно или завершается.
        Обеспечивает очистку ресурсов, если менеджер не инициировал остановку.

        Args:
            agent_id: Идентификатор агента
        """
        logger.info("Listener for agent %s reported it has stopped.", agent_id)

        # Task might already be removed if _stop_listener_for_agent was called
        # but this ensures cleanup if listener stops on its own.
        self.listener_tasks.pop(agent_id, None)
        self._cleanup_redis_client(agent_id)

        # Если слушатель остановился, а соединения еще есть, это может быть проблемой.
        # Однако, основная логика остановки слушателя - отсутствие соединений.
        # Если слушатель упал, новые соединения создадут нового слушателя.


async def central_redis_listener(agent_id: str, r: redis.Redis, conn_manager: ConnectionManager):
    """
    Централизованный слушатель Redis для агента.

    Args:
        agent_id: Идентификатор агента
        r: Redis клиент
        conn_manager: Менеджер соединений
    """
    output_channel = f"agent:{agent_id}:output"
    pubsub = None
    expected_channels = ["websocket", "dashboard", "web"]

    logger.info("[Central Listener %s] Subscribing to %s", agent_id, output_channel)

    try:
        pubsub = await _setup_redis_subscription(r, output_channel)
        await _process_redis_messages(agent_id, pubsub, conn_manager, expected_channels)

    except (redis_base.exceptions.ConnectionError, OSError) as e:
        logger.error("[Central Listener %s] Redis connection error: %s", agent_id, e)
    except asyncio.CancelledError:
        logger.info("[Central Listener %s] Task cancelled.", agent_id)
    except (json.JSONDecodeError, ValueError) as e:
        logger.error("[Central Listener %s] Data processing error: %s", agent_id, e, exc_info=True)
    finally:
        await _cleanup_redis_resources(agent_id, pubsub, output_channel, conn_manager)


async def _setup_redis_subscription(r: redis.Redis, output_channel: str) -> redis.client.PubSub:
    """Настроить подписку Redis."""
    pubsub = r.pubsub()
    await pubsub.subscribe(output_channel)
    return pubsub


async def _process_redis_messages(
    agent_id: str,
    pubsub: redis.client.PubSub,
    conn_manager: ConnectionManager,
    expected_channels: List[str],
):
    """Обработать сообщения из Redis."""
    while True:
        # Проверка на отмену задачи извне
        await asyncio.sleep(0.001)

        message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
        if message and message.get("type") == "message":
            await _handle_redis_message(agent_id, message, conn_manager, expected_channels)

        # Проверить наличие активных соединений
        if not conn_manager.active_connections.get(agent_id):
            logger.info(
                "[Central Listener %s] No active connections found. Stopping listener.", agent_id
            )
            break


async def _handle_redis_message(
    agent_id: str, message: dict, conn_manager: ConnectionManager, expected_channels: List[str]
):
    """Обработать одно сообщение из Redis."""
    raw_data = message["data"]
    try:
        data = json.loads(raw_data)
        response_channel = data.get("channel")
        response_thread_id = data.get("thread_id")

        if _should_send_to_websocket(response_channel, response_thread_id, expected_channels):
            decoded_data = _decode_message_data(raw_data)
            await conn_manager.send_to_thread(agent_id, response_thread_id, decoded_data)

    except json.JSONDecodeError:
        logger.error("[Central Listener %s] Failed to decode JSON: %s", agent_id, raw_data)
    except Exception as e:
        logger.error(
            "[Central Listener %s] Error processing message: %s", agent_id, e, exc_info=True
        )


def _should_send_to_websocket(
    response_channel: str, response_thread_id: str, expected_channels: List[str]
) -> bool:
    """Определить, следует ли отправить сообщение в WebSocket."""
    return response_channel in expected_channels and response_thread_id


def _decode_message_data(raw_data) -> str:
    """Декодировать данные сообщения."""
    return raw_data.decode("utf-8") if isinstance(raw_data, bytes) else raw_data


async def _cleanup_redis_resources(
    agent_id: str, pubsub: redis.client.PubSub, output_channel: str, conn_manager: ConnectionManager
):
    """Очистить ресурсы Redis."""
    logger.info("[Central Listener %s] Cleaning up...", agent_id)

    if pubsub:
        await _close_pubsub_connection(agent_id, pubsub, output_channel)

    # Уведомляем ConnectionManager, что слушатель завершился
    await conn_manager.notify_listener_stopped(agent_id)


async def _close_pubsub_connection(agent_id: str, pubsub: redis.client.PubSub, output_channel: str):
    """Закрыть соединение pubsub."""
    try:
        await pubsub.unsubscribe(output_channel)
        if hasattr(pubsub, "aclose"):
            await pubsub.aclose()
        else:
            await pubsub.close()
        logger.info(
            "[Central Listener %s] Unsubscribed from %s and closed pubsub.",
            agent_id,
            output_channel,
        )
    except Exception as clean_e:
        logger.error("[Central Listener %s] Error closing pubsub: %s", agent_id, clean_e)
