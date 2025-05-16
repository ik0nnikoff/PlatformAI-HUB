import asyncio
import logging
import os
import json
import uuid
import time
from typing import Dict, Optional, Any, List

import httpx # Added for async HTTP requests
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from langchain_core.messages import BaseMessage, HumanMessage, AIMessage
from datetime import datetime, timezone

# Imports from the old runner_main.py that will be needed
import redis.asyncio as redis # type: ignore
from redis import exceptions as redis_exceptions # ADDED

from app.agent_runner.langgraph_factory import create_agent_app
from app.agent_runner.langgraph_models import TokenUsageData # AgentState is used by factory
from app.db.alchemy_models import ChatMessageDB, SenderType
from app.db.crud import chat_crud
from app.core.config import settings
from app.core.base.runnable_component import RunnableComponent
from app.core.base.status_updater import StatusUpdater

# Logging utilities (can be kept separate or integrated if they don't need to be global)
# For now, assuming setup_logging_for_agent will be called externally and logger passed in.

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


def convert_db_history_to_langchain_static(db_messages: List[ChatMessageDB], logger: logging.Logger) -> List[BaseMessage]:
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


class AgentRunner(RunnableComponent, StatusUpdater):
    """
    Управляет жизненным циклом и выполнением одного экземпляра агента.

    Отвечает за:
    - Загрузку конфигурации агента.
    - Создание и запуск приложения LangGraph.
    - Прослушивание команд через Redis Pub/Sub.
    - Обработку входящих сообщений и вызов агента.
    - Сохранение истории чата и данных об использовании токенов.
    - Публикацию ответов агента.
    - Обновление статуса агента в Redis.
    - Корректное завершение работы и очистку ресурсов.

    Атрибуты:
        agent_id (str): Уникальный идентификатор агента.
        config_url (str): URL для загрузки конфигурации агента.
        db_session_factory (Optional[async_sessionmaker[AsyncSession]]): Фабрика для создания асинхронных сессий БД.
        logger (logging.LoggerAdapter): Адаптер логгера для журналирования.
        agent_config (Optional[Dict]): Загруженная конфигурация агента.
        agent_app (Optional[Any]): Экземпляр приложения LangGraph агента.
        redis_pubsub_client (Optional[redis.Redis]): Клиент Redis для Pub/Sub, используемый для получения команд.
        pubsub_handler_task (Optional[asyncio.Task]): Задача asyncio, выполняющая прослушивание Pub/Sub.
        static_state_config (Optional[Dict]): Статическая конфигурация состояния, извлеченная из конфигурации агента.
    """
    def __init__(self, 
                 agent_id: str, 
                 config_url: str, 
                 db_session_factory: Optional[async_sessionmaker[AsyncSession]],
                 logger_adapter: logging.LoggerAdapter): # Removed redis_pubsub_url

        super().__init__(logger=logger_adapter) # Initialize RunnableComponent with the logger
        # StatusUpdater and RedisClientManager are initialized via their own super() calls if needed.

        self.agent_id = agent_id
        self._component_id = agent_id  # Required by StatusUpdater
        self._status_key_prefix = "agent_status:"  # Required by StatusUpdater

        self.config_url = config_url
        self.db_session_factory = db_session_factory
        
        self.agent_config: Optional[Dict] = None
        self.agent_app: Optional[Any] = None # Consider more specific type if possible

        # For Pub/Sub specific to this agent's command listening
        self.redis_pubsub_client: Optional[redis.Redis] = None
        self.pubsub_handler_task: Optional[asyncio.Task] = None

        self.logger.info(f"AgentRunner for {self.agent_id} initialized. Config URL: {self.config_url}. PubSub will use REDIS_URL from settings: {settings.REDIS_URL}")

    async def _initialize_pubsub_client(self):
        """
        Инициализирует и проверяет соединение с клиентом Redis Pub/Sub.

        Использует `settings.REDIS_URL` для подключения. В случае ошибки
        соединения или другой неожиданной ошибки, логирует проблему,
        устанавливает `self.redis_pubsub_client` в `None` и пробрасывает исключение.
        """
        redis_url_to_use = str(settings.REDIS_URL)
        try:
            self.redis_pubsub_client = redis.from_url(redis_url_to_use)
            await self.redis_pubsub_client.ping()
            self.logger.info(f"Redis Pub/Sub client connected and pinged successfully for {self.agent_id} at {redis_url_to_use}.")
        except redis_exceptions.ConnectionError as e: # CHANGED
            self.logger.error(f"[{self.agent_id}] Failed to connect to Redis Pub/Sub at {redis_url_to_use}: {e}")
            self.redis_pubsub_client = None # Ensure client is None if connection failed
            raise  # Re-raise to be caught by setup
        except Exception as e:
            self.logger.error(f"[{self.agent_id}] An unexpected error occurred during Pub/Sub client initialization using {redis_url_to_use}: {e}", exc_info=True)
            self.redis_pubsub_client = None
            raise # Re-raise

    async def setup(self) -> None:
        """
        Настраивает AgentRunner перед запуском основного цикла.

        Выполняет следующие шаги:
        1. Инициализирует `StatusUpdater` для обновления статуса агента в Redis.
        2. Устанавливает статус агента "инициализация".
        3. Загружает конфигурацию агента по `config_url`.
        4. Если конфигурация не загружена, устанавливает статус "ошибка" и вызывает исключение.
        5. Создает приложение LangGraph агента на основе конфигурации.
        6. Инициализирует выделенный клиент Redis для прослушивания команд через Pub/Sub.
        7. Если инициализация Pub/Sub клиента не удалась, пробрасывает исключение.
        8. Устанавливает статус агента "запущен" с указанием PID процесса.

        В случае любой ошибки во время настройки, логирует ошибку, пытается
        установить статус "ошибка" в Redis и пробрасывает исключение дальше,
        чтобы `RunnableComponent` мог корректно обработать его и вызвать `cleanup`.
        """
        self.logger.info(f"[{self.agent_id}] Starting setup...")
        try:
            # Initialize StatusUpdater (uses default REDIS_URL from settings for status updates)
            # _component_id and _status_key_prefix are already set in __init__
            await self.setup_status_updater() 
            await self.mark_as_initializing()

            self.logger.info(f"[{self.agent_id}] Fetching configuration from {self.config_url}...")
            self.agent_config = await fetch_config_static(self.config_url, self.logger)
            if not self.agent_config:
                error_msg = f"[{self.agent_id}] Failed to fetch or invalid configuration from {self.config_url}."
                self.logger.error(error_msg)
                await self.mark_as_error(error_msg)
                raise ValueError(error_msg)
            
            self.logger.info(f"[{self.agent_id}] Configuration fetched successfully. Creating agent app...")
            # Ensuring this line is as follows for the TypeError fix:
            self.agent_app, static_state_config = create_agent_app(self.agent_config, self.agent_id)
            self.static_state_config = static_state_config # Store for use in _process_message
            self.logger.info(f"[{self.agent_id}] LangGraph application created successfully.")

            # Initialize dedicated Redis client for Pub/Sub
            self.logger.info(f"[{self.agent_id}] Initializing Redis Pub/Sub client...")
            await self._initialize_pubsub_client()
            # If _initialize_pubsub_client raises, setup will fail here, which is desired.

            await self.mark_as_running(pid=os.getpid() if hasattr(os, 'getpid') else None)
            self.logger.info(f"[{self.agent_id}] Setup complete. Agent is running.")

        except Exception as e:
            error_detail = f"Setup failed for agent {self.agent_id}: {str(e)}"
            self.logger.error(error_detail, exc_info=True)
            # Attempt to mark as error in Redis, but ensure Redis client for status is up
            if hasattr(self, '_redis_client') and self._redis_client: # Check if StatusUpdater's client was set up
                 await self.mark_as_error(error_detail)
            # Re-raise the exception so RunnableComponent's run() method can catch it and call cleanup.
            raise

    async def _handle_pubsub_message(self, message: Dict[str, Any]):
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
        self.logger.debug(f"[{self.agent_id}] Received PubSub message: {message}")
        try:
            data_str = message.get('data')
            if isinstance(data_str, bytes):
                data_str = data_str.decode('utf-8')
            
            if not data_str:
                self.logger.warning(f"[{self.agent_id}] Empty message data received.")
                return

            payload = json.loads(data_str)
            self.logger.info(f"[{self.agent_id}] Processing message for chat_id: {payload.get('chat_id')}")

            chat_id = payload.get("chat_id")
            user_input = payload.get("text")
            interaction_id = payload.get("interaction_id") # Important for tracking

            if not chat_id or user_input is None or not interaction_id:
                self.logger.warning(f"[{self.agent_id}] Missing chat_id, text, or interaction_id in payload: {payload}")
                return

            # Determine context memory depth from agent_config
            enable_context_memory = self.agent_config.get("enableContextMemory", True)
            context_memory_depth = self.agent_config.get("contextMemoryDepth", 10) # Default to 10 if not specified
            if not enable_context_memory:
                self.logger.info(f"[{self.agent_id}] Context memory is disabled by agent config. History will not be loaded with depth.")
                actual_history_limit = 0
            else:
                actual_history_limit = context_memory_depth
            
            self.logger.info(f"[{self.agent_id}] Using history limit: {actual_history_limit} (enabled: {enable_context_memory}, configured depth: {context_memory_depth})")

            history_messages_db: List[ChatMessageDB] = []
            if self.db_session_factory:
                async with self.db_session_factory() as db_session:
                    # Save user message
                    user_msg_db = await chat_crud.db_add_chat_message(
                        db=db_session,
                        agent_id=self.agent_id,
                        thread_id=chat_id, # Changed from chat_id to thread_id
                        sender_type=SenderType.USER,
                        content=user_input,
                        channel=payload.get("channel"),
                        timestamp=datetime.now(timezone.utc), # Consider parsing from payload if available and reliable
                        interaction_id=interaction_id
                    )
                    history_messages_db.append(user_msg_db) # Add to history for current turn
            
            # langchain_history = convert_db_history_to_langchain_static(history_messages_db, self.logger) # Temporarily commented out

            initial_graph_input = {
                # "messages": langchain_history +[HumanMessage(content=user_input)], # Temporarily simplified
                "messages": [HumanMessage(content=user_input)], # Temporarily simplified
            }

            # Ensure key names here match exactly what langgraph_factory.create_agent_app returns in static_state_config
            # and what agent_node expects in the state.
            expected_static_keys = {
                "system_prompt": "system_prompt", # Key expected in static_state_config and graph state
                "temperature": "temperature",   # Key expected in static_state_config and graph state
                "model_id": "model_id",         # Key expected in static_state_config and graph state
                "provider": "provider"        # Key expected in static_state_config and graph state
            }
            if self.static_state_config:
                for static_key, graph_key in expected_static_keys.items():
                    if static_key in self.static_state_config:
                        initial_graph_input[graph_key] = self.static_state_config[static_key]
                    else:
                        self.logger.warning(
                            f"[{self.agent_id}] Key '{static_key}' not found in static_state_config. "
                            f"Graph key '{graph_key}' may be missing. "
                            f"Check agent configuration and langgraph_factory.py."
                        )
                        # Provide a default or handle absence as needed by your graph's logic
                        if graph_key == "system_prompt":
                            initial_graph_input[graph_key] = "" # Default for system_prompt
                        # Add defaults for other critical keys if necessary
                        elif graph_key == "model_id":
                            self.logger.error(f"[{self.agent_id}] Critical key 'model_id' is missing from static_state_config. Graph execution will likely fail.")
                            initial_graph_input[graph_key] = "default_model_id_placeholder" # Example placeholder
                        elif graph_key == "provider":
                            self.logger.error(f"[{self.agent_id}] Critical key 'provider' is missing from static_state_config. Graph execution will likely fail.")
                            initial_graph_input[graph_key] = "default_provider_placeholder" # Example placeholder
            else:
                self.logger.error(
                    f"[{self.agent_id}] static_state_config is not set. Critical parameters like system_prompt, temperature, etc., will be missing."
                )
                # Set defaults for critical keys if static_state_config is missing entirely
                initial_graph_input["system_prompt"] = ""
                # Defaults for other keys might be needed here if the graph cannot proceed without them

            # Invoke agent
            self.logger.debug(f"[{self.agent_id}] Invoking agent for interaction_id: {interaction_id} with input: {initial_graph_input}") # Log the input
            agent_response_stream = self.agent_app.astream(
                initial_graph_input, # Use the input dict that now includes system_prompt and other params
                config={"configurable": {"thread_id": chat_id, "agent_id": self.agent_id}}
            )
            
            extracted_text_content = "" # Initialize to store the final text
            token_usage_data: Optional[TokenUsageData] = None
            
            self.logger.debug(f"[{self.agent_id}] Starting to process agent_response_stream for interaction_id: {interaction_id}")
            async for event_part in agent_response_stream:
                self.logger.info(f"[{self.agent_id}] Stream event_part: {event_part}") # DEBUG LINE
                if isinstance(event_part, dict):
                    # Corrected path to messages based on new logs
                    messages_chunk = event_part.get('agent_node', {}).get('messages', [])
                    if messages_chunk and isinstance(messages_chunk, list):
                        for msg_item in messages_chunk:
                            if isinstance(msg_item, AIMessage):
                                self.logger.info(f"[{self.agent_id}] Processing AIMessage from stream. Current extracted_text_content='{extracted_text_content[:100]}...'. New msg_item.content='{msg_item.content[:100]}...'. Interaction ID: {interaction_id}")
                                extracted_text_content = msg_item.content
                                self.logger.info(f"[{self.agent_id}] Updated extracted_text_content to: '{extracted_text_content[:100]}...'. Interaction ID: {interaction_id}")
                                
                                if hasattr(msg_item, 'usage_metadata') and msg_item.usage_metadata:
                                    usage_metadata = msg_item.usage_metadata
                                    usage_from_msg = usage_metadata.get("token_usage") or usage_metadata.get("usage_metadata")
                                    if usage_from_msg:
                                        try:
                                            token_usage_data = TokenUsageData(**usage_from_msg)
                                            self.logger.debug(f"[{self.agent_id}] Token usage from AIMessage metadata: {token_usage_data}")
                                        except Exception as e_token_msg:
                                            self.logger.warning(f"[{self.agent_id}] Could not parse token_usage from AIMessage metadata: {usage_from_msg}, error: {e_token_msg}")
                                    else:
                                        self.logger.debug(f"[{self.agent_id}] usage_metadata present but does not contain all required TokenUsageData fields: {usage_metadata}")
                    
                    # Fallback или альтернативный способ получить token usage, если он напрямую в event_part
                    if token_usage_data is None and "token_usage" in event_part and event_part["token_usage"]:
                        try:
                            token_usage_data = TokenUsageData(**event_part["token_usage"])
                            self.logger.debug(f"[{self.agent_id}] Token usage data from event part: {token_usage_data}")
                        except Exception as e_token_event:
                            self.logger.warning(f"[{self.agent_id}] Could not parse token_usage from event_part: {event_part.get('token_usage')}, error: {e_token_event}")

                    # Новый fallback: если есть token_usage_events (список), взять первый (или все)
                    if token_usage_data is None and "token_usage_events" in event_part and event_part["token_usage_events"]:
                        try:
                            tu_event = event_part["token_usage_events"][0]
                            if isinstance(tu_event, dict):
                                token_usage_data = TokenUsageData(**tu_event)
                            else:
                                token_usage_data = tu_event
                            self.logger.debug(f"[{self.agent_id}] Token usage from token_usage_events: {token_usage_data}")
                        except Exception as e_token_event:
                            self.logger.warning(f"[{self.agent_id}] Could not parse token_usage from token_usage_events: {event_part.get('token_usage_events')}, error: {e_token_event}")

            self.logger.info(f"[{self.agent_id}] Finished processing agent_response_stream for interaction_id: {interaction_id}")
            full_response_content = extracted_text_content # Use the extracted content

            self.logger.info(f"[{self.agent_id}] Final extracted_text_content for interaction_id {interaction_id}: '{full_response_content}'")
            self.logger.info(f"[{self.agent_id}] Agent response for interaction_id {interaction_id} (preview from full_response_content): {full_response_content[:100]}...")

            if self.db_session_factory:
                async with self.db_session_factory() as db_session:
                    # Save agent response
                    if full_response_content: # Only save if there's content
                        await chat_crud.db_add_chat_message(
                            db=db_session,
                            agent_id=self.agent_id,
                            thread_id=chat_id, # Changed from chat_id to thread_id
                            sender_type=SenderType.AGENT,
                            content=full_response_content,
                            channel=payload.get("channel"), # Agent responds on the same channel
                            timestamp=datetime.now(timezone.utc),
                            interaction_id=interaction_id # Link AI response to the same interaction
                        )
                    # Save token usage if available
                    self.logger.info(f"[{self.agent_id}] token_usage_data before publish: {token_usage_data}")
                    if token_usage_data:
                        # Формируем payload для очереди token_usage
                        token_usage_payload = {
                            "agent_id": self.agent_id,
                            "thread_id": chat_id,
                            "interaction_id": interaction_id,
                            "call_type": getattr(token_usage_data, "call_type", None),
                            "model_id": getattr(token_usage_data, "model_id", None),
                            "prompt_tokens": getattr(token_usage_data, "prompt_tokens", 0),
                            "completion_tokens": getattr(token_usage_data, "completion_tokens", 0),
                            "total_tokens": getattr(token_usage_data, "total_tokens", 0),
                            "timestamp": getattr(token_usage_data, "timestamp", datetime.now(timezone.utc).isoformat())
                        }
                        # Публикуем в Redis очередь для token_usage_worker
                        if self.redis_pubsub_client:
                            await self.redis_pubsub_client.rpush(
                                settings.REDIS_TOKEN_USAGE_QUEUE_NAME,
                                json.dumps(token_usage_payload)
                            )
                            self.logger.info(f"[{self.agent_id}] Published token usage to queue {settings.REDIS_TOKEN_USAGE_QUEUE_NAME}")
                            self.logger.info(f"[{self.agent_id}] Published token usage event to Redis queue: {settings.REDIS_TOKEN_USAGE_QUEUE_NAME} | Data: {token_usage_payload}")

            # Publish response back to a response channel (e.g., agent_responses:{chat_id})
            response_channel = f"agent:{self.agent_id}:output" # NEW - MATCHES TELEGRAM BOT
            response_payload = {
                "chat_id": chat_id,
                "thread_id": chat_id, # ADDED thread_id
                "agent_id": self.agent_id,
                "interaction_id": interaction_id,
                "response": full_response_content,
                "timestamp": datetime.now(timezone.utc).isoformat()
            }
            if self.redis_pubsub_client:
                await self.redis_pubsub_client.publish(response_channel, json.dumps(response_payload))
                self.logger.info(f"[{self.agent_id}] Published response to {response_channel}")

            await self.update_last_active_time()

        except json.JSONDecodeError as e:
            self.logger.error(f"[{self.agent_id}] JSONDecodeError processing PubSub message: {e}. Data: {data_str}", exc_info=True)
        except Exception as e:
            self.logger.error(f"[{self.agent_id}] Error processing PubSub message: {e}", exc_info=True)
            # Optionally, publish an error response
            if 'payload' in locals() and 'chat_id' in payload and 'interaction_id' in payload:
                error_response_channel = f"agent_responses:{payload['chat_id']}"
                error_payload = {
                    "chat_id": payload['chat_id'],
                    "agent_id": self.agent_id,
                    "interaction_id": payload['interaction_id'],
                    "error": str(e),
                    "timestamp": datetime.now(timezone.utc).isoformat()
                }
                if self.redis_pubsub_client:
                    try:
                        await self.redis_pubsub_client.publish(error_response_channel, json.dumps(error_payload))
                        self.logger.info(f"[{self.agent_id}] Published error notification to {error_response_channel}")
                    except Exception as pub_err:
                        self.logger.error(f"[{self.agent_id}] Failed to publish error notification: {pub_err}", exc_info=True)


    async def _pubsub_listener_loop(self):
        """
        Асинхронный цикл для прослушивания сообщений из Redis Pub/Sub.

        Подписывается на канал команд агента (`agent:{self.agent_id}:input`).
        В бесконечном цикле (пока `self._running` истинно):
        - Ожидает новое сообщение с таймаутом, чтобы периодически проверять флаг `self._running`.
        - Если сообщение получено, вызывает `self._handle_pubsub_message` для его обработки.
        - Использует короткую паузу `asyncio.sleep(0.01)` для предотвращения загрузки CPU в отсутствие сообщений.
        - Обрабатывает `redis_exceptions.ConnectionError` (логирует и ожидает перед повторной попыткой).
        - Обрабатывает `asyncio.CancelledError` для корректного завершения цикла.
        - Логирует другие исключения и продолжает работу после небольшой паузы.

        При завершении цикла (нормальном или из-за исключения) отписывается от канала
        и закрывает объект `pubsub`.
        """
        if not self.redis_pubsub_client:
            self.logger.error(f"[{self.agent_id}] PubSub client not initialized. Cannot start listener loop.")
            return

        pubsub = self.redis_pubsub_client.pubsub()
        command_channel = f"agent:{self.agent_id}:input" # Match Telegram integration
        
        try:
            await pubsub.subscribe(command_channel)
            self.logger.info(f"[{self.agent_id}] Subscribed to Redis channel: {command_channel}")

            while self._running: # Check flag from RunnableComponent
                try:
                    # Timeout allows the loop to periodically check self._running
                    message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=0.1)
                    if message:
                        await self._handle_pubsub_message(message)
                    # Yield control to allow other tasks to run, and for self._running to be checked
                    await asyncio.sleep(0.01) # Short sleep to prevent tight loop if no messages
                except redis_exceptions.ConnectionError as e: # CHANGED
                    self.logger.error(f"[{self.agent_id}] PubSub connection error in listener loop: {e}. Attempting to reconnect...", exc_info=True)
                    # Implement reconnection logic if needed, or rely on setup/run to re-establish
                    await asyncio.sleep(5) # Wait before retrying or exiting
                    # Potentially re-subscribe or re-initialize client if connection is lost.
                    # For now, we'll let the main run loop handle component restart if it fails critically.
                    # If re-subscription is needed:
                    # await pubsub.subscribe(command_channel)
                except asyncio.CancelledError:
                    self.logger.info(f"[{self.agent_id}] PubSub listener loop cancelled.")
                    break # Exit loop on cancellation
                except Exception as e:
                    self.logger.error(f"[{self.agent_id}] Error in PubSub listener loop: {e}", exc_info=True)
                    await asyncio.sleep(1) # Brief pause before continuing

        finally:
            if pubsub:
                try:
                    await pubsub.unsubscribe(command_channel)
                    await pubsub.close() # Close the pubsub object itself
                    self.logger.info(f"[{self.agent_id}] Unsubscribed from {command_channel} and closed pubsub object.")
                except Exception as e_close_pubsub:
                    self.logger.error(f"[{self.agent_id}] Error closing pubsub resources: {e_close_pubsub}", exc_info=True)


    async def run_loop(self) -> None:
        """
        Основной рабочий цикл AgentRunner.

        Этот метод запускается после успешного `setup`.
        Он выполняет следующие действия:
        1. Проверяет, инициализирован ли `redis_pubsub_client`. Если нет, останавливает выполнение.
        2. Создает и запускает задачу `_pubsub_listener_loop` для прослушивания команд из Redis.
        3. Входит в цикл `while self._running`:
            - Проверяет, не завершилась ли задача `pubsub_handler_task` (прослушиватель).
                - Если завершилась, пытается получить результат задачи (`.result()`), чтобы выявить возможные ошибки.
                - Логирует `asyncio.CancelledError` или другие исключения, если задача была отменена или завершилась с ошибкой.
                - Устанавливает `self._running = False`, чтобы остановить агент.
            - Периодически обновляет время последней активности агента (`update_last_active_time`).
            - Приостанавливает выполнение на `settings.AGENT_RUNNER_HEARTBEAT_INTERVAL`.
        4. Если основной цикл завершается (например, из-за установки `self._running = False` внешним сигналом):
            - Если задача `pubsub_handler_task` все еще активна, отменяет ее.
            - Ожидает завершения задачи `pubsub_handler_task`, обрабатывая возможные `asyncio.CancelledError`
              или другие исключения при ожидании.
        5. Обрабатывает `asyncio.CancelledError` для самого `run_loop` (например, если весь компонент останавливается):
            - Если задача `pubsub_handler_task` активна, отменяет ее.
            - Ожидает завершения задачи `pubsub_handler_task`, обрабатывая исключения.
        6. Логирует выход из `run_loop`.
        """
        self.logger.info(f"[{self.agent_id}] Starting run_loop...")
        
        if not self.redis_pubsub_client: # Should have been initialized in setup
            self.logger.error(f"[{self.agent_id}] Redis Pub/Sub client not available at start of run_loop. Stopping.")
            self._running = False # Signal to stop
            return

        # Start the Pub/Sub listener as a background task managed by this component
        self.pubsub_handler_task = asyncio.create_task(self._pubsub_listener_loop())
        
        try:
            while self._running:
                if self.pubsub_handler_task.done():
                    # If the listener task stopped unexpectedly, log it and potentially stop the agent.
                    try:
                        self.pubsub_handler_task.result() # Raise exception if task failed

                    except asyncio.CancelledError:
                        self.logger.info(f"[{self.agent_id}] PubSub listener task was found cancelled unexpectedly in run_loop.") # MODIFIED
                    except Exception as e:
                        self.logger.error(f"[{self.agent_id}] PubSub listener task failed unexpectedly in run_loop: {e}", exc_info=True) # MODIFIED
                    
                    self._running = False # Stop the agent if listener stops
                    break 
                
                await self.update_last_active_time() # Periodically update last active time
                await asyncio.sleep(settings.AGENT_RUNNER_HEARTBEAT_INTERVAL) # Check periodically

            # If loop exits due to self._running becoming False (e.g. signal)
            if self.pubsub_handler_task and not self.pubsub_handler_task.done():
                self.logger.info(f"[{self.agent_id}] run_loop stopping, cancelling PubSub listener task...")
                self.pubsub_handler_task.cancel()
                try:
                    await self.pubsub_handler_task # Ensure cancellation is processed
                except asyncio.CancelledError:
                    self.logger.info(f"[{self.agent_id}] PubSub listener task successfully cancelled after run_loop stop.") # MODIFIED
                except Exception as e:                    
                    self.logger.error(f"[{self.agent_id}] Exception while awaiting PubSub listener task after run_loop stop: {e}", exc_info=True) # MODIFIED

        except asyncio.CancelledError:
            self.logger.info(f"[{self.agent_id}] run_loop was cancelled.")
            if self.pubsub_handler_task and not self.pubsub_handler_task.done():
                self.logger.info(f"[{self.agent_id}] run_loop cancelled, cancelling PubSub listener task...") # ADDED
                self.pubsub_handler_task.cancel()
                # Wait for it to actually cancel
                try:
                    await self.pubsub_handler_task # ADDED
                except asyncio.CancelledError:
                    self.logger.info(f"[{self.agent_id}] PubSub listener task successfully cancelled after run_loop cancellation.") # MODIFIED
                except Exception as e: # ADDED
                    self.logger.error(f"[{self.agent_id}] Exception while awaiting PubSub listener task after run_loop cancellation: {e}", exc_info=True) # ADDED
        finally:
            self.logger.info(f"[{self.agent_id}] Exiting run_loop.")


    async def cleanup(self) -> None:
        """
        Очищает ресурсы, используемые AgentRunner.

        Выполняется при остановке компонента (штатной или из-за ошибки).
        Этапы очистки:
        1. Если задача `pubsub_handler_task` (прослушиватель Pub/Sub) активна, отменяет ее.
        2. Ожидает завершения `pubsub_handler_task`, обрабатывая `asyncio.CancelledError`
           или другие исключения, которые могут возникнуть при ожидании.
        3. Устанавливает `self.pubsub_handler_task = None`.
        4. Если `self.redis_pubsub_client` (клиент Redis для команд) существует:
            - Пытается закрыть соединение с клиентом Redis.
            - Логирует успешное закрытие или ошибку при закрытии.
            - Устанавливает `self.redis_pubsub_client = None`.
        5. Устанавливает статус агента "остановлен" в Redis через `StatusUpdater`.
        6. Вызывает `cleanup_status_updater` для очистки ресурсов, используемых `StatusUpdater`
           (например, его собственного клиента Redis). По умолчанию статус "остановлен" не удаляется из Redis.
        7. Логирует завершение очистки.
        """
        self.logger.info(f"[{self.agent_id}] Starting cleanup...")
        
        # Stop the Pub/Sub listener task if it's running
        if self.pubsub_handler_task and not self.pubsub_handler_task.done():
            self.logger.info(f"[{self.agent_id}] Cancelling PubSub listener task during cleanup...")
            self.pubsub_handler_task.cancel()
            try:
                await self.pubsub_handler_task # Ensure cancellation is processed
            except asyncio.CancelledError:
                self.logger.info(f"[{self.agent_id}] PubSub listener task successfully cancelled during cleanup.") # MODIFIED
            except Exception as e:
                 self.logger.error(f"[{self.agent_id}] Exception while awaiting PubSub listener task during cleanup: {e}", exc_info=True) # MODIFIED
        self.pubsub_handler_task = None

        # Close the dedicated Pub/Sub Redis client
        if self.redis_pubsub_client:
            try: # ADDED
                await self.redis_pubsub_client.close()
                self.logger.info(f"[{self.agent_id}] Closed dedicated Redis Pub/Sub client.") # ADDED
            except Exception as e_close_redis: # ADDED
                self.logger.error(f"[{self.agent_id}] Error closing dedicated Redis Pub/Sub client: {e_close_redis}", exc_info=True) # ADDED
            self.redis_pubsub_client = None # Ensure it's None after attempting to close
        
        # Mark as stopped in Redis (via StatusUpdater)
        # Decide if status should be cleared from Redis on normal shutdown.
        # For now, just mark as stopped. ProcessManager might have other policies.
        await self.mark_as_stopped(reason="AgentRunner cleanup initiated.")
        
        # Cleanup StatusUpdater's Redis client (the one used for status updates)
        # clear_status_on_cleanup=False by default, meaning the "stopped" status remains.
        # Set to True if the key itself should be deleted.
        await self.cleanup_status_updater(clear_status_on_cleanup=False) 

        self.logger.info(f"[{self.agent_id}] Cleanup complete.")

# Helper function convert_db_history_to_langchain_static can remain as is or be moved
# into the class if it makes more sense contextually.
# For now, it's kept as a static helper.
