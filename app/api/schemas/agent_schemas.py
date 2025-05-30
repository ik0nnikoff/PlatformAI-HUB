from pydantic import BaseModel, Field as PydanticField, model_validator, ConfigDict
from typing import List, Dict, Any, Optional
from datetime import datetime
import logging

from .common_schemas import IntegrationType # Импортируем из соседнего файла

logger = logging.getLogger(__name__)

# --- Pydantic Models (API Layer) for Agents ---

class AgentConfigSimpleSettingsModel(BaseModel):
    modelId: str = "gpt-4o-mini"
    temperature: float = 0.2
    systemPrompt: str = "You are a helpful AI assistant."
    limitToKnowledgeBase: bool = False
    answerInUserLanguage: bool = True
    enableContextMemory: bool = True
    contextMemoryDepth: int = 10
    provider: str = "OpenAI"
    useMarkdown: bool = True
    useContextMemory: bool = True  # Added for compatibility
    streaming: bool = True  # Added streaming support

class AgentConfigSimpleToolSettings(BaseModel):
    # Knowledge Base tool settings
    knowledgeBaseIds: Optional[List[str]] = None
    retrievalLimit: Optional[int] = 4
    rewriteAttempts: Optional[int] = 3
    description: Optional[str] = None
    name: Optional[str] = None
    returnToAgent: Optional[bool] = False
    rewriteQuery: Optional[bool] = True
    
    # Knowledge Base model configuration (новые поля для поддержки специфичных моделей)
    modelId: Optional[str] = None  # Модель для RAG операций этой базы знаний
    provider: Optional[str] = None  # Провайдер модели для RAG операций
    temperature: Optional[float] = None  # Температура для RAG операций
    
    # Web Search tool settings
    searchLimit: Optional[int] = 3
    include_domains: Optional[List[str]] = None
    excludeDomains: Optional[List[str]] = None
    
    # API Request tool settings
    apiUrl: Optional[str] = None
    method: Optional[str] = "GET"
    params: Optional[List[Dict[str, Any]]] = None
    headers: Optional[Any] = None  # Can be dict or list of {"key": "name", "value": "val"}
    
    # Common settings
    model_config = ConfigDict(extra="allow")  # Allow additional fields

class AgentConfigSimpleTool(BaseModel):
    id: str
    type: str # e.g., "knowledgeBase", "webSearch", "apiRequest"
    settings: Optional[Dict[str, Any]] = None  # More flexible to handle different tool types
    
    model_config = ConfigDict(extra="allow")  # Allow additional fields

class AgentConfigSimpleIntegrationSettings(BaseModel):
    """Settings for integrations like Telegram"""
    # Telegram integration settings
    botToken: Optional[str] = None
    webhookUrl: Optional[str] = None
    
    # Common integration settings
    model_config = ConfigDict(extra="allow")  # Allow additional fields

class AgentConfigSimpleIntegration(BaseModel):
    id: str
    type: str # e.g., "telegram", "slack", etc.
    name: Optional[str] = None
    enabled: Optional[bool] = True
    settings: Optional[Dict[str, Any]] = None
    
    model_config = ConfigDict(extra="allow")  # Allow additional fields

class AgentConfigSimpleSettings(BaseModel):
    """Settings within config.simple.settings"""
    # Model configuration
    model: Optional[Dict[str, Any]] = None
    
    # Tools configuration
    tools: Optional[List[Dict[str, Any]]] = None
    
    # Integrations configuration  
    integrations: Optional[List[Dict[str, Any]]] = None
    
    model_config = ConfigDict(extra="allow")  # Allow additional fields

class AgentConfigSimple(BaseModel):
    settings: Optional[AgentConfigSimpleSettings] = None

    @model_validator(mode='before')
    @classmethod
    def check_config_structure(cls, data: Any) -> Any:
        """Validate the new structure config.simple.settings"""
        if isinstance(data, dict):
            # Check if 'settings' exists and contains expected structure
            if 'settings' in data and isinstance(data['settings'], dict):
                settings = data['settings']
                # Validate tools structure
                if 'tools' in settings and isinstance(settings['tools'], list):
                    for tool in settings['tools']:
                        if not isinstance(tool, dict) or 'id' not in tool or 'type' not in tool:
                            logger.warning(f"Invalid tool structure: {tool}")
                
                # Validate integrations structure  
                if 'integrations' in settings and isinstance(settings['integrations'], list):
                    for integration in settings['integrations']:
                        if not isinstance(integration, dict) or 'id' not in integration or 'type' not in integration:
                            logger.warning(f"Invalid integration structure: {integration}")
        return data

class AgentConfigStructure(BaseModel):
    simple: Optional[AgentConfigSimple] = None
    # advanced: Optional[Dict[str, Any]] = None # Пример для расширения

class AgentConfigInput(BaseModel):
    name: str = PydanticField(..., min_length=1, max_length=100)
    description: str = PydanticField("", max_length=500)
    userId: str # Changed from userId to ownerId to match new structure
    config_json: Dict[str, Any] = PydanticField(..., alias='config')

class AgentConfigOutput(BaseModel):
    id: str
    name: str
    description: Optional[str] = None
    ownerId: str # Ранее userId
    config: AgentConfigStructure # Валидированная структура конфига
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(
        from_attributes=True,
        json_encoders={datetime: lambda v: v.isoformat()} # Для корректной сериализации datetime
    )

class AgentListItem(BaseModel):
    id: str
    name: str
    description: Optional[str] = None
    status: str # Статус будет получаться из Redis или другого источника

    model_config = ConfigDict(from_attributes=True)

class AgentStatus(BaseModel):
    agent_id: str # ID агента, для которого этот статус
    status: str  # Например, "running", "stopped", "initializing", "restarting", "error_config", "error_redis", etc.
    pid: Optional[int] = None
    error_detail: Optional[str] = None
    last_active: Optional[float] = None # Timestamp последней активности

class IntegrationStatus(BaseModel):
    type: IntegrationType # Тип интеграции, например, "telegram"
    status: str # Например, "running", "stopped", "error", "starting", "stopping", "not_found"
    pid: Optional[int] = None
    agent_id: str # ID агента, к которому относится интеграция
    error_detail: Optional[str] = None
    last_active: Optional[float] = None # Timestamp последней активности, если применимо
