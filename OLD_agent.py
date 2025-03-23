import os
import logging
from dotenv import load_dotenv
from qdrant_client.http import models
from qdrant_client import QdrantClient
from langchain_qdrant import QdrantVectorStore
from langchain import hub
from pydantic import BaseModel, Field
from langchain_openai import OpenAIEmbeddings, ChatOpenAI
from langchain_core.output_parsers import StrOutputParser
from typing import List
from typing_extensions import TypedDict
from langgraph.graph import END, StateGraph, START

question = "Как часто надо чистить стиральную машину?"
CLIENT_ID = "client123"
DATASOURCE_ID = "source_456"

load_dotenv()
logging.basicConfig(
    # level=logging.INFO,
    level=None,
    format="%(asctime)s - %(levelname)s - %(message)s",
)

embeddings = OpenAIEmbeddings()
llm = ChatOpenAI(model="gpt-4o-mini", temperature=0)

client = QdrantClient(os.getenv("QDRANT_URL", "http://qdrant:6333"))
vectorstore = QdrantVectorStore(
    client,
    collection_name=os.getenv("QDRANT_COLLECTION", "test_collection"),
    embedding=embeddings
)
retriever = vectorstore.as_retriever()

# Data model
class GradeDocuments(BaseModel):
    """Binary score for relevance check on retrieved documents."""

    binary_score: str = Field(
        description="Documents are relevant to the question, 'yes' or 'no'"
    )

### Hallucination Grader
class GradeHallucinations(BaseModel):
    """Binary score for hallucination present in generation answer."""

    binary_score: str = Field(
        description="Answer is grounded in the facts, 'yes' or 'no'"
    )

### Answer Grader
class GradeAnswer(BaseModel):
    """Binary score to assess answer addresses question."""

    binary_score: str = Field(
        description="Answer addresses the question, 'yes' or 'no'"
    )

class GraphState(TypedDict):
    """
    Represents the state of our graph.

    Attributes:
        question: question
        generation: LLM generation
        documents: list of documents
    """

    question: str
    generation: str
    documents: List[str]

### Nodes
def retrieve(state):
    """
    Retrieve documents

    Args:
        state (dict): The current graph state

    Returns:
        state (dict): New key added to state, documents, that contains retrieved documents
    """
    logging.info("---RETRIEVE---")
    question = state["question"]

    # Retrieval
    documents = retriever.invoke(
        input=question,
        filter=models.Filter(
                        must=[
                            models.FieldCondition(
                                key="metadata.client_id",
                                match=models.MatchValue(value=CLIENT_ID),
                            ),
                            models.FieldCondition(
                                key="metadata.datasource_id",
                                match=models.MatchValue(value=DATASOURCE_ID),
                            )
                        ]
                    )
        )
    return {"documents": documents, "question": question}

def generate(state):
    """
    Generate answer

    Args:
        state (dict): The current graph state

    Returns:
        state (dict): New key added to state, generation, that contains LLM generation
    """
    logging.info("---GENERATE---")
    question = state["question"]
    documents = state["documents"]

    # RAG generation
    prompt = hub.pull("rlm/rag-prompt")
    rag_chain = prompt | llm | StrOutputParser()

    generation = rag_chain.invoke({"context": documents, "question": question})
    return {"documents": documents, "question": question, "generation": generation}

def grade_documents(state):
    """
    Determines whether the retrieved documents are relevant to the question.

    Args:
        state (dict): The current graph state

    Returns:
        state (dict): Updates documents key with only filtered relevant documents
    """

    logging.info("---CHECK DOCUMENT RELEVANCE TO QUESTION---")
    question = state["question"]
    documents = state["documents"]

    structured_llm_grader = llm.with_structured_output(GradeDocuments)
    grade_prompt = hub.pull("efriis/self-rag-retrieval-grader")
    retrieval_grader = grade_prompt | structured_llm_grader

    # Score each doc
    filtered_docs = []
    for d in documents:
        score = retrieval_grader.invoke(
            {"question": question, "document": d.page_content}
        )
        grade = score.binary_score
        if grade == "yes":
            logging.info("---GRADE: DOCUMENT RELEVANT---")
            filtered_docs.append(d)
        else:
            logging.info("---GRADE: DOCUMENT NOT RELEVANT---")
            continue
    return {"documents": filtered_docs, "question": question}

def transform_query(state):
    """
    Transform the query to produce a better question.

    Args:
        state (dict): The current graph state

    Returns:
        state (dict): Updates question key with a re-phrased question
    """

    logging.info("---TRANSFORM QUERY---")
    question = state["question"]
    documents = state["documents"]

    re_write_prompt = hub.pull("efriis/self-rag-question-rewriter")
    question_rewriter = re_write_prompt | llm | StrOutputParser()

    # Re-write question
    better_question = question_rewriter.invoke({"question": question})
    return {"documents": documents, "question": better_question}

### Edges
def decide_to_generate(state):
    """
    Determines whether to generate an answer, or re-generate a question.

    Args:
        state (dict): The current graph state

    Returns:
        str: Binary decision for next node to call
    """

    logging.info("---ASSESS GRADED DOCUMENTS---")
    state["question"]
    filtered_documents = state["documents"]

    if not filtered_documents:
        # All documents have been filtered check_relevance
        # We will re-generate a new query
        logging.info("---DECISION: ALL DOCUMENTS ARE NOT RELEVANT TO QUESTION, TRANSFORM QUERY---")
        return "transform_query"
    else:
        # We have relevant documents, so generate answer
        logging.info("---DECISION: GENERATE---")
        return "generate"
    
def grade_generation_v_documents_and_question(state):
    """
    Determines whether the generation is grounded in the document and answers question.

    Args:
        state (dict): The current graph state

    Returns:
        str: Decision for next node to call
    """

    logging.info("---CHECK HALLUCINATIONS---")
    question = state["question"]
    documents = state["documents"]
    generation = state["generation"]

    structured_llm_grader = llm.with_structured_output(GradeHallucinations)
    # https://smith.langchain.com/hub/efriis/self-rag-hallucination-grader
    hallucination_prompt = hub.pull("efriis/self-rag-hallucination-grader")
    hallucination_grader = hallucination_prompt | structured_llm_grader

    structured_llm_grader = llm.with_structured_output(GradeAnswer)
    answer_prompt = hub.pull("efriis/self-rag-answer-grader")
    answer_grader = answer_prompt | structured_llm_grader

    score = hallucination_grader.invoke(
        {"documents": documents, "generation": generation}
    )
    grade = score.binary_score

    # Check hallucination
    if grade == "yes":
        logging.info("---DECISION: GENERATION IS GROUNDED IN DOCUMENTS---")
        # Check question-answering
        logging.info("---GRADE GENERATION vs QUESTION---")
        score = answer_grader.invoke({"question": question, "generation": generation})
        grade = score.binary_score
        if grade == "yes":
            logging.info("---DECISION: GENERATION ADDRESSES QUESTION---")
            return "useful"
        else:
            logging.info("---DECISION: GENERATION DOES NOT ADDRESS QUESTION---")
            return "not useful"
    else:
        logging.info("---DECISION: GENERATION IS NOT GROUNDED IN DOCUMENTS, RE-TRY---")
        return "not supported"

workflow = StateGraph(GraphState)

# Define the nodes
workflow.add_node("retrieve", retrieve)  # retrieve
workflow.add_node("grade_documents", grade_documents)  # grade documents
workflow.add_node("generate", generate)  # generatae
workflow.add_node("transform_query", transform_query)  # transform_query

# Build graph
workflow.add_edge(START, "retrieve")
workflow.add_edge("retrieve", "grade_documents")
workflow.add_conditional_edges(
    "grade_documents",
    decide_to_generate,
    {
        "transform_query": "transform_query",
        "generate": "generate",
    },
)
workflow.add_edge("transform_query", "retrieve")
workflow.add_conditional_edges(
    "generate",
    grade_generation_v_documents_and_question,
    {
        "not supported": "generate",
        "useful": END,
        "not useful": "transform_query",
    },
)

# Compile
app = workflow.compile()

# Run
inputs = {"question": question }
for output in app.stream(inputs):
    for key, value in output.items():
        logging.info(f"Node '{key}':")

# Final generation
print(value["generation"])