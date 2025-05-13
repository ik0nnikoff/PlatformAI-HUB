from pydantic import BaseModel, ConfigDict
from datetime import datetime
from typing import Optional
from .common_schemas import SenderType

class ChatMessageCreate(BaseModel): # Новая схема
    agent_id: str
    thread_id: str
    sender_type: SenderType
    content: str
    channel: Optional[str] = None
    timestamp: Optional[datetime] = None # Обрабатывается в воркере, если отсутствует
    interaction_id: Optional[str] = None

class ChatMessageOutput(BaseModel):
    id: int
    agent_id: str
    thread_id: str
    sender_type: SenderType
    content: str
    channel: Optional[str] = None
    timestamp: datetime

    model_config = ConfigDict(from_attributes=True)

class ChatListItemOutput(BaseModel):
    thread_id: str
    first_message_content: str
    first_message_timestamp: datetime
    last_message_content: str
    last_message_timestamp: datetime
    last_message_sender_type: SenderType
    last_message_channel: Optional[str] = None
    message_count: int

    model_config = ConfigDict(from_attributes=True)
