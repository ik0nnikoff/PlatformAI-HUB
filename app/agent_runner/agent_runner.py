"""
Agent Runner for LangGraph-based AI Agents in PlatformAI Hub.

This module provides the core agent execution functionality including:
- LangGraph workflow orchestration
- Voice_v2 integration for TTS decisions
- Redis-based state management
- Performance monitoring and metrics
- Agent lifecycle management
"""

import asyncio
import json
import logging
import os
from typing import Any, Dict, List, Optional, Tuple

import httpx
from langchain_core.messages import BaseMessage
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.services.redis_wrapper import RedisService

from app.agent_runner.common.config_mixin import AgentConfigMixin
from app.agent_runner.handlers.history_handler import HistoryManager
from app.agent_runner.handlers.message_handler import MessageProcessor
from app.agent_runner.handlers.token_handler import TokenManager
from app.agent_runner.langgraph.factory import create_agent_app
from app.agent_runner.models.contexts import (
    ProcessingContext,
    InvocationContext,
    GraphInputContext,
    ResponseContext,
    HistorySaveContext,
    TokenContext
)
from app.core.base.service_component import ServiceComponentBase
from app.core.config import settings
from app.services.voice_v2.core.orchestrator import VoiceServiceOrchestrator

# --- Helper Functions (some might become methods or stay as utilities) ---


async def fetch_config(config_url: str, logger: logging.Logger) -> Optional[Dict]:
    """Fetches agent configuration from the management service using httpx."""
    logger.info(f"Fetching configuration from: {config_url}")
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.get(config_url)
            # Raises HTTPStatusError for 4xx/5xx responses
            response.raise_for_status()
            config_data = response.json()
        logger.info("Configuration fetched successfully.")
        return config_data
    except httpx.TimeoutException:
        logger.error(f"Timeout fetching configuration from {config_url}.")
    except httpx.RequestError as e:
        logger.error(
            f"Failed to fetch configuration from {config_url} due to request error: {e}"
        )
    except json.JSONDecodeError as e:
        logger.error(
            f"Failed to decode JSON from configuration response ({config_url}): {e}"
        )
    except Exception as e:  # Catch-all for unexpected errors
        logger.error(
            f"Unexpected error fetching configuration from {config_url}: {e}",
            exc_info=True
        )
    return None


class AgentRunner(ServiceComponentBase, AgentConfigMixin):
    """
    Управляет жизненным циклом и выполнением одного экземпляра агента.
    Наследуется от ServiceComponentBase для унифицированного управления
    состоянием и жизненным циклом.

    """

    def __init__(
        self,
        agent_id: str,
        db_session_factory: Optional[async_sessionmaker[AsyncSession]],
        logger_adapter: logging.LoggerAdapter,
    ):
        """Инициализатор AgentRunner."""
        super().__init__(
            component_id=agent_id,
            status_key_prefix="agent_status:",
            logger_adapter=logger_adapter
        )

        # Initialize AgentConfigMixin
        AgentConfigMixin.__init__(self)

        self.db_session_factory = db_session_factory
        self._pubsub_channel = f"agent:{self._component_id}:input"
        self.response_channel = f"agent:{self._component_id}:output"

        self.config_url = str
        self.config: Optional[Dict] = None
        self.agent_config: Optional[Dict] = None
        self.agent_app: Optional[Any] = None

        # Voice processing orchestrator
        self.voice_orchestrator: Optional[VoiceServiceOrchestrator] = None

        # Initialize handlers
        self.message_processor = MessageProcessor(self._component_id, self.logger)
        self.history_manager = HistoryManager(
            self._component_id, db_session_factory, self.logger
        )
        self.token_manager = TokenManager(self._component_id, self.logger)

        self.logger.info(
            "AgentRunner for agent %s initialized. PID: %s",
            self._component_id, os.getpid()
        )

    async def _load_config(self) -> bool:
        """
        Загружает конфигурацию агента.

        """
        self.config_url = (
            f"http://{settings.MANAGER_HOST}:{settings.MANAGER_PORT}"
            f"{settings.API_V1_STR}/agents/{self._component_id}/config"
        )

        self.agent_config = await fetch_config(self.config_url, self.logger)
        if not self.agent_config:
            self.logger.error(
                "Failed to fetch or invalid configuration from %s.",
                self.config_url
            )
            return False

        # Extract any additional config if needed in future
        self.logger.info("Agent configuration loaded and prepared successfully.")
        return True

    async def _setup_app(self) -> bool:
        """Создает и настраивает приложение LangGraph на основе загруженной конфигурации."""
        self.logger.info("Creating LangGraph application...")
        try:
            self.agent_app = create_agent_app(
                self.agent_config, self._component_id, self.logger
            )
            # Note: Configurations extracted directly from agent_config as needed
        except Exception as e:
            self.logger.error("Failed to create LangGraph application: %s", e, exc_info=True)
            return False

        # Note: Voice capabilities are already available through LangGraph tools
        # VoiceServiceOrchestrator initialization is not required since voice_v2 tools are loaded
        # await self._setup_voice_orchestrator()

        self.logger.info("LangGraph application created successfully.")
        return True

    async def _handle_pubsub_message(self, message_data: bytes) -> None:
        """Обрабатывает входящее сообщение от Redis Pub/Sub."""
        try:
            redis_cli = await self.redis_client
        except RuntimeError as exc:
            self.logger.error(
                "Redis client not available for handling pubsub message: %s", exc
            )
            return

        # Parse payload
        payload = await self.message_processor.parse_pubsub_payload(message_data)
        if payload is None:
            return

        # Validate payload
        if not self.message_processor.validate_payload(payload):
            return

        try:
            # Extract data from payload
            data = self.message_processor.extract_payload_data(payload)

            # Create processing context
            context = ProcessingContext(**data)

            # Process user message
            response_content, audio_url = await self._process_user_message(context)

            # Publish response
            response_ctx = ResponseContext(
                chat_id=context.chat_id,
                response_content=response_content,
                channel=context.channel,
                audio_url=audio_url,
                response_channel=self.response_channel
            )
            await self.message_processor.publish_response(response_ctx, redis_cli)

            await self.update_last_active_time()

        except Exception as exc:
            self.logger.error("Error processing PubSub message: %s", exc, exc_info=True)
            await self.message_processor.publish_error_notification(payload, exc, redis_cli)

    async def _process_user_message(self, context: ProcessingContext) -> Tuple[str, Optional[str]]:
        """Обрабатывает сообщение пользователя через агента."""
        # Save user message
        save_ctx = HistorySaveContext(
            sender_type="user",
            thread_id=context.chat_id,
            content=context.user_text,
            channel=context.channel,
            interaction_id=context.interaction_id,
        )
        await self.history_manager.save_history(save_ctx, redis_client=self.redis_client)

        # Get chat history
        history_db = await self.history_manager.get_history(
            thread_id=context.chat_id,
            redis_client=self.redis_client,
            agent_config=self.agent_config
        )

        # Invoke agent
        invocation_context = InvocationContext(
            user_input=context.user_text,
            user_data=context.user_data,
            thread_id=context.chat_id,
            channel=context.channel,
            interaction_id=context.interaction_id,
            image_urls=context.image_urls,
        )
        response_content, _, audio_url = await self._invoke_agent(
            history_db, invocation_context
        )

        # Save agent response
        response_save_ctx = HistorySaveContext(
            sender_type="agent",
            thread_id=context.chat_id,
            content=response_content,
            channel=context.channel,
            interaction_id=context.interaction_id,
        )
        await self.history_manager.save_history(response_save_ctx, redis_client=self.redis_client)

        # Save token usage
        token_ctx = TokenContext(
            interaction_id=context.interaction_id,
            thread_id=context.chat_id,
            agent_app=self.agent_app,
            config=self.config
        )
        await self.token_manager.save_tokens(token_ctx, redis_client=self.redis_client)

        return response_content, audio_url

    async def _invoke_agent(
        self,
        history_db: List[BaseMessage],
        context: InvocationContext,
    ) -> Tuple[str, Optional[BaseMessage], Optional[str]]:
        """Вызывает агента LangGraph для обработки пользовательского ввода и возвращает ответ."""
        # Prepare input based on images presence
        enhanced_user_input = context.user_input
        message_content = context.user_input

        if context.image_urls:
            enhanced_user_input, message_content = (
                self.message_processor.prepare_image_content(
                    context.user_input, context.image_urls
                )
            )

        # Prepare graph input
        context_dict = {
            "user_data": context.user_data,
            "channel": context.channel,
            "image_urls": context.image_urls,
            "thread_id": context.thread_id,
            "component_id": self._component_id
        }

        graph_ctx = GraphInputContext(
            history_db=history_db,
            message_content=message_content,
            user_input=context.user_input,
            enhanced_user_input=enhanced_user_input,
            context=context_dict
        )
        graph_input = self._prepare_graph_input(graph_ctx)

        # Process graph stream
        response_content, final_message = await self._process_graph_stream(graph_input)

        # Extract audio URL if available
        audio_url = await self._extract_audio_url()

        if audio_url:
            self.logger.info("TTS audio URL available: %s", audio_url)

        return response_content, final_message, audio_url

    async def _process_graph_stream(
        self, graph_input: Dict[str, Any]
    ) -> Tuple[str, Optional[BaseMessage]]:
        """Обрабатывает поток выполнения LangGraph."""
        self.logger.info(
            "Invoking graph for thread_id: %s (Initial history messages: %d)",
            self.config["configurable"]["thread_id"],
            len(graph_input["messages"]) - 1
        )

        response_content = "No response generated."
        final_message = None

        async for output in self.agent_app.astream(
            graph_input, self.config, stream_mode="updates"
        ):
            if not self._running or self.needs_restart:
                self.logger.warning("Shutdown or restart requested during graph stream.")
                break

            for key, value in output.items():
                self.logger.debug("Graph node '%s' output: %s", key, value)
                if key in ("agent", "generate"):
                    response_content, final_message = await self._extract_agent_response(
                        value, response_content, final_message
                    )
                elif key == "tools" or "tool" in key:
                    self.logger.debug("Tool node output: %s", key)

        self.logger.info(
            "Graph execution finished. Final response: %s...",
            response_content[:100]
        )

        return response_content, final_message

    def _prepare_graph_input(
        self, ctx: GraphInputContext
    ) -> Dict[str, Any]:
        """Подготавливает входные данные для LangGraph."""
        graph_input, self.config = self.message_processor.prepare_graph_input(ctx)
        return graph_input

    def _get_voice_settings_from_config(
        self, agent_config: Dict[str, Any]
    ) -> Optional[Dict[str, Any]]:
        """Извлекает настройки голоса из конфигурации агента."""
        return agent_config.get("voice_settings")

    async def _setup_voice_orchestrator(self) -> None:
        """
        Инициализирует Voice Service Orchestrator если в конфигурации
        агента есть голосовые настройки.
        """
        try:
            voice_settings = self._get_voice_settings_from_config(
                self.agent_config
            )
            if not voice_settings or not voice_settings.get('enabled', False):
                self.logger.debug("Voice settings not enabled for agent %s", self._component_id)
                return

            # Создаем Redis service wrapper для VoiceOrchestrator
            redis_service = RedisService()
            await redis_service.initialize()

            # Инициализируем orchestrator
            self.voice_orchestrator = VoiceServiceOrchestrator()

            await self.voice_orchestrator.initialize()

            self.logger.info(
                "Voice orchestrator initialized successfully for agent %s",
                self._component_id
            )

        except Exception as e:
            self.logger.error("Error setting up voice orchestrator: %s", e, exc_info=True)
            self.voice_orchestrator = None

    async def _extract_agent_response(
        self, value: Dict, current_response: str, current_message: Optional[BaseMessage]
    ) -> Tuple[str, Optional[BaseMessage]]:
        """Извлекает ответ агента из вывода node."""
        return self.message_processor.extract_agent_response(
            value, current_response, current_message
        )

    async def _extract_audio_url(self) -> Optional[str]:
        """Извлекает audio_url из финального состояния LangGraph."""
        try:
            final_state = self.agent_app.get_state(self.config)
            self.logger.info("Final state type: %s", type(final_state))
            self.logger.info(
                "Final state has values: %s",
                hasattr(final_state, 'values') if final_state else 'No final_state'
            )

            if final_state and hasattr(final_state, 'values') and final_state.values:
                self.logger.info(
                    "Final state values keys: %s",
                    list(final_state.values.keys())
                )
                self.logger.debug("Full final state values: %s", final_state.values)

                state_audio_url = final_state.values.get('audio_url')
                if state_audio_url:
                    self.logger.info(
                        "✅ Extracted audio_url from final state: %s",
                        state_audio_url
                    )
                    await self._clear_audio_url_from_state()
                    return state_audio_url

                self.logger.debug("❌ No audio_url found in final state values")

            self.logger.debug("❌ Final state or values not available")

        except Exception as exc:
            self.logger.error(
                "Could not extract audio_url from final state: %s", exc, exc_info=True
            )

        return None

    async def _clear_audio_url_from_state(self) -> None:
        """Очищает audio_url из состояния LangGraph."""
        try:
            self.agent_app.update_state(
                config=self.config,
                values={"audio_url": None},
                as_node=None  # Don't trigger any subsequent nodes
            )
            self.logger.info(
                "🧹 Cleared audio_url from LangGraph state to prevent persistence"
            )
        except Exception as clear_error:
            self.logger.error(
                "Failed to clear audio_url from state: %s", clear_error, exc_info=True
            )

    async def cleanup(self) -> None:
        """Очищает ресурсы AgentRunner."""
        self.logger.info("AgentRunner cleanup started.")

        try:
            # Cleanup voice orchestrator
            if self.voice_orchestrator:
                await self.voice_orchestrator.cleanup()
                self.voice_orchestrator = None

            # Cleanup history manager
            await self.history_manager.cleanup(self.redis_client)

        except RuntimeError as e:
            self.logger.error("Redis client not available during cleanup: %s", e)

        # LangGraph app cleanup (if any specific method exists)
        if hasattr(self.agent_app, "cleanup"):
            try:
                self.logger.info("Cleaning up LangGraph application.")
                # Убедимся, что это корутина, если это так
                if asyncio.iscoroutinefunction(self.agent_app.cleanup):
                    await self.agent_app.cleanup()
                elif callable(self.agent_app.cleanup):
                    self.agent_app.cleanup()  # Если это обычная функция
                else:
                    self.logger.warning(
                        "agent_app.cleanup is not callable or a coroutine function."
                    )
            except Exception as e:
                self.logger.error(
                    "Error during LangGraph application cleanup: %s", e, exc_info=True
                )

        self.agent_app = None
        self.agent_config = None
        self.config = None
        self.config_url = None

        await super().cleanup()
        self.logger.info("AgentRunner cleanup finished.")

    async def mark_as_error(self, error_message: str, details: Optional[Dict[str, Any]] = None):
        """Marks the component's status as 'error'."""
        # Support both old 'error_type' parameter and new 'details' parameter
        if details is None:
            details = {"error_type": "UnknownError"}
        elif isinstance(details, str):
            # Support legacy string parameter
            details = {"error_type": details}
        error_type = details.get("error_type", "UnknownError")
        self.logger.error(
            "Marking component as error. Error Type: %s, Message: %s", error_type, error_message
        )
        # Use inherited StatusUpdater methods directly
        await self.set_status("error", {"error_message": error_message, **details})

    async def _handle_status_update_exception(
        self, e: Exception, phase: str
    ):
        """
        Handles exceptions during status updates, logging them
        and marking the component as error.
        """
        error_message = f"Failed to update status during {phase}: {e}"
        self.logger.error(error_message, exc_info=True)
        # Use inherited StatusUpdater methods directly
        await self.set_status(
            "error",
            {
                "error_message": (
                    f"Internal error: Failed to update status during {phase}."
                ),
                "error_type": "StatusUpdateError",
            },
        )

    async def run_loop(self) -> None:
        """Основной цикл работы AgentRunner."""
        if not self.agent_app:
            self.logger.error(
                "Agent application not initialized. Cannot start run_loop."
            )
            await self.mark_as_error(
                "Agent application not initialized",
                {"error_type": "ConfigurationError"}
            )
            return

        self.logger.info("AgentRunner run_loop starting...")

        # Регистрируем _pubsub_listener_loop как основную задачу
        # self._pubsub_channel уже установлен в __init__
        self._register_main_task(self._pubsub_listener_loop(), name="AgentPubSubListener")

        try:
            await super().run_loop()
        except Exception as e:
            self.logger.critical(
                "Unexpected error in AgentRunner run_loop: %s", e, exc_info=True
            )
            self._running = False
            self.clear_restart_request()
            await self.mark_as_error(
                f"Run_loop critical error: {e}", {"error_type": "RuntimeError"}
            )
        finally:
            self.logger.info("AgentRunner run_loop finished.")
            self._running = False

    async def setup(self) -> None:
        """
        Настройка AgentRunner.
        Загружает конфигурацию и создает приложение LangGraph.
        """
        self.logger.info("Setting up AgentRunner...")

        # Call parent setup
        await super().setup()

        # Load configuration
        if not await self._load_config():
            self.logger.critical(
                "Failed to load configuration. AgentRunner cannot start."
            )
            await self.mark_as_error(
                "Failed to load configuration", {"error_type": "ConfigurationError"}
            )
            raise RuntimeError("Configuration loading failed.")

        # Setup LangGraph application
        if not await self._setup_app():
            self.logger.critical(
                "Failed to setup LangGraph application. AgentRunner cannot start."
            )
            await self.mark_as_error(
                "Failed to setup LangGraph app", {"error_type": "ConfigurationError"}
            )
            raise RuntimeError("LangGraph application setup failed.")

        self.logger.debug("AgentRunner setup completed successfully.")
