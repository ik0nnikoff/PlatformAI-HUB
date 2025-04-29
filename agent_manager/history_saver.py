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
    db_session_factory: async_sessionmaker[AsyncSession],
    # --- ИЗМЕНЕНИЕ: Добавляем shutdown_event ---
    shutdown_event: asyncio.Event
    # --- КОНЕЦ ИЗМЕНЕНИЯ ---
):
    """
    Запускает, мониторит и перезапускает воркер сохранения истории при сбоях.
    Останавливается при установке shutdown_event.
    """
    logger.info("History Saver Supervisor started.")

    while not shutdown_event.is_set(): # Проверяем событие в начале каждой итерации
        redis_client = None
        worker_task = None
        # --- ИЗМЕНЕНИЕ: Создаем задачу ожидания события ---
        shutdown_waiter = asyncio.create_task(shutdown_event.wait(), name="ShutdownWaiter")
        # --- КОНЕЦ ИЗМЕНЕНИЯ ---
        should_restart = False # По умолчанию не перезапускаем, только если воркер упал

        try:
            # Создаем Redis клиент для воркера
            redis_client = redis.from_url(redis_url)
            await redis_client.ping()
            logger.info("Redis connection successful for History Saver.")

            # Запускаем воркер как задачу asyncio
            worker_task = asyncio.create_task(
                run_history_saver_worker(redis_client, db_session_factory),
                name="HistoryWorker"
            )
            logger.info(f"History Saver Worker task created (ID: {id(worker_task)}).")

            # --- ИЗМЕНЕНИЕ: Ожидаем либо воркера, либо события ---
            done, pending = await asyncio.wait(
                [worker_task, shutdown_waiter],
                return_when=asyncio.FIRST_COMPLETED
            )

            if shutdown_waiter in done:
                # Событие завершения установлено
                logger.info("Supervisor received shutdown signal via event.")
                if worker_task in pending:
                    logger.info("Cancelling worker task due to shutdown signal...")
                    worker_task.cancel()
                    # Даем шанс воркеру завершиться чисто
                    await asyncio.wait([worker_task], timeout=5.0)
                break # Выходим из цикла while

            if worker_task in done:
                # Воркер завершился первым
                try:
                    # Проверяем, не было ли исключения в воркере
                    worker_task.result()
                    # Если исключения не было, это неожиданно (кроме случая отмены)
                    if not worker_task.cancelled():
                         logger.warning(f"History Saver Worker task {id(worker_task)} finished without error and without being cancelled.")
                         should_restart = True # Перезапускаем, если завершился сам по себе
                    else:
                         # Воркер был отменен (возможно, из-за ошибки Redis или другой проблемы)
                         # Не перезапускаем, если он был отменен
                         logger.info("History Saver Worker task was cancelled.")

                except (redis.RedisError, Exception) as e:
                     # Воркер завершился с ошибкой
                     logger.error(f"History Saver Worker task failed with error: {e}", exc_info=isinstance(e, Exception) and not isinstance(e, redis.RedisError))
                     should_restart = True # Перезапускаем при ошибке

                # Если воркер завершился (неважно как), а событие УЖЕ установлено, выходим
                if shutdown_event.is_set():
                    logger.info("Worker finished, and shutdown event is set. Exiting supervisor loop.")
                    break # Выходим из цикла while
            # --- КОНЕЦ ИЗМЕНЕНИЯ ---

        except redis.RedisError as e:
            # Ошибка при создании клиента Redis
            logger.error(f"History Saver Supervisor: Failed to connect to Redis: {e}")
            should_restart = True # Попробуем перезапустить
        except asyncio.CancelledError:
            # Сам супервизор был отменен
            logger.info("History Saver Supervisor task itself was cancelled directly.")
            # --- ИЗМЕНЕНИЕ: Обрабатываем воркер перед выходом ---
            if worker_task and not worker_task.done():
                logger.info("Cancelling worker task due to supervisor cancellation...")
                worker_task.cancel()
                try:
                    # Ждем завершения воркера, чтобы поймать его исключение, если оно есть
                    await worker_task
                except asyncio.CancelledError:
                    logger.info("Worker task confirmed cancelled during supervisor shutdown.")
                except Exception as worker_err:
                    # Логируем ошибку воркера, чтобы она не потерялась
                    # Не считаем ConnectionError во время shutdown критической ошибкой супервизора
                    if isinstance(worker_err, redis.exceptions.ConnectionError):
                         logger.warning(f"Worker task encountered connection error during supervisor shutdown: {worker_err}")
                    else:
                         logger.error(f"Worker task failed unexpectedly during supervisor shutdown: {worker_err}", exc_info=True)
            elif worker_task and worker_task.done():
                 # Если воркер уже завершился (возможно, с ошибкой), проверим исключение, чтобы оно не потерялось
                 try:
                     worker_task.result() # Это вызовет исключение, если оно было
                 except asyncio.CancelledError:
                     pass # Ожидаемо, если воркер был отменен ранее
                 except Exception as worker_err:
                     if isinstance(worker_err, redis.exceptions.ConnectionError):
                          logger.warning(f"Worker task had already encountered connection error when supervisor was cancelled: {worker_err}")
                     else:
                          logger.error(f"Worker task had already failed when supervisor was cancelled: {worker_err}", exc_info=True)
            # --- КОНЕЦ ИЗМЕНЕНИЯ ---
            break # Выходим из цикла супервизора
        except Exception as e:
            # Неожиданная ошибка в цикле супервизора
            logger.error(f"Unexpected error in History Saver Supervisor main loop: {e}", exc_info=True)
            should_restart = True # Попробуем перезапустить
        finally:
            # Отменяем shutdown_waiter, если он еще работает
            if 'shutdown_waiter' in locals() and not shutdown_waiter.done(): # Проверяем существование переменной
                shutdown_waiter.cancel()
            # Очистка клиента Redis для текущей итерации
            if redis_client:
                try:
                    await redis_client.aclose()
                    logger.info("Redis client for History Saver iteration closed.")
                except Exception as close_err:
                    logger.error(f"Error closing Redis client for History Saver iteration: {close_err}")

        # --- Логика перезапуска ---
        if should_restart and not shutdown_event.is_set():
            logger.info(f"Restarting History Saver Worker in {RESTART_DELAY} seconds...")
            try:
                # Ожидаем перед перезапуском, но прерываемся, если событие установлено
                await asyncio.wait_for(shutdown_event.wait(), timeout=RESTART_DELAY)
                # Если wait_for завершился без TimeoutError, значит событие установлено
                logger.info("Shutdown event set during restart delay. Exiting loop.")
                break # Выходим из цикла
            except asyncio.TimeoutError:
                # Время ожидания истекло, событие не установлено, продолжаем перезапуск
                logger.debug("Restart delay finished.")
            except asyncio.CancelledError:
                logger.info("Supervisor cancelled during restart delay. Exiting loop.")
                break # Выходим из цикла
        elif shutdown_event.is_set():
             logger.info("Shutdown event is set after finally block. Exiting loop.")
             break # Дополнительная проверка на выход

    logger.info("History Saver Supervisor stopped.")
