from langgraph.graph import StateGraph, START, END
from langgraph.checkpoint.memory import MemorySaver
from state import GraphState
from nodes import retrieve, generate, grade_documents, transform_query, no_documents
from decisions import decide_to_generate, grade_generation_v_documents_and_question

# memory = MemorySaver()
workflow = StateGraph(GraphState)

# Определение узлов
workflow.add_node("retrieve", retrieve)
workflow.add_node("grade_documents", grade_documents)
workflow.add_node("generate", generate)
workflow.add_node("transform_query", transform_query)
workflow.add_node("no_documents", no_documents)

# Определение связей
workflow.add_edge(START, "retrieve")
workflow.add_edge("retrieve", "grade_documents")
workflow.add_conditional_edges("grade_documents", decide_to_generate, {
    "transform_query": "transform_query",
    "generate": "generate",
    "no_documents": "no_documents",
})
workflow.add_edge("transform_query", "retrieve")
workflow.add_conditional_edges("generate", grade_generation_v_documents_and_question, {
    # "not supported": "generate",
    "useful": END,
    "not useful": "transform_query",
    "not supported": "transform_query",
})
workflow.add_edge("no_documents", END)

# Компиляция
app = workflow.compile()
# app = workflow.compile(checkpointer=memory)