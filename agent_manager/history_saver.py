import asyncio
import logging
import json
import os
from datetime import datetime, timezone
from typing import Optional, Dict, Any

import redis.asyncio as redis
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from pydantic import ValidationError

# --- ИЗМЕНЕНИЕ: Исправляем имя импорта ---
from .db import SessionLocal # Импортируем фабрику сессий (было AsyncSessionLocal)
# --- КОНЕЦ ИЗМЕНЕНИЯ ---
from . import crud
from .models import SenderType # Импортируем Enum

# --- Configuration ---
logger = logging.getLogger(__name__)
# Читаем имя очереди из переменной окружения или используем значение по умолчанию
REDIS_HISTORY_QUEUE_NAME = os.getenv("REDIS_HISTORY_QUEUE_NAME", "chat_history_queue")
# Читаем URL Redis из переменной окружения (необходимо для супервизора)
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
# Задержка перед перезапуском воркера в случае сбоя (в секундах)
RESTART_DELAY = 5

# --- Worker Logic ---

async def run_history_saver_worker(
    redis_client: redis.Redis,
    # --- ИЗМЕНЕНИЕ: Используем SessionLocal в type hint ---
    db_session_factory: async_sessionmaker[AsyncSession] # Тип фабрики остается прежним
    # --- КОНЕЦ ИЗМЕНЕНИЯ ---
):
    """
    Основной цикл воркера: читает сообщения из очереди Redis и сохраняет их в БД.
    """
    logger.info(f"History Saver Worker started. Listening to queue: '{REDIS_HISTORY_QUEUE_NAME}'")
    while True:
        try:
            # Блокирующее чтение из списка Redis (ожидает бесконечно)
            # BRPOP возвращает кортеж (имя_очереди, значение) или None по таймауту
            result = await redis_client.brpop([REDIS_HISTORY_QUEUE_NAME], timeout=0)

            if result:
                queue_name, raw_data = result
                logger.debug(f"Received message from queue '{queue_name.decode()}'")

                try:
                    data: Dict[str, Any] = json.loads(raw_data)

                    # Валидация и извлечение данных
                    agent_id = data.get("agent_id")
                    thread_id = data.get("thread_id")
                    sender_type_str = data.get("sender_type")
                    content = data.get("content")
                    channel = data.get("channel")
                    timestamp_iso = data.get("timestamp")

                    if not all([agent_id, thread_id, sender_type_str, content, timestamp_iso]):
                        logger.error(f"Invalid message format received (missing fields): {data}")
                        continue # Пропускаем некорректное сообщение

                    # Преобразование строки timestamp в datetime
                    try:
                        timestamp = datetime.fromisoformat(timestamp_iso)
                        # Убедимся, что timestamp имеет timezone (UTC)
                        if timestamp.tzinfo is None:
                             timestamp = timestamp.replace(tzinfo=timezone.utc)
                    except ValueError:
                        logger.error(f"Invalid timestamp format received: {timestamp_iso}")
                        continue

                    # Преобразование строки sender_type в Enum
                    try:
                        sender_type = SenderType(sender_type_str)
                    except ValueError:
                         logger.error(f"Invalid sender_type received: {sender_type_str}")
                         continue

                    # Сохранение в БД
                    try:
                        # Используем переданную фабрику (которая является SessionLocal)
                        async with db_session_factory() as session:
                            await crud.db_add_chat_message(
                                db=session,
                                agent_id=agent_id,
                                thread_id=thread_id,
                                sender_type=sender_type,
                                content=content,
                                channel=channel,
                                timestamp=timestamp
                            )
                            # Коммит происходит внутри db_add_chat_message
                            logger.debug(f"Successfully saved message for Agent={agent_id}, Thread={thread_id}")
                    except Exception as db_err:
                        # Ошибка уже логируется внутри db_add_chat_message
                        logger.error(f"Database error saving message for Agent={agent_id}, Thread={thread_id}: {db_err}")
                        # ВАЖНО: Решить, что делать с сообщением при ошибке БД.
                        # Варианты:
                        # 1. Просто пропустить (данные потеряны).
                        # 2. Попробовать положить обратно в очередь (риск бесконечного цикла при постоянной ошибке).
                        # 3. Положить в "dead-letter" очередь для ручного разбора.
                        # Пока просто пропускаем.

                except json.JSONDecodeError:
                    logger.error(f"Failed to decode JSON message: {raw_data[:200]}...")
                except Exception as e:
                    logger.error(f"Unexpected error processing message: {e}", exc_info=True)

            # Небольшая пауза, чтобы не загружать ЦП в случае непредвиденных быстрых циклов
            await asyncio.sleep(0.01)

        except redis.RedisError as e:
            logger.error(f"Redis connection error in history saver: {e}. Attempting reconnect...")
            # Супервизор должен обнаружить падение и перезапустить воркер
            raise # Поднимаем исключение, чтобы супервизор сработал
        except asyncio.CancelledError:
             logger.info("History Saver Worker received cancellation request.")
             break # Выходим из цикла при отмене
        except Exception as e:
            logger.error(f"Unexpected critical error in History Saver Worker loop: {e}", exc_info=True)
            # Поднимаем исключение, чтобы супервизор сработал
            raise

    logger.info("History Saver Worker stopped.")


# --- Supervisor Logic ---

async def supervise_history_saver(
    redis_url: str,
    # --- ИЗМЕНЕНИЕ: Используем SessionLocal в type hint ---
    db_session_factory: async_sessionmaker[AsyncSession] # Тип фабрики остается прежним
    # --- КОНЕЦ ИЗМЕНЕНИЯ ---
):
    """
    Запускает, мониторит и перезапускает воркер сохранения истории при сбоях.
    """
    logger.info("History Saver Supervisor started.")
    while True:
        redis_client = None
        worker_task = None
        try:
            # Создаем Redis клиент для воркера
            redis_client = redis.from_url(redis_url)
            await redis_client.ping() # Проверяем соединение
            logger.info("Redis connection successful for History Saver.")

            # Запускаем воркер как задачу asyncio
            worker_task = asyncio.create_task(
                run_history_saver_worker(redis_client, db_session_factory) # Передаем SessionLocal
            )
            logger.info(f"History Saver Worker task created (ID: {id(worker_task)}).")

            # Ожидаем завершения воркера
            await worker_task

            # Если воркер завершился сам (без CancelledError), это неожиданно
            logger.warning(f"History Saver Worker task {id(worker_task)} finished unexpectedly.")

        except redis.RedisError as e:
            logger.error(f"History Saver Supervisor: Failed to connect to Redis at {redis_url}: {e}")
        except asyncio.CancelledError:
            logger.info("History Saver Supervisor received cancellation request.")
            if worker_task and not worker_task.done():
                worker_task.cancel()
                try:
                    await worker_task # Даем воркеру шанс завершиться корректно
                except asyncio.CancelledError:
                    logger.info("History Saver Worker task cancelled successfully.")
            break # Выходим из цикла супервизора
        except Exception as e:
            logger.error(f"Unexpected error in History Saver Supervisor: {e}", exc_info=True)
            if worker_task and not worker_task.done():
                 logger.warning("Attempting to cancel running worker task due to supervisor error...")
                 worker_task.cancel()
                 try:
                     await worker_task
                 except asyncio.CancelledError:
                     pass # Ожидаемо

        finally:
            if redis_client:
                try:
                    await redis_client.aclose()
                    logger.info("Redis client for History Saver closed.")
                except Exception as close_err:
                    logger.error(f"Error closing Redis client for History Saver: {close_err}")

        # Если мы здесь, значит воркер упал или была ошибка Redis
        logger.info(f"Restarting History Saver Worker in {RESTART_DELAY} seconds...")
        await asyncio.sleep(RESTART_DELAY)

    logger.info("History Saver Supervisor stopped.")
