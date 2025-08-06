"""
Message handling utilities for AgentRunner.

This module provides message processing and response handling functionality.
"""

import json
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple, Union

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage

from app.agent_runner.models.contexts import GraphInputContext, ResponseContext
from app.core.config import settings


class MessageProcessor:
    """Handles message processing and formatting for agents."""

    def __init__(self, component_id: str, logger):
        self.component_id = component_id
        self.logger = logger

    async def parse_pubsub_payload(self, message_data: bytes) -> Optional[Dict[str, Any]]:
        """Парсит payload из сообщения Redis Pub/Sub."""
        try:
            data_str = message_data.decode("utf-8")
            payload = json.loads(data_str)
            self.logger.info("Processing message for chat_id: %s", payload.get('chat_id'))
            return payload
        except json.JSONDecodeError as exc:
            self.logger.error(
                "JSONDecodeError processing PubSub message: %s. Data: %s",
                exc, message_data.decode("utf-8", errors="replace"), exc_info=True
            )
            return None

    def validate_payload(self, payload: Dict[str, Any]) -> bool:
        """Валидирует обязательные поля в payload."""
        chat_id = payload.get("chat_id")
        user_text = payload.get("text")

        if not chat_id or user_text is None:
            self.logger.warning(
                "Missing 'text' or 'chat_id' in Redis payload: %s", payload
            )
            return False

        return True

    def extract_payload_data(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Извлекает данные из payload."""
        user_text = payload.get("text")
        chat_id = payload.get("chat_id")
        user_data = payload.get("user_data", {})
        channel = payload.get("channel", "unknown")
        platform_id = payload.get("platform_user_id")
        image_urls = payload.get("image_urls", [])

        interaction_id = str(uuid.uuid4())
        self.logger.debug(
            "Generated InteractionID: %s for Thread: %s", interaction_id, chat_id
        )

        if image_urls:
            self.logger.info(
                "Processing message with %d images for chat_id: %s",
                len(image_urls), chat_id
            )

        return {
            "user_text": user_text,
            "chat_id": chat_id,
            "user_data": user_data,
            "channel": channel,
            "platform_id": platform_id,
            "image_urls": image_urls,
            "interaction_id": interaction_id,
        }

    def prepare_image_content(
        self, user_input: str, image_urls: List[str]
    ) -> Tuple[str, Union[str, List]]:
        """Подготавливает контент с изображениями в зависимости от режима обработки."""
        image_count = len(image_urls)
        self.logger.info(
            "Enhanced user input with image information: %d images attached",
            image_count
        )

        if settings.IMAGE_VISION_MODE == "url":
            # URL mode: Create multimodal message with image URLs for direct Vision API
            self.logger.info(
                "Using URL mode - creating multimodal message with %d images",
                image_count
            )
            content_parts = [{"type": "text", "text": user_input}]
            for url in image_urls:
                content_parts.append({"type": "image_url", "image_url": {"url": url}})
            message_content = content_parts
            enhanced_user_input = user_input  # Keep original text for graph input
        else:
            # Binary mode: Use text instructions for Vision tools
            self.logger.info("Using binary mode - creating text instructions for Vision tools")
            if user_input.strip():
                image_info = (
                    f"[ВАЖНО: Пользователь прикрепил {image_count} изображение(я). "
                    f"ОБЯЗАТЕЛЬНО вызови функцию analyze_images с этими URL: {image_urls} "
                    "для анализа изображений перед ответом.] "
                )
                enhanced_user_input = image_info + user_input
            else:
                enhanced_user_input = (
                    f"Пожалуйста, проанализируй {image_count} изображение(я), "
                    "которые я прикрепил. Используй функцию analyze_images для их анализа."
                )
            message_content = enhanced_user_input

        self.logger.info(
            "Image processing mode: %s. Message type: %s",
            settings.IMAGE_VISION_MODE,
            'multimodal' if isinstance(message_content, list) else 'text'
        )

        return enhanced_user_input, message_content

    def prepare_graph_input(
        self, ctx: GraphInputContext
    ) -> Tuple[Dict[str, Any], Dict[str, Any]]:
        """Подготавливает входные данные для LangGraph."""
        config = {
            "configurable": {
                "thread_id": ctx.context["thread_id"],
                "agent_id": ctx.context["component_id"]
            }
        }

        messages = ctx.history_db + [
            self._create_human_message(ctx.enhanced_user_input)
        ]

        graph_input = {
            "messages": messages,
            "user_data": ctx.context["user_data"],
            "channel": ctx.context["channel"],
            "image_urls": ctx.context.get("image_urls", []),
            "chat_id": ctx.context["thread_id"],
            "platform_user_id": ctx.context.get("platform_user_id", ""),
            "token_usage_events": [],
        }

        return graph_input, config

    def _create_human_message(self, enhanced_user_input: str) -> HumanMessage:
        """Создает человеческое сообщение для LangGraph."""
        return HumanMessage(content=enhanced_user_input)

    async def publish_response(
        self, ctx: ResponseContext, redis_cli
    ) -> None:
        """Публикует ответ агента в Redis канал."""
        response_payload = {
            "chat_id": ctx.chat_id,
            "response": ctx.response_content,
            "channel": ctx.channel,
        }

        # Add audio_url if TTS tool was used by agent
        if ctx.audio_url:
            response_payload["audio_url"] = ctx.audio_url
            self.logger.info("Including audio_url in response payload: %s", ctx.audio_url)

        response_str = json.dumps(response_payload, ensure_ascii=False)
        await redis_cli.publish(ctx.response_channel, response_str)

        self.logger.debug(
            "Published response to channel %s: %s",
            ctx.response_channel, response_str
        )

    async def publish_error_notification(
        self, payload: Dict[str, Any], error: Exception, redis_cli
    ) -> None:
        """Публикует уведомление об ошибке."""
        if "chat_id" not in payload:
            return

        error_channel = f"agent_responses:{payload['chat_id']}"
        error_data = {
            "chat_id": payload["chat_id"],
            "agent_id": self.component_id,
            "interaction_id": payload.get("interaction_id", "unknown"),
            "error": str(error),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

        try:
            await redis_cli.publish(
                error_channel, json.dumps(error_data).encode("utf-8")
            )
            self.logger.info("Published error notification to %s", error_channel)
        except Exception as pub_err:
            self.logger.error(
                "Failed to publish error notification: %s", pub_err, exc_info=True
            )

    def extract_agent_response(
        self, value: Dict, current_response: str, current_message: Optional[BaseMessage]
    ) -> Tuple[str, Optional[BaseMessage]]:
        """Извлекает ответ агента из вывода node."""
        if "messages" in value and value["messages"]:
            last_msg = value["messages"][-1]
            if isinstance(last_msg, AIMessage):
                response_content = last_msg.content
                final_message = last_msg

                # Check for TTS tool calls in the message
                if hasattr(last_msg, 'tool_calls') and last_msg.tool_calls:
                    for tool_call in last_msg.tool_calls:
                        if tool_call.get('name') == 'generate_voice_response':
                            self.logger.debug("Found TTS tool call: %s", tool_call)

                return response_content, final_message

        return current_response, current_message
