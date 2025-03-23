from typing import List, Annotated
from typing_extensions import TypedDict
from langgraph.graph.message import add_messages

class GraphState(TypedDict):
    """
    Represents the state of our graph.
    Attributes:
        question: question
        generation: LLM generation
        documents: list of documents
    """
    question: str
    # question: Annotated[list, add_messages]
    generation: str
    documents: List[str]
    # messages: Annotated[list, add_messages]