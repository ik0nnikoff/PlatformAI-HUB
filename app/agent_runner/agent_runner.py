import asyncio
import logging
import os
import json
import uuid
import time
from typing import Dict, Optional, Any, List

import httpx
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from langchain_core.messages import BaseMessage, HumanMessage, AIMessage
from datetime import datetime, timezone

import redis.asyncio as redis
from redis import exceptions as redis_exceptions

from app.agent_runner.langgraph_factory import create_agent_app
from app.agent_runner.langgraph_models import TokenUsageData
from app.db.alchemy_models import ChatMessageDB, SenderType
from app.db.crud import chat_crud
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

        self.config_url = str
        self.agent_config: Optional[Dict] = None
        self.agent_app: Optional[Any] = None
        # self.pubsub_handler_task: Optional[asyncio.Task] = None # Удалено, т.к. задача управляется ServiceComponentBase
        self.static_state_config: Optional[Dict] = None

        self.logger.info(f"AgentRunner for agent {self._component_id} initialized. PID: {os.getpid()}")

    async def _load_and_prepare_config(self) -> bool:
        """
        Загружает конфигурацию агента и подготавливает необходимые параметры.

        Returns:
            bool: True, если конфигурация успешно загружена и подготовлена, иначе False.
        """
        self.config_url = f"http://{settings.MANAGER_HOST}:{settings.MANAGER_PORT}{settings.API_V1_STR}/agents/{self._component_id}/config"

        self.logger.info(f"[{self._component_id}] Fetching configuration from {self.config_url}...")
        self.agent_config = await fetch_config_static(self.config_url, self.logger)
        if not self.agent_config:
            self.logger.error(f"[{self._component_id}] Failed to fetch or invalid configuration from {self.config_url}.")
            return False

        # Extract static state config if available
        self.static_state_config = {}
        self.logger.info(f"[{self._component_id}] Agent configuration loaded and prepared successfully.")
        return True

    async def _setup_langgraph_app(self) -> bool:
        """
        Создает и настраивает приложение LangGraph на основе загруженной конфигурации.

        Returns:
            bool: True, если приложение успешно создано и настроено, иначе False.
        """
        self.logger.info(f"[{self._component_id}] Creating LangGraph application...")
        try:
            self.agent_app, static_state_config = create_agent_app(self.agent_config, self._component_id)
            self.static_state_config = static_state_config # Store for use in _process_message
        except Exception as e:
            self.logger.error(f"[{self._component_id}] Failed to create LangGraph application: {e}", exc_info=True)
            return False

        self.logger.info(f"[{self._component_id}] LangGraph application created successfully.")
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
            self.logger.error(f"[{self._component_id}] Redis client not available for handling pubsub message: {e}")
            return
        
        data_str: Optional[str] = None
        payload: Optional[Dict[str, Any]] = None # Инициализируем payload
        try:
            data_str = message_data.decode('utf-8')
            payload = json.loads(data_str)
            self.logger.info(f"[{self._component_id}] Processing message for chat_id: {payload.get('chat_id')}")

            chat_id = payload.get("chat_id")
            user_input = payload.get("text")
            interaction_id = payload.get("interaction_id") # Important for tracking

            if not chat_id or user_input is None or not interaction_id:
                self.logger.warning(f"[{self._component_id}] Missing chat_id, text, or interaction_id in payload: {payload}")
                return

            # Determine context memory depth from agent_config
            enable_context_memory = self.agent_config.get("enableContextMemory", True)
            context_memory_depth = self.agent_config.get("contextMemoryDepth", 10) # Default to 10 if not specified
            if not enable_context_memory:
                self.logger.info(f"[{self._component_id}] Context memory is disabled by agent config. History will not be loaded with depth.")
                actual_history_limit = 0
            else:
                actual_history_limit = context_memory_depth
            
            self.logger.info(f"[{self._component_id}] Using history limit: {actual_history_limit} (enabled: {enable_context_memory}, configured depth: {context_memory_depth})")

            history_messages_db: List[ChatMessageDB] = []
            if self.db_session_factory:
                async with self.db_session_factory() as db_session:
                    # Save user message
                    user_msg_db = await chat_crud.db_add_chat_message(
                        db=db_session,
                        agent_id=self._component_id,
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
                            f"[{self._component_id}] Key '{static_key}' not found in static_state_config. "
                            f"Graph key '{graph_key}' may be missing. "
                            f"Check agent configuration and langgraph_factory.py."
                        )
                        # Provide a default or handle absence as needed by your graph's logic
                        if graph_key == "system_prompt":
                            initial_graph_input[graph_key] = "" # Default for system_prompt
                        # Add defaults for other critical keys if necessary
                        elif graph_key == "model_id":
                            self.logger.error(f"[{self._component_id}] Critical key 'model_id' is missing from static_state_config. Graph execution will likely fail.")
                            initial_graph_input[graph_key] = "default_model_id_placeholder" # Example placeholder
                        elif graph_key == "provider":
                            self.logger.error(f"[{self._component_id}] Critical key 'provider' is missing from static_state_config. Graph execution will likely fail.")
                            initial_graph_input[graph_key] = "default_provider_placeholder" # Example placeholder
            else:
                self.logger.error(
                    f"[{self._component_id}] static_state_config is not set. Critical parameters like system_prompt, temperature, etc., will be missing."
                )
                # Set defaults for critical keys if static_state_config is missing entirely
                initial_graph_input["system_prompt"] = ""
                # Defaults for other keys might be needed here if the graph cannot proceed without them

            # Invoke agent
            self.logger.debug(f"[{self._component_id}] Invoking agent for interaction_id: {interaction_id} with input: {initial_graph_input}") # Log the input
            agent_response_stream = self.agent_app.astream(
                initial_graph_input, # Use the input dict that now includes system_prompt and other params
                config={"configurable": {"thread_id": chat_id, "agent_id": self._component_id}}
            )
            
            extracted_text_content = "" # Initialize to store the final text
            token_usage_data: Optional[TokenUsageData] = None
            
            self.logger.debug(f"[{self._component_id}] Starting to process agent_response_stream for interaction_id: {interaction_id}")
            async for event_part in agent_response_stream:
                self.logger.info(f"[{self._component_id}] Stream event_part: {event_part}") # DEBUG LINE
                if isinstance(event_part, dict):
                    # Corrected path to messages based on new logs
                    messages_chunk = event_part.get('agent_node', {}).get('messages', [])
                    if messages_chunk and isinstance(messages_chunk, list):
                        for msg_item in messages_chunk:
                            if isinstance(msg_item, AIMessage):
                                self.logger.info(f"[{self._component_id}] Processing AIMessage from stream. Current extracted_text_content='{extracted_text_content[:100]}...'. New msg_item.content='{msg_item.content[:100]}...'. Interaction ID: {interaction_id}")
                                extracted_text_content = msg_item.content
                                self.logger.info(f"[{self._component_id}] Updated extracted_text_content to: '{extracted_text_content[:100]}...'. Interaction ID: {interaction_id}")
                                
                                if hasattr(msg_item, 'usage_metadata') and msg_item.usage_metadata:
                                    usage_metadata = msg_item.usage_metadata
                                    usage_from_msg = usage_metadata.get("token_usage") or usage_metadata.get("usage_metadata")
                                    if usage_from_msg:
                                        try:
                                            token_usage_data = TokenUsageData(**usage_from_msg)
                                            self.logger.debug(f"[{self._component_id}] Token usage from AIMessage metadata: {token_usage_data}")
                                        except Exception as e_token_msg:
                                            self.logger.warning(f"[{self._component_id}] Could not parse token_usage from AIMessage metadata: {usage_from_msg}, error: {e_token_msg}")
                                    else:
                                        self.logger.debug(f"[{self._component_id}] usage_metadata present but does not contain all required TokenUsageData fields: {usage_metadata}")
                    
                    # Fallback или альтернативный способ получить token usage, если он напрямую в event_part
                    if token_usage_data is None and "token_usage" in event_part and event_part["token_usage"]:
                        try:
                            token_usage_data = TokenUsageData(**event_part["token_usage"])
                            self.logger.debug(f"[{self._component_id}] Token usage data from event part: {token_usage_data}")
                        except Exception as e_token_event:
                            self.logger.warning(f"[{self._component_id}] Could not parse token_usage from event_part: {event_part.get('token_usage')}, error: {e_token_event}")

                    # Новый fallback: если есть token_usage_events (список), взять первый (или все)
                    if token_usage_data is None and "token_usage_events" in event_part and event_part["token_usage_events"]:
                        try:
                            tu_event = event_part["token_usage_events"][0]
                            if isinstance(tu_event, dict):
                                token_usage_data = TokenUsageData(**tu_event)
                            else:
                                token_usage_data = tu_event
                            self.logger.debug(f"[{self._component_id}] Token usage from token_usage_events: {token_usage_data}")
                        except Exception as e_token_event:
                            self.logger.warning(f"[{self._component_id}] Could not parse token_usage from token_usage_events: {event_part.get('token_usage_events')}, error: {e_token_event}")

            self.logger.info(f"[{self._component_id}] Finished processing agent_response_stream for interaction_id: {interaction_id}")
            full_response_content = extracted_text_content # Use the extracted content

            self.logger.info(f"[{self._component_id}] Final extracted_text_content for interaction_id {interaction_id}: '{full_response_content}'")
            self.logger.info(f"[{self._component_id}] Agent response for interaction_id {interaction_id} (preview from full_response_content): {full_response_content[:100]}...")

            if self.db_session_factory:
                async with self.db_session_factory() as db_session:
                    # Save agent response
                    if full_response_content: # Only save if there's content
                        await chat_crud.db_add_chat_message(
                            db=db_session,
                            agent_id=self._component_id,
                            thread_id=chat_id, # Changed from chat_id to thread_id
                            sender_type=SenderType.AGENT,
                            content=full_response_content,
                            channel=payload.get("channel"), # Agent responds on the same channel
                            timestamp=datetime.now(timezone.utc),
                            interaction_id=interaction_id # Link AI response to the same interaction
                        )
                    # Save token usage if available
                    self.logger.info(f"[{self._component_id}] token_usage_data before publish: {token_usage_data}")
                    if token_usage_data:
                        # Формируем payload для очереди token_usage
                        token_usage_payload = {
                            "agent_id": self._component_id,
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
                        await redis_cli.publish(
                            settings.REDIS_TOKEN_USAGE_QUEUE_NAME,
                            json.dumps(token_usage_payload)
                        )
                        self.logger.info(f"[{self._component_id}] Published token usage to queue {settings.REDIS_TOKEN_USAGE_QUEUE_NAME}")
                        self.logger.info(f"[{self._component_id}] Published token usage event to Redis queue: {settings.REDIS_TOKEN_USAGE_QUEUE_NAME} | Data: {token_usage_payload}")

            # Publish response back to a response channel (e.g., agent_responses:{chat_id})
            response_channel = f"agent:{self._component_id}:output" # NEW - MATCHES TELEGRAM BOT
            response_payload = {
                "chat_id": chat_id,
                "thread_id": chat_id, # ADDED thread_id
                "agent_id": self._component_id,
                "interaction_id": interaction_id,
                "response": full_response_content,
                "timestamp": datetime.now(timezone.utc).isoformat()
            }

            await redis_cli.publish(response_channel, json.dumps(response_payload))
            self.logger.debug(f"[{self._component_id}] Published to {response_channel} response: {json.dumps(response_payload)}")

            await self.update_last_active_time()

        except json.JSONDecodeError as e:
            self.logger.error(f"[{self._component_id}] JSONDecodeError processing PubSub message: {e}. Data: {data_str}", exc_info=True)
        except Exception as e:
            self.logger.error(f"[{self._component_id}] Error processing PubSub message: {e}", exc_info=True)
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
                    self.logger.info(f"[{self._component_id}] Published error notification to {error_response_channel}")
                except Exception as pub_err:
                    self.logger.error(f"[{self._component_id}] Failed to publish error notification: {pub_err}", exc_info=True)


    async def _save_history_to_redis_queue(self, agent_id: str, chat_id: str, interaction_id: str,
                                           message_type: str, message_content: str) -> None:
        """
        Сохраняет историю сообщений в очередь Redis для последующей обработки.

        Args:
            agent_id (str): Идентификатор агента.
            chat_id (str): Идентификатор чата.
            interaction_id (str): Идентификатор взаимодействия.
            message_type (str): Тип сообщения (например, "user" или "agent").
            message_content (str): Содержимое сообщения.
        """
        message_to_save = {
            "agent_id": agent_id,
            "chat_id": chat_id,
            "interaction_id": interaction_id,
            "message_type": message_type,
            "message_content": message_content,
            "timestamp": datetime.now(timezone.utc).isoformat()
        }
        try:
            # Use self.redis_client for LPUSH
            redis_cli = await self.redis_client
            await redis_cli.lpush(settings.REDIS_HISTORY_QUEUE_NAME, json.dumps(message_to_save))
            self.logger.info(f"[{self._component_id}] Message {message_type} for interaction {interaction_id} pushed to history queue.")
        except redis_exceptions.RedisError as e:
            self.logger.error(f"[{self._component_id}] Redis error pushing message to history queue: {e}", exc_info=True)
        except Exception as e:
            self.logger.error(f"[{self._component_id}] Failed to push message to history queue for interaction {interaction_id}: {e}", exc_info=True)


    # --- Core Lifecycle Methods (setup, run_loop, cleanup from ServiceComponentBase) ---

    async def setup(self) -> None:
        """
        Настраивает AgentRunner.
        Вызывает super().setup() для инициализации StatusUpdater и RedisClientManager.
        Затем загружает конфигурацию агента и настраивает приложение LangGraph.
        """
        self.logger.info(f"[{self._component_id}] AgentRunner setup started.")
        await super().setup() # This calls ServiceComponentBase.setup() which handles StatusUpdater, Redis init, and clears needs_restart and _main_tasks

        if not await self._load_and_prepare_config():
            self.logger.critical(f"[{self._component_id}] Failed to load or prepare agent configuration. AgentRunner cannot start.")
            await self.mark_as_error(reason="Failed to load agent config")
            raise RuntimeError("Agent configuration failed to load.")

        if not await self._setup_langgraph_app():
            self.logger.critical(f"[{self._component_id}] Failed to setup LangGraph application. AgentRunner cannot start.")
            await self.mark_as_error(reason="Failed to setup LangGraph app")
            raise RuntimeError("LangGraph application setup failed.")
        
        self.logger.info(f"[{self._component_id}] AgentRunner setup completed successfully.")


    async def run_loop(self) -> None:
        """
        Основной цикл работы AgentRunner.
        Регистрирует слушателя Pub/Sub как основную задачу и передает управление
        в `super().run_loop()` для ее выполнения и мониторинга.
        """
        if not self.agent_app:
            self.logger.error(f"[{self._component_id}] Agent application not initialized. Cannot start run_loop.")
            await self.mark_as_error("Agent application not initialized")
            # self.initiate_shutdown() # ServiceComponentBase.run_loop handles shutdown if _running becomes False
            return

        self.logger.info(f"[{self._component_id}] AgentRunner run_loop starting...")
        
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
            self.logger.info(f"[{self._component_id}] AgentRunner run_loop finished.")
            self._running = False 


    async def cleanup(self) -> None:
        """
        Очищает ресурсы AgentRunner.
        Вызывает super().cleanup() для отмены зарегистрированных задач (включая pubsub_handler_task),
        закрытия RedisClientManager и обновления статуса.
        """
        self.logger.info(f"[{self._component_id}] AgentRunner cleanup started.")
        
        # self.pubsub_handler_task будет отменен в super().cleanup(), так как он был зарегистрирован.
        # Дополнительная логика отмены здесь не нужна, если только нет специфичных для AgentRunner задач,
        # которые не были зарегистрированы через _register_main_task.

        # LangGraph app cleanup (if any specific method exists)
        if hasattr(self.agent_app, 'cleanup'):
            try:
                self.logger.info(f"[{self._component_id}] Cleaning up LangGraph application.")
                # Убедимся, что это корутина, если это так
                if asyncio.iscoroutinefunction(self.agent_app.cleanup):
                    await self.agent_app.cleanup()
                elif callable(self.agent_app.cleanup):
                    self.agent_app.cleanup() # Если это обычная функция
                else:
                    self.logger.warning(f"[{self._component_id}] agent_app.cleanup is not callable or a coroutine function.")
            except Exception as e:
                self.logger.error(f"[{self._component_id}] Error during LangGraph application cleanup: {e}", exc_info=True)

        self.agent_app = None
        self.agent_config = None
        self.static_state_config = None
        self.pubsub_handler_task = None # Очищаем ссылку

        await super().cleanup() # Это вызовет отмену self.pubsub_handler_task и другие базовые очистки
        self.logger.info(f"[{self._component_id}] AgentRunner cleanup finished.")

    # Removed get_restart_flag and set_restart_flag, using self.needs_restart directly
    # The main runner loop in runner_main.py will check agent_runner.needs_restart

# Helper function convert_db_history_to_langchain_static can remain as is or be moved
# into the class if it makes more sense contextually.
# For now, it's kept as a static helper.

# Removed _run_signal_handler, as shutdown is handled by RunnableComponent.initiate_shutdown()
# The main `run` method from RunnableComponent (via ServiceComponentBase) orchestrates setup, run_loop, cleanup.
