import os
import asyncio
import logging
from dotenv import load_dotenv
import redis.asyncio as redis
from pydantic import BaseModel, Field
from typing import Annotated, Any, Dict, Literal, Sequence, TypedDict, List, Optional, Tuple, Set
from langchain_openai import ChatOpenAI
from langchain_core.messages import BaseMessage, HumanMessage, AIMessage, ToolMessage
from langchain_core.prompts import PromptTemplate, ChatPromptTemplate, MessagesPlaceholder
from langgraph.graph.message import add_messages
from langgraph.graph import END, StateGraph, START, CompiledGraph
from langgraph.prebuilt import ToolNode, tools_condition
from langgraph.checkpoint.memory import MemorySaver

# Import from sibling modules
from .models import AgentState
from .tools import configure_tools, BaseTool # Import BaseTool

# --- Load Environment Variables ---
# Load secrets like API keys, Qdrant URL, Redis URL etc.
dotenv_path = os.path.join(os.path.dirname(__file__), '..', '.env')
if os.path.exists(dotenv_path):
    load_dotenv(dotenv_path=dotenv_path)
else:
    print(f"Warning: .env file not found at {dotenv_path}")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(agent_id)s - %(message)s",
)
logger = logging.getLogger(__name__)

# Global flag for graceful shutdown
running = True

# --- Nodes Definition (Moved to Top Level) ---
async def agent_node(state: AgentState, config: dict):
    """Agent node modified to read config from state and get agent_id from config."""
    agent_id = config.get('configurable', {}).get('agent_id', 'unknown_agent')
    log_adapter = logging.LoggerAdapter(logger, {'agent_id': agent_id})
    log_adapter.info("---CALL AGENT---")

    # Read config from state
    messages = state["messages"]
    final_system_prompt = state["system_prompt"]
    temperature = state["temperature"]
    model_id = state["model_id"]
    configured_tools = state["configured_tools"]

    prompt = ChatPromptTemplate.from_messages([
        ("system", final_system_prompt),
        MessagesPlaceholder(variable_name="messages")
    ])
    model = ChatOpenAI(temperature=temperature, streaming=True, model=model_id)
    if configured_tools:
         valid_tools = [t for t in configured_tools if t is not None]
         if valid_tools:
             model = model.bind_tools(valid_tools)
         else:
              log_adapter.warning("Agent called but no valid tools were configured after filtering.")
    else:
         log_adapter.warning("Agent called but no tools are configured.")

    chain = prompt | model
    # Inject logger adapter into state for tools like get_bonus_points
    # This allows predefined tools using InjectedState to access the logger
    # Note: This assumes tools are adapted to get agent_id from config if needed,
    # or we pass log_adapter explicitly if InjectedState isn't sufficient.
    # For now, let's rely on tools getting agent_id from config if they log.
    # state_with_logger = {**state, "log_adapter": log_adapter} # Removed for now
    try:
        response = await chain.ainvoke({"messages": messages}, config=config) # Pass messages directly
        return {"messages": [response]}
    except Exception as e:
        log_adapter.error(f"Error invoking agent model: {e}", exc_info=True)
        error_message = AIMessage(content=f"Sorry, an error occurred: {e}")
        return {"messages": [error_message]}


async def grade_documents_node(state: AgentState, config: dict):
    """Grade documents node, reading config from state."""
    agent_id = config.get('configurable', {}).get('agent_id', 'unknown_agent')
    log_adapter = logging.LoggerAdapter(logger, {'agent_id': agent_id})
    log_adapter.info("---CHECK RELEVANCE---")

    class grade(BaseModel):
        """Binary score for relevance check."""
        binary_score: str = Field(description="Relevance score 'yes' or 'no'")

    # Read config from state
    messages = state["messages"]
    current_question = state["question"]
    model_id = state["model_id"]
    datastore_tool_names = state["datastore_tool_names"]

    log_adapter.info(f"Grading documents for question: {current_question}")

    last_message = messages[-1] if messages else None
    # Ensure the last message is a ToolMessage from one of the configured datastore tools
    if not isinstance(last_message, ToolMessage) or last_message.name not in datastore_tool_names:
         log_adapter.warning(f"Grade documents called, but last message is not a valid ToolMessage from retriever. Message: {last_message}")
         return {"documents": [], "question": current_question}

    docs = last_message.content.split("\n---RETRIEVER_DOC---\n")
    if not docs or all(not d for d in docs):
         log_adapter.info("No documents retrieved to grade.")
         return {"documents": [], "question": current_question}

    prompt = PromptTemplate(
        template="""Вы оцениваете релевантность извлеченного документа для вопроса пользователя. \n
                Вот извлеченный документ: \n\n {context} \n\n
                Вот вопрос пользователя: {question} \n
                Если документ содержит ключевые слова или семантическое значение, связанные с вопросом пользователя, оцените его как релевантный. \n
                Дайте двоичную оценку 'yes' или 'no', чтобы указать, соответствует ли документ вопросу.""",
        input_variables=["context", "question"],
    )
    model = ChatOpenAI(temperature=0, model=model_id)
    llm_with_tool = model.with_structured_output(grade)

    async def process_doc(doc):
        chain = prompt | llm_with_tool
        try:
            scored_result = await chain.ainvoke({"question": current_question, "context": doc})
            log_adapter.debug(f"Doc: '{doc[:50]}...' Score: {scored_result.binary_score}")
            return doc, scored_result.binary_score
        except Exception as e:
            log_adapter.error(f"Error processing document for grading: {e}", exc_info=True)
            return doc, "no"

    filtered_docs = []
    tasks = [process_doc(d) for d in docs if d]
    results = await asyncio.gather(*tasks)
    filtered_docs = [doc for doc, score in results if score == "yes"]
    log_adapter.info(f"Found {len(filtered_docs)} relevant documents out of {len(docs)}.")

    return {"documents": filtered_docs, "question": current_question}

async def rewrite_node(state: AgentState, config: dict):
    """Rewrite node, reading config from state."""
    agent_id = config.get('configurable', {}).get('agent_id', 'unknown_agent')
    log_adapter = logging.LoggerAdapter(logger, {'agent_id': agent_id})
    log_adapter.info("---TRANSFORM QUERY---")

    # Read config from state
    original_question = state["original_question"]
    messages = state["messages"]
    rewrite_count = state.get("rewrite_count", 0) # Use get for safety
    max_rewrites = state["max_rewrites"]
    model_id = state["model_id"]

    log_adapter.info(f"Rewrite attempt {rewrite_count + 1}/{max_rewrites}")

    if rewrite_count < max_rewrites:
        log_adapter.info(f"Rewriting original question: {original_question}")
        prompt_msg = HumanMessage(
            content=f"""You are an expert at rephrasing questions for better retrieval.
Look at the original question and the chat history. The previous retrieval attempt failed to find relevant documents.
Rephrase the original question to be more specific or clearer, considering the context of the conversation.
Do not add conversational filler, just output the rephrased question.

Chat History:
{messages}

Original Question: {original_question}

Rephrased Question:"""
        )

        model = ChatOpenAI(temperature=0, model=model_id, streaming=False)
        try:
            response = await model.ainvoke([prompt_msg])
            rewritten_question = response.content.strip()
            log_adapter.info(f"Rewritten question: {rewritten_question}")

            if not rewritten_question or rewritten_question.lower() == original_question.lower():
                 log_adapter.warning("Rewriting resulted in empty or identical question. Stopping rewrite.")
                 # Fall through to "no answer" case below
            else:
                # Use a HumanMessage to trigger the agent again with the rewritten question
                # The agent will then decide to use the retriever tool based on this input
                trigger_message = HumanMessage(content=f"Переформулируй запрос так: {rewritten_question}")

                return {
                    "messages": [trigger_message],
                    "question": rewritten_question, # Update the question in state for the next retrieval/grading
                    "rewrite_count": rewrite_count + 1
                }
        except Exception as e:
            log_adapter.error(f"Error during question rewriting: {e}", exc_info=True)
            # Fall through to "no answer" case below

    # If rewrite limit reached or rewrite failed/was empty
    log_adapter.warning(f"Max rewrites ({max_rewrites}) reached or rewrite failed for original question: {original_question}")
    no_answer_message = AIMessage(content="К сожалению, я не смог найти релевантную информацию по вашему запросу даже после его уточнения. Попробуйте задать вопрос по-другому.")
    # Return only the final message, ending the flow here for this branch
    return {
        "messages": [no_answer_message],
        "rewrite_count": 0 # Reset count for next turn if needed, though this branch ends
    }


async def generate_node(state: AgentState, config: dict):
    """Generate node, reading config from state."""
    agent_id = config.get('configurable', {}).get('agent_id', 'unknown_agent')
    log_adapter = logging.LoggerAdapter(logger, {'agent_id': agent_id})
    log_adapter.info("---GENERATE---")

    # Read config from state
    messages = state["messages"]
    current_question = state["question"]
    documents = state["documents"]
    model_id = state["model_id"]
    temperature = state["temperature"]

    if not documents:
         log_adapter.warning("Generate called with no relevant documents.")
         # Check if the previous message was the "max rewrites reached" message from rewrite_node
         if messages and isinstance(messages[-1], AIMessage) and "не смог найти релевантную информацию" in messages[-1].content:
              log_adapter.info("Passing through 'max rewrites reached' message.")
              return {"messages": [messages[-1]]}
         else:
              # If generate is somehow called without docs and not after max rewrites, provide a generic no-info response
              no_answer_response = AIMessage(content="К сожалению, я не смог найти информацию по вашему запросу в доступных источниках.")
              return {"messages": [no_answer_response]}

    log_adapter.info(f"Generating answer for question: {current_question} using {len(documents)} documents.")
    documents_str = "\n\n".join(documents)

    prompt = PromptTemplate(
        template="""Ты помощник для задач с ответами на вопросы. Используйте следующие фрагменты извлеченного контекста, чтобы ответить на вопрос.
        Если у тебя нет ответа на вопрос, просто скажи что у тебя нет данных для ответа на этот вопрос, предложи переформулировать фопрос.
        Старайся отвечать кратко и содержательно.\n
            Вопрос: {question} \n
            Контекст: {context} \n
            Ответ:""",
        input_variables=["context", "question"],
    )
    llm = ChatOpenAI(model_name=model_id, temperature=temperature, streaming=True)
    rag_chain = prompt | llm
    try:
        response = await rag_chain.ainvoke({"context": documents_str, "question": current_question})
        # Ensure response is AIMessage
        if not isinstance(response, BaseMessage):
             response = AIMessage(content=str(response)) # Convert if needed
        return {"messages": [response]}
    except Exception as e:
        log_adapter.error(f"Error during generation: {e}", exc_info=True)
        return {"messages": [AIMessage(content="An error occurred while generating the response.")]}


# --- Edges Definition (Moved to Top Level) ---
async def decide_to_generate(state: AgentState, config: dict) -> Literal["generate", "rewrite"]:
    """Decides whether to generate an answer or rewrite the question."""
    agent_id = config.get('configurable', {}).get('agent_id', 'unknown_agent')
    log_adapter = logging.LoggerAdapter(logger, {'agent_id': agent_id})
    log_adapter.info("---ASSESS GRADED DOCUMENTS---")

    # Read config from state
    filtered_documents = state["documents"]
    rewrite_count = state.get("rewrite_count", 0) # Use get for safety
    max_rewrites = state["max_rewrites"]

    if not filtered_documents:
        if rewrite_count < max_rewrites:
             log_adapter.info("---DECISION: NO RELEVANT DOCUMENTS, REWRITE---")
             return "rewrite"
        else:
             # If max rewrites reached, rewrite_node handles the final message.
             # Route to 'generate' here, and 'generate' will pass through the message from 'rewrite'.
             log_adapter.warning(f"---DECISION: NO RELEVANT DOCUMENTS AND MAX REWRITES ({max_rewrites}) REACHED, GENERATE (PASS THROUGH)---")
             return "generate"
    else:
        log_adapter.info("---DECISION: RELEVANT DOCUMENTS FOUND, GENERATE---")
        return "generate"

def route_tools(state: AgentState, config: dict) -> Literal["retrieve", "safe_tools", "__end__"]:
    """Routes to the appropriate tool node or ends if no tool is called."""
    agent_id = config.get('configurable', {}).get('agent_id', 'unknown_agent')
    log_adapter = logging.LoggerAdapter(logger, {'agent_id': agent_id})
    log_adapter.info("---ROUTE TOOLS---")

    # Read config from state
    datastore_tool_names = state["datastore_tool_names"]
    safe_tool_names = state["safe_tool_names"] # Assuming safe_tool_names are passed in state

    # Use LangGraph's built-in condition to check for tool calls
    next_node = tools_condition(state)
    if next_node == END:
        log_adapter.info("---DECISION: NO TOOLS CALLED, END---")
        return END

    messages = state["messages"]
    last_message = messages[-1]
    # Ensure the last message has tool calls (redundant check due to tools_condition, but safe)
    if not hasattr(last_message, "tool_calls") or not last_message.tool_calls:
         log_adapter.warning("Routing tools, but last message has no tool calls despite tools_condition not being END. Ending.")
         return END

    # Simple routing based on the first tool call
    first_tool_call = last_message.tool_calls[0]
    tool_name = first_tool_call["name"]
    log_adapter.info(f"Tool call detected: {tool_name}")

    # Check against configured tool names from state
    if tool_name in datastore_tool_names:
        log_adapter.info(f"---DECISION: ROUTE TO RETRIEVE ({tool_name})---")
        return "retrieve"
    elif tool_name in safe_tool_names:
        log_adapter.info(f"---DECISION: ROUTE TO SAFE TOOLS ({tool_name})---")
        return "safe_tools"
    else:
         log_adapter.warning(f"Tool call '{tool_name}' does not match any configured tool node. Ending.")
         # Optionally return a ToolMessage error? For now, just end.
         return END


# --- Agent Factory Function (Refactored) ---
def create_agent_app(agent_config: Dict, agent_id: str, redis_client: redis.Redis) -> Tuple[CompiledGraph, Dict[str, Any]]:
    """
    Creates the LangGraph application and returns the compiled app and static state config.
    """
    log_adapter = logging.LoggerAdapter(logger, {'agent_id': agent_id})
    status_key = f"agent_status:{agent_id}"

    # ... (update_status helper remains the same) ...
    async def update_status(status: str, error_detail: Optional[str] = None):
        """Helper to update Redis status."""
        mapping = {"status": status, "pid": os.getpid()}
        if error_detail:
            mapping["error_detail"] = error_detail
        try:
            await redis_client.hset(status_key, mapping=mapping)
            log_adapter.info(f"Status updated to: {status}")
        except Exception as e:
            log_adapter.error(f"Failed to update Redis status to {status}: {e}")

    log_adapter.info("Creating agent graph...")
    # ... (config validation remains the same) ...
    if not isinstance(agent_config, dict) or "config" not in agent_config:
         log_adapter.error("Invalid agent configuration structure: 'config' key missing.")
         raise ValueError("Invalid agent configuration: 'config' key missing.")

    config_data = agent_config.get("config", {})
    if not config_data:
         log_adapter.error("Agent configuration is missing 'config' data.")
         raise ValueError("Invalid agent configuration: missing 'config' data")

    # --- Extract Settings ---
    model_settings = config_data.get("model", {})
    system_prompt_template = config_data.get("systemPrompt", "You are a helpful AI assistant.")
    model_id = model_settings.get("modelId", "gpt-4o-mini")
    temperature = model_settings.get("temperature", 0.2)
    use_memory = model_settings.get("useContextMemory", True) # Needed for checkpointer

    log_adapter.info(f"Model: {model_id}, Temperature: {temperature}")

    # --- Configure Tools ---
    try:
        configured_tools, safe_tools_list, datastore_tool_names, max_rewrites = configure_tools(agent_config, agent_id)
        safe_tool_names = {t.name for t in safe_tools_list} # Get names for state config
    except Exception as e:
        log_adapter.error(f"Failed during tool configuration: {e}", exc_info=True)
        raise ValueError(f"Failed during tool configuration: {e}")

    # --- System Prompt Construction ---
    final_system_prompt = system_prompt_template
    if model_settings.get("limitToKnowledgeBase", False) and datastore_tool_names:
        final_system_prompt += "\nAnswer ONLY from the provided context from the knowledge base. If the answer is not in the context, say you don't know."
    if model_settings.get("answerInUserLanguage", True):
        final_system_prompt += "\nAnswer in the same language as the user's question."
    log_adapter.debug(f"Using system prompt: {final_system_prompt}")

    # --- Static State Configuration ---
    # This dictionary will be merged into the initial state by the runner
    static_state_config = {
        "model_id": model_id,
        "temperature": temperature,
        "system_prompt": final_system_prompt,
        "configured_tools": configured_tools,
        "safe_tool_names": safe_tool_names,
        "datastore_tool_names": datastore_tool_names,
        "max_rewrites": max_rewrites,
    }

    # --- Graph Definition ---
    workflow = StateGraph(AgentState)

    # Add agent node (using top-level function)
    workflow.add_node("agent", agent_node)

    # Add datastore-related nodes if configured (using top-level functions)
    if datastore_tool_names:
         datastore_tools_combined = [t for t in configured_tools if t.name in datastore_tool_names]
         if datastore_tools_combined:
             retrieve_node = ToolNode(datastore_tools_combined, name="retrieve_node")
             workflow.add_node("retrieve", retrieve_node)
             workflow.add_node("grade_documents", grade_documents_node)
             workflow.add_node("rewrite", rewrite_node)
             workflow.add_node("generate", generate_node)
             log_adapter.info("Added nodes: retrieve, grade_documents, rewrite, generate")
         else:
              log_adapter.warning("Datastore tool names found, but no corresponding tool instances. Skipping datastore nodes.")
              datastore_tool_names = set() # Clear the set to avoid routing errors
              static_state_config["datastore_tool_names"] = datastore_tool_names # Update static config
    else:
         log_adapter.info("No datastore tools configured. Retrieval/Grading/Rewrite/Generate nodes skipped.")

    # Add safe tools node if configured
    if safe_tools_list:
         valid_safe_tools = [t for t in safe_tools_list if t is not None]
         if valid_safe_tools:
             safe_tools_node = ToolNode(valid_safe_tools, name="safe_tools_node")
             workflow.add_node("safe_tools", safe_tools_node)
             log_adapter.info("Added node: safe_tools")
         else:
              log_adapter.info("No valid safe tools configured after filtering. Safe_tools node skipped.")
    else:
         log_adapter.info("No safe tools configured. Safe_tools node skipped.")

    # --- Define Edges (using top-level functions) ---
    workflow.add_edge(START, "agent")

    if "safe_tools" in workflow.nodes:
        workflow.add_edge("safe_tools", "agent")

    if "retrieve" in workflow.nodes:
        workflow.add_edge("retrieve", "grade_documents")
        workflow.add_edge("rewrite", "agent")
        workflow.add_edge("generate", END)

        workflow.add_conditional_edges(
            "grade_documents",
            decide_to_generate, # Use top-level function
            {"rewrite": "rewrite", "generate": "generate"},
        )

    possible_routes = {}
    if "safe_tools" in workflow.nodes:
        possible_routes["safe_tools"] = "safe_tools"
    if "retrieve" in workflow.nodes:
        possible_routes["retrieve"] = "retrieve"
    possible_routes[END] = END

    if "safe_tools" in workflow.nodes or "retrieve" in workflow.nodes:
        workflow.add_conditional_edges(
            "agent",
            route_tools, # Use top-level function
            possible_routes
        )
    else:
         workflow.add_edge("agent", END)
         log_adapter.info("No tools configured, agent output directly routed to END.")


    # --- Compile ---
    memory = MemorySaver() if use_memory else None
    try:
        app = workflow.compile(checkpointer=memory)
        log_adapter.info("Agent graph compiled successfully.")
        # Return the app and the static config needed for initial state
        return app, static_state_config
    except Exception as e:
        log_adapter.error(f"Failed to compile agent graph: {e}", exc_info=True)
        raise # Re-raise exception to prevent runner from starting

