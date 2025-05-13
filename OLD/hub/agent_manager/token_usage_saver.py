import asyncio
import logging
import json
import os
from datetime import datetime, timezone # Убедимся, что timezone импортирован
from typing import Dict, Any, Optional

import redis.asyncio as redis
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.exc import SQLAlchemyError

from .db import SessionLocal # Фабрика сессий БД
from . import crud
from .models import SenderType # Нужен для db_get_chat_message_by_interaction_id

# --- Configuration ---
logger = logging.getLogger(__name__)
REDIS_TOKEN_USAGE_QUEUE_NAME = os.getenv("REDIS_TOKEN_USAGE_QUEUE_NAME", "token_usage_queue")
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
RESTART_DELAY = 5
# Задержка перед повторной попыткой найти сообщение чата, если оно еще не сохранено
CHAT_MESSAGE_FIND_RETRY_DELAY = int(os.getenv("TOKEN_SAVER_RETRY_DELAY", "10")) # 10 секунд
MAX_CHAT_MESSAGE_FIND_ATTEMPTS = int(os.getenv("TOKEN_SAVER_MAX_RETRIES", "6")) # Максимум ~1 минута попыток

# --- Worker Logic ---

async def run_token_usage_saver_worker(
    redis_client: redis.Redis,
    db_session_factory: async_sessionmaker[AsyncSession]
):
    """
    Основной цикл воркера: читает данные об использовании токенов из очереди Redis,
    находит связанное сообщение чата и сохраняет лог в БД.
    """
    logger.info(f"Token Usage Saver Worker started. Listening to queue: '{REDIS_TOKEN_USAGE_QUEUE_NAME}'")
    while True:
        try:
            # Используем blpop для атомарного извлечения и удаления, если элементов несколько
            # brpop возвращает кортеж (имя_очереди, значение)
            result = await redis_client.brpop([REDIS_TOKEN_USAGE_QUEUE_NAME], timeout=1) # Таймаут 1с для проверки флага running

            if result:
                queue_name_bytes, raw_data_bytes = result
                queue_name = queue_name_bytes.decode('utf-8') if isinstance(queue_name_bytes, bytes) else queue_name_bytes
                raw_data = raw_data_bytes.decode('utf-8') if isinstance(raw_data_bytes, bytes) else raw_data_bytes
                
                logger.debug(f"Received token usage data from queue '{queue_name}'")

                try:
                    data: Dict[str, Any] = json.loads(raw_data)
                    interaction_id = data.get("interaction_id")
                    agent_id = data.get("agent_id")
                    thread_id = data.get("thread_id") # Для логирования

                    if not all([interaction_id, agent_id]):
                        logger.error(f"Invalid token usage data format (missing interaction_id or agent_id): {data}")
                        continue

                    # Пытаемся найти ID сообщения чата по interaction_id
                    chat_message_id: Optional[int] = None
                    for attempt in range(MAX_CHAT_MESSAGE_FIND_ATTEMPTS):
                        async with db_session_factory() as session:
                            # Ищем сообщение от агента, так как использование токенов связано с его ответом
                            chat_message = await crud.db_get_chat_message_by_interaction_id(
                                db=session,
                                agent_id=agent_id,
                                interaction_id=interaction_id,
                                sender_type=SenderType.AGENT # Явно указываем, что ищем сообщение агента
                            )
                        if chat_message:
                            chat_message_id = chat_message.id
                            logger.info(f"Found ChatMessage ID {chat_message_id} for InteractionID {interaction_id} (Agent: {agent_id}, Thread: {thread_id})")
                            break
                        else:
                            logger.warning(f"ChatMessage not yet found for InteractionID {interaction_id} (Agent: {agent_id}, Thread: {thread_id}). Attempt {attempt + 1}/{MAX_CHAT_MESSAGE_FIND_ATTEMPTS}. Retrying in {CHAT_MESSAGE_FIND_RETRY_DELAY}s...")
                            
                            # Проверяем, не остановлен ли воркер, перед длительным ожиданием
                            # Простой способ проверить активность Redis и косвенно - воркера
                            # (ping может быть не лучшим способом, т.к. воркер может быть отменен, а Redis жив)
                            # Лучше проверять внешний флаг или событие, если оно есть.
                            # В данном контексте, если супервизор отменит задачу, asyncio.sleep прервется.
                            await asyncio.sleep(CHAT_MESSAGE_FIND_RETRY_DELAY)
                    
                    if not chat_message_id:
                        logger.error(f"Failed to find ChatMessage ID for InteractionID {interaction_id} (Agent: {agent_id}, Thread: {thread_id}) after {MAX_CHAT_MESSAGE_FIND_ATTEMPTS} attempts. Skipping token log.")
                        # TODO: Рассмотреть dead-letter queue для таких случаев
                        continue

                    # Подготовка данных для сохранения в TokenUsageLogDB
                    timestamp_str = data.get("timestamp")
                    try:
                        parsed_timestamp = datetime.fromisoformat(timestamp_str) if timestamp_str else datetime.now(timezone.utc)
                        if parsed_timestamp.tzinfo is None: # Убедимся, что есть таймзона
                            parsed_timestamp = parsed_timestamp.replace(tzinfo=timezone.utc)
                    except ValueError:
                        logger.warning(f"Invalid timestamp format '{timestamp_str}' for InteractionID {interaction_id}. Using current time.")
                        parsed_timestamp = datetime.now(timezone.utc)


                    token_log_payload = {
                        "agent_id": agent_id,
                        "message_id": chat_message_id,
                        "interaction_id": interaction_id,
                        "call_type": data.get("call_type", "unknown"),
                        "model_id": data.get("model_id", "unknown"),
                        "prompt_tokens": data.get("prompt_tokens", 0),
                        "completion_tokens": data.get("completion_tokens", 0),
                        "total_tokens": data.get("total_tokens", 0),
                        "timestamp": parsed_timestamp
                    }

                    async with db_session_factory() as session:
                        await crud.db_add_token_usage_log(session, token_log_payload)
                    logger.debug(f"Successfully saved token usage log for InteractionID {interaction_id}")

                except json.JSONDecodeError:
                    logger.error(f"Failed to decode JSON token usage data: {raw_data[:200]}...")
                except SQLAlchemyError as db_err: # Ловим ошибки SQLAlchemy отдельно
                    logger.error(f"Database error saving token usage log for InteractionID {data.get('interaction_id')}: {db_err}", exc_info=True)
                    # Решение о повторной попытке или DLQ
                except Exception as e:
                    logger.error(f"Unexpected error processing token usage data: {e}", exc_info=True)

            # await asyncio.sleep(0.01) # Небольшая пауза, если brpop с таймаутом

        except redis.RedisError as e:
            logger.error(f"Redis connection error in token usage saver: {e}. Attempting reconnect...")
            raise # Позволяем супервизору обработать
        except asyncio.CancelledError:
            logger.info("Token Usage Saver Worker received cancellation request.")
            break
        except Exception as e:
            logger.error(f"Unexpected critical error in Token Usage Saver Worker loop: {e}", exc_info=True)
            raise # Позволяем супервизору обработать

    logger.info("Token Usage Saver Worker stopped.")


# --- Supervisor Logic ---

async def supervise_token_usage_saver(
    redis_url: str,
    db_session_factory: async_sessionmaker[AsyncSession],
    shutdown_event: asyncio.Event
):
    """
    Запускает, мониторит и перезапускает воркер сохранения использования токенов.
    """
    logger.info("Token Usage Saver Supervisor started.")
    while not shutdown_event.is_set():
        redis_client = None
        worker_task = None
        # Создаем задачу ожидания события завершения для текущей итерации супервизора
        # Это позволяет прервать ожидание перезапуска, если пришел сигнал shutdown
        shutdown_waiter_task = asyncio.create_task(shutdown_event.wait(), name="TokenSaverShutdownWaiter")
        should_restart_worker = False

        try:
            redis_client = redis.from_url(redis_url)
            await redis_client.ping()
            logger.info("Redis connection successful for Token Usage Saver.")

            worker_task = asyncio.create_task(
                run_token_usage_saver_worker(redis_client, db_session_factory),
                name="TokenUsageWorker"
            )
            logger.info(f"Token Usage Saver Worker task created (ID: {id(worker_task)}).")

            # Ожидаем завершения либо воркера, либо сигнала shutdown
            done, pending = await asyncio.wait(
                [worker_task, shutdown_waiter_task],
                return_when=asyncio.FIRST_COMPLETED
            )

            if shutdown_waiter_task in done:
                logger.info("Token Usage Supervisor received shutdown signal via event.")
                if worker_task in pending: # Если воркер еще работает
                    logger.info("Cancelling token usage worker task due to shutdown signal...")
                    worker_task.cancel()
                    # Даем воркеру шанс завершиться корректно
                    await asyncio.wait([worker_task], timeout=5.0) 
                break # Выходим из основного цикла супервизора

            if worker_task in done:
                # Воркер завершился первым
                try:
                    worker_task.result() # Проверяем, не было ли исключения
                    # Если воркер завершился без исключения и не был отменен, это неожиданно
                    if not worker_task.cancelled():
                        logger.warning(f"Token Usage Worker task {id(worker_task)} finished unexpectedly without error.")
                        should_restart_worker = True 
                except asyncio.CancelledError:
                    logger.info(f"Token Usage Worker task {id(worker_task)} was cancelled.")
                    # Если отмена была из-за shutdown_event, то should_restart_worker останется False
                except (redis.RedisError, Exception) as e:
                    logger.error(f"Token Usage Worker task {id(worker_task)} failed: {e}", exc_info=isinstance(e, Exception) and not isinstance(e, redis.RedisError))
                    should_restart_worker = True
                
                # Если воркер завершился, а событие shutdown уже установлено, выходим
                if shutdown_event.is_set():
                    logger.info("Token worker finished, and shutdown event is set. Exiting supervisor loop.")
                    break
        
        except redis.RedisError as e:
            logger.error(f"Token Usage Supervisor: Failed to connect to Redis: {e}")
            should_restart_worker = True # Попытаемся перезапустить после паузы
        except asyncio.CancelledError:
            logger.info("Token Usage Supervisor task itself was cancelled.")
            if worker_task and not worker_task.done(): # Если супервизор отменен, отменяем и воркер
                worker_task.cancel()
                try: 
                    await worker_task # Ждем завершения воркера
                except asyncio.CancelledError: pass # Ожидаемо
                except Exception as wt_e: logger.error(f"Token worker task failed during supervisor cancellation: {wt_e}", exc_info=True)
            break # Выходим из цикла супервизора
        except Exception as e:
            logger.error(f"Unexpected error in Token Usage Supervisor main loop: {e}", exc_info=True)
            should_restart_worker = True # Попытаемся перезапустить
        finally:
            # Отменяем задачу ожидания shutdown_event, если она еще не завершена
            if 'shutdown_waiter_task' in locals() and not shutdown_waiter_task.done():
                shutdown_waiter_task.cancel()
            
            if redis_client:
                try:
                    await redis_client.aclose()
                    logger.info("Redis client for Token Usage Saver iteration closed.")
                except Exception as close_err:
                    logger.error(f"Error closing Redis client for Token Usage Saver iteration: {close_err}")

        if should_restart_worker and not shutdown_event.is_set():
            logger.info(f"Restarting Token Usage Saver Worker in {RESTART_DELAY} seconds...")
            try:
                # Ожидаем RESTART_DELAY секунд, но прерываемся, если пришел сигнал shutdown
                await asyncio.wait_for(shutdown_event.wait(), timeout=RESTART_DELAY)
                # Если wait_for завершился без TimeoutError, значит shutdown_event установлен
                logger.info("Shutdown event set during restart delay for Token Usage Saver. Exiting loop.")
                break 
            except asyncio.TimeoutError:
                # Таймаут истек, shutdown_event не установлен, продолжаем перезапуск
                logger.debug("Token Usage Saver restart delay finished.")
            except asyncio.CancelledError: # Если сам супервизор отменен во время ожидания
                logger.info("Token Usage Supervisor cancelled during restart delay. Exiting loop.")
                break
        elif shutdown_event.is_set():
            logger.info("Shutdown event is set for Token Usage Supervisor after finally block. Exiting loop.")
            break
            
    logger.info("Token Usage Saver Supervisor stopped.")

# Пример того, как это может быть вызвано в main.py вашего agent_manager
# async def main_manager_startup():
#     # ... другая инициализация ...
#     
#     # Предполагается, что SessionLocal - это ваша async_sessionmaker
#     # from .db import SessionLocal as db_session_factory 
#     db_session_factory = SessionLocal 
#
#     shutdown_event_token_saver = asyncio.Event()
#     
#     # Сохраняем задачи, чтобы можно было их отменить при shutdown
#     # global background_tasks
#     # background_tasks = {}
#
#     token_saver_supervisor_task = asyncio.create_task(
#         supervise_token_usage_saver(REDIS_URL, db_session_factory, shutdown_event_token_saver),
#         name="TokenUsageSupervisor"
#     )
#     # background_tasks["token_saver"] = (token_saver_supervisor_task, shutdown_event_token_saver)
#     logger.info("Token Usage Saver supervisor task created.")
#
#     # ...
#
# async def main_manager_shutdown():
#     # ...
#     # if "token_saver" in background_tasks:
#     #     task, event = background_tasks["token_saver"]
#     #     logger.info("Requesting Token Usage Saver supervisor to shut down...")
#     #     event.set()
#     #     try:
#     #         await asyncio.wait_for(task, timeout=10.0) # Даем время на завершение
#     #         logger.info("Token Usage Saver supervisor shut down successfully.")
#     #     except asyncio.TimeoutError:
#     #         logger.warning("Token Usage Saver supervisor did not shut down in time. Cancelling task.")
#     #         task.cancel()
#     #     except Exception as e:
#     #         logger.error(f"Error during Token Usage Saver supervisor shutdown: {e}")
#     # ...
