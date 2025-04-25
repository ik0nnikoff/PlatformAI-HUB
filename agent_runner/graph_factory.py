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
from langgraph.graph import END, StateGraph, START
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

# --- Agent Factory Function (Refactored) ---
def create_agent_app(agent_config: Dict, agent_id: str, redis_client: redis.Redis) -> Tuple[Any, Dict[str, Any]]:
    """
    Creates the LangGraph application and returns the compiled app and static state config.
    Nodes and edges are defined inside this function to access tools via closure.
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
    # Adjust path based on the actual structure logged: agent_config['config']['simple']['settings']
    config_data = agent_config.get("config", {})
    simple_config = config_data.get("simple", {}) # Get the 'simple' dictionary
    settings_data = simple_config.get("settings", {}) # Get the 'settings' dictionary

    if not settings_data:
        log_adapter.error("Agent configuration is missing 'config.simple.settings' data.")
        raise ValueError("Invalid agent configuration: missing 'config.simple.settings' data")

    model_settings = settings_data.get("model", {}) # Get model settings from settings_data

    # --- ИСПРАВЛЕНИЕ: Извлекаем systemPrompt и useContextMemory из model_settings ---
    system_prompt_template = model_settings.get("systemPrompt", "You are a helpful AI assistant.") # Get systemPrompt from model_settings
    model_id = model_settings.get("modelId", "gpt-4o-mini") # Get modelId from model_settings
    temperature = model_settings.get("temperature", 0.1) # Get temperature from model_settings
    use_memory = model_settings.get("useContextMemory", True) # Get useContextMemory from model_settings
    # --- Конец исправления ---

    log_adapter.info(f"Model: {model_id}, Temperature: {temperature}")
    # --- УДАЛЕНО: log_adapter.debug(f"Extracted system prompt template: '{system_prompt_template}'")

    # --- Configure Tools ---
    try:
        # --- ИСПРАВЛЕНИЕ: Передаем весь agent_config в configure_tools ---
        # configure_tools ожидает всю структуру для доступа, например, к userId
        configured_tools, safe_tools_list, datastore_tool_names, max_rewrites = configure_tools(agent_config, agent_id) # Передаем agent_config
        safe_tool_names = {t.name for t in safe_tools_list} # Get names for state config
        datastore_tools_combined = [t for t in configured_tools if t.name in datastore_tool_names]
        valid_safe_tools = [t for t in safe_tools_list if t is not None]
    except Exception as e:
        log_adapter.error(f"Failed during tool configuration: {e}", exc_info=True)
        raise ValueError(f"Failed during tool configuration: {e}")

    # --- System Prompt Construction ---
    final_system_prompt = system_prompt_template
    # Use model_settings directly
    if model_settings.get("limitToKnowledgeBase", False) and datastore_tool_names:
        final_system_prompt += "\nAnswer ONLY from the provided context from the knowledge base. If the answer is not in the context, say you don't know."
    if model_settings.get("answerInUserLanguage", True):
        final_system_prompt += "\nAnswer in the same language as the user's question."
    log_adapter.debug(f"Using system prompt: {final_system_prompt}")

    # --- Static State Configuration (for initial state, excluding non-serializable tools) ---
    static_state_config = {
        "model_id": model_id,
        "temperature": temperature,
        "system_prompt": final_system_prompt,
        "safe_tool_names": safe_tool_names,
        "datastore_tool_names": datastore_tool_names,
        "max_rewrites": max_rewrites,
    }

    # --- Nodes Definition (Now INSIDE create_agent_app) ---
    async def agent_node(state: AgentState, config: dict):
        """Agent node accessing tools via closure."""
        node_agent_id = config.get('configurable', {}).get('agent_id', 'unknown_agent') # Use ID from config
        node_log_adapter = logging.LoggerAdapter(logger, {'agent_id': node_agent_id})
        node_log_adapter.info("---CALL AGENT---")

        # Read config from state (model_id, temp, system_prompt are now in state)
        messages = state["messages"]
        node_system_prompt = state["system_prompt"]
        node_temperature = state["temperature"]
        node_model_id = state["model_id"]

        # --- УДАЛЕНО: Логирование состояния перед вызовом LLM ---
        # node_log_adapter.info(f"Agent Node State - Model ID: {node_model_id}")
        # node_log_adapter.info(f"Agent Node State - Temperature: {node_temperature}")
        # node_log_adapter.info(f"Agent Node State - System Prompt: '{node_system_prompt}'")
        # --- Конец удаленного кода ---

        prompt = ChatPromptTemplate.from_messages([
            ("system", node_system_prompt),
            MessagesPlaceholder(variable_name="messages")
        ])
        model = ChatOpenAI(temperature=node_temperature, streaming=True, model=node_model_id)

        # Use configured_tools from the outer scope
        if configured_tools:
             valid_tools_for_binding = [t for t in configured_tools if t is not None]
             if valid_tools_for_binding:
                 model = model.bind_tools(valid_tools_for_binding)
             else:
                  node_log_adapter.warning("Agent called but no valid tools were configured after filtering.")
        else:
             node_log_adapter.warning("Agent called but no tools are configured.")

        chain = prompt | model
        try:
            # --- УДАЛЕНО: Логирование сообщений, отправляемых в LLM ---
            # full_prompt_messages = prompt.format_messages(messages=messages)
            # node_log_adapter.info(f"Messages sent to LLM: {full_prompt_messages}")
            # --- Конец удаленного кода ---

            response = await chain.ainvoke({"messages": messages}, config=config)
            return {"messages": [response]}
        except Exception as e:
            node_log_adapter.error(f"Error invoking agent model: {e}", exc_info=True)
            error_message = AIMessage(content=f"Sorry, an error occurred: {e}")
            return {"messages": [error_message]}

    async def grade_documents_node(state: AgentState, config: dict):
        """Grade documents node, reading config from state."""
        node_agent_id = config.get('configurable', {}).get('agent_id', 'unknown_agent')
        node_log_adapter = logging.LoggerAdapter(logger, {'agent_id': node_agent_id})
        node_log_adapter.info("---CHECK RELEVANCE---")

        class grade(BaseModel):
            """Binary score for relevance check."""
            binary_score: str = Field(description="Relevance score 'yes' or 'no'")

        # Read config from state
        messages = state["messages"]
        current_question = state["question"]
        node_model_id = state["model_id"]
        node_datastore_tool_names = state["datastore_tool_names"]

        node_log_adapter.info(f"Grading documents for question: {current_question}")

        last_message = messages[-1] if messages else None
        # Ensure the last message is a ToolMessage from one of the configured datastore tools
        if not isinstance(last_message, ToolMessage) or last_message.name not in node_datastore_tool_names:
             node_log_adapter.warning(f"Grade documents called, but last message is not a valid ToolMessage from retriever. Message: {last_message}")
             return {"documents": [], "question": current_question}

        docs = last_message.content.split("\n---RETRIEVER_DOC---\n")
        if not docs or all(not d for d in docs):
             node_log_adapter.info("No documents retrieved to grade.")
             return {"documents": [], "question": current_question}

        prompt = PromptTemplate(
            template="""Вы оцениваете релевантность извлеченного документа для вопроса пользователя. \n
                    Вот извлеченный документ: \n\n {context} \n\n
                    Вот вопрос пользователя: {question} \n
                    Если документ содержит ключевые слова или семантическое значение, связанные с вопросом пользователя, оцените его как релевантный. \n
                    Дайте двоичную оценку 'yes' или 'no', чтобы указать, соответствует ли документ вопросу.""",
            input_variables=["context", "question"],
        )
        model = ChatOpenAI(temperature=0, model=node_model_id)
        llm_with_tool = model.with_structured_output(grade)

        async def process_doc(doc):
            chain = prompt | llm_with_tool
            try:
                scored_result = await chain.ainvoke({"question": current_question, "context": doc})
                node_log_adapter.debug(f"Doc: '{doc[:50]}...' Score: {scored_result.binary_score}")
                return doc, scored_result.binary_score
            except Exception as e:
                node_log_adapter.error(f"Error processing document for grading: {e}", exc_info=True)
                return doc, "no"

        filtered_docs = []
        tasks = [process_doc(d) for d in docs if d]
        results = await asyncio.gather(*tasks)
        filtered_docs = [doc for doc, score in results if score == "yes"]
        node_log_adapter.info(f"Found {len(filtered_docs)} relevant documents out of {len(docs)}.")

        return {"documents": filtered_docs, "question": current_question}

    async def rewrite_node(state: AgentState, config: dict):
        """Rewrite node, reading config from state."""
        node_agent_id = config.get('configurable', {}).get('agent_id', 'unknown_agent')
        node_log_adapter = logging.LoggerAdapter(logger, {'agent_id': node_agent_id})
        node_log_adapter.info("---TRANSFORM QUERY---")

        # Read config from state
        original_question = state["original_question"]
        messages = state["messages"]
        rewrite_count = state.get("rewrite_count", 0) # Use get for safety
        node_max_rewrites = state["max_rewrites"]
        node_model_id = state["model_id"]

        node_log_adapter.info(f"Rewrite attempt {rewrite_count + 1}/{node_max_rewrites}")

        if rewrite_count < node_max_rewrites:
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

            model = ChatOpenAI(temperature=0, model=node_model_id, streaming=False)
            try:
                response = await model.ainvoke([prompt_msg])
                rewritten_question = response.content.strip()
                node_log_adapter.info(f"Rewritten question: {rewritten_question}")

                if not rewritten_question or rewritten_question.lower() == original_question.lower():
                     node_log_adapter.warning("Rewriting resulted in empty or identical question. Stopping rewrite.")
                     # Fall through to "no answer" case below
                else:
                    trigger_message = HumanMessage(content=f"Переформулируй запрос так: {rewritten_question}")

                    return {
                        "messages": [trigger_message],
                        "question": rewritten_question, # Update the question in state for the next retrieval/grading
                        "rewrite_count": rewrite_count + 1
                    }
            except Exception as e:
                node_log_adapter.error(f"Error during question rewriting: {e}", exc_info=True)
                # Fall through to "no answer" case below

        # If rewrite limit reached or rewrite failed/was empty
        node_log_adapter.warning(f"Max rewrites ({node_max_rewrites}) reached or rewrite failed for original question: {original_question}")
        no_answer_message = AIMessage(content="К сожалению, я не смог найти релевантную информацию по вашему запросу даже после его уточнения. Попробуйте задать вопрос по-другому.")
        return {
            "messages": [no_answer_message],
            "rewrite_count": 0 # Reset count for next turn if needed, though this branch ends
        }

    async def generate_node(state: AgentState, config: dict):
        """Generate node, reading config from state."""
        node_agent_id = config.get('configurable', {}).get('agent_id', 'unknown_agent')
        node_log_adapter = logging.LoggerAdapter(logger, {'agent_id': node_agent_id})
        node_log_adapter.info("---GENERATE---")

        # Read config from state
        messages = state["messages"]
        current_question = state["question"]
        documents = state["documents"]
        node_model_id = state["model_id"]
        node_temperature = state["temperature"]

        if not documents:
             node_log_adapter.warning("Generate called with no relevant documents.")
             if messages and isinstance(messages[-1], AIMessage) and "не смог найти релевантную информацию" in messages[-1].content:
                  node_log_adapter.info("Passing through 'max rewrites reached' message.")
                  return {"messages": [messages[-1]]}
             else:
                  no_answer_response = AIMessage(content="К сожалению, я не смог найти информацию по вашему запросу в доступных источниках.")
                  return {"messages": [no_answer_response]}

        node_log_adapter.info(f"Generating answer for question: {current_question} using {len(documents)} documents.")
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
        llm = ChatOpenAI(model_name=node_model_id, temperature=node_temperature, streaming=True)
        rag_chain = prompt | llm
        try:
            response = await rag_chain.ainvoke({"context": documents_str, "question": current_question})
            if not isinstance(response, BaseMessage):
                 response = AIMessage(content=str(response))
            return {"messages": [response]}
        except Exception as e:
            node_log_adapter.error(f"Error during generation: {e}", exc_info=True)
            return {"messages": [AIMessage(content="An error occurred while generating the response.")]}

    # --- Edges Definition (Now INSIDE create_agent_app) ---
    async def decide_to_generate(state: AgentState, config: dict) -> Literal["generate", "rewrite"]:
        """Decides whether to generate an answer or rewrite the question."""
        node_agent_id = config.get('configurable', {}).get('agent_id', 'unknown_agent')
        node_log_adapter = logging.LoggerAdapter(logger, {'agent_id': node_agent_id})
        node_log_adapter.info("---ASSESS GRADED DOCUMENTS---")

        # Read config from state
        filtered_documents = state["documents"]
        rewrite_count = state.get("rewrite_count", 0) # Use get for safety
        node_max_rewrites = state["max_rewrites"]

        if not filtered_documents:
            if rewrite_count < node_max_rewrites:
                 node_log_adapter.info("---DECISION: NO RELEVANT DOCUMENTS, REWRITE---")
                 return "rewrite"
            else:
                 node_log_adapter.warning(f"---DECISION: NO RELEVANT DOCUMENTS AND MAX REWRITES ({node_max_rewrites}) REACHED, GENERATE (PASS THROUGH)---")
                 return "generate"
        else:
            node_log_adapter.info("---DECISION: RELEVANT DOCUMENTS FOUND, GENERATE---")
            return "generate"

    def route_tools(state: AgentState, config: dict) -> Literal["retrieve", "safe_tools", "__end__"]:
        """Routes to the appropriate tool node or ends if no tool is called."""
        node_agent_id = config.get('configurable', {}).get('agent_id', 'unknown_agent')
        node_log_adapter = logging.LoggerAdapter(logger, {'agent_id': node_agent_id})
        node_log_adapter.info("---ROUTE TOOLS---")

        # Read tool names from state
        node_datastore_tool_names = state["datastore_tool_names"]
        node_safe_tool_names = state["safe_tool_names"]

        next_node = tools_condition(state)
        if next_node == END:
            node_log_adapter.info("---DECISION: NO TOOLS CALLED, END---")
            return END

        messages = state["messages"]
        last_message = messages[-1]
        if not hasattr(last_message, "tool_calls") or not last_message.tool_calls:
             node_log_adapter.warning("Routing tools, but last message has no tool calls. Ending.")
             return END

        first_tool_call = last_message.tool_calls[0]
        tool_name = first_tool_call["name"]
        node_log_adapter.info(f"Tool call detected: {tool_name}")

        if tool_name in node_datastore_tool_names:
            node_log_adapter.info(f"---DECISION: ROUTE TO RETRIEVE ({tool_name})---")
            return "retrieve"
        elif tool_name in node_safe_tool_names:
            node_log_adapter.info(f"---DECISION: ROUTE TO SAFE TOOLS ({tool_name})---")
            return "safe_tools"
        else:
             node_log_adapter.warning(f"Tool call '{tool_name}' does not match any configured tool node. Ending.")
             return END

    # --- Graph Definition ---
    workflow = StateGraph(AgentState)

    # Add agent node (using inner function)
    workflow.add_node("agent", agent_node)

    # Add datastore-related nodes if configured (using inner functions)
    if datastore_tool_names:
         if datastore_tools_combined:
             retrieve_node = ToolNode(datastore_tools_combined, name="retrieve_node")
             workflow.add_node("retrieve", retrieve_node)
             workflow.add_node("grade_documents", grade_documents_node)
             workflow.add_node("rewrite", rewrite_node)
             workflow.add_node("generate", generate_node)
             log_adapter.info("Added nodes: retrieve, grade_documents, rewrite, generate")
         else:
              log_adapter.warning("Datastore tool names found, but no corresponding tool instances. Skipping datastore nodes.")
    else:
         log_adapter.info("No datastore tools configured. Retrieval/Grading/Rewrite/Generate nodes skipped.")

    # Add safe tools node if configured
    if safe_tools_list:
         if valid_safe_tools:
             safe_tools_node = ToolNode(valid_safe_tools, name="safe_tools_node")
             workflow.add_node("safe_tools", safe_tools_node)
             log_adapter.info("Added node: safe_tools")
         else:
              log_adapter.info("No valid safe tools configured after filtering. Safe_tools node skipped.")
    else:
         log_adapter.info("No safe tools configured. Safe_tools node skipped.")

    # --- Define Edges (using inner functions) ---
    workflow.add_edge(START, "agent")

    if "safe_tools" in workflow.nodes:
        workflow.add_edge("safe_tools", "agent")

    if "retrieve" in workflow.nodes:
        workflow.add_edge("retrieve", "grade_documents")
        workflow.add_edge("rewrite", "agent")
        workflow.add_edge("generate", END)

        workflow.add_conditional_edges(
            "grade_documents",
            decide_to_generate,
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
            route_tools,
            possible_routes
        )
    else:
         workflow.add_edge("agent", END)
         log_adapter.info("No tools configured, agent output directly routed to END.")

    # --- Compile ---
    memory = MemorySaver() if use_memory else None
    try:
        app = workflow.compile(checkpointer=memory)
        log_adapter.info(f"Agent graph compiled successfully. Checkpointer: {'Enabled' if memory else 'Disabled'}")
        return app, static_state_config
    except Exception as e:
        log_adapter.error(f"Failed to compile agent graph: {e}", exc_info=True)
        raise

