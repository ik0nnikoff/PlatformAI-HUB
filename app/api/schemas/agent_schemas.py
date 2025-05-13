from pydantic import BaseModel, Field as PydanticField, model_validator, ConfigDict
from typing import List, Dict, Any, Optional
from datetime import datetime

from .common_schemas import IntegrationType # Импортируем из соседнего файла

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

class AgentConfigSimpleToolSettings(BaseModel):
    knowledgeBaseIds: Optional[List[str]] = None
    retrievalLimit: Optional[int] = 4
    rewriteAttempts: Optional[int] = 3
    searchLimit: Optional[int] = 3

class AgentConfigSimpleTool(BaseModel):
    id: str
    type: str # e.g., "knowledgeBase", "webSearch", "apiRequest"
    name: str
    settings: Optional[AgentConfigSimpleToolSettings] = None

class AgentConfigSimple(BaseModel):
    settings: Optional[Dict[str, Any]] = None

    @model_validator(mode='before')
    @classmethod
    def check_config_structure(cls, data: Any) -> Any:
        # Валидация оставлена как есть из исходного файла,
        # но может потребовать пересмотра или быть удалена,
        # если AgentConfigInput обеспечивает достаточную валидацию.
        if isinstance(data, dict):
            if 'settings' in data and isinstance(data['settings'], dict):
                pass
            else:
                pass # Логика из исходного файла
        return data

class AgentConfigStructure(BaseModel):
    simple: Optional[AgentConfigSimple] = None
    # advanced: Optional[Dict[str, Any]] = None # Пример для расширения

class AgentConfigInput(BaseModel):
    name: str = PydanticField(..., min_length=1, max_length=100)
    description: str = PydanticField("", max_length=500)
    userId: str # В будущем может быть заменено на объект пользователя или ID из системы аутентификации
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
    agent_id: str
    status: str # e.g., "running", "stopped", "error", "starting"
    pid: Optional[int] = None
    last_active: Optional[float] = None # Timestamp последнего активного действия
    error_detail: Optional[str] = None

class IntegrationStatus(BaseModel):
    integration_type: IntegrationType
    status: str
    pid: Optional[int] = None
    last_active: Optional[float] = None
    error_detail: Optional[str] = None
