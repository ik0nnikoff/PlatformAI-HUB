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
from app.agent_runner.common.config_mixin import AgentConfigMixin

# Global flag for graceful shutdown (if needed by runner_main that uses this factory)
# This might be re-evaluated if it's better managed by the AgentRunner instance
running = True 

class GraphFactory(AgentConfigMixin):
    """
    Фабрика для создания и настройки графа агента с использованием LangGraph.
    Наследует от AgentConfigMixin для централизованного доступа к конфигурации.
    """
    def __init__(self, agent_config: Dict, agent_id: str, logger: logging.LoggerAdapter):
        super().__init__()  # Initialize AgentConfigMixin
        self.agent_config = agent_config
        self.agent_id = agent_id
        self.logger = logger
        
        # Эти атрибуты будут инициализированы в соответствующих методах
        self.llm: Optional[ChatOpenAI] = None
        self.tools: list = []
        self.safe_tools: list = []
        self.datastore_tools: list = []
        self.datastore_names: set = set()
        self.safe_tool_names: set = set()
        self.max_rewrites: int = 3
        self.system_prompt: str = ""

        # Configure the main LLM instance upon initialization
        self._configure_main_llm()

    def _configure_main_llm(self) -> None:
        """
        Configures the main LLM instance (self.llm) for the graph.
        This is typically called once during factory initialization.
        Nodes requiring LLMs with different parameters should use _create_llm_instance.
        """
        # Use centralized configuration method
        model_config = self._get_model_config()

        self.llm = self._create_llm_instance(
            provider=model_config["provider"],
            model_name=model_config["model_id"],
            temperature=model_config["temperature"],
            streaming=model_config["streaming"]
        )
        if self.llm:
            self.logger.info(f"Main LLM configured: {model_config['model_id']} via {model_config['provider']}")
        else:
            self.logger.error(f"Failed to configure main LLM: {model_config['model_id']} via {model_config['provider']}")

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
                safety_settings = [
                    {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_MEDIUM_AND_ABOVE"},
                    {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_MEDIUM_AND_ABOVE"},
                    {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_MEDIUM_AND_ABOVE"},
                    {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_MEDIUM_AND_ABOVE"},
                ]
                extra_body_kwargs["safety_settings"] = safety_settings
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
                self.tools,
                self.safe_tools,
                self.datastore_tools, # Added to capture this specific list
                self.datastore_tool_names,
                self.max_rewrites,
            ) = configure_tools(self.agent_config, self.agent_id)
            
            self.safe_tool_names = {t.name for t in self.safe_tools if t} # Ensure t is not None
            self.logger.info(
                f"Tools configured: {len(self.tools)} total, "
                f"{len(self.safe_tools)} safe, "
                f"{len(self.datastore_tools)} datastore tools. "
                f"Max rewrites: {self.max_rewrites}."
            )
        except Exception as e:
            self.logger.error(f"Failed during tool configuration: {e}", exc_info=True)
            # Reset tool-related attributes to safe defaults
            self.tools = []
            self.safe_tools = []
            self.datastore_tools = []
            self.datastore_names = set()
            self.safe_tool_names = set()
            # self.max_rewrites can retain its default or be set to a safe value like 0
            # raise ValueError(f"Failed during tool configuration: {e}") # Optionally re-raise

    def _build_system_prompt(self) -> None:
        """
        Constructs the system prompt based on agent_config and configured tools.
        The final prompt is stored in self.system_prompt.
        """
        # Use centralized configuration method
        model_config = self._get_model_config()

        prompt_template = model_config["system_prompt"]
        limit_to_kb = model_config["limit_to_kb"]
        answer_in_user_lang = model_config["answer_in_user_lang"]
        use_markdown = model_config["use_markdown"]

        final_prompt = prompt_template

        # self.datastore_tool_names should be populated by _configure_tools before this method is called
        if limit_to_kb and self.datastore_names:
            final_prompt += "\nAnswer ONLY from the provided context from the knowledge base. If the answer is not in the context, say you don't know."
        
        if answer_in_user_lang:
            final_prompt += "\nAnswer in the same language as the user's question."
        
        if use_markdown:
            final_prompt += "\nFormat your responses using Markdown syntax where appropriate. If you include code blocks, specify the language."
        
        self.system_prompt = final_prompt
        self.logger.debug(f"System prompt constructed: {self.system_prompt}")

    def _extract_token_data(self, response: BaseMessage, call_type: str, node_model_id: str) -> Optional[TokenUsageData]:
        """
        Извлекает данные об использовании токенов из ответа модели и возвращает TokenUsageData объект.
        
        Args:
            response: Ответ от модели (BaseMessage)
            call_type: Тип вызова для логирования
            node_model_id: ID модели по умолчанию
            
        Returns:
            TokenUsageData объект или None если токены не найдены
        """
        prompt_tokens, completion_tokens, total_tokens = 0, 0, 0
        model_meta = node_model_id

        if hasattr(response, 'usage_metadata') and response.usage_metadata:
            usage_meta = response.usage_metadata
            prompt_tokens = usage_meta.get('prompt_tokens', 0) if usage_meta.get('prompt_tokens') is not None else usage_meta.get('input_tokens', 0)
            completion_tokens = usage_meta.get('completion_tokens', 0) if usage_meta.get('completion_tokens') is not None else usage_meta.get('output_tokens', 0)
            total_tokens = usage_meta.get('total_tokens', 0)
            self.logger.info(f"Token usage from usage_metadata: P:{prompt_tokens} C:{completion_tokens} T:{total_tokens}")
            if hasattr(response, 'response_metadata') and response.response_metadata and response.response_metadata.get('model_name'):
                model_meta = response.response_metadata['model_name']
        elif hasattr(response, 'response_metadata') and response.response_metadata and 'token_usage' in response.response_metadata:
            usage = response.response_metadata['token_usage']
            prompt_tokens = usage.get('prompt_tokens', 0)
            completion_tokens = usage.get('completion_tokens', 0)
            total_tokens = usage.get('total_tokens', 0)
            self.logger.info(f"Token usage from response_metadata['token_usage']: P:{prompt_tokens} C:{completion_tokens} T:{total_tokens}")
            if response.response_metadata.get('model_name'):
                model_meta = response.response_metadata['model_name']
        else:
            self.logger.warning(f"Token usage data not found in usage_metadata or response_metadata for {call_type}.")
            return None

        if total_tokens > 0 or prompt_tokens > 0 or completion_tokens > 0:
            return TokenUsageData(
                call_type=call_type,
                model_id=model_meta,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_tokens=total_tokens,
                timestamp=datetime.now(timezone.utc).isoformat()
            )
        return None

    def _get_tokens(self, state: AgentState, call_type: str, node_model_id: str, response: BaseMessage ) -> None:
        """
        Извлекает данные об использовании токенов и добавляет их в state.
        """
        token_event_data = self._extract_token_data(response, call_type, node_model_id)
        if token_event_data:
            self.logger.info(f"Token usage for {call_type}: {token_event_data.total_tokens} tokens recorded.")
            state.get("token_usage_events", []).append(token_event_data)

    async def _agent_node(self, state: AgentState, config: dict):
        """Agent node logic, adapted to be a method of GraphFactory."""
        self.logger.info(f"---CALL AGENT NODE (Agent ID: {self.agent_id})---")

        messages = state["messages"]        
        # Используем централизованные методы для получения конфигурации
        llm_config = self._get_node_config("agent")
        node_model_id = llm_config.get("model_name", "gpt-4o-mini")
        node_system_prompt = self.system_prompt # System prompt from factory

        # Используем централизованные методы для создания промпта и модели
        prompt = self._create_prompt_with_time(node_system_prompt)
        model = self._create_node_llm("agent")

        if not model:
            error_message = self._handle_llm_error("agent", llm_config.get("provider", "OpenAI"))
            return {"messages": [error_message]}

        model = self._bind_tools_to_model(model)
        chain = prompt | model

        response: Optional[AIMessage] = None 

        try:
            response_raw = await chain.ainvoke({"messages": messages}, config=config)
            if not isinstance(response_raw, AIMessage):
                self.logger.error(f"Agent node received unexpected response type: {type(response_raw)}. Content: {str(response_raw)[:200]}")
                content_str = str(response_raw) if not hasattr(response_raw, 'content') else response_raw.content
                response = AIMessage(content=f"Error: Unexpected response structure from LLM. Raw: {content_str[:100]}")
            else:
                response = response_raw

            self.logger.info(f"Agent node response content preview: {response.content[:100] if response and response.content else 'N/A'}")
            if hasattr(response, 'response_metadata'):
                self.logger.info(f"Agent node response_metadata: {response.response_metadata}")
            if hasattr(response, 'usage_metadata'):
                self.logger.info(f"Agent node usage_metadata: {response.usage_metadata}")

            # Используем централизованный метод учета токенов
            self._get_tokens(state, "agent_llm", node_model_id, response)

            # Tool call recovery logic from original agent_node
            if hasattr(response, 'invalid_tool_calls') and response.invalid_tool_calls and \
               (not hasattr(response, 'tool_calls') or not response.tool_calls):
                
                self.logger.warning(f"Response has invalid_tool_calls but no valid tool_calls. Attempting recovery. Invalid: {response.invalid_tool_calls}")
                recovered_calls = []
                remaining_invalid = []
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
                        remaining_invalid.append(itc_obj)
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
                            remaining_invalid.append(itc_obj)
                    else:
                        remaining_invalid.append(itc_obj)
                
                if recovered_calls:
                    if not hasattr(response, 'tool_calls') or response.tool_calls is None:
                        response.tool_calls = []
                    
                    response.tool_calls.extend(recovered_calls)
                    response.invalid_tool_calls = remaining_invalid
                    self.logger.info(f"Successfully recovered/added {len(recovered_calls)} tool_calls. New tool_calls: {response.tool_calls}")

            # return {"messages": [response], "token_usage_events": current_token_events}
            return {"messages": [response]}
        except Exception as e:
            self.logger.error(f"Error invoking agent model in _agent_node: {e}", exc_info=True)
            error_message = AIMessage(content=f"Sorry, an error occurred in agent processing: {e}")
            # return {"messages": [error_message], "token_usage_events": state.get("token_usage_events", [])}
            return {"messages": [error_message]}

    async def _grade_docs_node(self, state: AgentState) -> Dict[str, Any]:
        """Grades documents for relevance to the question."""
        self.logger.info(f"---CHECK RELEVANCE (Agent ID: {self.agent_id})---")

        class Grade(BaseModel):
            """Binary score for relevance check."""
            binary_score: str = Field(description="Relevance score 'yes' or 'no'")

        messages = state["messages"]
        current_question = state["question"]
        llm_config = self._get_node_config("grading")
        node_model_id = llm_config.get("model_name", "gpt-4o-mini")  # Получаем model_id из конфига

        self.logger.info(f"Grading documents for question: '{current_question}'")

        last_message = messages[-1] if messages else None
        if not isinstance(last_message, ToolMessage) or not last_message.name or last_message.name not in self.datastore_names:
            self.logger.warning(
                f"Grade documents called, but last message is not a valid ToolMessage "
                f"from a configured datastore tool. Last message: {type(last_message)}, name: {getattr(last_message, 'name', 'N/A')}. "
                f"Expected one of: {self.datastore_tool_names}"
            )
            return {"documents": [], "question": current_question}

        # Парсим документы из ToolMessage
        docs_content = last_message.content
        if not isinstance(docs_content, str):
            self.logger.warning(f"ToolMessage content is not a string: {type(docs_content)}. Cannot split into documents.")
            docs = []
        else:
            docs = docs_content.split("\\n---RETRIEVER_DOC---\\n")
            
        if not docs or all(not d.strip() for d in docs):
            self.logger.info("No documents retrieved or all documents are empty.")
            return {"documents": [], "question": current_question}

        # Создаем модель и промпт используя централизованные методы
        prompt = self._create_grading_prompt()
        model = self._create_node_llm("grading")

        if not model:
            return {"documents": [], "question": current_question}
            
        llm_with_tool = model.with_structured_output(Grade, include_raw=True)

        async def process_doc(doc_content: str) -> Tuple[str, str]:
            chain = prompt | llm_with_tool

            try:
                # Ensure doc_content is not empty or just whitespace
                if not doc_content.strip():
                    self.logger.debug(f"Skipping empty document content for grading.")
                    return doc_content, "no"

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

                    # Используем централизованный метод _extract_token_data через _get_tokens
                    # Создаем временное состояние для сбора токенов
                    temp_state = {"token_usage_events": []}
                    self._get_tokens(temp_state, "grading_llm", node_model_id, raw_ai_message)
                    
                    # Добавляем токены в основное состояние
                    if temp_state["token_usage_events"]:
                        current_token_events.extend(temp_state["token_usage_events"])
                else:
                    self.logger.warning(f"Grading raw AIMessage not found or not AIMessage type in invocation_result for doc: '{doc_content[:100]}...'")
                
                return doc_content, binary_score
            except Exception as e:
                self.logger.error(f"Error processing document for grading ('{doc_content[:100]}...'): {e}", exc_info=True)
                return doc_content, "no"

        filtered_docs: List[str] = [] # Explicitly type
        tasks = [process_doc(d) for d in docs if d.strip()] # Process only non-empty docs
        
        current_token_events = state.get("token_usage_events", [])
        if tasks: # Only run gather if there are tasks
            results = await asyncio.gather(*tasks)
            for doc_content, score in results:
                if score == "yes":
                    filtered_docs.append(doc_content)
        
            # Токены уже добавлены в current_token_events через _get_tokens в process_doc
            if any(evt.call_type == "grading_llm" for evt in current_token_events): # Log only if grading tokens were added
                total_grading_tokens = sum(evt.total_tokens for evt in current_token_events if evt.call_type == "grading_llm")
                self.logger.info(f"Total token usage for grading_llm batch: {total_grading_tokens} tokens.")
        else:
            self.logger.info("No valid documents to grade after filtering.")


        self.logger.info(f"Found {len(filtered_docs)} relevant documents out of {len(docs)} initial (after split).")
        return {"documents": filtered_docs, "question": current_question, "token_usage_events": current_token_events}

    async def _rewrite_node(self, state: AgentState) -> Dict[str, Any]:
        """Rewrites the question if no relevant documents are found, up to a max limit."""
        self.logger.info(f"---TRANSFORM QUERY (Agent ID: {self.agent_id})---")

        original_question = state["original_question"]
        messages = state["messages"]
        rewrite_count = state.get("rewrite_count", 0)
        node_max_rewrites = self.max_rewrites # Max rewrites from factory
        
        # Используем централизованные методы для получения конфигурации
        llm_config = self._get_node_config("rewrite")
        node_model_id = llm_config.get("model_name", "gpt-4o-mini")

        self.logger.info(f"Rewrite attempt {rewrite_count + 1}/{node_max_rewrites} for question: '{original_question}'")

        if rewrite_count < node_max_rewrites:
            self.logger.info(f"Rewriting original question: {original_question}")
            prompt_msg = self._create_rewrite_prompt(original_question, messages)
        
            # Используем централизованный метод создания LLM
            model = self._create_node_llm("rewrite")

            if not model:
                self.logger.error(f"Could not initialize LLM for rewriting. Falling through to no answer.")
                # Fall through to "no answer" case by not returning early
            else:
                try:
                    response = await model.ainvoke([prompt_msg])
                    if not isinstance(response, AIMessage):
                        self.logger.error(f"Rewrite node received unexpected response type: {type(response)}")
                        rewritten_question = ""
                    else:
                        rewritten_question = response.content.strip()
                    
                    self.logger.info(f"Rewritten question: {rewritten_question}")

                    # Используем централизованный метод учета токенов
                    self._get_tokens(state, "rewrite_llm", node_model_id, response)

                    if not rewritten_question or rewritten_question.lower() == original_question.lower():
                        self.logger.warning("Rewriting resulted in empty or identical question. Stopping rewrite.")
                        # Fall through to "no answer" case below
                    else:
                        trigger_message = HumanMessage(content=f"Переформулируй запрос так: {rewritten_question}")
                        return {
                            "messages": [trigger_message],
                            "question": rewritten_question, 
                            "rewrite_count": rewrite_count + 1,
                        }
                except Exception as e:
                    self.logger.error(f"Error during question rewriting: {e}", exc_info=True)
                    # Fall through to "no answer" case below

        # If rewrite limit reached or rewrite failed/was empty
        self.logger.warning(f"Max rewrites ({node_max_rewrites}) reached or rewrite failed for original question: '{original_question}'")
        no_answer_message = AIMessage(content="К сожалению, я не смог найти релевантную информацию по вашему запросу даже после его уточнения. Попробуйте задать вопрос по-другому.")
        return {
            "messages": [no_answer_message],
            "rewrite_count": 0,
        }

    async def _generate_node(self, state: AgentState) -> Dict[str, Any]:
        """Generates an answer using the question and provided documents."""
        self.logger.info(f"---GENERATE (Agent ID: {self.agent_id})---")

        messages = state["messages"]
        current_question = state["question"]
        documents = state["documents"]
        
        # Используем централизованные методы для получения конфигурации
        llm_config = self._get_node_config("generate")
        node_model_id = llm_config.get("model_name", "gpt-4o-mini")

        if not documents:
            self.logger.warning("Generate node called with no relevant documents.")
            # Check if the last message is the "max rewrites reached" message from rewrite_node
            if messages and isinstance(messages[-1], AIMessage) and \
               "не смог найти релевантную информацию по вашему запросу даже после его уточнения" in messages[-1].content:
                self.logger.info("Passing through 'max rewrites reached' message from rewrite_node.")
                return {"messages": [messages[-1]]}
            else:
                no_docs_response = AIMessage(content="К сожалению, я не смог найти информацию по вашему запросу в доступных источниках.")
                return {"messages": [no_docs_response]}

        self.logger.info(f"Generating answer for question: '{current_question}' using {len(documents)} documents.")
        documents_str = "\\n\\n".join(documents)

        # Используем централизованные методы
        prompt = self._create_rag_prompt_template()
        llm = self._create_node_llm("generate")

        if not llm:
            self.logger.error(f"Could not initialize LLM for generation.")
            error_response = AIMessage(content=f"An error occurred: Could not initialize LLM for generation.")
            return {"messages": [error_response]}

        rag_chain = prompt | llm
        try:
            response = await rag_chain.ainvoke({"context": documents_str, "question": current_question})

            # Используем централизованный метод учета токенов
            self._get_tokens(state, "generation_llm", node_model_id, response)

            final_msg = response
            if not isinstance(response, BaseMessage):
                 self.logger.warning(f"Generate node got non-BaseMessage response: {type(response)}. Converting to AIMessage.")
                 final_msg = AIMessage(content=str(response))
                 
            return {"messages": [final_msg]}
        except Exception as e:
            self.logger.error(f"Error during generation: {e}", exc_info=True)
            error_response = AIMessage(content="An error occurred while generating the response.")
            return {"messages": [error_response]}

    async def _decide_to_generate_edge(self, state: AgentState) -> Literal["generate", "rewrite"]:
        """Decides whether to generate an answer or rewrite the question."""
        self.logger.info("---ASSESS GRADED DOCUMENTS---")

        filtered_documents = state["documents"]
        rewrite_count = state.get("rewrite_count", 0)
        # Use max_rewrites directly from factory configuration
        node_max_rewrites = self.max_rewrites # Direct access to factory configuration

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
        node_datastore_names = self.datastore_names
        node_safe_names = self.safe_tool_names

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

        if tool_name in node_datastore_names:
            self.logger.info(f"---DECISION: ROUTE TO RETRIEVE ({tool_name})---")
            return "retrieve"
        elif tool_name in node_safe_names:
            self.logger.info(f"---DECISION: ROUTE TO SAFE TOOLS ({tool_name})---")
            return "safe_tools"
        else:
            self.logger.warning(f"Tool call '{tool_name}' does not match any configured tool node. Ending.")
            return END

    # ===== CENTRALIZED HELPER METHODS FOR NODE OPERATIONS =====

    def _get_node_config(self, node_type: str = "default") -> Dict[str, Any]:
        """
        Получает конфигурацию LLM для узлов с возможностью 
        специфических настроек для разных типов узлов.
        """
        # Use centralized configuration method
        model_config = self._get_model_config()
        
        # Базовые настройки
        base_config = {
            "model_id": model_config["model_id"],
            "provider": model_config["provider"],
            "temperature": model_config["temperature"]
        }
        
        # Специфические настройки для разных типов узлов
        node_specific_overrides = {
            "agent": {
                "streaming": True,
                "temperature": base_config["temperature"]
            },
            "grading": {
                "streaming": False,
                "temperature": 0.0  # Grading должен быть детерминистическим
            },
            "rewrite": {
                "streaming": False,
                "temperature": 0.1
            },
            "generate": {
                "streaming": True,
                "temperature": base_config["temperature"]
            }
        }
        
        # Применяем специфические настройки если они есть
        if node_type in node_specific_overrides:
            base_config.update(node_specific_overrides[node_type])
        else:
            base_config["streaming"] = True  # По умолчанию включаем стриминг
            
        return base_config

    def _create_node_llm(self, node_type: str = "default") -> Optional[ChatOpenAI]:
        """
        Создает экземпляр LLM для узла с соответствующей конфигурацией.
        
        Args:
            node_type: Тип узла ("agent", "grading", "rewrite", "generate")
        
        Returns:
            ChatOpenAI instance или None при ошибке
        """
        config = self._get_node_config(node_type)
        
        model = self._create_llm_instance(
            provider=config["provider"],
            model_name=config["model_id"],
            temperature=config["temperature"],
            streaming=config["streaming"],
            log_adapter_override=self.logger
        )
        
        if model:
            self.logger.info(f"Created {node_type} LLM: {config['model_id']} via {config['provider']}")
        else:
            self.logger.error(f"Failed to create {node_type} LLM: {config['model_id']} via {config['provider']}")
            
        return model

    def _get_moscow_time(self) -> str:
        """Возвращает текущее время в московском часовом поясе в формате ISO."""
        moscow_tz = timezone(timedelta(hours=3))
        return datetime.now(moscow_tz).isoformat()

    def _create_prompt_with_time(self, system_prompt: str) -> ChatPromptTemplate:
        """
        Создает базовый ChatPromptTemplate с временной меткой для агента.
        """
        time_str = self._get_moscow_time()
        
        if "{current_time}" not in system_prompt:
            prompt_with_time = system_prompt + "\nТекущее время (Москва): {current_time}"
        else:
            prompt_with_time = system_prompt
        
        return ChatPromptTemplate.from_messages([
            ("system", prompt_with_time),
            MessagesPlaceholder(variable_name="messages")
        ]).partial(current_time=time_str)

    def _create_rag_template(self) -> PromptTemplate:
        """Создает стандартный PromptTemplate для RAG генерации."""
        time_str = self._get_moscow_time()
        
        template = """Ты помощник для задач с ответами на вопросы. Используйте следующие фрагменты извлеченного контекста, чтобы ответить на вопрос.
            Если у тебя нет ответа на вопрос, просто скажи что у тебя нет данных для ответа на этот вопрос, предложи переформулировать фопрос.
            Старайся отвечать кратко и содержательно.\n
                Текущее время (по Москве): {current_time}\n
                Вопрос: {question} \n
                Контекст: {context} \n
                Ответ:"""
        
        return PromptTemplate(
            template=template,
            input_variables=["context", "question", "current_time"],
        ).partial(current_time=time_str)

    def _create_grading_template(self) -> PromptTemplate:
        """Создает стандартный PromptTemplate для оценки релевантности документов."""
        return PromptTemplate(
            template="""Вы оцениваете релевантность извлеченного документа для вопроса пользователя. \n
                    Вот извлеченный документ: \n\n {context} \n\n
                    Вот вопрос пользователя: {question} \n
                    Если документ содержит ключевые слова или семантическое значение, связанные с вопросом пользователя, оцените его как релевантный. \n
                    Дайте двоичную оценку 'yes' или 'no', чтобы указать, соответствует ли документ вопросу.""",
            input_variables=["context", "question"],
        )

    def _create_rewrite_prompt(self, original_question: str, messages: List[BaseMessage]) -> HumanMessage:
        """Создает сообщение для переформулирования вопроса."""
        return HumanMessage(
            content=f"""You are an expert at rephrasing questions for better retrieval.
Look at the original question and the chat history. The previous retrieval attempt failed to find relevant documents.
Rephrase the original question to be more specific or clearer, considering the context of the conversation.
Do not add conversational filler, just output the rephrased question.

Chat History:
{messages}

Original Question: {original_question}

Rephrased Question:"""
        )

    def _handle_llm_error(self, node_type: str, provider: str, error_message: str = None) -> AIMessage:
        """
        Создает стандартное сообщение об ошибке для узлов при неудачной инициализации LLM.
        """
        if error_message:
            content = f"An error occurred: {error_message}"
        else:
            content = f"Sorry, an error occurred: Could not initialize LLM for provider {provider} in {node_type}_node."
        
        self.logger.error(f"{node_type} node error: {content}")
        return AIMessage(content=content)

    def _bind_tools_to_model(self, model: ChatOpenAI) -> ChatOpenAI:
        """
        Привязывает инструменты к модели с проверкой валидности.
        """
        if self.tools:
            valid_tools_for_binding = [t for t in self.tools if t is not None]
            if valid_tools_for_binding:
                model = model.bind_tools(valid_tools_for_binding)
                self.logger.info(f"Model bound to {len(valid_tools_for_binding)} tools.")
            else:
                self.logger.warning("No valid tools were configured after filtering.")
        else:
            self.logger.warning("No tools are configured in self.tools.")
        
        return model

    # ===== END CENTRALIZED HELPER METHODS =====

    def create_graph(self) -> Any:
        """
        Creates the LangGraph application and returns the compiled app.
        """
        self.logger.info("Creating agent graph in GraphFactory...")

        self._configure_tools()
        self._build_system_prompt()

        # Configure memory settings for later use
        model_config = self._get_model_config()
        self.enable_context_memory = model_config["enable_context_memory"]
        self.logger.info(f"Context memory enabled: {self.enable_context_memory}")


        # Initialize StateGraph
        workflow = StateGraph(AgentState)

        # Add nodes
        workflow.add_node("agent", self._agent_node)
        self.logger.info("Added node: agent")

        if self.datastore_tool_names:
            if self.datastore_tools: # Check if tools were actually created
                retrieve_node = ToolNode(self.datastore_tools, name="retrieve_node")
                workflow.add_node("retrieve", retrieve_node)
                workflow.add_node("grade_documents", self._grade_docs_node)
                workflow.add_node("rewrite", self._rewrite_node)
                workflow.add_node("generate", self._generate_node)
                self.logger.info("Added RAG nodes: retrieve, grade_documents, rewrite, generate")
            else:
                self.logger.warning("Datastore tool names configured, but no datastore tools were created. Skipping RAG nodes.")
        else:
            self.logger.info("No datastore tools configured. RAG nodes skipped.")

        if self.safe_tool_names:
            if self.safe_tools: # Check if tools were actually created
                safe_tools_node = ToolNode(self.safe_tools, name="safe_tools_node")
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
        if self.enable_context_memory: # Use the configured setting
            memory_saver_instance = MemorySaver()
        
        app = workflow.compile(checkpointer=memory_saver_instance)
        self.logger.info(f"Agent graph compiled successfully in GraphFactory. Checkpointer: {'Enabled' if memory_saver_instance else 'Disabled'}")

        return app

def create_agent_app(agent_config: Dict, agent_id: str, logger: logging.LoggerAdapter) -> Any:
    """
    Creates and configures an agent graph using the GraphFactory.
    """
    logger.info(f"Attempting to create agent graph for agent_id: {agent_id} using GraphFactory.")

    if not isinstance(agent_config, dict) or "config" not in agent_config:
         logger.error("Invalid agent configuration structure: 'config' key missing for agent_id: %s", agent_id)
         raise ValueError("Invalid agent configuration: 'config' key missing.")

    try:
        factory = GraphFactory(agent_config, agent_id, logger)
        app = factory.create_graph() # create_graph handles its internal configurations
        logger.info(f"GraphFactory successfully created graph for agent_id: {agent_id}.")
        return app
    except Exception as e:
        logger.error(f"Error creating graph with GraphFactory for agent_id: {agent_id}: {e}", exc_info=True)
        # Re-raise the exception so it's caught by the agent_runner, which will then handle marking status.
        raise
