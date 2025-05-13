import logging
from sqlalchemy import Column, String, Text, DateTime, JSON, Integer, ForeignKey, Enum as SQLEnum, Boolean, UniqueConstraint, func, Index
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
import uuid
# Импортируем Base из app.db.session, где он определен
from app.db.session import Base
# Импортируем Enum типы, которые будут использоваться в моделях
# Они будут определены в app.api.schemas.common_schemas
from app.api.schemas.common_schemas import SenderType, IntegrationType # Предполагаем, что IntegrationType тоже там будет

logger = logging.getLogger(__name__)

class AgentConfigDB(Base):
    __tablename__ = "agent_configs"

    id = Column(String, primary_key=True, index=True)
    name = Column(String, index=True, nullable=False)
    description = Column(Text, nullable=True)
    owner_id = Column(String, index=True, nullable=False) # Ранее userId
    config_json = Column(JSON, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    chat_messages = relationship("ChatMessageDB", back_populates="agent_config", cascade="all, delete-orphan")
    authorizations = relationship("AgentUserAuthorizationDB", back_populates="agent_config", cascade="all, delete-orphan")
    token_usage_logs = relationship("TokenUsageLogDB", back_populates="agent_config", cascade="all, delete-orphan") # Новая связь

class ChatMessageDB(Base):
    __tablename__ = "chat_messages"

    id = Column(Integer, primary_key=True, index=True)
    agent_id = Column(String, ForeignKey("agent_configs.id", ondelete="CASCADE"), nullable=False, index=True)
    thread_id = Column(String, nullable=False, index=True)
    # Убрано unique=True для соответствия старой модели
    interaction_id = Column(String, nullable=True, index=True) 
    sender_type = Column(SQLEnum(SenderType, name="sender_type_enum", create_type=True), nullable=False)
    content = Column(Text, nullable=False)
    channel = Column(String, nullable=True)
    timestamp = Column(DateTime(timezone=True), server_default=func.now(), index=True) # server_default оставлен, т.к. был и в старой ChatMessageDB
    
    # Изменено для соответствия "один-ко-многим" и старой логике ForeignKey
    token_usage_logs = relationship("TokenUsageLogDB", back_populates="chat_message", cascade="all, delete-orphan")

    agent_config = relationship("AgentConfigDB", back_populates="chat_messages")

class UserDB(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    platform = Column(String, nullable=False, index=True) # e.g., 'telegram', 'web'
    platform_user_id = Column(String, nullable=False, index=True)
    phone_number = Column(String, nullable=True, unique=True, index=True)
    first_name = Column(String, nullable=True)
    last_name = Column(String, nullable=True)
    username = Column(String, nullable=True, index=True)
    # is_authorized = Column(Boolean, default=False, nullable=False) # Удалено, используется AgentUserAuthorizationDB
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    # Связь с авторизациями
    agent_authorizations = relationship("AgentUserAuthorizationDB", back_populates="user", cascade="all, delete-orphan")

    __table_args__ = (UniqueConstraint('platform', 'platform_user_id', name='uq_platform_platform_user_id'),)

class AgentUserAuthorizationDB(Base):
    __tablename__ = "agent_user_authorizations"

    id = Column(Integer, primary_key=True, index=True)
    agent_id = Column(String, ForeignKey("agent_configs.id", ondelete="CASCADE"), nullable=False, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    is_authorized = Column(Boolean, default=False, nullable=False, index=True) # <--- ВОССТАНОВЛЕНО ЭТО ПОЛЕ
    # Можно добавить доп. поля, например, 'role' или 'expires_at'
    # status = Column(String, default="active", nullable=False) # Например: active, revoked
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    agent_config = relationship("AgentConfigDB", back_populates="authorizations")
    user = relationship("UserDB", back_populates="agent_authorizations")

    __table_args__ = (UniqueConstraint('agent_id', 'user_id', name='uq_agent_user_authorization'),)

class TokenUsageLogDB(Base):
    __tablename__ = "token_usage_logs"

    # Соответствует OLD/hub/agent_manager/models.py
    id = Column(Integer, primary_key=True, index=True)
    # Убрано ondelete="CASCADE"
    agent_id = Column(String, ForeignKey("agent_configs.id"), nullable=False, index=True)
    # Убрано ondelete="CASCADE"
    message_id = Column(Integer, ForeignKey("chat_messages.id"), nullable=True, index=True)
    interaction_id = Column(String, nullable=False, index=True)

    call_type = Column(String, nullable=False, index=True)
    model_id = Column(String, nullable=False)
    prompt_tokens = Column(Integer, nullable=False)
    completion_tokens = Column(Integer, nullable=False)
    total_tokens = Column(Integer, nullable=False)
    # Убран server_default, nullable=False (соответствует старой модели)
    timestamp = Column(DateTime(timezone=True), nullable=False, index=True)

    # Связи, соответствующие AgentConfigDB и ChatMessageDB
    agent_config = relationship("AgentConfigDB", back_populates="token_usage_logs")
    chat_message = relationship("ChatMessageDB", back_populates="token_usage_logs")

    # Индексы для часто запрашиваемых полей (оставляем, т.к. полезны и не противоречат)
    __table_args__ = (
        Index("ix_token_usage_logs_agent_id_timestamp", "agent_id", "timestamp"),
        Index("ix_token_usage_logs_call_type_timestamp", "call_type", "timestamp"),
        # Индексы для id, message_id, interaction_id создаются ForeignKey или primary_key=True, index=True
    )

# Необходимо убедиться, что все Enum (SenderType, IntegrationType) определены
# и доступны для импорта в этом файле, если они используются напрямую в Column определениях.
# В данном случае SenderType используется, IntegrationType - нет.
# Если IntegrationType нужен для других моделей SQLAlchemy, его нужно будет импортировать.
