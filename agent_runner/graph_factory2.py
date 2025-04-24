import os
import logging
import asyncio
from typing import Dict, Literal, List, Set
from pydantic import BaseModel, Field
from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage, AIMessage, ToolMessage, BaseMessage
from langchain_core.prompts import PromptTemplate, ChatPromptTemplate, MessagesPlaceholder
from langgraph.graph import StateGraph, END, START
from langgraph.prebuilt import ToolNode
from langgraph.checkpoint.memory import MemorySaver

from .models import AgentState
from .tools import configure_tools # Import the tool configuration function

logger = logging.getLogger(__name__)

# --- Graph Node Definitions ---

async def agent_node(state: AgentState, config: Dict):
    """Agent node: Calls the LLM to decide the next action or respond."""
    agent_id = config.get('configurable', {}).get('agent_id', 'unknown_agent')
    log_adapter = logging.LoggerAdapter(logger, {'agent_id': agent_id})
    log_adapter.info("--- Node: Agent ---")

    model_id = state.get("model_id", "gpt-4o-mini")
    temperature = state.get("temperature", 0.2)
    final_system_prompt = state.get("system_prompt", "You are a helpful assistant.")
    configured_tools = state.get("configured_tools", [])

    prompt = ChatPromptTemplate.from_messages([
        ("system", final_system_prompt),
        MessagesPlaceholder(variable_name="messages")
    ])
    model = ChatOpenAI(temperature=temperature, streaming=True, model=model_id)
    if configured_tools:
        model = model.bind_tools(configured_tools)
    else:
        log_adapter.warning("Agent node called but no tools are configured in state.")

    chain = prompt | model
    try:
        # Pass only messages to the chain, other state elements are for graph logic
        response = await chain.ainvoke({"messages": state["messages"]})
        return {"messages": [response]}
    except Exception as e:
         log_adapter.error(f"Error invoking agent model: {e}", exc_info=True)
         # Return an error message to the state?
         error_message = AIMessage(content=f"Sorry, an error occurred: {e}")
         return {"messages": [error_message]}


async def grade_documents_node(state: AgentState, config: Dict):
    """Grades the relevance of retrieved documents."""
    agent_id = config.get('configurable', {}).get('agent_id', 'unknown_agent')
    log_adapter = logging.LoggerAdapter(logger, {'agent_id': agent_id})
    log_adapter.info("--- Node: Grade Documents ---")

    class GradeDocumentsModel(BaseModel):
        binary_score: str = Field(description="Relevance score 'yes' or 'no'")

    messages = state["messages"]
    rewrite_count = state.get("rewrite_count", 0)
    model_id = state.get("model_id", "gpt-4o-mini") # Get model from state
    datastore_tool_names = state.get("datastore_tool_names", set())

    # Find the last tool message containing retriever output
    docs_content = ""
    question_for_grading = state.get("question", "") # Get current question from state first
    retriever_output_found = False
    ai_msg_index = -1

    for i in range(len(messages) - 1, -1, -1):
        msg = messages[i]
        if isinstance(msg, ToolMessage) and msg.tool_call_id:
            potential_ai_msg_index = i - 1
            if potential_ai_msg_index >= 0 and isinstance(messages[potential_ai_msg_index], AIMessage):
                ai_msg = messages[potential_ai_msg_index]
                if ai_msg.tool_calls:
                    for tc in ai_msg.tool_calls:
                        # Check if the tool call ID matches and the tool name is a datastore tool
                        if tc['id'] == msg.tool_call_id and tc['name'] in datastore_tool_names:
                            docs_content = msg.content
                            retriever_output_found = True
                            ai_msg_index = potential_ai_msg_index
                            break
            if retriever_output_found:
                break

    if not retriever_output_found:
        log_adapter.warning("Could not find retriever tool output in messages for grading.")
        return {"documents": [], "question": question_for_grading}

    docs = [d for d in docs_content.split("\n---RETRIEVER_DOC---\n") if d.strip()] # Split and remove empty docs

    # Determine the question that led to this retrieval
    if rewrite_count > 0:
        log_adapter.info(f"Grading based on rewritten question (count: {rewrite_count}): {question_for_grading}")
    else:
        log_adapter.info("Grading based on initial user question.")
        question_found = False
        start_search_index = ai_msg_index - 1 if ai_msg_index >= 0 else len(messages) - 2
        for i in range(start_search_index, -1, -1):
            if isinstance(messages[i], HumanMessage):
                question_for_grading = messages[i].content
                question_found = True
                break
        if not question_found:
            log_adapter.warning("Could not find originating user question for grading. Using question from state as fallback.")
            # question_for_grading is already set from state

    log_adapter.info(f"Checking relevance for question: {question_for_grading}")

    if not docs:
         log_adapter.info("No documents found to grade.")
         return {"documents": [], "question": question_for_grading}

    model = ChatOpenAI(temperature=0, model=model_id)
    llm_with_tool = model.with_structured_output(GradeDocumentsModel)
    prompt_template = PromptTemplate(
        template="""Evaluate relevance of retrieved document for user question.\nDocument: {context}\nUser Question: {question}\nIs document relevant ('yes' or 'no')?""",
        input_variables=["context", "question"],
    )
    chain = prompt_template | llm_with_tool

    async def process_doc(doc):
        try:
            scored_result = await chain.ainvoke({"question": question_for_grading, "context": doc})
            log_adapter.debug(f"Grading doc: '{doc[:50]}...' -> {scored_result.binary_score}")
            return doc, scored_result.binary_score
        except Exception as e:
            log_adapter.error(f"Error processing document for grading: {e}")
            return doc, "no"

    tasks = [process_doc(d) for d in docs]
    results = await asyncio.gather(*tasks)
    filtered_docs = [doc for doc, score in results if score == "yes"]

    log_adapter.info(f"Relevant documents found: {len(filtered_docs)}")
    return {"documents": filtered_docs, "question": question_for_grading}


async def rewrite_node(state: AgentState, config: Dict):
    """Rewrites the question for better retrieval if documents were not relevant."""
    agent_id = config.get('configurable', {}).get('agent_id', 'unknown_agent')
    log_adapter = logging.LoggerAdapter(logger, {'agent_id': agent_id})
    log_adapter.info("--- Node: Rewrite ---")

    question = state["question"] # Question used for the failed retrieval
    messages = state["messages"]
    rewrite_count = state.get("rewrite_count", 0)
    max_rewrites = state.get("max_rewrites", 3)
    model_id = state.get("model_id", "gpt-4o-mini")

    message_no_answer = "No relevant information found. Please try rephrasing your question."
    message_to_research = "Performing search again with updated query: " # Agent internal thought

    model = ChatOpenAI(temperature=0, model=model_id, streaming=True)

    if rewrite_count < max_rewrites:
        log_adapter.info(f"Attempting rewrite {rewrite_count + 1}/{max_rewrites}")
        msg_content = f"""Review the dialogue context and the last question. Rephrase the question to be clearer or more specific for information retrieval.\nInitial Question: {question}\nDialogue Context: {messages}\nImproved Question (output only the improved question):"""
        msg = [HumanMessage(content=msg_content)]
        try:
            response = await model.ainvoke(msg)
            rewritten_question = response.content.strip()
            log_adapter.info(f"Rewritten question: {rewritten_question}")

            if not rewritten_question or rewritten_question.lower() == question.lower():
                log_adapter.warning("Rewriting resulted in empty or identical question. Stopping rewrite.")
                # Fall through to "no answer" case
            else:
                # Prepare message to trigger agent retrieval again
                # Note: This message is internal to the graph flow
                internal_reretrieve_message = HumanMessage(content=message_to_research + rewritten_question)
                log_adapter.info("Decision: Agent to retrieve with rewritten question.")
                return {
                    "messages": [internal_reretrieve_message], # Add internal message to trigger agent
                    "rewrite_count": rewrite_count + 1,
                    "question": rewritten_question # Update question in state
                }
        except Exception as e:
            log_adapter.error(f"Error during question rewriting: {e}", exc_info=True)
            # Fall through to "no answer" case

    # If rewrite limit reached or rewrite failed/was empty
    log_adapter.info("Decision: Rewrite limit reached or rewrite failed. Reporting no answer.")
    final_answer_message = AIMessage(content=message_no_answer)
    return {
        "messages": [final_answer_message], # Add final "no answer" message
        "rewrite_count": 0, # Reset count
        "question": question # Keep last question that failed
    }


async def generate_node(state: AgentState, config: Dict):
    """Generates the final response using relevant documents."""
    agent_id = config.get('configurable', {}).get('agent_id', 'unknown_agent')
    log_adapter = logging.LoggerAdapter(logger, {'agent_id': agent_id})
    log_adapter.info("--- Node: Generate ---")

    question = state["question"]
    documents = state.get("documents", [])
    model_id = state.get("model_id", "gpt-4o-mini")
    temperature = state.get("temperature", 0.2)

    if not documents:
        log_adapter.warning("Generate node called with no documents!")
        return {"messages": [AIMessage(content="No relevant information found to answer the question.")]}

    documents_str = "\n\n".join(documents)
    prompt_template = PromptTemplate(
        template="""You are an assistant for question-answering tasks. Use the following pieces of retrieved context to answer the question.
        If you don't know the answer based on the context, just say that you don't know. Keep the answer concise and professional.\n
        Question: {question} \n
        Context: {context} \n
        Answer:""",
        input_variables=["context", "question"],
    )
    llm = ChatOpenAI(model_name=model_id, temperature=temperature, streaming=True)
    rag_chain = prompt_template | llm

    try:
        response = await rag_chain.ainvoke({"context": documents_str, "question": question})
        # Ensure response is AIMessage
        if not isinstance(response, BaseMessage):
             response = AIMessage(content=str(response)) # Convert if needed
        return {"messages": [response]}
    except Exception as e:
        log_adapter.error(f"Error during generation: {e}", exc_info=True)
        return {"messages": [AIMessage(content="An error occurred while generating the response.")]}

# --- Graph Edge Logic ---

async def decide_to_generate_edge(state: AgentState) -> Literal["generate", "rewrite"]:
    """Edge: Decides whether to generate a response or rewrite the question."""
    agent_id = state.get("config", {}).get('configurable', {}).get('agent_id', 'unknown_agent')
    log_adapter = logging.LoggerAdapter(logger, {'agent_id': agent_id})
    log_adapter.info("--- Edge: Decide to Generate ---")

    if not state.get("documents"):
        log_adapter.info("Decision: No relevant documents found -> Rewrite")
        return "rewrite"
    else:
        log_adapter.info("Decision: Relevant documents found -> Generate")
        return "generate"

async def route_tools_edge(state: AgentState) -> Literal["retrieve", "safe_tools", "__end__"]:
    """Edge: Routes to the appropriate tool node or ends if no tool is called."""
    agent_id = state.get("config", {}).get('configurable', {}).get('agent_id', 'unknown_agent')
    log_adapter = logging.LoggerAdapter(logger, {'agent_id': agent_id})
    log_adapter.info("--- Edge: Route Tools ---")

    last_message = state["messages"][-1]
    if not isinstance(last_message, AIMessage) or not last_message.tool_calls:
        log_adapter.info("Decision: No tool call detected -> END")
        return END

    datastore_tool_names = state.get("datastore_tool_names", set())
    safe_tool_names = state.get("safe_tool_names", set()) # Assuming safe_tool_names are passed in state

    # Assumes single tool call for simplicity. Adapt if parallel calls needed.
    first_tool_call = last_message.tool_calls[0]
    tool_name = first_tool_call["name"]
    log_adapter.info(f"Routing tool call: {tool_name}")

    if tool_name in datastore_tool_names:
        log_adapter.info("Decision: Route to Retrieve node")
        return "retrieve"
    elif tool_name in safe_tool_names:
        log_adapter.info("Decision: Route to Safe Tools node")
        return "safe_tools"
    else:
        log_adapter.warning(f"Unknown tool '{tool_name}' called. Routing to END.")
        # Optionally, add a ToolMessage indicating the error?
        # tool_error_message = ToolMessage(content=f"Error: Tool '{tool_name}' not found.", tool_call_id=first_tool_call['id'])
        # Need to decide how to inject this back into the state before END.
        return END

# --- Graph Factory Function ---

def create_agent_app(agent_config: Dict, agent_id: str):
    """
    Creates and compiles the LangGraph application based on the provided configuration.
    """
    log_adapter = logging.LoggerAdapter(logger, {'agent_id': agent_id})
    log_adapter.info("Creating agent graph...")

    # --- Extract Configuration ---
    config_simple = agent_config.get("config", {}).get("simple", {})
    settings = config_simple.get("settings", {})
    model_settings = settings.get("model", {})
    # tool_settings = settings.get("tools", []) # Handled by configure_tools

    model_id = model_settings.get("modelId", "gpt-4o-mini")
    temperature = model_settings.get("temperature", 0.2)
    system_prompt_base = model_settings.get("systemPrompt", "You are a helpful AI assistant.")
    limit_to_kb = model_settings.get("limitToKnowledgeBase", False)
    answer_in_user_lang = model_settings.get("answerInUserLanguage", True)
    use_memory = model_settings.get("useContextMemory", True)

    # --- Configure Tools ---
    configured_tools, safe_tools_list, datastore_tool_names, max_rewrites = configure_tools(agent_config, agent_id)
    safe_tool_names = {t.name for t in safe_tools_list}

    # --- Construct Final System Prompt ---
    final_system_prompt = system_prompt_base
    if limit_to_kb and datastore_tool_names:
        final_system_prompt += "\nAnswer ONLY from the provided context from the knowledge base. If the answer is not in the context, say you don't know."
    if answer_in_user_lang:
        final_system_prompt += "\nAnswer in the same language as the user's question."
    final_system_prompt += "\nBe concise and professional."
    if datastore_tool_names:
        final_system_prompt += " Before answering, always check the knowledge base if available."
    log_adapter.info(f"Final system prompt: {final_system_prompt}")

    # --- Define Graph ---
    workflow = StateGraph(AgentState)

    # Add nodes
    workflow.add_node("agent", agent_node)
    if datastore_tool_names:
        # Combine all datastore tools into one node if multiple retrievers exist
        datastore_tools_combined = [t for t in configured_tools if t.name in datastore_tool_names]
        retrieve_node = ToolNode(datastore_tools_combined, name="retrieve_node")
        workflow.add_node("retrieve", retrieve_node)
        workflow.add_node("grade_documents", grade_documents_node)
        workflow.add_node("rewrite", rewrite_node)
        workflow.add_node("generate", generate_node)
        log_adapter.info("Added nodes: retrieve, grade_documents, rewrite, generate")
    else:
        log_adapter.info("No datastore tools configured. Retrieval/Grading/Rewrite/Generate nodes skipped.")

    if safe_tools_list:
        safe_tools_node = ToolNode(safe_tools_list, name="safe_tools_node")
        workflow.add_node("safe_tools", safe_tools_node)
        log_adapter.info("Added node: safe_tools")
    else:
        log_adapter.info("No safe tools configured. Safe_tools node skipped.")

    # Define edges
    workflow.add_edge(START, "agent")

    if safe_tools_list:
        workflow.add_edge("safe_tools", "agent") # Always return to agent after safe tool

    if datastore_tool_names:
        workflow.add_edge("retrieve", "grade_documents")
        workflow.add_edge("rewrite", "agent") # After rewrite, agent decides next (could be END or another tool)
        workflow.add_edge("generate", END) # After generate, end

        workflow.add_conditional_edges(
            "grade_documents",
            decide_to_generate_edge,
            {"rewrite": "rewrite", "generate": "generate"},
        )
    # else: # If no datastore, agent might call safe tools or end (handled by route_tools_edge)

    # Conditional edge for agent routing
    possible_routes = {END: END} # Always include END possibility
    if safe_tools_list:
        possible_routes["safe_tools"] = "safe_tools"
    if datastore_tool_names:
        possible_routes["retrieve"] = "retrieve"

    workflow.add_conditional_edges(
        "agent",
        route_tools_edge,
        possible_routes
    )

    # --- Compile with Checkpointer and State Injection ---
    memory = MemorySaver() if use_memory else None
    checkpointer = memory if use_memory else None

    # Inject static configuration into the state for nodes/edges to access
    static_state = {
        "model_id": model_id,
        "temperature": temperature,
        "system_prompt": final_system_prompt,
        "configured_tools": configured_tools,
        "safe_tool_names": safe_tool_names,
        "datastore_tool_names": datastore_tool_names,
        "max_rewrites": max_rewrites,
    }

    app = workflow.compile(
        checkpointer=checkpointer,
        # Inject static state that nodes/edges can access via the 'config' parameter
        # Note: This assumes 'config' is passed correctly during invocation (e.g., `app.astream(..., config=...)`)
        # We might need to adjust how state is passed or accessed if this doesn't work as expected.
        # An alternative is to pass these during graph construction if they don't change per request.
        # Let's try passing via config first.
    )

    log_adapter.info("Agent graph compiled.")
    # Return the compiled app and the static state needed for invocation
    return app, static_state

