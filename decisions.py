import logging
from config import llm
from langchain import hub
from models import GradeHallucinations, GradeAnswer

MAX_TRANSFORM_ATTEMPTS = 2
attempt_counter = 0

def decide_to_generate(state):
    """Determine next step based on document relevance"""
    global attempt_counter
    logging.info("---ASSESS GRADED DOCUMENTS---")
    filtered_documents = state["documents"]

    if not filtered_documents:
        if attempt_counter >= MAX_TRANSFORM_ATTEMPTS:
            logging.info("---DECISION: NO DOCUMENTS FOUND AFTER RETRIES---")
            return "no_documents"
        
        logging.info("---DECISION: TRANSFORM QUERY---")
        attempt_counter += 1
        return "transform_query"

    logging.info("---DECISION: GENERATE---")
    return "generate"

def grade_generation_v_documents_and_question(state):
    """Check answer relevance"""
    logging.info("---CHECK HALLUCINATIONS---")
    question = state["question"]
    documents = state["documents"]
    generation = state["generation"]

    hallucination_prompt = hub.pull("efriis/self-rag-hallucination-grader")
    hallucination_grader = hallucination_prompt | llm.with_structured_output(GradeHallucinations)
    score = hallucination_grader.invoke({"documents": documents, "generation": generation})

    if score.binary_score == "yes":
        answer_prompt = hub.pull("efriis/self-rag-answer-grader")
        answer_grader = answer_prompt | llm.with_structured_output(GradeAnswer)
        answer_score = answer_grader.invoke({"question": question, "generation": generation})

        if answer_score.binary_score == "yes":
            logging.info("---DECISION: USEFUL---")
            return "useful"
        else:
            logging.info("---DECISION: NOT USEFUL---")
            return "not useful"
    else:
        logging.info("---DECISION: NOT SUPPORTED---")
        return "not supported"