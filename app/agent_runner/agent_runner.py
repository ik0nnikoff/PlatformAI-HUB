import os
import asyncio
import logging
import json
import uuid
from typing import Dict, Optional, Any, List, Tuple

import httpx
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from langchain_core.messages import BaseMessage, HumanMessage, AIMessage
from datetime import datetime, timezone

from redis import exceptions as redis_exceptions

from app.agent_runner.langgraph.factory import create_agent_app # Updated import
from app.agent_runner.langgraph.models import TokenUsageData # Updated import
from app.db.alchemy_models import ChatMessageDB, SenderType
from app.db.crud.chat_crud import db_get_recent_chat_history
from app.core.config import settings
from app.core.base.service_component import ServiceComponentBase # Added import

# --- Helper Functions (some might become methods or stay as utilities) ---

async def fetch_config_static(config_url: str, logger: logging.Logger) -> Optional[Dict]:
    """Fetches agent configuration from the management service using httpx."""
    logger.info(f"Fetching configuration from: {config_url}")
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.get(config_url)
            response.raise_for_status()  # Raises HTTPStatusError for 4xx/5xx responses
            config_data = response.json()
        logger.info("Configuration fetched successfully.")
        return config_data
    except httpx.TimeoutException:
        logger.error(f"Timeout fetching configuration from {config_url}.")
    except httpx.RequestError as e:
        logger.error(f"Failed to fetch configuration from {config_url} due to request error: {e}")
    except json.JSONDecodeError as e:
        logger.error(f"Failed to decode JSON from configuration response ({config_url}): {e}")
    except Exception as e: # Catch-all for unexpected errors
        logger.error(f"Unexpected error fetching configuration from {config_url}: {e}", exc_info=True)
    return None


def convert_db_history_to_langchain(db_messages: List[ChatMessageDB], logger: logging.Logger) -> List[BaseMessage]:
    """Converts messages from DB format (ChatMessageDB) to LangChain BaseMessage list."""
    converted = []
    if not ChatMessageDB or not SenderType:
        logger.error("ChatMessageDB model or SenderType Enum not available for history conversion.")
        return []

    for msg in db_messages:
        if not isinstance(msg, ChatMessageDB):
             logger.warning(f"Skipping message conversion due to unexpected type: {type(msg)}")
             continue
        
        if msg.sender_type == SenderType.USER:
            converted.append(HumanMessage(content=msg.content))
        elif msg.sender_type == SenderType.AGENT:
            converted.append(AIMessage(content=msg.content))
        else:
            logger.warning(f"Skipping message conversion due to unhandled sender_type: {msg.sender_type}")
    return converted


class AgentRunner(ServiceComponentBase): # Changed inheritance
    """
    Управляет жизненным циклом и выполнением одного экземпляра агента.
    Наследуется от ServiceComponentBase для унифицированного управления состоянием и жизненным циклом.

    Отвечает за:
    - Загрузку конфигурации агента.
    - Создание и запуск приложения LangGraph.
    - Прослушивание команд через Redis Pub/Sub.
    - Обработку входящих сообщений и вызов агента.
    - Сохранение истории чата и данных об использовании токенов.
    - Публикацию ответов агента.
    - Обновление статуса агента в Redis (через ServiceComponentBase -> StatusUpdater).
    - Корректное завершение работы и очистку ресурсов (через ServiceComponentBase -> RunnableComponent).

    Атрибуты:
        agent_id (str): Уникальный идентификатор агента. (Передан в super)
        db_session_factory (Optional[async_sessionmaker[AsyncSession]]): Фабрика для создания асинхронных сессий БД.
        logger (logging.LoggerAdapter): Адаптер логгера для журналирования. (Установлен в ServiceComponentBase)
        agent_config (Optional[Dict]): Загруженная конфигурация агента.
        agent_app (Optional[Any]): Экземпляр приложения LangGraph агента.
        # pubsub_handler_task (Optional[asyncio.Task]): Задача asyncio, выполняющая прослушивание Pub/Sub. # Удалено, используется _register_main_task
        static_state_config (Optional[Dict]): Статическая конфигурация состояния, извлеченная из конфигурации агента.
    """

    def __init__(self,
                 agent_id: str,
                 db_session_factory: Optional[async_sessionmaker[AsyncSession]],
                 logger_adapter: logging.LoggerAdapter):
        """
        Инициализатор AgentRunner.

        Args:
            agent_id: Уникальный идентификатор агента.
            db_session_factory: Фабрика для создания асинхронных сессий БД.
            logger_adapter: Адаптер логгера.
        """
        super().__init__(component_id=agent_id,
                         status_key_prefix="agent_status:",
                         logger_adapter=logger_adapter)

        self.db_session_factory = db_session_factory
        self._pubsub_channel = f"agent:{self._component_id}:input"
        self.response_channel = f"agent:{self._component_id}:output"
        self.loaded_threads_key = f"agent_threads:{self._component_id}"

        self.config_url = str
        self.config: Optional[Dict] = None
        self.agent_config: Optional[Dict] = None
        self.agent_app: Optional[Any] = None
        self.static_state_config: Optional[Dict] = None

        self.logger.info(f"AgentRunner for agent {self._component_id} initialized. PID: {os.getpid()}")


    async def _load_and_prepare_config(self) -> bool:
        """
        Загружает конфигурацию агента и подготавливает необходимые параметры.

        Returns:
            bool: True, если конфигурация успешно загружена и подготовлена, иначе False.
        """
        self.config_url = f"http://{settings.MANAGER_HOST}:{settings.MANAGER_PORT}{settings.API_V1_STR}/agents/{self._component_id}/config"

        self.logger.info(f"Fetching configuration from {self.config_url}...")
        self.agent_config = await fetch_config_static(self.config_url, self.logger)
        if not self.agent_config:
            self.logger.error(f"Failed to fetch or invalid configuration from {self.config_url}.")
            return False

        # Extract static state config if available
        self.static_state_config = {}
        self.logger.info(f"Agent configuration loaded and prepared successfully.")
        return True


    async def _setup_langgraph_app(self) -> bool:
        """
        Создает и настраивает приложение LangGraph на основе загруженной конфигурации.

        Returns:
            bool: True, если приложение успешно создано и настроено, иначе False.
        """
        self.logger.info(f"Creating LangGraph application...")
        try:
            self.agent_app, static_state_config = create_agent_app(self.agent_config, self._component_id, self.logger)
            self.static_state_config = static_state_config # Store for use in _process_message
        except Exception as e:
            self.logger.error(f"Failed to create LangGraph application: {e}", exc_info=True)
            return False

        self.logger.info(f"LangGraph application created successfully.")
        return True


    async def _handle_pubsub_message(self, message_data: bytes) -> None:
        """
        Обрабатывает входящее сообщение от Redis Pub/Sub.

        Этапы обработки:
        1. Декодирует данные сообщения (ожидается JSON).
        2. Извлекает `chat_id`, `text` (пользовательский ввод) и `interaction_id`.
        3. Если обязательные поля отсутствуют, логирует предупреждение и завершает обработку.
        4. Определяет глубину истории контекста на основе конфигурации агента.
        5. Если включена работа с БД, сохраняет сообщение пользователя в БД.
        6. Формирует входные данные для графа LangGraph, включая пользовательский ввод и статические параметры
           (system_prompt, temperature, model_id, provider) из `self.static_state_config`.
        7. Асинхронно вызывает основной поток (stream) агента `self.agent_app.astream`.
        8. Обрабатывает поток событий от агента:
            - Извлекает текстовое содержимое ответа агента (AIMessage).
            - Извлекает данные об использовании токенов (`TokenUsageData`) из метаданных сообщения или напрямую из события.
        9. Если включена работа с БД и получен ответ от агента, сохраняет сообщение агента в БД.
        10. Если доступны данные об использовании токенов, формирует полезную нагрузку и отправляет ее
            в очередь Redis (`settings.REDIS_TOKEN_USAGE_QUEUE_NAME`) для обработки `TokenUsageWorker`.
        11. Публикует ответ агента в соответствующий канал Redis (`agent:{self.agent_id}:output`).
        12. Обновляет время последней активности агента.

        В случае ошибок (например, JSONDecodeError или других исключений) логирует ошибку
        и, если возможно, публикует уведомление об ошибке в канал ответов.

        Args:
            message (Dict[str, Any]): Сообщение, полученное из Redis Pub/Sub.
                                      Ожидается, что `message['data']` содержит JSON-строку.
        """

        try:
            redis_cli = await self.redis_client
        except RuntimeError as e:
            self.logger.error(f"Redis client not available for handling pubsub message: {e}")
            return
        
        data_str: Optional[str] = None
        payload: Optional[Dict[str, Any]] = None # Инициализируем payload
        try:
            data_str = message_data.decode('utf-8')
            payload = json.loads(data_str)
            self.logger.info(f"Processing message for chat_id: {payload.get('chat_id')}")

            user_input = payload.get("text")
            chat_id = payload.get("chat_id")
            user_data = payload.get("user_data", {})
            channel = payload.get("channel", "unknown")
            platform_user_id = payload.get("platform_user_id")

            interaction_id = str(uuid.uuid4())
            self.logger.debug(f"Generated InteractionID: {interaction_id} for Thread: {chat_id}")

            if not chat_id or user_input is None:
                self.logger.warning(f"Missing 'text' or 'chat_id' in Redis payload: {payload}")
                return

            await self._save_history(
                sender_type="user",
                thread_id=chat_id,
                content=user_input,
                channel=channel,
                interaction_id=interaction_id
            )


            history_messages_db = await self._get_history(thread_id=chat_id)

            final_response_content, final_message_object = await self._send_agent(
                history_messages_db=history_messages_db,
                user_input=user_input,
                user_data=user_data,
                thread_id=chat_id,
                channel=channel,
                interaction_id=interaction_id
            )

            await self._save_history(
                sender_type="agent",
                thread_id=chat_id,
                content=final_response_content,
                channel=channel,
                interaction_id=interaction_id
            )

            await self._save_token_usage(
                interaction_id=interaction_id,
                thread_id=chat_id
            )

            response_payload = {
                "chat_id": chat_id,
                "response": final_response_content,
                "channel": channel
            }

            await redis_cli.publish(self.response_channel, json.dumps(response_payload))
            self.logger.debug(f"Published to {self.response_channel} response: {json.dumps(response_payload)}")

            await self.update_last_active_time()

        except json.JSONDecodeError as e:
            self.logger.error(f"JSONDecodeError processing PubSub message: {e}. Data: {data_str}", exc_info=True)
        except Exception as e:
            self.logger.error(f"Error processing PubSub message: {e}", exc_info=True)
            # Optionally, publish an error response
            if 'payload' in locals() and 'chat_id' in payload and 'interaction_id' in payload:
                error_response_channel = f"agent_responses:{payload['chat_id']}"
                error_payload = {
                    "chat_id": payload['chat_id'],
                    "agent_id": self._component_id,
                    "interaction_id": payload['interaction_id'],
                    "error": str(e),
                    "timestamp": datetime.now(timezone.utc).isoformat()
                }

                try:
                    # Changed from rpush to publish
                    await redis_cli.publish(error_response_channel, json.dumps(error_payload).encode('utf-8'))
                    self.logger.info(f"Published error notification to {error_response_channel}")
                except Exception as pub_err:
                    self.logger.error(f"Failed to publish error notification: {pub_err}", exc_info=True)


    async def _save_token_usage(
            self,
            interaction_id: str,
            thread_id: str
            ) -> None:
        """
        Сохраняет данные об использовании токенов в Redis для дальнейшей обработки.
        Отправляет данные в очередь Redis для обработки `TokenUsageWorker`.
        """
        try:
            redis_cli = await self.redis_client
        except RuntimeError as e:
            self.logger.error(f"Redis client not available for handling pubsub message: {e}")
            return
        
        # Получение финального состояния графа ---
        retrieved_final_state: Optional[Dict[str, Any]] = None
        try:
            final_graph_state_snapshot = self.agent_app.get_state(self.config)
            if final_graph_state_snapshot:
                retrieved_final_state = final_graph_state_snapshot.values
                self.logger.debug(f"Retrieved final graph state snapshot for InteractionID {interaction_id}")
            else:
                self.logger.warning(f"self.agent_app.get_state(config) returned None for InteractionID {interaction_id}")
        except Exception as e_get_state:
            self.logger.error(f"Error calling self.agent_app.get_state(config) for InteractionID {interaction_id}: {e_get_state}", exc_info=True)

        # Используем retrieved_final_state ---
        if retrieved_final_state and "token_usage_events" in retrieved_final_state:
            token_events: List[TokenUsageData] = retrieved_final_state["token_usage_events"]
            if token_events:
                self.logger.info(f"Found {len(token_events)} token usage events for InteractionID: {interaction_id}.")
                for token_data in token_events:
                    try:
                        token_payload = {
                            "interaction_id": interaction_id,
                            "agent_id": self._component_id,
                            "thread_id": thread_id,
                            "call_type": token_data.call_type,
                            "model_id": token_data.model_id,
                            "prompt_tokens": token_data.prompt_tokens,
                            "completion_tokens": token_data.completion_tokens,
                            "total_tokens": token_data.total_tokens,
                            "timestamp": token_data.timestamp
                        }
                        await redis_cli.lpush(settings.REDIS_TOKEN_USAGE_QUEUE_NAME, json.dumps(token_payload))
                        self.logger.debug(f"Queued token usage data to '{settings.REDIS_TOKEN_USAGE_QUEUE_NAME}': {token_payload}")
                    except redis_exceptions.RedisError as e:
                        self.logger.error(f"Failed to queue token usage data for InteractionID {interaction_id}: {e}")
                    except Exception as e_gen:
                        self.logger.error(f"Unexpected error queuing token usage data for InteractionID {interaction_id}: {e_gen}", exc_info=True)
            else:
                self.logger.info(f"No token usage events recorded in retrieved final state for InteractionID: {interaction_id}.")
        else:
            self.logger.warning(f"Could not retrieve token_usage_events from final graph state for InteractionID: {interaction_id}. State: {retrieved_final_state is not None}")


    async def _send_agent(
            self,
            history_messages_db: List[BaseMessage],
            user_input: str,
            user_data: Dict[str, Any],
            thread_id: str,
            channel: Optional[str] = None,
            interaction_id: Optional[str] = None
            ) -> Tuple[str, Optional[BaseMessage]]:
        """
        """
        graph_input = {
            "messages": history_messages_db + [HumanMessage(content=user_input)],
            "user_data": user_data,
            "channel": channel,
            "original_question": user_input,
            "question": user_input,
            "rewrite_count": 0,
            "documents": [],
            "current_interaction_id": interaction_id,
            "token_usage_events": [],
            **self.static_state_config
        }
        self.config = {"configurable": {"thread_id": str(thread_id), "agent_id": self._component_id}}

        self.logger.info(f"Invoking graph for thread_id: {thread_id} (Initial history messages: {len(history_messages_db)})")
        final_response_content = "No response generated."
        final_message_object = None

        async for output in self.agent_app.astream(graph_input, self.config, stream_mode="updates"):
            if not self._running or self.needs_restart:
                self.logger.warning("Shutdown or restart requested during graph stream.")
                break

            for key, value in output.items():
                self.logger.debug(f"Graph node '{key}' output: {value}")
                if key == "agent" or key == "generate":
                    if "messages" in value and value["messages"]:
                        last_msg = value["messages"][-1]
                        if isinstance(last_msg, AIMessage):
                            final_response_content = last_msg.content
                            final_message_object = last_msg

        self.logger.info(f"Graph execution finished. Final response: {final_response_content[:100]}...")

        return final_response_content, final_message_object


    async def _get_history(
            self,
            thread_id: str
            ) -> List[BaseMessage]:
        """
        Получает историю сообщений из БД для указанного thread_id.
        Возвращает список сообщений в формате LangChain BaseMessage.
        Если история не найдена, возвращает пустой список.
        """
        can_load_history = db_get_recent_chat_history is not None and self.db_session_factory is not None and ChatMessageDB is not None and SenderType is not None
        if not can_load_history:
            self.logger.warning("Database history loading is disabled (CRUD, DB session factory, ChatMessageDB, or SenderType not available).")

        try:
            redis_cli = await self.redis_client
        except RuntimeError as e:
            self.logger.error(f"Redis client not available for handling pubsub message: {e}")
            return

        enable_context_memory = self.static_state_config.get("enableContextMemory", True)
        history_limit = self.static_state_config.get("contextMemoryDepth", 10)

        if not enable_context_memory:
            self.logger.info(f"Context memory is disabled by agent config. History will not be loaded with depth.")
            context_memory_depth = 0
        else:
            self.logger.info(f"Using history limit: {history_limit} (enabled: {enable_context_memory}, configured depth: {history_limit})")
            loaded_messages: List[BaseMessage] = []
            if can_load_history and history_limit > 0:
                try:
                    is_loaded = await redis_cli.sismember(self.loaded_threads_key, thread_id)
                    if not is_loaded:
                        self.logger.info(f"Thread '{thread_id}' not found in cache '{self.loaded_threads_key}'. Loading history from DB with depth {history_limit}.")
                        async with self.db_session_factory() as session:
                            history_from_db = await db_get_recent_chat_history(
                                db=session,
                                agent_id=self._component_id,
                                thread_id=thread_id,
                                limit=history_limit
                            )
                            loaded_messages = convert_db_history_to_langchain(history_from_db, self.logger)

                        self.logger.info(f"Loaded {len(loaded_messages)} messages from DB for thread '{thread_id}'.")

                        await redis_cli.sadd(self.loaded_threads_key, thread_id)
                        self.logger.info(f"Added thread '{thread_id}' to cache '{self.loaded_threads_key}'.")
                        return loaded_messages
                    else:
                        self.logger.info(f"Thread '{thread_id}' found in cache '{self.loaded_threads_key}'. Skipping DB load.")
                        return []

                except redis_exceptions as redis_err:
                    self.logger.error(f"Redis error checking/adding thread cache for '{thread_id}': {redis_err}. Proceeding without history.")
                    return []
                except Exception as db_err:
                    self.logger.error(f"Database error loading history for thread '{thread_id}': {db_err}. Proceeding without history.", exc_info=True)
                    return []
            else:
                if not await redis_cli.sismember(self.loaded_threads_key, thread_id):
                    self.logger.warning(f"Cannot load history for thread '{thread_id}' because DB/CRUD/Models are unavailable (but memory was enabled).")
                    await redis_cli.sadd(self.loaded_threads_key, thread_id)
                    return []


    async def _save_history(
            self,
            sender_type: str,
            thread_id: str,
            content: str,
            channel: Optional[str],
            interaction_id: str
            ) -> None:

        try:
            redis_cli = await self.redis_client
        except RuntimeError as e:
            self.logger.error(f"Redis client not available for handling pubsub message: {e}")
            return
        
        message_to_save = {
            "agent_id": self._component_id,
            "thread_id": thread_id,
            "sender_type": sender_type,
            "content": content,
            "channel": channel,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "interaction_id": interaction_id
        }
        try:
            await redis_cli.lpush(settings.REDIS_HISTORY_QUEUE_NAME, json.dumps(message_to_save))
            self.logger.info(f"Queued {sender_type} message for history (Thread: {thread_id}, InteractionID: {interaction_id})")
        except redis_exceptions.RedisError as e:
            self.logger.error(f"Failed to queue message for history (Thread: {thread_id}): {e}", exc_info=True)
        except Exception as e:
            self.logger.error(f"Unexpected error queuing message for history (Thread: {thread_id}): {e}", exc_info=True)


    async def setup(self) -> None:
        """
        Настраивает AgentRunner.
        Вызывает super().setup() для инициализации StatusUpdater и RedisClientManager.
        Затем загружает конфигурацию агента и настраивает приложение LangGraph.
        """
        self.logger.info(f"AgentRunner setup started.")
        await super().setup() # This calls ServiceComponentBase.setup() which handles StatusUpdater, Redis init, and clears needs_restart and _main_tasks

        if not await self._load_and_prepare_config():
            self.logger.critical(f"Failed to load or prepare agent configuration. AgentRunner cannot start.")
            await self.mark_as_error(error_message="Failed to load agent config")
            raise RuntimeError("Agent configuration failed to load.")

        if not await self._setup_langgraph_app():
            self.logger.critical(f"Failed to setup LangGraph application. AgentRunner cannot start.")
            await self.mark_as_error(error_message="Failed to setup LangGraph app")
            raise RuntimeError("LangGraph application setup failed.")
        
        self.logger.debug(f"AgentRunner setup completed successfully.")


    async def run_loop(self) -> None:
        """
        Основной цикл работы AgentRunner.
        Регистрирует слушателя Pub/Sub как основную задачу и передает управление
        в `super().run_loop()` для ее выполнения и мониторинга.
        """
        if not self.agent_app:
            self.logger.error(f"Agent application not initialized. Cannot start run_loop.")
            await self.mark_as_error("Agent application not initialized")
            # self.initiate_shutdown() # ServiceComponentBase.run_loop handles shutdown if _running becomes False
            return

        self.logger.info(f"AgentRunner run_loop starting...")
        
        # Регистрируем _pubsub_listener_loop как основную задачу
        # self._pubsub_channel уже установлен в __init__
        self._register_main_task(self._pubsub_listener_loop(), name="AgentPubSubListener")

        try:
            await super().run_loop()
        except Exception as e:
            self.logger.critical(f"Unexpected error in AgentRunner run_loop for {self.agent_id}: {e}", exc_info=True)
            self._running = False
            self.clear_restart_request()
            await self.mark_as_error(f"Run_loop critical error: {e}")
        finally:
            self.logger.info(f"AgentRunner run_loop finished.")
            self._running = False 


    async def cleanup(self) -> None:
        """
        Очищает ресурсы AgentRunner.
        Вызывает super().cleanup() для отмены зарегистрированных задач (включая pubsub_handler_task),
        закрытия RedisClientManager и обновления статуса.
        """
        self.logger.info(f"AgentRunner cleanup started.")
        
        try:
            redis_cli = await self.redis_client
            try:
                deleted_count = await redis_cli.delete(self.loaded_threads_key)
                self.logger.info(f"Cleared loaded threads cache '{self.loaded_threads_key}' (deleted: {deleted_count}) on start/restart.")
            except redis_exceptions.RedisError as cache_clear_err:
                self.logger.error(f"Failed to clear loaded threads cache '{self.loaded_threads_key}': {cache_clear_err}")
        except RuntimeError as e:
            self.logger.error(f"Redis client not available for handling pubsub message: {e}")
            return
        
        # LangGraph app cleanup (if any specific method exists)
        if hasattr(self.agent_app, 'cleanup'):
            try:
                self.logger.info(f"Cleaning up LangGraph application.")
                # Убедимся, что это корутина, если это так
                if asyncio.iscoroutinefunction(self.agent_app.cleanup):
                    await self.agent_app.cleanup()
                elif callable(self.agent_app.cleanup):
                    self.agent_app.cleanup() # Если это обычная функция
                else:
                    self.logger.warning(f"agent_app.cleanup is not callable or a coroutine function.")
            except Exception as e:
                self.logger.error(f"Error during LangGraph application cleanup: {e}", exc_info=True)

        self.agent_app = None
        self.agent_config = None
        self.config = None
        self.config_url = None
        self.static_state_config = None
        self.pubsub_handler_task = None

        await super().cleanup()
        self.logger.info(f"AgentRunner cleanup finished.")

    async def mark_as_error(self, error_message: str, error_type: str = "UnknownError"):
        """Marks the component's status as 'error'."""
        self.logger.error(f"Marking component as error. Error Type: {error_type}, Message: {error_message}")
        # Removed 'reason' keyword argument, pass error_message directly
        await self.status_updater.mark_as_error(error_message, error_type=error_type)

    async def _handle_status_update_exception(self, e: Exception, phase: str):
        """Handles exceptions during status updates, logging them and marking the component as error."""
        error_message = f"Failed to update status during {phase}: {e}"
        self.logger.error(error_message, exc_info=True)
        # Ensure this call is also compatible with the updated mark_as_error
        await self.status_updater.mark_as_error(
            message=f"Internal error: Failed to update status during {phase}.",
            error_type="StatusUpdateError"
        )

# Helper function convert_db_history_to_langchain_static can remain as is or be moved
# into the class if it makes more sense contextually.
# For now, it's kept as a static helper.

# Removed _run_signal_handler, as shutdown is handled by RunnableComponent.initiate_shutdown()
# The main `run` method from RunnableComponent (via ServiceComponentBase) orchestrates setup, run_loop, cleanup.
