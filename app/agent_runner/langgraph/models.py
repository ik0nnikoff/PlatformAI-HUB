from typing import Annotated, Sequence, TypedDict, List, Dict, Any, Set, Optional
from langgraph.graph.message import add_messages
from langchain_core.messages import BaseMessage
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
        image_urls: List of image URLs to be processed by vision models.
        image_analysis: Results from image analysis by vision models.

        # Configuration passed down from factory/runner
        model_id: str
        temperature: float
        system_prompt: str
        configured_tools: List[BaseTool]
        safe_tool_names: Set[str]
    datastore_names: Set[str]
    max_rewrites: int
    provider: str
    enable_memory: bool
    memory_depth: int
    
    # Token usage tracking
    interaction_id: Optional[str]
        token_usage_events: List[TokenUsageData]
    """
    messages: Annotated[Sequence[BaseMessage], add_messages]
    documents: List[str]
    question: str
    original_question: str
    rewrite_count: int
    channel: str
    user_data: Dict[str, Any]
    image_urls: List[str]  # URLs of images to be processed
    image_analysis: List[Dict[str, Any]]  # Results from image analysis

    # Configuration fields
    model_id: str
    temperature: float
    system_prompt: str
    configured_tools: List[BaseTool]
    safe_tool_names: Set[str]
    datastore_names: Set[str]
    max_rewrites: int
    provider: str
    enable_memory: bool
    memory_depth: int

    # New fields for token usage tracking
    interaction_id: Optional[str] = None 
    token_usage_events: List[TokenUsageData] = Field(default_factory=list)
