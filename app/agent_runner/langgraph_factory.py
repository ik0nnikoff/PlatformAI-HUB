import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Literal, Optional, Tuple
from pydantic import BaseModel, Field

from langchain_openai import ChatOpenAI
from langchain_core.messages import BaseMessage, HumanMessage, AIMessage, ToolMessage
from langchain_core.prompts import PromptTemplate, ChatPromptTemplate, MessagesPlaceholder
from langgraph.graph import END, StateGraph, START
from langgraph.prebuilt import ToolNode, tools_condition
from langgraph.checkpoint.memory import MemorySaver

from app.agent_runner.langgraph_models import AgentState, TokenUsageData
from app.agent_runner.langgraph_tools import configure_tools # BaseTool is not directly used here, but by configure_tools
from app.core.config import settings

logger = logging.getLogger(__name__) # Use standard logger

# Global flag for graceful shutdown (if needed by runner_main that uses this factory)
running = True


# --- Helper function to get LLM client ---
def _get_llm(
    provider: str,
    model_name: str,
    temperature: float,
    streaming: bool,
    log_adapter: logging.LoggerAdapter
) -> Optional[ChatOpenAI]:
    """
    Creates and returns an LLM client based on the provider.
    Uses API keys from global settings.
    """
    log_adapter.info(f"Attempting to create LLM client for provider: {provider}, model: {model_name}")
    model_kwargs = {}
    extra_body_kwargs = {}
    
    is_gemini = model_name.startswith("google/")
    if is_gemini and provider.lower() == "openrouter":
        log_adapter.info(f"Detected Google Gemini model: {model_name}. Disabling streaming (not supported by Gemini through OpenRouter).")
        streaming = False
        
    if streaming:
        if provider.lower() in ["openai", "openrouter"]:
            model_kwargs["stream_options"] = {"include_usage": True}

    if provider.lower() == "openai":
        api_key = settings.OPENAI_API_KEY
        if not api_key:
            log_adapter.error("OPENAI_API_KEY not found in settings for OpenAI provider.")
            return None
        return ChatOpenAI(
            model=model_name,
            temperature=temperature,
            streaming=streaming,
            openai_api_key=api_key,
            model_kwargs=model_kwargs
        )
    elif provider.lower() == "openrouter":
        api_key = settings.OPENROUTER_API_KEY
        if not api_key:
            log_adapter.error("OPENROUTER_API_KEY not found in settings for OpenRouter provider.")
            return None
            
        if is_gemini:
            log_adapter.info("Using safe configuration for Gemini through OpenRouter")
            default_safety_settings = [
                {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_MEDIUM_AND_ABOVE"},
                {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_MEDIUM_AND_ABOVE"},
                {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_MEDIUM_AND_ABOVE"},
                {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_MEDIUM_AND_ABOVE"},
            ]
            extra_body_kwargs["safety_settings"] = default_safety_settings
            log_adapter.info(f"Added Gemini safety settings (using snake_case key 'safety_settings'): {extra_body_kwargs.get('safety_settings')}")
        
        return ChatOpenAI(
            model=model_name,
            temperature=temperature,
            streaming=streaming,
            openai_api_key=api_key,
            base_url="https://openrouter.ai/api/v1",
            model_kwargs=model_kwargs,
            extra_body=extra_body_kwargs if extra_body_kwargs else None
        )
    else:
        log_adapter.error(f"Unsupported LLM provider: {provider}")
        return None

# --- Agent Factory Function ---
def create_agent_app(agent_config: Dict, agent_id: str) -> Tuple[Any, Dict[str, Any]]:
    """
    Creates the LangGraph application and returns the compiled app and static state config.
    Nodes and edges are defined inside this function to access tools via closure.
    """
    log_adapter = logging.LoggerAdapter(logger, {'agent_id': agent_id})

    log_adapter.info("Creating agent graph...")
    if not isinstance(agent_config, dict) or "config" not in agent_config:
         log_adapter.error("Invalid agent configuration structure: 'config' key missing.")
         raise ValueError("Invalid agent configuration: 'config' key missing.")

    config_data = agent_config.get("config", {})
    if not config_data:
         log_adapter.error("Agent configuration is missing 'config' data.")
         raise ValueError("Invalid agent configuration: missing 'config' data")

    simple_config = config_data.get("simple", {})
    settings_data = simple_config.get("settings", {})

    if not settings_data:
        log_adapter.error("Agent configuration is missing 'config.simple.settings' data.")
        raise ValueError("Invalid agent configuration: missing 'config.simple.settings' data")

    model_settings = settings_data.get("model", {})
    system_prompt_template = model_settings.get("systemPrompt", "You are a helpful AI assistant.")
    model_id = model_settings.get("modelId", "gpt-4o-mini")
    temperature = model_settings.get("temperature", 0.1)
    provider = model_settings.get("provider", "OpenAI") # Default to OpenAI if not specified
    enable_context_memory = model_settings.get("enableContextMemory", True)
    context_memory_depth = model_settings.get("contextMemoryDepth", 10)
    use_markdown = model_settings.get("useMarkdown", True)
    
    log_adapter.info(f"Provider: {provider}, Model: {model_id}, Temperature: {temperature}")
    log_adapter.info(f"Context Memory: {enable_context_memory}, Depth: {context_memory_depth}, Markdown: {use_markdown}")

    try:
        # Remove log_adapter from the call as it's created within configure_tools
        configured_tools, safe_tools_list, datastore_tool_names, max_rewrites = configure_tools(agent_config, agent_id)
        safe_tool_names = {t.name for t in safe_tools_list if t} 
        # datastore_tools_combined = [t for t in configured_tools if t.name in datastore_tool_names] # Not directly used later
        # valid_safe_tools = [t for t in safe_tools_list if t is not None] # Not directly used later
    except Exception as e:
        log_adapter.error(f"Failed during tool configuration: {e}", exc_info=True)
        raise ValueError(f"Failed during tool configuration: {e}")

    final_system_prompt = system_prompt_template
    if model_settings.get("limitToKnowledgeBase", False) and datastore_tool_names:
        final_system_prompt += "\nAnswer ONLY from the provided context from the knowledge base. If the answer is not in the context, say you don't know."
    if model_settings.get("answerInUserLanguage", True):
        final_system_prompt += "\nAnswer in the same language as the user's question."
    if use_markdown:
        final_system_prompt += "\nFormat your responses using Markdown syntax where appropriate. If you include code blocks, specify the language."
        
    log_adapter.debug(f"Using system prompt: {final_system_prompt}")

    static_state_config = {
        "model_id": model_id,
        "temperature": temperature,
        "system_prompt": final_system_prompt,
        "safe_tool_names": list(safe_tool_names), # Ensure serializable for potential state saving
        "datastore_tool_names": list(datastore_tool_names), # Ensure serializable
        "max_rewrites": max_rewrites,
        "provider": provider,
        "enableContextMemory": enable_context_memory,
        "contextMemoryDepth": context_memory_depth,
    }

    async def agent_node(state: AgentState, config: dict):
        node_agent_id = config.get('configurable', {}).get('agent_id', agent_id) # Fallback to outer agent_id
        node_log_adapter = logging.LoggerAdapter(logger, {'agent_id': node_agent_id})
        node_log_adapter.info("---CALL AGENT---")

        messages = state["messages"]
        node_system_prompt = state["system_prompt"]
        node_temperature = state["temperature"]
        node_model_id = state["model_id"]
        node_provider = state["provider"]

        moscow_tz = timezone(timedelta(hours=3))
        current_time_str = datetime.now(moscow_tz).isoformat()

        # Ensure current_time placeholder exists before formatting
        if "{current_time}" not in node_system_prompt:
             node_system_prompt += "\nТекущее время (Москва): {current_time}"
        
        # Use .partial for current_time
        prompt_template = ChatPromptTemplate.from_messages([
            ("system", node_system_prompt),
            MessagesPlaceholder(variable_name="messages")
        ])
        prompt = prompt_template.partial(current_time=current_time_str)


        model = _get_llm(
            provider=node_provider,
            model_name=node_model_id,
            temperature=node_temperature,
            streaming=True, # Streaming is True for agent responses
            log_adapter=node_log_adapter
        )
        if not model:
            error_message = AIMessage(content=f"Sorry, an error occurred: Could not initialize LLM for provider {node_provider}.")
            return {"messages": [error_message], "token_usage_events": state.get("token_usage_events", [])}


        valid_tools_for_binding = [t for t in configured_tools if t is not None]
        if valid_tools_for_binding:
            model = model.bind_tools(valid_tools_for_binding)
        else:
            node_log_adapter.warning("Agent called but no valid tools were configured after filtering.")

        chain = prompt | model
        try:
            response = await chain.ainvoke({"messages": messages}, config=config)
            
            node_log_adapter.info(f"Agent node response type: {type(response)}")
            node_log_adapter.info(f"Agent node response content preview: {response.content[:100] if hasattr(response, 'content') else 'N/A'}")
            if hasattr(response, 'response_metadata'):
                node_log_adapter.info(f"Agent node response_metadata: {response.response_metadata}")
            if hasattr(response, 'usage_metadata'):
                node_log_adapter.info(f"Agent node usage_metadata: {response.usage_metadata}")

            token_event_data = None
            prompt_tokens, completion_tokens, total_tokens = 0, 0, 0
            model_name_from_meta = node_model_id

            if hasattr(response, 'usage_metadata') and response.usage_metadata:
                usage_meta = response.usage_metadata
                prompt_tokens = usage_meta.get('prompt_tokens', 0) if usage_meta.get('prompt_tokens') is not None else usage_meta.get('input_tokens', 0)
                completion_tokens = usage_meta.get('completion_tokens', 0) if usage_meta.get('completion_tokens') is not None else usage_meta.get('output_tokens', 0)
                total_tokens = usage_meta.get('total_tokens', 0)
                node_log_adapter.info(f"Token usage from usage_metadata: P:{prompt_tokens} C:{completion_tokens} T:{total_tokens}")
                if hasattr(response, 'response_metadata') and response.response_metadata and response.response_metadata.get('model_name'):
                    model_name_from_meta = response.response_metadata['model_name']
            elif hasattr(response, 'response_metadata') and response.response_metadata and 'token_usage' in response.response_metadata:
                usage = response.response_metadata['token_usage']
                prompt_tokens = usage.get('prompt_tokens', 0)
                completion_tokens = usage.get('completion_tokens', 0)
                total_tokens = usage.get('total_tokens', 0)
                node_log_adapter.info(f"Token usage from response_metadata['token_usage']: P:{prompt_tokens} C:{completion_tokens} T:{total_tokens}")
                if response.response_metadata.get('model_name'):
                    model_name_from_meta = response.response_metadata['model_name']
            else:
                node_log_adapter.warning("Token usage data not found in usage_metadata or response_metadata for agent_node.")

            current_token_events = state.get("token_usage_events", [])
            if total_tokens > 0 or prompt_tokens > 0 or completion_tokens > 0:
                token_event_data = TokenUsageData(
                    call_type="agent_llm",
                    model_id=model_name_from_meta,
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                    total_tokens=total_tokens,
                    timestamp=datetime.now(timezone.utc).isoformat()
                )
                current_token_events.append(token_event_data)
                node_log_adapter.info(f"Token usage for agent_llm: {token_event_data.total_tokens} tokens recorded.")
            
            if hasattr(response, 'invalid_tool_calls') and response.invalid_tool_calls and \
               (not hasattr(response, 'tool_calls') or not response.tool_calls):
                node_log_adapter.warning(f"Response has invalid_tool_calls but no valid tool_calls. Attempting recovery. Invalid: {response.invalid_tool_calls}")
                recovered_calls = []
                remaining_invalid_calls = []
                for itc_obj in response.invalid_tool_calls:
                    error_value = None
                    itc_dict = {}
                    if isinstance(itc_obj, dict):
                        itc_dict = itc_obj
                        error_value = itc_dict.get('error')
                    elif hasattr(itc_obj, 'error') and hasattr(itc_obj, 'name') and hasattr(itc_obj, 'args') and hasattr(itc_obj, 'id'):
                        error_value = itc_obj.error
                        itc_dict = {"name": itc_obj.name, "args": itc_obj.args, "id": itc_obj.id, "type": getattr(itc_obj, 'type', 'function')}
                    else:
                        node_log_adapter.warning(f"Skipping unrecognized invalid_tool_call object: {itc_obj}")
                        remaining_invalid_calls.append(itc_obj)
                        continue
                    
                    if error_value is None:
                        args_value = itc_dict.get("args", {}) # Ensure args is a dict
                        call_to_add = {"name": itc_dict.get("name"), "args": args_value, "id": itc_dict.get("id"), "type": itc_dict.get("type", "function")}
                        if call_to_add.get("name") and call_to_add.get("id"):
                            recovered_calls.append(call_to_add)
                            node_log_adapter.info(f"Recovered tool call: {call_to_add}")
                        else:
                            node_log_adapter.warning(f"Could not fully recover tool call, missing name or id: {itc_dict}")
                            remaining_invalid_calls.append(itc_obj)
                    else:
                        remaining_invalid_calls.append(itc_obj)
                
                if recovered_calls:
                    if not hasattr(response, 'tool_calls') or response.tool_calls is None:
                        response.tool_calls = []
                    response.tool_calls.extend(recovered_calls)
                    response.invalid_tool_calls = remaining_invalid_calls
                    node_log_adapter.info(f"Successfully recovered/added {len(recovered_calls)} tool_calls. New tool_calls: {response.tool_calls}")

            return {"messages": [response], "token_usage_events": current_token_events}
        except Exception as e:
            node_log_adapter.error(f"Error invoking agent model: {e}", exc_info=True)
            error_message = AIMessage(content=f"Sorry, an error occurred: {e}")
            return {"messages": [error_message], "token_usage_events": state.get("token_usage_events", [])}

    async def grade_documents_node(state: AgentState, config: dict):
        node_agent_id = config.get('configurable', {}).get('agent_id', agent_id)
        node_log_adapter = logging.LoggerAdapter(logger, {'agent_id': node_agent_id})
        node_log_adapter.info("---CHECK RELEVANCE---")

        class Grade(BaseModel): # Renamed to Grade to avoid conflict with pydantic's grade
            """Binary score for relevance check."""
            binary_score: str = Field(description="Relevance score 'yes' or 'no'")

        messages = state["messages"]
        current_question = state["question"]
        node_model_id = state["model_id"] # Could use a specific grading model from config if available
        node_datastore_tool_names = state["datastore_tool_names"]
        node_provider = state["provider"]
        node_temperature = 0.0

        node_log_adapter.info(f"Grading documents for question: {current_question}")

        last_message = messages[-1] if messages else None
        if not isinstance(last_message, ToolMessage) or last_message.name not in node_datastore_tool_names:
             node_log_adapter.warning(f"Grade documents called, but last message is not a valid ToolMessage from retriever. Message: {last_message}")
             return {"documents": [], "question": current_question, "token_usage_events": state.get("token_usage_events", [])}

        # Assuming docs are split by a specific delimiter from the tool output
        # If docs are already a list in ToolMessage.content, this needs adjustment
        content_str = last_message.content
        if isinstance(content_str, list): # If content is already a list of docs
            docs = [str(d) for d in content_str] # Ensure all are strings
        elif isinstance(content_str, str):
             docs = content_str.split("\n---RETRIEVER_DOC---\n") if content_str else []
        else:
            node_log_adapter.warning(f"Unexpected document content type: {type(content_str)}. Cannot grade.")
            docs = []

        if not docs or all(not d.strip() for d in docs): # Check for empty or whitespace-only docs
             node_log_adapter.info("No documents retrieved or all documents are empty.")
             return {"documents": [], "question": current_question, "token_usage_events": state.get("token_usage_events", [])}

        prompt = PromptTemplate(
            template="""
Вы оцениваете релевантность извлеченного документа для вопроса пользователя.
Вот извлеченный документ: \n\n {context} \n\n
Вот вопрос пользователя: {question} \n
Если документ содержит ключевые слова или семантическое значение, связанные с вопросом пользователя, оцените его как релевантный.
Дайте двоичную оценку 'yes' или 'no', чтобы указать, соответствует ли документ вопросу.""",
            input_variables=["context", "question"],
        )
        
        model = _get_llm(
            provider=node_provider,
            model_name=node_model_id, 
            temperature=node_temperature,
            streaming=False, # Grading doesn't need streaming
            log_adapter=node_log_adapter
        )
        if not model:
            node_log_adapter.error(f"Could not initialize LLM for grading (provider: {node_provider}). Returning no documents.")
            return {"documents": [], "question": current_question, "token_usage_events": state.get("token_usage_events", [])}
            
        llm_with_tool = model.with_structured_output(Grade, include_raw=True)

        async def process_doc(doc_content_item: str): # Changed variable name
            chain = prompt | llm_with_tool
            token_event_for_doc = None
            try:
                # Ensure doc_content_item is a string for the context
                context_str = str(doc_content_item) if not isinstance(doc_content_item, str) else doc_content_item

                invocation_result = await chain.ainvoke({"question": current_question, "context": context_str})
                
                parsed_grade_obj = invocation_result.get("parsed") # Renamed variable
                raw_ai_message = invocation_result.get("raw")
                
                binary_score = "no"
                if parsed_grade_obj:
                    binary_score = parsed_grade_obj.binary_score
                else:
                    node_log_adapter.warning(f"Grading failed to parse output for doc: {context_str[:100]}")

                if raw_ai_message:
                    node_log_adapter.debug(f"Grading raw AIMessage metadata: {raw_ai_message.response_metadata if hasattr(raw_ai_message, 'response_metadata') else 'N/A'}")
                    node_log_adapter.debug(f"Grading raw AIMessage usage_metadata: {raw_ai_message.usage_metadata if hasattr(raw_ai_message, 'usage_metadata') else 'N/A'}")

                    prompt_tokens, completion_tokens, total_tokens = 0, 0, 0
                    model_name_from_meta = node_model_id

                    if hasattr(raw_ai_message, 'usage_metadata') and raw_ai_message.usage_metadata:
                        usage_meta = raw_ai_message.usage_metadata
                        prompt_tokens = usage_meta.get('prompt_tokens', 0) if usage_meta.get('prompt_tokens') is not None else usage_meta.get('input_tokens', 0)
                        completion_tokens = usage_meta.get('completion_tokens', 0) if usage_meta.get('completion_tokens') is not None else usage_meta.get('output_tokens', 0)
                        total_tokens = usage_meta.get('total_tokens', 0)
                        if hasattr(raw_ai_message, 'response_metadata') and raw_ai_message.response_metadata and raw_ai_message.response_metadata.get('model_name'):
                            model_name_from_meta = raw_ai_message.response_metadata['model_name']
                    elif hasattr(raw_ai_message, 'response_metadata') and raw_ai_message.response_metadata and 'token_usage' in raw_ai_message.response_metadata:
                        usage = raw_ai_message.response_metadata['token_usage']
                        prompt_tokens = usage.get('prompt_tokens', 0)
                        completion_tokens = usage.get('completion_tokens', 0)
                        total_tokens = usage.get('total_tokens', 0)
                        if raw_ai_message.response_metadata.get('model_name'):
                            model_name_from_meta = raw_ai_message.response_metadata['model_name']
                    
                    if total_tokens > 0 or prompt_tokens > 0 or completion_tokens > 0:
                        token_event_for_doc = TokenUsageData(
                            call_type="grading_llm",
                            model_id=model_name_from_meta,
                            prompt_tokens=prompt_tokens,
                            completion_tokens=completion_tokens,
                            total_tokens=total_tokens,
                            timestamp=datetime.now(timezone.utc).isoformat()
                        )
                else:
                    node_log_adapter.warning("Grading raw AIMessage not found in invocation_result.")
                
                return context_str, binary_score, token_event_for_doc # Return context_str
            except Exception as e:
                node_log_adapter.error(f"Error processing document for grading: {e}", exc_info=True)
                return str(doc_content_item), "no", None # Ensure original doc content is returned as string

        filtered_docs = []
        # Filter out empty or whitespace-only strings before processing
        valid_docs_to_process = [d for d in docs if d and d.strip()]
        tasks = [process_doc(d) for d in valid_docs_to_process]
        
        current_token_events = state.get("token_usage_events", [])
        if tasks: # Only run gather if there are tasks
            results = await asyncio.gather(*tasks)
            for doc_content, score, token_event_data_item in results: # Renamed variable
                if score == "yes":
                    filtered_docs.append(doc_content)
                if token_event_data_item:
                    current_token_events.append(token_event_data_item)
        else:
            node_log_adapter.info("No valid documents to grade after filtering.")


        node_log_adapter.info(f"Graded documents. Relevant: {len(filtered_docs)}/{len(valid_docs_to_process)}")
        return {"documents": filtered_docs, "question": current_question, "token_usage_events": current_token_events}

    async def rewrite_question_node(state: AgentState, config: dict):
        node_agent_id = config.get('configurable', {}).get('agent_id', agent_id)
        node_log_adapter = logging.LoggerAdapter(logger, {'agent_id': node_agent_id})
        node_log_adapter.info("---REWRITE QUESTION---")

        current_question = state["question"]
        node_model_id = state["model_id"]
        node_provider = state["provider"]
        node_temperature = 0.3 # Allow some creativity for rewriting

        prompt = PromptTemplate(
            template="""
Вы являетесь экспертом в преобразовании вопросов пользователя для улучшения результатов поиска в векторной базе данных. \\\\
Ваша задача - переформулировать следующий вопрос, чтобы он был более четким, конкретным и лучше подходил для поиска релевантных документов. \\\\
Сохраните основной смысл вопроса, но сделайте его более эффективным для поиска. Верните ТОЛЬКО переформулированный вопрос.
Original question: {question}""",
            input_variables=["question"],
        )
        
        model = _get_llm(
            provider=node_provider,
            model_name=node_model_id, 
            temperature=node_temperature,
            streaming=False, # No streaming for rewrite
            log_adapter=node_log_adapter
        )
        if not model:
            node_log_adapter.error(f"Could not initialize LLM for rewriting (provider: {node_provider}). Using original question.")
            return {"question": current_question, "rewrite_attempts": state.get("rewrite_attempts", 0) + 1, "token_usage_events": state.get("token_usage_events", [])}

        chain = prompt | model
        try:
            response = await chain.ainvoke({"question": current_question}, config=config) # Pass config
            rewritten_question = response.content.strip()
            node_log_adapter.info(f"Original question: '{current_question}' | Rewritten question: '{rewritten_question}'")
            
            current_token_events = state.get("token_usage_events", [])
            prompt_tokens, completion_tokens, total_tokens = 0,0,0
            model_name_from_meta = node_model_id

            if hasattr(response, 'usage_metadata') and response.usage_metadata:
                usage_meta = response.usage_metadata
                prompt_tokens = usage_meta.get('prompt_tokens', 0) if usage_meta.get('prompt_tokens') is not None else usage_meta.get('input_tokens', 0)
                completion_tokens = usage_meta.get('completion_tokens', 0) if usage_meta.get('completion_tokens') is not None else usage_meta.get('output_tokens', 0)
                total_tokens = usage_meta.get('total_tokens', 0)
                if hasattr(response, 'response_metadata') and response.response_metadata and response.response_metadata.get('model_name'):
                    model_name_from_meta = response.response_metadata['model_name']
            elif hasattr(response, 'response_metadata') and response.response_metadata and 'token_usage' in response.response_metadata:
                usage = response.response_metadata['token_usage']
                prompt_tokens = usage.get('prompt_tokens', 0)
                completion_tokens = usage.get('completion_tokens', 0)
                total_tokens = usage.get('total_tokens', 0)
                if response.response_metadata.get('model_name'):
                    model_name_from_meta = response.response_metadata['model_name']
            
            if total_tokens > 0 or prompt_tokens > 0 or completion_tokens > 0:
                token_event_data = TokenUsageData(
                    call_type="rewrite_llm",
                    model_id=model_name_from_meta,
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                    total_tokens=total_tokens,
                    timestamp=datetime.now(timezone.utc).isoformat()
                )
                current_token_events.append(token_event_data)
                node_log_adapter.info(f"Token usage for rewrite_llm: {token_event_data.total_tokens} tokens recorded.")

            return {"question": rewritten_question, "rewrite_attempts": state.get("rewrite_attempts", 0) + 1, "token_usage_events": current_token_events}
        except Exception as e:
            node_log_adapter.error(f"Error rewriting question: {e}", exc_info=True)
            return {"question": current_question, "rewrite_attempts": state.get("rewrite_attempts", 0) + 1, "token_usage_events": state.get("token_usage_events", [])}

    def should_rewrite_question(state: AgentState) -> Literal["rewrite_question_node", "tool_node", "__end__"]:
        node_log_adapter = logging.LoggerAdapter(logger, {'agent_id': agent_id}) # Use outer agent_id for conditional logic
        node_log_adapter.info("---SHOULD REWRITE QUESTION?---")
        documents = state.get("documents", [])
        rewrite_attempts = state.get("rewrite_attempts", 0)
        max_rewrites_allowed = state.get("max_rewrites", 1) # Default to 1 if not in state

        # If there are documents, no need to rewrite.
        if documents:
            node_log_adapter.info("Documents found, no rewrite needed.")
            return "tool_node" # Proceed to tool_node for potential non-datastore tools or direct response
        
        # If no documents and rewrite attempts are less than max allowed.
        if rewrite_attempts < max_rewrites_allowed:
            node_log_adapter.info(f"No documents, {rewrite_attempts}/{max_rewrites_allowed} rewrites. Proceeding to rewrite.")
            return "rewrite_question_node"
        
        # If no documents and max rewrites reached.
        node_log_adapter.info(f"No documents after {rewrite_attempts} rewrites. Ending or proceeding to agent for direct answer.")
        # Decide if we should go to agent_node to answer "I don't know" or END.
        # If the last action was trying to use datastore tools, and it failed after rewrites,
        # then it's likely best to go to agent_node to formulate a final response.
        # This logic might need refinement based on desired flow.
        # For now, if datastore tools were attempted (implied by rewrite logic), let agent try to answer.
        # If no datastore tools configured, this path might not be hit often.
        messages = state["messages"]
        last_message = messages[-1] if messages else None
        # If the last message was a tool call (likely datastore), let agent respond
        if isinstance(last_message, ToolMessage) and last_message.name in state.get("datastore_tool_names", []):
             node_log_adapter.info("Max rewrites reached, datastore tools were used. Letting agent formulate final response.")
             return "agent_node" # Let agent respond e.g. "I couldn't find information"

        node_log_adapter.info("Max rewrites reached, no relevant documents. Ending flow or agent will respond.")
        return "agent_node" # Default to agent_node to give a final response or END if agent decides so.


    def decide_after_retrieval(state: AgentState) -> Literal["agent_node", "rewrite_question_node"]:
        node_log_adapter = logging.LoggerAdapter(logger, {'agent_id': agent_id})
        node_log_adapter.info("---DECIDE AFTER RETRIEVAL---")
        documents = state.get("documents", [])
        rewrite_attempts = state.get("rewrite_attempts", 0)
        max_rewrites_allowed = state.get("max_rewrites", 1)

        if documents:
            node_log_adapter.info("Documents found after retrieval/grading. Proceeding to agent.")
            # Reset rewrite_attempts if documents are found, to allow rewriting for new user turns if needed.
            # state["rewrite_attempts"] = 0 # This modification won't persist unless returned by the node.
            # Instead, the state update should be part of the return.
            return "agent_node" 
        
        node_log_adapter.info("No documents after retrieval/grading.")
        if rewrite_attempts < max_rewrites_allowed:
            node_log_adapter.info(f"Attempting rewrite ({rewrite_attempts + 1}/{max_rewrites_allowed}).")
            return "rewrite_question_node"
        else:
            node_log_adapter.info("Max rewrites reached. Proceeding to agent to respond without documents.")
            return "agent_node"


    # --- Graph Definition ---
    workflow = StateGraph(AgentState)

    # Add nodes
    workflow.add_node("agent_node", agent_node)
    tool_node_instance = ToolNode(configured_tools if configured_tools else []) # Ensure tool_node gets a list
    workflow.add_node("tool_node", tool_node_instance) # Pass the actual tools
    workflow.add_node("grade_documents_node", grade_documents_node)
    workflow.add_node("rewrite_question_node", rewrite_question_node)

    # Define edges
    workflow.add_edge(START, "agent_node") # Start with the agent to process initial input

    # Conditional Edges
    # After agent_node, decide if tools are needed, or if it's a direct response
    workflow.add_conditional_edges(
        "agent_node",
        tools_condition, # LangGraph's built-in condition
        {"tools": "tool_node", END: "__end__"} # If tools called, go to tool_node, else END
    )

    # After tool_node, decide based on which tools were called
    def after_tool_node_condition(state: AgentState) -> Literal["grade_documents_node", "agent_node", "__end__"]:
        node_log_adapter = logging.LoggerAdapter(logger, {'agent_id': agent_id})
        node_log_adapter.info("---DECISION AFTER TOOL NODE---")
        messages = state["messages"]
        last_message = messages[-1] if messages else None

        if not isinstance(last_message, ToolMessage):
            node_log_adapter.warning("Last message not a ToolMessage after tool_node. Ending.") # Should not happen
            return "__end__"

        # If the tool used was a datastore tool, go to grading
        if last_message.name in state.get("datastore_tool_names", []):
            node_log_adapter.info(f"Datastore tool '{last_message.name}' used. Proceeding to grade documents.")
            return "grade_documents_node"
        
        # If a non-datastore tool was used (e.g., a safe tool directly called by LLM)
        # or if it's a response from a tool that doesn't require grading (e.g. a calculator)
        # go back to the agent to process the tool's output.
        node_log_adapter.info(f"Non-datastore tool '{last_message.name}' used or no grading needed. Returning to agent.")
        return "agent_node"

    workflow.add_conditional_edges("tool_node", after_tool_node_condition)
    workflow.add_conditional_edges("grade_documents_node", decide_after_retrieval)
    workflow.add_conditional_edges("rewrite_question_node", after_tool_node_condition) # After rewrite, try tools again (which leads to after_tool_node_condition)

    # Compile the graph
    memory_checkpointer = None
    if enable_context_memory: # enable_context_memory is defined earlier from agent_config
        memory_checkpointer = MemorySaver()
        log_adapter.info("Context memory enabled. Initializing MemorySaver checkpointer.")
    else:
        log_adapter.info("Context memory disabled by agent config. Compiling graph without a persistent checkpointer.")

    try:
        if memory_checkpointer:
            app = workflow.compile(checkpointer=memory_checkpointer)
            log_adapter.info("Agent graph compiled successfully with MemorySaver checkpointer.")
        else:
            app = workflow.compile() 
            log_adapter.info("Agent graph compiled successfully without a dedicated checkpointer.")
            
    except Exception as e:
        log_adapter.error(f"Error compiling agent graph: {e}", exc_info=True)
        raise
        
    return app, static_state_config
