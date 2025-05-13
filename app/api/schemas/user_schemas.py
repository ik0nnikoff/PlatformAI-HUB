from pydantic import BaseModel, ConfigDict
from typing import Optional
from datetime import datetime

class UserOutput(BaseModel):
    id: int
    platform: str
    platform_user_id: str
    phone_number: Optional[str] = None
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    username: Optional[str] = None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)

class TokenUsageLogOutput(BaseModel):
    id: int
    agent_id: str
    message_id: Optional[int] = None
    interaction_id: str
    call_type: str
    model_id: str
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    timestamp: datetime

    model_config = ConfigDict(from_attributes=True)
