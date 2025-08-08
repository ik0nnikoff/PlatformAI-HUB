"""
Data structures for AgentRunner to reduce method parameter complexity.
"""

from dataclasses import dataclass
from typing import Any, Dict, List, Optional


@dataclass
class ProcessingContext:
    """Context data for message processing."""
    user_text: str
    chat_id: str
    user_data: Dict[str, Any]
    channel: str
    interaction_id: str
    image_urls: List[str]
    platform_id: Optional[str] = None


@dataclass
class InvocationContext:
    """Context data for agent invocation."""
    user_input: str
    user_data: Dict[str, Any]
    thread_id: str
    channel: Optional[str] = None
    interaction_id: Optional[str] = None
    image_urls: Optional[List[str]] = None


@dataclass
class HistorySaveContext:
    """Context data for saving history."""
    sender_type: str
    thread_id: str
    content: str
    channel: Optional[str]
    interaction_id: str


@dataclass
class GraphInputContext:
    """Context data for preparing graph input."""
    history_db: List
    message_content: str
    user_input: str
    enhanced_user_input: str
    context: Dict[str, Any]


@dataclass
class ResponseContext:
    """Context data for publishing responses."""
    chat_id: str
    response_content: str
    channel: str
    audio_url: Optional[str]
    response_channel: str


@dataclass
class TokenContext:
    """Context data for token operations."""
    interaction_id: str
    thread_id: str
    agent_app: Any
    config: Dict[str, Any]
