import logging
import asyncio # Added asyncio
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional, Tuple, List, Literal # Added List, Literal
from pydantic import BaseModel, Field # Added BaseModel, Field

from langchain_openai import ChatOpenAI
from langchain_core.messages import BaseMessage, HumanMessage, AIMessage, ToolMessage
from langchain_core.prompts import PromptTemplate, ChatPromptTemplate, MessagesPlaceholder
from langgraph.graph import END, StateGraph, START # Add START
from langgraph.prebuilt import ToolNode, tools_condition
from langgraph.checkpoint.memory import MemorySaver

from .models import AgentState, TokenUsageData
from .tools import configure_tools
from app.core.config import settings

# Global flag for graceful shutdown (if needed by runner_main that uses this factory)
# This might be re-evaluated if it's better managed by the AgentRunner instance
running = True 

class GraphFactory:
    def __init__(self, agent_config: Dict, agent_id: str, logger: logging.LoggerAdapter):
        self.agent_config = agent_config
        self.agent_id = agent_id
        self.logger = logger
        
        # Эти атрибуты будут инициализированы в соответствующих методах
        self.llm: Optional[ChatOpenAI] = None
        self.configured_tools_list: list = []
        self.safe_tools_list: list = []
        self.datastore_tool_list: list = []
        self.datastore_tool_names: set = set()
        self.safe_tool_names: set = set()
        self.max_rewrites: int = 3
        self.system_prompt: str = ""
        self.static_state_config: dict = {}

        # Configure the main LLM instance upon initialization
        self._configure_main_llm()

    def _configure_main_llm(self) -> None:
        """
        Configures the main LLM instance (self.llm) for the graph.
        This is typically called once during factory initialization.
        Nodes requiring LLMs with different parameters should use _create_llm_instance.
        """
        config_data = self.agent_config.get("config", {})
        simple_config = config_data.get("simple", {})
        settings_data = simple_config.get("settings", {})
        model_settings = settings_data.get("model", {})

        provider = model_settings.get("provider", "OpenAI")
        model_name = model_settings.get("modelId", "gpt-4o-mini")
        temperature = model_settings.get("temperature", 0.1)
        # Streaming for the main LLM can be a default, or configured if needed.
        # For now, let's assume the main agent LLM might use streaming.
        streaming = model_settings.get("streaming", True) 

        self.llm = self._create_llm_instance(
            provider=provider,
            model_name=model_name,
            temperature=temperature,
            streaming=streaming # Pass streaming to the creator method
        )
        if self.llm:
            self.logger.info(f"Main LLM configured: {model_name} via {provider}")
        else:
            self.logger.error(f"Failed to configure main LLM: {model_name} via {provider}")


    def _create_llm_instance(
        self, 
        provider: str, 
        model_name: str, 
        temperature: float, 
        streaming: bool,
        log_adapter_override: Optional[logging.LoggerAdapter] = None
    ) -> Optional[ChatOpenAI]:
        """
        Creates and returns an LLM client based on the provider and parameters.
        Uses self.logger by default, but can be overridden.
        """
        logger_to_use = log_adapter_override if log_adapter_override else self.logger
        logger_to_use.info(f"Attempting to create LLM instance for provider: {provider}, model: {model_name}, temp: {temperature}, streaming: {streaming}")
        
        model_kwargs = {}
        extra_body_kwargs = {}
        
        is_gemini = model_name.startswith("google/")
        # Gemini via OpenRouter does not support streaming according to original logic
        if is_gemini and provider.lower() == "openrouter":
            logger_to_use.info(f"Detected Google Gemini model: {model_name} via OpenRouter. Forcing streaming to False.")
            streaming = False 
            
        if streaming:
            if provider.lower() in ["openai", "openrouter"]:
                # For OpenAI and OpenRouter, include_usage is often enabled with streaming
                model_kwargs["stream_options"] = {"include_usage": True}

        if provider.lower() == "openai":
            api_key = settings.OPENAI_API_KEY
            if not api_key:
                logger_to_use.error("OPENAI_API_KEY not found in settings for OpenAI provider.")
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
                logger_to_use.error("OPENROUTER_API_KEY not found in settings for OpenRouter provider.")
                return None
            
            if is_gemini:
                logger_to_use.info("Applying safety settings for Gemini model via OpenRouter.")
                default_safety_settings = [
                    {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_MEDIUM_AND_ABOVE"},
                    {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_MEDIUM_AND_ABOVE"},
                    {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_MEDIUM_AND_ABOVE"},
                    {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_MEDIUM_AND_ABOVE"},
                ]
                extra_body_kwargs["safety_settings"] = default_safety_settings
                logger_to_use.info(f"Added Gemini safety settings (using snake_case key 'safety_settings'): {extra_body_kwargs.get('safety_settings')}")

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
            logger_to_use.error(f"Unsupported LLM provider: {provider}")
            return None

    def _configure_tools(self) -> None:
        """
        Configures tools based on the agent_config and stores them in self.
        Uses the configure_tools function from langgraph.tools.
        """
        try:
            (
                self.configured_tools_list,
                self.safe_tools_list,
                self.datastore_tool_list, # Added to capture this specific list
                self.datastore_tool_names,
                self.max_rewrites,
            ) = configure_tools(self.agent_config, self.agent_id)
            
            self.safe_tool_names = {t.name for t in self.safe_tools_list if t} # Ensure t is not None
            self.logger.info(
                f"Tools configured: {len(self.configured_tools_list)} total, "
                f"{len(self.safe_tools_list)} safe, "
                f"{len(self.datastore_tool_list)} datastore tools. "
                f"Max rewrites: {self.max_rewrites}."
            )
        except Exception as e:
            self.logger.error(f"Failed during tool configuration: {e}", exc_info=True)
            # Reset tool-related attributes to safe defaults
            self.configured_tools_list = []
            self.safe_tools_list = []
            self.datastore_tool_list = []
            self.datastore_tool_names = set()
            self.safe_tool_names = set()
            # self.max_rewrites can retain its default or be set to a safe value like 0
            # raise ValueError(f"Failed during tool configuration: {e}") # Optionally re-raise

    def _build_system_prompt(self) -> None:
        """
        Constructs the system prompt based on agent_config and configured tools.
        The final prompt is stored in self.system_prompt.
        """
        config_data = self.agent_config.get("config", {})
        simple_config = config_data.get("simple", {})
        settings_data = simple_config.get("settings", {})
        model_settings = settings_data.get("model", {})

        system_prompt_template = model_settings.get("systemPrompt", "You are a helpful AI assistant.")
        limit_to_kb = model_settings.get("limitToKnowledgeBase", False)
        answer_in_user_lang = model_settings.get("answerInUserLanguage", True)
        use_markdown = model_settings.get("useMarkdown", True)

        final_system_prompt = system_prompt_template

        # self.datastore_tool_names should be populated by _configure_tools before this method is called
        if limit_to_kb and self.datastore_tool_names:
            final_system_prompt += "\nAnswer ONLY from the provided context from the knowledge base. If the answer is not in the context, say you don't know."
        
        if answer_in_user_lang:
            final_system_prompt += "\nAnswer in the same language as the user's question."
        
        if use_markdown:
            final_system_prompt += "\nFormat your responses using Markdown syntax where appropriate. If you include code blocks, specify the language."
        
        self.system_prompt = final_system_prompt
        self.logger.debug(f"System prompt constructed: {self.system_prompt}")

    async def _agent_node(self, state: AgentState, config: dict):
        """Agent node logic, adapted to be a method of GraphFactory."""
        self.logger.info(f"---CALL AGENT NODE (Agent ID: {self.agent_id})---")

        messages = state["messages"]
        node_system_prompt = state["system_prompt"] # System prompt from state (can be dynamic)
        node_temperature = state["temperature"]
        node_model_id = state["model_id"]
        node_provider = state["provider"]

        moscow_tz = timezone(timedelta(hours=3))
        current_time_str = datetime.now(moscow_tz).isoformat()

        # Ensure current_time placeholder is in the system prompt
        # This logic might be better placed when the system_prompt in state is initially set,
        # but for now, we replicate the original node's behavior.
        if "{current_time}" not in node_system_prompt:
             node_system_prompt_with_time = node_system_prompt + "\nТекущее время (Москва): {current_time}"
        else:
            node_system_prompt_with_time = node_system_prompt
        
        prompt = ChatPromptTemplate.from_messages([
            ("system", node_system_prompt_with_time),
            MessagesPlaceholder(variable_name="messages")
        ]).partial(current_time=current_time_str)

        # Create LLM instance for this specific call
        # Agent node typically uses streaming=True
        model = self._create_llm_instance(
            provider=node_provider,
            model_name=node_model_id,
            temperature=node_temperature,
            streaming=True, 
            log_adapter_override=self.logger # Use the factory's logger
        )

        if not model:
            error_message = AIMessage(content=f"Sorry, an error occurred: Could not initialize LLM for provider {node_provider} in agent_node.")
            return {"messages": [error_message], "token_usage_events": state.get("token_usage_events", [])}

        # Use configured_tools_list from self
        if self.configured_tools_list:
             valid_tools_for_binding = [t for t in self.configured_tools_list if t is not None]
             if valid_tools_for_binding:
                 model = model.bind_tools(valid_tools_for_binding)
                 self.logger.info(f"Agent model bound to {len(valid_tools_for_binding)} tools.")
             else:
                  self.logger.warning("Agent node called but no valid tools were configured after filtering.")
        else:
             self.logger.warning("Agent node called but no tools are configured in self.configured_tools_list.")

        chain = prompt | model
        response: Optional[AIMessage] = None # Ensure response is an AIMessage or None

        try:
            response_raw = await chain.ainvoke({"messages": messages}, config=config)
            if not isinstance(response_raw, AIMessage):
                self.logger.error(f"Agent node received unexpected response type: {type(response_raw)}. Content: {str(response_raw)[:200]}")
                # Attempt to create a fallback AIMessage if possible, or handle error
                content_str = str(response_raw) if not hasattr(response_raw, 'content') else response_raw.content
                response = AIMessage(content=f"Error: Unexpected response structure from LLM. Raw: {content_str[:100]}")
            else:
                response = response_raw

            self.logger.info(f"Agent node response content preview: {response.content[:100] if response and response.content else 'N/A'}")
            if hasattr(response, 'response_metadata'):
                self.logger.info(f"Agent node response_metadata: {response.response_metadata}")
            if hasattr(response, 'usage_metadata'):
                self.logger.info(f"Agent node usage_metadata: {response.usage_metadata}")

            token_event_data = None
            prompt_tokens, completion_tokens, total_tokens = 0, 0, 0
            model_name_from_meta = node_model_id

            if hasattr(response, 'usage_metadata') and response.usage_metadata:
                usage_meta = response.usage_metadata
                prompt_tokens = usage_meta.get('prompt_tokens', 0) if usage_meta.get('prompt_tokens') is not None else usage_meta.get('input_tokens', 0)
                completion_tokens = usage_meta.get('completion_tokens', 0) if usage_meta.get('completion_tokens') is not None else usage_meta.get('output_tokens', 0)
                total_tokens = usage_meta.get('total_tokens', 0)
                self.logger.info(f"Token usage from usage_metadata: P:{prompt_tokens} C:{completion_tokens} T:{total_tokens}")
                if hasattr(response, 'response_metadata') and response.response_metadata and response.response_metadata.get('model_name'):
                    model_name_from_meta = response.response_metadata['model_name']
            elif hasattr(response, 'response_metadata') and response.response_metadata and 'token_usage' in response.response_metadata:
                usage = response.response_metadata['token_usage']
                prompt_tokens = usage.get('prompt_tokens', 0)
                completion_tokens = usage.get('completion_tokens', 0)
                total_tokens = usage.get('total_tokens', 0)
                self.logger.info(f"Token usage from response_metadata['token_usage']: P:{prompt_tokens} C:{completion_tokens} T:{total_tokens}")
                if response.response_metadata.get('model_name'):
                    model_name_from_meta = response.response_metadata['model_name']
            else:
                self.logger.warning("Token usage data not found in usage_metadata or response_metadata for agent_node.")

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
                self.logger.info(f"Token usage for agent_llm: {token_event_data.total_tokens} tokens recorded.")

            # Tool call recovery logic from original agent_node
            if hasattr(response, 'invalid_tool_calls') and response.invalid_tool_calls and \
               (not hasattr(response, 'tool_calls') or not response.tool_calls):
                
                self.logger.warning(f"Response has invalid_tool_calls but no valid tool_calls. Attempting recovery. Invalid: {response.invalid_tool_calls}")
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
                        itc_dict = {
                            "name": itc_obj.name,
                            "args": itc_obj.args,
                            "id": itc_obj.id,
                            "type": getattr(itc_obj, 'type', 'function')
                        }
                    else:
                        self.logger.warning(f"Skipping unrecognized invalid_tool_call object: {itc_obj}")
                        remaining_invalid_calls.append(itc_obj)
                        continue
                    
                    if error_value is None:
                        args_value = itc_dict.get("args")
                        if args_value is None:
                            args_value = {}
                        
                        call_to_add = {
                            "name": itc_dict.get("name"),
                            "args": args_value,
                            "id": itc_dict.get("id"),
                            "type": itc_dict.get("type", "function")
                        }
                        
                        if call_to_add.get("name") and call_to_add.get("id"):
                            recovered_calls.append(call_to_add)
                            self.logger.info(f"Recovered tool call: {call_to_add}")
                        else:
                            self.logger.warning(f"Could not fully recover tool call, missing name or id: {itc_dict}")
                            remaining_invalid_calls.append(itc_obj)
                    else:
                        remaining_invalid_calls.append(itc_obj)
                
                if recovered_calls:
                    if not hasattr(response, 'tool_calls') or response.tool_calls is None:
                        response.tool_calls = []
                    
                    response.tool_calls.extend(recovered_calls)
                    response.invalid_tool_calls = remaining_invalid_calls
                    self.logger.info(f"Successfully recovered/added {len(recovered_calls)} tool_calls. New tool_calls: {response.tool_calls}")

            return {"messages": [response], "token_usage_events": current_token_events}
        except Exception as e:
            self.logger.error(f"Error invoking agent model in _agent_node: {e}", exc_info=True)
            error_message = AIMessage(content=f"Sorry, an error occurred in agent processing: {e}")
            return {"messages": [error_message], "token_usage_events": state.get("token_usage_events", [])}

    async def _grade_documents_node(self, state: AgentState) -> Dict[str, Any]:
        """Grades documents for relevance to the question."""
        self.logger.info(f"---CHECK RELEVANCE (Agent ID: {self.agent_id})---")

        class Grade(BaseModel): # Renamed from 'grade' to 'Grade' to follow CapWords convention
            """Binary score for relevance check."""
            binary_score: str = Field(description="Relevance score 'yes' or 'no'")

        messages = state["messages"]
        current_question = state["question"]
        node_model_id = state["model_id"] 
        node_datastore_tool_names = state["datastore_tool_names"] # This comes from static_state_config initially
        node_provider = state["provider"]
        node_temperature = 0.0 # Grading typically uses temperature 0

        self.logger.info(f"Grading documents for question: '{current_question}'")

        last_message = messages[-1] if messages else None
        if not isinstance(last_message, ToolMessage) or not last_message.name or last_message.name not in node_datastore_tool_names:
            self.logger.warning(
                f"Grade documents called, but last message is not a valid ToolMessage "
                f"from a configured datastore tool. Last message: {type(last_message)}, name: {getattr(last_message, 'name', 'N/A')}. "
                f"Expected one of: {node_datastore_tool_names}"
            )
            return {"documents": [], "question": current_question, "token_usage_events": state.get("token_usage_events", [])}

        # Assuming documents are split by a specific separator in the ToolMessage content
        # The original code used "\\n---RETRIEVER_DOC---\\n"
        docs_content = last_message.content
        if not isinstance(docs_content, str):
            self.logger.warning(f"ToolMessage content is not a string: {type(docs_content)}. Cannot split into documents.")
            docs = []
        else:
            docs = docs_content.split("\\n---RETRIEVER_DOC---\\n")
            
        if not docs or all(not d.strip() for d in docs):
            self.logger.info("No documents retrieved or all documents are empty.")
            return {"documents": [], "question": current_question, "token_usage_events": state.get("token_usage_events", [])}

        prompt = PromptTemplate(
            template="""Вы оцениваете релевантность извлеченного документа для вопроса пользователя. \\n
                    Вот извлеченный документ: \\n\\n {context} \\n\\n
                    Вот вопрос пользователя: {question} \\n
                    Если документ содержит ключевые слова или семантическое значение, связанные с вопросом пользователя, оцените его как релевантный. \\n
                    Дайте двоичную оценку 'yes' или 'no', чтобы указать, соответствует ли документ вопросу.""",
            input_variables=["context", "question"],
        )
        
        model = self._create_llm_instance(
            provider=node_provider,
            model_name=node_model_id, # Consider a specific, cheaper/faster model for grading
            temperature=node_temperature,
            streaming=False, # Grading doesn't need streaming
            log_adapter_override=self.logger
        )

        if not model:
            self.logger.error(f"Could not initialize LLM for grading (provider: {node_provider}). Returning no documents.")
            return {"documents": [], "question": current_question, "token_usage_events": state.get("token_usage_events", [])}
            
        llm_with_tool = model.with_structured_output(Grade, include_raw=True)

        async def process_doc(doc_content: str) -> Tuple[str, str, Optional[TokenUsageData]]:
            chain = prompt | llm_with_tool
            token_event_for_doc = None
            try:
                # Ensure doc_content is not empty or just whitespace
                if not doc_content.strip():
                    self.logger.debug(f"Skipping empty document content for grading.")
                    return doc_content, "no", None

                invocation_result = await chain.ainvoke({"question": current_question, "context": doc_content})
                
                parsed_grade = invocation_result.get("parsed")
                raw_ai_message = invocation_result.get("raw")
                
                binary_score = "no"
                if parsed_grade and isinstance(parsed_grade, Grade): # Check type
                    binary_score = parsed_grade.binary_score
                else:
                    self.logger.warning(f"Grading failed to parse output or got unexpected type for doc: '{doc_content[:100]}...'. Parsed: {parsed_grade}")

                if raw_ai_message and isinstance(raw_ai_message, AIMessage): # Check type
                    self.logger.debug(f"Grading raw AIMessage metadata: {raw_ai_message.response_metadata}")
                    self.logger.debug(f"Grading raw AIMessage usage_metadata: {raw_ai_message.usage_metadata}")

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
                    self.logger.warning(f"Grading raw AIMessage not found or not AIMessage type in invocation_result for doc: '{doc_content[:100]}...'")
                
                return doc_content, binary_score, token_event_for_doc
            except Exception as e:
                self.logger.error(f"Error processing document for grading ('{doc_content[:100]}...'): {e}", exc_info=True)
                return doc_content, "no", None

        filtered_docs_content: List[str] = [] # Explicitly type
        tasks = [process_doc(d) for d in docs if d.strip()] # Process only non-empty docs
        
        current_token_events = state.get("token_usage_events", [])
        if tasks: # Only run gather if there are tasks
            results = await asyncio.gather(*tasks)
            for doc_content, score, token_event_data_item in results:
                if score == "yes":
                    filtered_docs_content.append(doc_content)
                if token_event_data_item:
                    current_token_events.append(token_event_data_item)
        
            if any(evt.call_type == "grading_llm" for evt in current_token_events): # Log only if grading tokens were added
                total_grading_tokens = sum(evt.total_tokens for evt in current_token_events if evt.call_type == "grading_llm")
                self.logger.info(f"Total token usage for grading_llm batch: {total_grading_tokens} tokens.")
        else:
            self.logger.info("No valid documents to grade after filtering.")


        self.logger.info(f"Found {len(filtered_docs_content)} relevant documents out of {len(docs)} initial (after split).")
        return {"documents": filtered_docs_content, "question": current_question, "token_usage_events": current_token_events}

    async def _rewrite_node(self, state: AgentState) -> Dict[str, Any]:
        """Rewrites the question if no relevant documents are found, up to a max limit."""
        self.logger.info(f"---TRANSFORM QUERY (Agent ID: {self.agent_id})---")

        original_question = state["original_question"]
        messages = state["messages"]
        rewrite_count = state.get("rewrite_count", 0)
        # node_max_rewrites should come from the state, which is initialized from static_state_config
        # or from self.max_rewrites if it's guaranteed to be the same as in state.
        # For now, let's assume it's in state as per original logic.
        node_max_rewrites = state["max_rewrites"]
        node_model_id = state["model_id"]
        node_provider = state["provider"]
        node_temperature = 0.1 # As per original

        self.logger.info(f"Rewrite attempt {rewrite_count + 1}/{node_max_rewrites} for question: '{original_question}'")
        current_token_events = state.get("token_usage_events", [])

        if rewrite_count < node_max_rewrites:
            self.logger.info(f"Rewriting original question: {original_question}")
            # Original prompt used `log_adapter.info` which is not available here directly.
            # The prompt itself is fine.
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
        
            model = self._create_llm_instance(
                provider=node_provider,
                model_name=node_model_id,
                temperature=node_temperature,
                streaming=False, # Rewriting doesn't need streaming
                log_adapter_override=self.logger
            )

            if not model:
                self.logger.error(f"Could not initialize LLM for rewriting (provider: {node_provider}). Falling through to no answer.")
                # Fall through to "no answer" case by not returning early
            else:
                try:
                    response = await model.ainvoke([prompt_msg]) # Original was model.ainvoke([prompt_msg])
                    if not isinstance(response, AIMessage):
                        self.logger.error(f"Rewrite node received unexpected response type: {type(response)}")
                        rewritten_question = ""
                    else:
                        rewritten_question = response.content.strip()
                    
                    self.logger.info(f"Rewritten question: {rewritten_question}")

                    token_event_data = None
                    prompt_tokens, completion_tokens, total_tokens = 0, 0, 0
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
                    else:
                        self.logger.warning("Token usage data not found for rewrite_node.")
                    
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
                        self.logger.info(f"Token usage for rewrite_llm: {token_event_data.total_tokens} tokens recorded.")

                    if not rewritten_question or rewritten_question.lower() == original_question.lower():
                        self.logger.warning("Rewriting resulted in empty or identical question. Stopping rewrite.")
                        # Fall through to "no answer" case below
                    else:
                        # The original code created a HumanMessage here, which seems to be intended
                        # to be the *new* input to the agent node after rewrite.
                        trigger_message = HumanMessage(content=f"Переформулируй запрос так: {rewritten_question}")
                        return {
                            "messages": [trigger_message], # This replaces the history, which might be intentional or not.
                                                         # Original graph structure implies agent is called next.
                            "question": rewritten_question, 
                            "rewrite_count": rewrite_count + 1,
                            "token_usage_events": current_token_events
                        }
                except Exception as e:
                    self.logger.error(f"Error during question rewriting: {e}", exc_info=True)
                    # Fall through to "no answer" case below

        # If rewrite limit reached or rewrite failed/was empty
        self.logger.warning(f"Max rewrites ({node_max_rewrites}) reached or rewrite failed for original question: '{original_question}'")
        no_answer_message = AIMessage(content="К сожалению, я не смог найти релевантную информацию по вашему запросу даже после его уточнения. Попробуйте задать вопрос по-другому.")
        return {
            "messages": [no_answer_message], # This becomes the final message.
            "rewrite_count": 0, # Reset count, though this branch likely ends the graph for this turn.
            "token_usage_events": current_token_events
        }

    async def _generate_node(self, state: AgentState) -> Dict[str, Any]:
        """Generates an answer using the question and provided documents."""
        self.logger.info(f"---GENERATE (Agent ID: {self.agent_id})---")

        messages = state["messages"]
        current_question = state["question"]
        documents = state["documents"]
        node_model_id = state["model_id"]
        node_temperature = state["temperature"]
        node_provider = state["provider"]
        current_token_events = state.get("token_usage_events", [])

        if not documents:
            self.logger.warning("Generate node called with no relevant documents.")
            # Check if the last message is the "max rewrites reached" message from rewrite_node
            if messages and isinstance(messages[-1], AIMessage) and \
               "не смог найти релевантную информацию по вашему запросу даже после его уточнения" in messages[-1].content:
                self.logger.info("Passing through 'max rewrites reached' message from rewrite_node.")
                return {"messages": [messages[-1]], "token_usage_events": current_token_events} # Pass existing message
            else:
                no_docs_response = AIMessage(content="К сожалению, я не смог найти информацию по вашему запросу в доступных источниках.")
                return {"messages": [no_docs_response], "token_usage_events": current_token_events}

        self.logger.info(f"Generating answer for question: '{current_question}' using {len(documents)} documents.")
        documents_str = "\\n\\n".join(documents)

        moscow_tz = timezone(timedelta(hours=3))
        current_time_str = datetime.now(moscow_tz).isoformat()
        
        # Original prompt template with current_time included
        prompt_template_str = """Ты помощник для задач с ответами на вопросы. Используйте следующие фрагменты извлеченного контекста, чтобы ответить на вопрос.
            Если у тебя нет ответа на вопрос, просто скажи что у тебя нет данных для ответа на этот вопрос, предложи переформулировать фопрос.
            Старайся отвечать кратко и содержательно.\\n
                Текущее время (по Москве): {current_time}\\n
                Вопрос: {question} \\n
                Контекст: {context} \\n
                Ответ:"""
        
        prompt = PromptTemplate(
            template=prompt_template_str,
            input_variables=["context", "question", "current_time"],
        ).partial(current_time=current_time_str)

        llm = self._create_llm_instance(
            provider=node_provider,
            model_name=node_model_id,
            temperature=node_temperature,
            streaming=True, # Generation can be streaming as per original
            log_adapter_override=self.logger
        )

        if not llm:
            self.logger.error(f"Could not initialize LLM for generation (provider: {node_provider}).")
            error_response = AIMessage(content=f"An error occurred: Could not initialize LLM for provider {node_provider}.")
            return {"messages": [error_response], "token_usage_events": current_token_events}

        rag_chain = prompt | llm
        try:
            response = await rag_chain.ainvoke({"context": documents_str, "question": current_question})

            token_event_data = None
            prompt_tokens, completion_tokens, total_tokens = 0, 0, 0
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
            else:
                self.logger.warning("Token usage data not found for generate_node.")

            if total_tokens > 0 or prompt_tokens > 0 or completion_tokens > 0:
                token_event_data = TokenUsageData(
                    call_type="generation_llm",
                    model_id=model_name_from_meta,
                    prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens,
                    total_tokens=total_tokens,
                    timestamp=datetime.now(timezone.utc).isoformat()
                )
                current_token_events.append(token_event_data)
                self.logger.info(f"Token usage for generation_llm: {token_event_data.total_tokens} tokens recorded.")

            final_response_message = response
            if not isinstance(response, BaseMessage):
                 self.logger.warning(f"Generate node got non-BaseMessage response: {type(response)}. Converting to AIMessage.")
                 final_response_message = AIMessage(content=str(response))
                 
            return {"messages": [final_response_message], "token_usage_events": current_token_events}
        except Exception as e:
            self.logger.error(f"Error during generation: {e}", exc_info=True)
            error_response = AIMessage(content="An error occurred while generating the response.")
            return {"messages": [error_response], "token_usage_events": current_token_events}

    async def _decide_to_generate_edge(self, state: AgentState) -> Literal["generate", "rewrite"]:
        """Decides whether to generate an answer or rewrite the question."""
        self.logger.info("---ASSESS GRADED DOCUMENTS---")

        filtered_documents = state["documents"]
        rewrite_count = state.get("rewrite_count", 0)
        # Ensure max_rewrites is available, e.g., from self.static_state_config or passed in state
        # For now, assuming it's on self, set during __init__ or _configure_tools
        node_max_rewrites = self.max_rewrites # Or state["max_rewrites"] if passed in static_state_config

        if not filtered_documents:
            if rewrite_count < node_max_rewrites:
                self.logger.info("---DECISION: NO RELEVANT DOCUMENTS, REWRITE---")
                return "rewrite"
            else:
                self.logger.warning(f"---DECISION: NO RELEVANT DOCUMENTS AND MAX REWRITES ({node_max_rewrites}) REACHED, GENERATE (PASS THROUGH)---")
                return "generate"
        else:
            self.logger.info("---DECISION: RELEVANT DOCUMENTS FOUND, GENERATE---")
            return "generate"

    def _route_tools_edge(self, state: AgentState) -> Literal["retrieve", "safe_tools", "__end__"]:
        """Routes to the appropriate tool node or ends if no tool is called."""
        self.logger.info("---ROUTE TOOLS---")

        # Tool names should be available on self, set during _configure_tools
        node_datastore_tool_names = self.datastore_tool_names
        node_safe_tool_names = self.safe_tool_names

        next_node = tools_condition(state) # langgraph.prebuilt.tools_condition
        if next_node == END:
            self.logger.info("---DECISION: NO TOOLS CALLED, END---")
            return END

        messages = state["messages"]
        last_message = messages[-1]
        if not hasattr(last_message, "tool_calls") or not last_message.tool_calls:
            self.logger.warning("Routing tools, but last message has no tool calls. Ending.")
            return END

        # Ensure tool_calls is not empty and is a list of dicts
        if not isinstance(last_message.tool_calls, list) or not last_message.tool_calls:
            self.logger.warning(f"Tool calls are present but not in expected format or empty: {last_message.tool_calls}. Ending.")
            return END
            
        first_tool_call = last_message.tool_calls[0]
        
        # Ensure first_tool_call is a dict and has a 'name' key
        if not isinstance(first_tool_call, dict) or "name" not in first_tool_call:
            self.logger.warning(f"First tool call is not a dict or missing 'name': {first_tool_call}. Ending.")
            return END
            
        tool_name = first_tool_call["name"]
        self.logger.info(f"Tool call detected: {tool_name}")

        if tool_name in node_datastore_tool_names:
            self.logger.info(f"---DECISION: ROUTE TO RETRIEVE ({tool_name})---")
            return "retrieve"
        elif tool_name in node_safe_tool_names:
            self.logger.info(f"---DECISION: ROUTE TO SAFE TOOLS ({tool_name})---")
            return "safe_tools"
        else:
            self.logger.warning(f"Tool call '{tool_name}' does not match any configured tool node. Ending.")
            return END

    def create_graph(self) -> Tuple[Any, Dict[str, Any]]:
        """
        Creates the LangGraph application and returns the compiled app and static state config.
        """
        self.logger.info("Creating agent graph in GraphFactory...")

        self._configure_tools()
        self._build_system_prompt()

        # Prepare static_state_config
        # This should largely come from self.agent_config and processed values
        model_settings = self.agent_config.get("config", {}).get("simple", {}).get("settings", {}).get("model", {})
        
        self.static_state_config = {
            "model_id": model_settings.get("modelId", "gpt-4o-mini"),
            "temperature": model_settings.get("temperature", 0.1),
            "system_prompt": self.system_prompt,
            "safe_tool_names": self.safe_tool_names, # from _configure_tools
            "datastore_tool_names": self.datastore_tool_names, # from _configure_tools
            "max_rewrites": self.max_rewrites, # from _configure_tools
            "provider": model_settings.get("provider", "OpenAI"),
            "enableContextMemory": model_settings.get("enableContextMemory", True),
            "contextMemoryDepth": model_settings.get("contextMemoryDepth", 10),
            "token_usage_events": [],
            "original_question": "", # Will be set per invocation
            "question": "", # Will be set per invocation
            "documents": [], # Will be populated during RAG
            "rewrite_count": 0, # Will be managed by rewrite logic
        }
        self.logger.info(f"Initial static_state_config: {self.static_state_config}")


        # Initialize StateGraph
        workflow = StateGraph(AgentState)

        # Add nodes
        workflow.add_node("agent", self._agent_node)
        self.logger.info("Added node: agent")

        if self.datastore_tool_names:
            if self.datastore_tool_list: # Check if tools were actually created
                retrieve_node = ToolNode(self.datastore_tool_list, name="retrieve_node")
                workflow.add_node("retrieve", retrieve_node)
                workflow.add_node("grade_documents", self._grade_documents_node)
                workflow.add_node("rewrite", self._rewrite_node)
                workflow.add_node("generate", self._generate_node)
                self.logger.info("Added RAG nodes: retrieve, grade_documents, rewrite, generate")
            else:
                self.logger.warning("Datastore tool names configured, but no datastore tools were created. Skipping RAG nodes.")
        else:
            self.logger.info("No datastore tools configured. RAG nodes skipped.")

        if self.safe_tool_names:
            if self.safe_tools_list: # Check if tools were actually created
                safe_tools_node = ToolNode(self.safe_tools_list, name="safe_tools_node")
                workflow.add_node("safe_tools", safe_tools_node)
                self.logger.info("Added node: safe_tools")
            else:
                self.logger.warning("Safe tool names configured, but no safe tools were created. Skipping safe_tools node.")
        else:
            self.logger.info("No safe tools configured. Safe_tools node skipped.")

        # Add edges
        workflow.add_edge(START, "agent")

        if "safe_tools" in workflow.nodes:
            workflow.add_edge("safe_tools", "agent")
            self.logger.info("Added edge: safe_tools -> agent")

        if "retrieve" in workflow.nodes: # This implies grade_documents, rewrite, generate are also there if configured correctly
            workflow.add_edge("retrieve", "grade_documents")
            workflow.add_edge("rewrite", "agent") # After rewriting, go back to agent for new tool call with rewritten query
            workflow.add_edge("generate", END)
            self.logger.info("Added RAG edges: retrieve->grade, rewrite->agent, generate->END")

            workflow.add_conditional_edges(
                "grade_documents",
                self._decide_to_generate_edge, # Use the class method
                {"rewrite": "rewrite", "generate": "generate"},
            )
            self.logger.info("Added conditional edge: grade_documents -> decide_to_generate")

        # Conditional routing from agent to tools or END
        possible_routes = {}
        if "safe_tools" in workflow.nodes:
            possible_routes["safe_tools"] = "safe_tools"
        if "retrieve" in workflow.nodes: # This is the entry point for datastore tools
            possible_routes["retrieve"] = "retrieve"
        possible_routes[END] = END # Always have an END route

        if "safe_tools" in workflow.nodes or "retrieve" in workflow.nodes:
            workflow.add_conditional_edges(
                "agent",
                self._route_tools_edge, # Use the class method
                possible_routes,
            )
            self.logger.info(f"Added conditional edge: agent -> route_tools (possible: {list(possible_routes.keys())})")
        else:
            # If no tools at all, agent directly goes to END
            workflow.add_edge("agent", END)
            self.logger.info("No tools configured, agent output directly routed to END.")

        # Compile the graph
        memory_saver_instance = None
        if self.static_state_config.get("enableContextMemory", False): # Check from static_state_config
            memory_saver_instance = MemorySaver()
        
        app = workflow.compile(checkpointer=memory_saver_instance)
        self.logger.info(f"Agent graph compiled successfully in GraphFactory. Checkpointer: {'Enabled' if memory_saver_instance else 'Disabled'}")

        return app, self.static_state_config

def create_agent_app(agent_config: Dict, agent_id: str, logger: logging.LoggerAdapter) -> Tuple[Any, Dict[str, Any]]:
    """
    Creates and configures an agent graph using the GraphFactory.
    """
    logger.info(f"Attempting to create agent graph for agent_id: {agent_id} using GraphFactory.")

    if not isinstance(agent_config, dict) or "config" not in agent_config:
         logger.error("Invalid agent configuration structure: 'config' key missing for agent_id: %s", agent_id)
         raise ValueError("Invalid agent configuration: 'config' key missing.")

    try:
        factory = GraphFactory(agent_config, agent_id, logger)
        app, static_config = factory.create_graph() # create_graph handles its internal configurations
        logger.info(f"GraphFactory successfully created graph for agent_id: {agent_id}.")
        return app, static_config
    except Exception as e:
        logger.error(f"Error creating graph with GraphFactory for agent_id: {agent_id}: {e}", exc_info=True)
        # Re-raise the exception so it's caught by the agent_runner, which will then handle marking status.
        raise
