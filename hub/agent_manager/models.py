from pydantic import BaseModel, Field as PydanticField, field_validator, model_validator, ConfigDict
from typing import List, Dict, Any, Optional
from enum import Enum
from datetime import datetime
from sqlalchemy import Column, String, Text, DateTime, func, JSON, Integer, ForeignKey, Enum as SQLEnum, Boolean, UniqueConstraint
from sqlalchemy.orm import declarative_base, relationship
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
    ownerId: str # --- ИЗМЕНЕНИЕ: Переименовано из userId ---
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
    first_message_content: str
    first_message_timestamp: datetime
    last_message_content: str
    last_message_timestamp: datetime
    last_message_sender_type: SenderType
    last_message_channel: Optional[str] = None
    message_count: int

    model_config = ConfigDict(from_attributes=True)


class UserOutput(BaseModel):
    id: int
    platform: str
    platform_user_id: str
    phone_number: Optional[str] = None
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    username: Optional[str] = None
    # --- ИЗМЕНЕНИЕ: Удаляем is_authorized ---
    # is_authorized: bool
    # --- КОНЕЦ ИЗМЕНЕНИЯ ---
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)

# --- SQLAlchemy Models (Database Layer) ---

class AgentConfigDB(Base):
    __tablename__ = "agent_configs"

    id = Column(String, primary_key=True, index=True)
    name = Column(String, index=True, nullable=False)
    description = Column(Text, nullable=True)
    # --- ИЗМЕНЕНИЕ: Переименовано user_id в owner_id ---
    owner_id = Column(String, index=True, nullable=False)
    # --- КОНЕЦ ИЗМЕНЕНИЯ ---
    config_json = Column(JSON, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    # --- ИЗМЕНЕНИЕ: Добавляем cascade="all, delete-orphan" ---
    # Связь с сообщениями чата (если нужно будет получать все сообщения агента)
    chat_messages = relationship("ChatMessageDB", back_populates="agent_config", cascade="all, delete-orphan")
    # --- КОНЕЦ ИЗМЕНЕНИЯ ---
    # --- НОВОЕ: Связь с авторизациями ---
    authorizations = relationship("AgentUserAuthorizationDB", back_populates="agent_config", cascade="all, delete-orphan")
    # --- КОНЕЦ НОВОГО ---

class ChatMessageDB(Base):
    __tablename__ = "chat_messages"

    id = Column(Integer, primary_key=True, index=True)
    # --- ИЗМЕНЕНИЕ: Добавляем ondelete="CASCADE" ---
    agent_id = Column(String, ForeignKey("agent_configs.id", ondelete="CASCADE"), nullable=False, index=True)
    # --- КОНЕЦ ИЗМЕНЕНИЯ ---
    thread_id = Column(String, nullable=False, index=True)
    # Используем SQLEnum для хранения типа отправителя
    sender_type = Column(SQLEnum(SenderType, name="sender_type_enum", create_type=True), nullable=False)
    content = Column(Text, nullable=False)
    channel = Column(String, nullable=True) # Канал, из которого пришло сообщение (websocket, telegram, etc.)
    # Добавляем timestamp
    timestamp = Column(DateTime(timezone=True), nullable=False, index=True, server_default=func.now()) # Добавляем server_default для удобства

    # Связь с конфигом агента
    agent_config = relationship("AgentConfigDB", back_populates="chat_messages")

class UserDB(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    platform = Column(String, nullable=False, index=True) # e.g., 'telegram', 'vk', 'whatsapp'
    platform_user_id = Column(String, nullable=False, index=True) # User ID on the specific platform
    username = Column(String, nullable=True)
    first_name = Column(String, nullable=True)
    last_name = Column(String, nullable=True)
    phone_number = Column(String, nullable=True, index=True)
    # --- ИЗМЕНЕНИЕ: Удаляем is_authorized ---
    # is_authorized = Column(Boolean, default=False, nullable=False, index=True) # Authorization status within our system
    # --- КОНЕЦ ИЗМЕНЕНИЯ ---
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        UniqueConstraint('platform', 'platform_user_id', name='uq_user_platform_id'),
    )

    # --- НОВОЕ: Связь с авторизациями ---
    agent_authorizations = relationship("AgentUserAuthorizationDB", back_populates="user")
    # --- КОНЕЦ НОВОГО ---


# --- НОВОЕ: Таблица авторизации пользователей для агентов ---
class AgentUserAuthorizationDB(Base):
    __tablename__ = "agent_user_authorizations"

    id = Column(Integer, primary_key=True, index=True)
    agent_id = Column(String, ForeignKey("agent_configs.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    is_authorized = Column(Boolean, default=False, nullable=False, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    # Связи
    agent_config = relationship("AgentConfigDB", back_populates="authorizations")
    user = relationship("UserDB", back_populates="agent_authorizations")

    __table_args__ = (
        UniqueConstraint('agent_id', 'user_id', name='uq_agent_user_authorization'),
    )
# --- КОНЕЦ НОВОГО ---
