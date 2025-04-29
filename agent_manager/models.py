from pydantic import BaseModel, Field as PydanticField, field_validator, model_validator, ConfigDict
from typing import List, Dict, Any, Optional, Union
from enum import Enum
from datetime import datetime

# --- SQLAlchemy Imports ---
# --- ИЗМЕНЕНИЕ: Добавляем Integer и ForeignKey ---
# --- ИЗМЕНЕНИЕ: Добавляем Boolean и UniqueConstraint ---
from sqlalchemy import Column, String, Text, DateTime, func, JSON, Integer, ForeignKey, Enum as SQLEnum, Boolean, UniqueConstraint
# --- КОНЕЦ ИЗМЕНЕНИЯ ---
# --- КОНЕЦ ИЗМЕНЕНИЯ ---
# --- ИЗМЕНЕНИЕ: Добавляем relationship ---
from sqlalchemy.orm import declarative_base, relationship
# --- КОНЕЦ ИЗМЕНЕНИЯ ---

# Import Base from db.py
from .db import Base

# --- Pydantic Models (API Layer) ---

class AgentConfigSimpleSettingsModel(BaseModel):
    modelId: str = "gpt-4o-mini"
    temperature: float = 0.2
    systemPrompt: str = "You are a helpful AI assistant."
    limitToKnowledgeBase: bool = False
    answerInUserLanguage: bool = True
    useContextMemory: bool = True

class AgentConfigSimpleToolSettings(BaseModel):
    knowledgeBaseIds: Optional[List[str]] = None
    retrievalLimit: Optional[int] = 4
    rewriteAttempts: Optional[int] = 3 # Added from runner logic
    searchLimit: Optional[int] = 3 # Added from runner logic
    # Add other tool-specific settings as needed

class AgentConfigSimpleTool(BaseModel):
    id: str
    type: str # e.g., "knowledgeBase", "webSearch", "apiRequest"
    name: str
    settings: Optional[AgentConfigSimpleToolSettings] = None

class AgentConfigSimple(BaseModel):
    settings: Optional[Dict[str, Any]] = None # Keep flexible for now, or define stricter model
    # Example stricter model:
    # settings: Optional[Dict[str, Union[AgentConfigSimpleSettingsModel, List[AgentConfigSimpleTool]]]] = None

    @model_validator(mode='before')
    @classmethod
    def check_config_structure(cls, data: Any) -> Any:
        if isinstance(data, dict):
            # Check if 'settings' exists and is a dict
            if 'settings' in data and isinstance(data['settings'], dict):
                # Check for known top-level keys within settings if needed
                # Example: if 'modelId' in data['settings']: ...
                pass # Basic validation passed
            else:
                # If settings is missing or not a dict, wrap it
                # This might not be the desired behavior, adjust as needed
                # For now, let's assume the input structure is correct or handled by AgentConfigInput
                pass
        return data


class AgentConfigStructure(BaseModel):
    # Define different config structures if needed, e.g., "simple", "advanced"
    simple: Optional[AgentConfigSimple] = None
    # advanced: Optional[Dict[str, Any]] = None # Example

class AgentConfigInput(BaseModel):
    name: str = PydanticField(..., min_length=1, max_length=100)
    description: str = PydanticField("", max_length=500)
    userId: str # TODO: Add validation if needed
    # Используем config_json: Dict[str, Any] для хранения сырого JSON
    config_json: Dict[str, Any] = PydanticField(..., alias='config')

    # Убираем model_config = ConfigDict(from_attributes=True), т.к. это модель ввода

class AgentConfigOutput(BaseModel):
    id: str
    name: str
    description: Optional[str] = None
    userId: str
    # Возвращаем валидированную структуру config
    config: AgentConfigStructure
    created_at: datetime
    updated_at: datetime

    # Используем ConfigDict для from_attributes
    model_config = ConfigDict(
        from_attributes=True,
        json_encoders={datetime: lambda v: v.isoformat()}
    )

class AgentListItem(BaseModel):
    id: str
    name: str
    description: Optional[str] = None
    status: str # Статус получаем из Redis

    # Используем ConfigDict для from_attributes
    model_config = ConfigDict(
        from_attributes=True
    )

class AgentStatus(BaseModel):
    agent_id: str
    status: str
    pid: Optional[int] = None
    last_active: Optional[float] = None
    error_detail: Optional[str] = None


class IntegrationType(str, Enum):
    TELEGRAM = "telegram"
    # Add other integration types here


class IntegrationStatus(BaseModel):
    integration_type: IntegrationType
    status: str
    pid: Optional[int] = None
    last_active: Optional[float] = None
    error_detail: Optional[str] = None


# --- НОВОЕ: Модели для истории чатов ---
class SenderType(str, Enum):
    USER = "user"
    AGENT = "agent"
    SYSTEM = "system" # Добавим system для возможных ошибок/уведомлений


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
    # --- ИЗМЕНЕНИЕ: Добавляем поля для первого сообщения ---
    first_message_content: str
    first_message_timestamp: datetime
    # --- КОНЕЦ ИЗМЕНЕНИЯ ---
    last_message_content: str
    last_message_timestamp: datetime
    last_message_sender_type: SenderType
    last_message_channel: Optional[str] = None
    message_count: int

    model_config = ConfigDict(from_attributes=True)


# --- SQLAlchemy Models (Database Layer) ---

class AgentConfigDB(Base):
    __tablename__ = "agent_configs"

    id = Column(String, primary_key=True, index=True)
    name = Column(String, index=True, nullable=False)
    description = Column(Text, nullable=True)
    user_id = Column(String, index=True, nullable=False) # Добавлено nullable=False и index=True
    config_json = Column(JSON, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    # Связь с сообщениями чата (если нужно будет получать все сообщения агента)
    chat_messages = relationship("ChatMessageDB", back_populates="agent_config")


# --- НОВОЕ: Модель для хранения истории чатов ---
class ChatMessageDB(Base):
    __tablename__ = "chat_messages"

    id = Column(Integer, primary_key=True, index=True)
    agent_id = Column(String, ForeignKey("agent_configs.id"), nullable=False, index=True)
    thread_id = Column(String, nullable=False, index=True)
    # Используем SQLEnum для хранения типа отправителя
    sender_type = Column(SQLEnum(SenderType, name="sender_type_enum", create_type=True), nullable=False)
    content = Column(Text, nullable=False)
    channel = Column(String, nullable=True) # Канал, из которого пришло сообщение (websocket, telegram, etc.)
    # Добавляем timestamp
    timestamp = Column(DateTime(timezone=True), nullable=False, index=True, server_default=func.now()) # Добавляем server_default для удобства

    # Связь с конфигом агента
    agent_config = relationship("AgentConfigDB", back_populates="chat_messages")

# --- КОНЕЦ НОВОГО ---

# --- НОВОЕ: Модель для хранения пользователей ---
class UserDB(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    platform = Column(String, nullable=False, index=True) # e.g., 'telegram', 'vk', 'whatsapp'
    platform_user_id = Column(String, nullable=False, index=True) # User ID on the specific platform
    username = Column(String, nullable=True)
    first_name = Column(String, nullable=True)
    last_name = Column(String, nullable=True)
    phone_number = Column(String, nullable=True, index=True)
    is_authorized = Column(Boolean, default=False, nullable=False, index=True) # Authorization status within our system
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        UniqueConstraint('platform', 'platform_user_id', name='uq_user_platform_id'),
    )

# --- КОНЕЦ НОВОГО ---
