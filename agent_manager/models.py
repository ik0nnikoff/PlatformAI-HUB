from pydantic import BaseModel, Field as PydanticField, field_validator, model_validator
from typing import List, Dict, Any, Optional
from enum import Enum
import json
from datetime import datetime

# --- SQLAlchemy Imports ---
from sqlalchemy import Column, String, Text, DateTime, func, JSON
from sqlalchemy.dialects.postgresql import UUID # If using UUID for IDs
import uuid # If using UUID

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
    def ensure_settings_structure(cls, data: Any) -> Any:
        """Ensure 'settings' contains 'model' and 'tools' keys if present."""
        if isinstance(data, dict) and 'settings' in data and isinstance(data['settings'], dict):
            if 'model' not in data['settings']:
                data['settings']['model'] = AgentConfigSimpleSettingsModel().model_dump()
            if 'tools' not in data['settings']:
                data['settings']['tools'] = []
        return data

class AgentConfigStructure(BaseModel):
    # Define different config structures if needed, e.g., "simple", "advanced"
    simple: Optional[AgentConfigSimple] = None
    # advanced: Optional[Dict[str, Any]] = None # Example

class AgentConfigInput(BaseModel):
    name: str = PydanticField(..., min_length=1, max_length=100)
    description: Optional[str] = PydanticField(None, max_length=500)
    userId: str # TODO: Link to a User model later
    # Use a structured config model
    config: AgentConfigStructure

    # Example validator for config structure
    @field_validator('config')
    def check_at_least_one_config_type(cls, v):
        if not v.simple and not getattr(v, 'advanced', None): # Check for other types if added
            raise ValueError("At least one configuration type (e.g., 'simple') must be provided")
        return v

class AgentConfigOutput(AgentConfigInput):
    id: str = PydanticField(..., description="Unique Agent ID assigned by the service")
    created_at: datetime
    updated_at: datetime

class AgentListItem(BaseModel):
    id: str
    name: str
    description: Optional[str] = None
    status: str # From AgentStatus

class AgentStatus(BaseModel):
    agent_id: str
    status: str = PydanticField(..., description="e.g., 'stopped', 'running', 'starting', 'error', 'error_config', 'error_app_create', 'error_script_not_found', 'error_start_failed', 'error_process_lost', 'stopping'")
    pid: Optional[int] = None
    last_active: Optional[float] = None

class IntegrationType(str, Enum):
    TELEGRAM = "telegram"
    # Add other integration types here later (e.g., webchat, slack)

class IntegrationStatus(BaseModel):
    agent_id: str
    integration_type: IntegrationType
    status: str = PydanticField(..., description="e.g., 'stopped', 'running', 'starting', 'error', 'error_script_not_found', 'error_start_failed', 'error_unsupported_type', 'stopping'")
    pid: Optional[int] = None
    last_active: Optional[float] = None # May not be applicable for all integrations

# --- SQLAlchemy Models (Database Layer) ---

class AgentConfigDB(Base):
    __tablename__ = "agent_configs"

    # Use UUID for primary key if desired, otherwise keep string
    # id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    id = Column(String, primary_key=True, index=True) # Using string ID for now
    name = Column(String, index=True, nullable=False)
    description = Column(Text, nullable=True)
    user_id = Column(String, index=True, nullable=False) # TODO: Add ForeignKey constraint when User model exists
    # Store the flexible config structure as JSON
    config_json = Column(JSON, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    # Add relationships later if needed, e.g., to User or ChatHistory
    # owner = relationship("User", back_populates="agents")
    # chat_history = relationship("ChatMessageDB", back_populates="agent")

# TODO: Add ChatMessageDB model for storing history
# class ChatMessageDB(Base):
#     __tablename__ = "chat_messages"
#     id = Column(Integer, primary_key=True, index=True) # Or UUID
#     agent_id = Column(String, ForeignKey("agent_configs.id"), nullable=False, index=True)
#     thread_id = Column(String, nullable=False, index=True)
#     timestamp = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
#     sender_type = Column(String, nullable=False) # e.g., 'user', 'agent', 'system', 'tool'
#     content = Column(Text, nullable=False)
#     metadata_json = Column(JSON, nullable=True) # For tool calls, latency, etc.
#
#     agent = relationship("AgentConfigDB", back_populates="chat_history")
