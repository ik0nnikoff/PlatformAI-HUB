from typing import Annotated, Sequence, TypedDict, List, Dict, Any
from langchain_core.messages import BaseMessage
from langgraph.graph.message import add_messages

class AgentState(TypedDict):
    """
    Represents the state of the agent graph.

    Attributes:
        messages: The history of messages in the conversation.
        documents: List of relevant documents retrieved.
        question: The current question being processed (potentially rewritten).
        rewrite_count: Number of times the question has been rewritten.
        channel: The communication channel (e.g., 'web', 'telegram').
        user_data: Dictionary containing information about the user.
    """
    messages: Annotated[Sequence[BaseMessage], add_messages]
    documents: List[str]
    question: str # The current question being processed (might be rewritten)
    original_question: str # The initial question from the user for this turn
    rewrite_count: int
    channel: str # e.g., 'web', 'telegram'
    user_data: Dict[str, Any] # Info about the user if available
