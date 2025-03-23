import logging
import pprint
from langchain import hub
from config import llm, client_id, datasource_id
from vector_store import retriever
from models import GradeDocuments, GradeHallucinations, GradeAnswer
from langchain_core.output_parsers import StrOutputParser

def retrieve(state):
    """Retrieve documents"""
    logging.info("---RETRIEVE---")
    question = state["question"]

    # Запрос документов в векторное хранилище
    documents = retriever.invoke(
        input=question,
        filter={
            "must": [
                {"key": "metadata.client_id", "match": {"value": client_id}},
                {"key": "metadata.datasource_id", "match": {"value": datasource_id}},
            ]
        }
    )
    return {"documents": documents, "question": question}

def generate(state):
    """Generate answer"""
    logging.info("---GENERATE---")
    question = state["question"]
    documents = state["documents"]

    prompt = hub.pull("rlm/rag-prompt")
    print("\n")
    pprint.pprint(prompt)
    print("\n")
    rag_chain = prompt | llm | StrOutputParser()

    generation = rag_chain.invoke({"context": documents, "question": question})
    return {"documents": documents, "question": question, "generation": generation}

def grade_documents(state):
    """Check document relevance"""
    logging.info("---CHECK DOCUMENT RELEVANCE---")
    question = state["question"]
    documents = state["documents"]

    structured_llm_grader = llm.with_structured_output(GradeDocuments)
    grade_prompt = hub.pull("efriis/self-rag-retrieval-grader")
    retrieval_grader = grade_prompt | structured_llm_grader

    # Фильтрация документов
    filtered_docs = [d for d in documents if retrieval_grader.invoke(
        {"question": question, "document": d.page_content}
    ).binary_score == "yes"]

    return {"documents": filtered_docs, "question": question}

def transform_query(state):
    """Rephrase query"""
    logging.info("---TRANSFORM QUERY---")
    question = state["question"]
    documents = state["documents"]

    re_write_prompt = hub.pull("efriis/self-rag-question-rewriter")
    question_rewriter = re_write_prompt | llm | StrOutputParser()

    better_question = question_rewriter.invoke({"question": question})
    logging.info(f"Better question: {better_question}")
    return {"documents": documents, "question": better_question}

def no_documents(state):
    """
    Handle the case where no relevant documents are found.
    """
    logging.info("---NO DOCUMENTS FOUND---")
    return {"generation": "Извините, я не смог найти информацию по вашему запросу."}