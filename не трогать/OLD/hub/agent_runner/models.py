from typing import Annotated, Sequence, TypedDict, List, Dict, Any, Set, Optional
from langchain_core.messages import BaseMessage
from langgraph.graph.message import add_messages
from langchain_core.tools import BaseTool
from pydantic import BaseModel, Field

class TokenUsageData(BaseModel):
    call_type: str # e.g., "agent_llm", "grading_llm", "rewrite_llm", "generation_llm"
    model_id: str
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    timestamp: str # ISO format datetime string

class AgentState(TypedDict):
    """
    Represents the state of the agent graph.

    Attributes:
        messages: The history of messages in the conversation.
        documents: List of relevant documents retrieved.
        question: The current question being processed (potentially rewritten).
        original_question: The initial question from the user for this turn
        rewrite_count: Number of times the question has been rewritten.
        channel: The communication channel (e.g., 'web', 'telegram').
        user_data: Dictionary containing information about the user.

        # Configuration passed down from factory/runner
        model_id: str
        temperature: float
        system_prompt: str
        configured_tools: List[BaseTool]
        safe_tool_names: Set[str]
        datastore_tool_names: Set[str]
        max_rewrites: int
        provider: str
        enableContextMemory: bool
        contextMemoryDepth: int
    """
    messages: Annotated[Sequence[BaseMessage], add_messages]
    documents: List[str]
    question: str # The current question being processed (might be rewritten)
    original_question: str # The initial question from the user for this turn
    rewrite_count: int
    channel: str # e.g., 'web', 'telegram'
    user_data: Dict[str, Any] # Info about the user if available

    # Configuration fields added
    model_id: str
    temperature: float
    system_prompt: str
    configured_tools: List[BaseTool]
    safe_tool_names: Set[str]
    datastore_tool_names: Set[str]
    max_rewrites: int
    provider: str
    enableContextMemory: bool
    contextMemoryDepth: int

    # New fields for token usage tracking
    current_interaction_id: Optional[str] = None # Unique ID for the current question-answer session
    token_usage_events: List[TokenUsageData] = Field(default_factory=list) # List of token usage events
