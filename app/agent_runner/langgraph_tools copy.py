# filepath: app/agent_runner/langgraph_tools.py
import os
import logging
import requests
import json
from typing import Annotated, Dict, List, Tuple, Set, Optional, Any
from functools import partial

from langchain_core.tools import tool, BaseTool, Tool
from langchain_openai import OpenAIEmbeddings # Assuming OpenAI for embeddings
from qdrant_client import QdrantClient, models as qdrant_models # Alias to avoid conflict if 'models' is used locally
from langchain_qdrant import QdrantVectorStore
from langchain.tools.retriever import create_retriever_tool
from langchain_community.tools.tavily_search import TavilySearchResults

# Assuming AgentState will be in langgraph_models.py in the same directory
# For InjectedState, it's part of langgraph.prebuilt, but we might not need it directly in this file
# if configure_tools passes the state correctly.
from langgraph.prebuilt import InjectedState

# Assuming config will be handled by the runner or a core module
from app.core.config import settings as app_settings # For QDRANT_URL etc.

logger = logging.getLogger(__name__)

# --- Predefined Tool Definitions ---

@tool
def auth_tool() -> str:
    """
    Call authorization function.

    Returns:
        str: Trigger to call external authorization function.
    """
    return "необходима авторизация. Допиши в ответе, в квадратных скобках не меняя содержимое: [AUTH_REQUIRED]"

@tool
def get_bonus_points(state: Annotated[dict, InjectedState]) -> str:
    """
    Getting the user's bonus points balance.

    Args:
        state (user_data): user data (phone number).

    Returns:
        str: The number of bonus points on the user's balance that can be spent or accumulated.
    """
    agent_id = state.get('config', {}).get('configurable', {}).get('agent_id', 'unknown_agent')
    log_adapter = logging.LoggerAdapter(logger, {'agent_id': agent_id})

    user_data = state.get("user_data", {})
    if user_data.get("is_authenticated"):
        user_phone = user_data.get("phone_number")
        if not user_phone:
            return "Номер телефона пользователя не найден для проверки баллов."
        try:
            # TODO: Make this URL configurable, perhaps via agent_config or global settings
            url = f"http://airsoft-rus.ru/obmen_rus/bals.php?type=info&tel={user_phone}"
            response = requests.get(url, timeout=10)
            response.raise_for_status()
            if response.text.strip().isdigit():
                 return f"Количество бонусных баллов на балансе: {response.text}"
            else:
                 log_adapter.warning(f"Bonus points API returned non-numeric text: {response.text}")
                 return f"Не удалось получить баланс бонусных баллов. Ответ API: {response.text}"
        except requests.exceptions.Timeout:
             log_adapter.error("Timeout error fetching bonus points.")
             return "Ошибка: Не удалось связаться с сервисом бонусных баллов (таймаут)."
        except requests.exceptions.RequestException as e:
            log_adapter.error(f"Request exception fetching bonus points: {e}")
            return f"Ошибка: Не удалось получить бонусные баллы ({e})."
    return "Необходима авторизация для просмотра бонусных баллов. Запусти авторизацию."


@tool
def get_user_info_tool(state: Annotated[dict, InjectedState]) -> str:
    """
    Obtaining information about the user, his phone number, first name,
    last name, authorization status and the channel through which the user communicates.

    Args:
        state (chanel): the channel (messenger) through which the user communicates.
        state (user_data): user data (phone number, first name, last name, authorization status).

    Returns:
        dict: information about the user.
    """
    agent_id = state.get('config', {}).get('configurable', {}).get('agent_id', 'unknown_agent')
    log_adapter = logging.LoggerAdapter(logger, {'agent_id': agent_id})

    channel = state.get("channel", "unknown")
    user_data = state.get("user_data", {})
    if user_data.get("is_authenticated"):
        user_fname = user_data.get("first_name", "N/A")
        user_lname = user_data.get("last_name", "N/A")
        user_phone = user_data.get("phone_number", "N/A")
        user_id = user_data.get("user_id", "N/A")

        return f"""Информация о пользователе:\n
                First Name : {user_fname}\n
                Last Name: {user_lname}\n
                User phone number: {user_phone}\n
                User ID: {user_id}\n
                User messenger: {channel}"""
    else:
        return f"User messenger: {channel}. Информация о пользователе недоступна без авторизации. Запусти авторизацию."

# --- Generic API Request Function ---
def make_api_request(
    input_args: Optional[Dict[str, Any]] = None, # Passed from LLM if tool has input schema
    api_config: Dict[str, Any] = None, # Bound from tool configuration
    agent_state: Optional[Dict[str, Any]] = None, # Full agent state injected
    log_adapter: Optional[logging.LoggerAdapter] = None
) -> str:
    """
    Makes an HTTP request based on the provided API configuration.
    Placeholders in URL and params (like {phone_number}) are replaced from agent_state.user_data.
    """
    if not api_config:
        return "Error: API tool called without configuration."
    
    effective_logger = log_adapter if log_adapter else logger

    url_template = api_config.get("apiUrl")
    method = api_config.get("method", "GET").upper()
    headers = api_config.get("headers", {})
    params_config = api_config.get("params", [])

    if not url_template:
        return f"Error: API configuration for tool '{api_config.get('name', 'unnamed')}' is missing 'apiUrl'."

    user_data = agent_state.get("user_data", {}) if agent_state else {}

    # Replace placeholders in URL
    url = url_template
    for key, value in user_data.items():
        url = url.replace(f"{{{key}}}", str(value))
    
    if not url.startswith("http://") and not url.startswith("https://"):
        url = "http://" + url

    effective_logger.info(f"Executing API tool '{api_config.get('name', 'unnamed')}'")
    effective_logger.debug(f"Method: {method}, URL: {url}")
    effective_logger.debug(f"API Config (partial): { {k:v for k,v in api_config.items() if k != 'params'} }")
    # effective_logger.debug(f"Agent State (user_data): {user_data}")

    query_params = {}
    for param_conf in params_config:
        param_key = param_conf.get("key")
        param_value_template = param_conf.get("value")
        if param_key and param_value_template is not None:
            param_value = param_value_template
            for uk, uv in user_data.items(): # Replace placeholders from user_data
                param_value = param_value.replace(f"{{{uk}}}", str(uv))
            
            # If LLM provides input_args, they can override/supplement user_data placeholders
            if input_args:
                 for ik, iv in input_args.items():
                    param_value = param_value.replace(f"{{{ik}}}", str(iv))

            if "{" in param_value and "}" in param_value: # Check for unresolved placeholders
                 effective_logger.warning(f"Unresolved placeholder in API param '{param_key}': {param_value}. Skipping param.")
                 continue
            query_params[param_key] = param_value
        else:
            effective_logger.warning(f"Skipping invalid parameter config in API tool: {param_conf}")

    effective_logger.debug(f"Headers: {headers}, Query Params: {query_params}")

    try:
        response = requests.request(
            method=method,
            url=url,
            headers=headers,
            params=query_params,
            timeout=api_config.get("timeout", 15) # Allow timeout to be configured
        )
        response.raise_for_status()
        try:
            json_response = response.json()
            return json.dumps(json_response, ensure_ascii=False, indent=2)
        except json.JSONDecodeError:
            return response.text
    except requests.exceptions.Timeout:
        effective_logger.error(f"Timeout for {method} {url}")
        return f"Error: API request timed out for '{api_config.get('name')}'."
    except requests.exceptions.HTTPError as e:
         effective_logger.error(f"HTTP error for {method} {url}: {e.response.status_code} {e.response.text}")
         return f"Error: API request for '{api_config.get('name')}' failed with status {e.response.status_code}. Details: {e.response.text[:200]}"
    except requests.exceptions.RequestException as e:
        effective_logger.error(f"Request exception for {method} {url}: {e}")
        return f"Error: Failed API request for '{api_config.get('name')}': {e}."
    except Exception as e:
        effective_logger.error(f"Unexpected error in API tool '{api_config.get('name')}': {e}", exc_info=True)
        return f"Error: Unexpected error in API tool '{api_config.get('name')}': {e}."

# --- Tool Configuration Logic ---
def configure_tools(agent_config_payload: Dict, agent_id: str) -> Tuple[List[BaseTool], List[BaseTool], Set[str], int]:
    """
    Configures tools based on the agent configuration payload.
    Assumes agent_config_payload is the direct configuration dict for the agent.
    """
    log_adapter = logging.LoggerAdapter(logger, {'agent_id': agent_id})

    # The payload IS the agent_config (e.g., from DB or API)
    # Example structure from previous context: agent_config_payload["config"]["simple"]["settings"]["tools"]
    try:
        tool_settings_list = agent_config_payload.get("config", {}).get("simple", {}).get("settings", {}).get("tools", [])
        # Max rewrites could be at a higher level or per KB tool. For now, let's look for it in KB settings.
        # Default max_rewrites, can be overridden by KB tool config.
        max_rewrites_default = agent_config_payload.get("config", {}).get("simple", {}).get("settings", {}).get("rewriteAttempts", 3)
    except AttributeError as e:
        log_adapter.error(f"Error accessing tool configurations in agent_config_payload: {e}. Payload: {json.dumps(agent_config_payload)[:500]}")
        return [], [], set(), 3 # Default if structure is not as expected

    if not tool_settings_list:
        log_adapter.info("No tools found in agent configuration.")

    configured_tools: List[BaseTool] = []
    safe_tools_list: List[BaseTool] = []
    datastore_tool_list: List[BaseTool] = []
    datastore_tool_names: Set[str] = set()
    current_max_rewrites = max_rewrites_default

    # --- Knowledge Base / Retriever Tools ---
    kb_configs = [t for t in tool_settings_list if t.get("type") == "knowledgeBase"]
    if kb_configs:
        log_adapter.info(f"Configuring {len(kb_configs)} Knowledge Base tool(s)...")
        qdrant_url = app_settings.QDRANT_URL
        qdrant_api_key = app_settings.QDRANT_API_KEY.get_secret_value() if app_settings.QDRANT_API_KEY else None
        # Collection name might be dynamic or from settings; for now, assume a primary one
        qdrant_collection = app_settings.QDRANT_COLLECTION 

        if not qdrant_url or not qdrant_collection:
             log_adapter.warning("QDRANT_URL or QDRANT_COLLECTION not set in app_settings. Knowledge base tools disabled.")
        else:
            try:
                # TODO: Embedding model should be configurable
                embeddings = OpenAIEmbeddings(api_key=app_settings.OPENAI_API_KEY.get_secret_value() if app_settings.OPENAI_API_KEY else None)
                qdrant_client = QdrantClient(url=qdrant_url, api_key=qdrant_api_key)
                vectorstore = QdrantVectorStore(
                    client=qdrant_client,
                    collection_name=qdrant_collection,
                    embedding=embeddings,
                )
                log_adapter.info(f"Connected to Qdrant: {qdrant_url}, collection: {qdrant_collection}")

                client_id = agent_config_payload.get("userId") # userId from the root of agent config
                if not client_id:
                    log_adapter.warning("Missing 'userId' in agent_config_payload, cannot filter KB by client.")

                for kb_config in kb_configs:
                    kb_settings = kb_config.get("settings", {})
                    kb_ids = kb_settings.get("knowledgeBaseIds", [])
                    search_limit = kb_settings.get("retrievalLimit", 4)
                    # Update max_rewrites if specified in this KB tool's settings
                    current_max_rewrites = kb_settings.get("rewriteAttempts", current_max_rewrites)
                    
                    tool_id = kb_config.get("id", f"kb_retriever_{'_'.join(map(str, kb_ids)) if kb_ids else 'all'}")
                    tool_name = kb_config.get("name", "Knowledge Base Search")
                    tool_description = kb_settings.get("description", "Searches the knowledge base for relevant documents.")

                    if not kb_ids:
                        log_adapter.warning(f"KB tool '{tool_id}' has no knowledgeBaseIds. It will search all accessible documents for client_id if specified, or all documents otherwise.")

                    must_conditions = []
                    if client_id:
                         must_conditions.append(
                             qdrant_models.FieldCondition(key="metadata.client_id", match=qdrant_models.MatchValue(value=client_id))
                         )
                    if kb_ids: # Only add datasource_id filter if kb_ids are provided
                        must_conditions.append(
                            qdrant_models.FieldCondition(
                                key="metadata.datasource_id",
                                match=qdrant_models.MatchAny(any=[str(kb_id) for kb_id in kb_ids])
                            )
                        )
                    
                    qdrant_filter = qdrant_models.Filter(must=must_conditions) if must_conditions else None
                    retriever = vectorstore.as_retriever(
                        search_kwargs={"k": search_limit, "filter": qdrant_filter}
                    )
                    retriever_tool = create_retriever_tool(
                        retriever,
                        name=tool_id, # tool_id используется как имя инструмента
                        description=tool_description, # tool_description используется как описание инструмента
                        document_separator="\n---RETRIEVER_DOC---\n"
                    )
                    datastore_tool_list.append(retriever_tool)
                    datastore_tool_names.add(tool_id)
                    log_adapter.info(f"Configured KB tool '{tool_name}' (ID: {tool_id}) for datasources: {kb_ids if kb_ids else 'ALL'}, client_id: {client_id}")

            except Exception as e:
                log_adapter.error(f"Failed to initialize Qdrant/Retriever tool: {e}", exc_info=True)

    log_adapter.info(f"Effective max_rewrites for graph: {current_max_rewrites}")

    # --- Web Search Tool ---
    web_search_configs = [t for t in tool_settings_list if t.get("type") == "webSearch"]
    if web_search_configs:
        ws_config = web_search_configs[0] # Assuming one web search tool for now
        ws_settings = ws_config.get("settings", {})
        ws_id = ws_config.get("id", "web_search")
        ws_name = ws_config.get("name", "Web Search")
        ws_description = ws_settings.get("description", "Performs a web search for recent information.")
        search_limit = ws_settings.get("searchLimit", 3)
        include_domains = ws_settings.get("include_domains", [])
        exclude_domains = ws_settings.get("excludeDomains", [])

        tavily_api_key = app_settings.TAVILY_API_KEY.get_secret_value() if app_settings.TAVILY_API_KEY else None
        if not tavily_api_key:
            log_adapter.warning("TAVILY_API_KEY not set. Web search tool disabled.")
        else:
            try:
                web_search_tool = TavilySearchResults(
                    max_results=int(search_limit),
                    name=ws_id, 
                    description=ws_description,
                    include_domains=include_domains,
                    exclude_domains=exclude_domains,
                    api_key=tavily_api_key
                )
                safe_tools_list.append(web_search_tool)
                log_adapter.info(f"Configured Web Search tool '{ws_name}' (ID: {ws_id})")
            except Exception as e:
                log_adapter.error(f"Failed to create Web Search tool: {e}", exc_info=True)

    # --- Dynamic API Request Tools ---
    api_request_configs = [t for t in tool_settings_list if t.get("type") == "apiRequest"]
    for api_config_entry in api_request_configs:
        tool_id = api_config_entry.get("id")
        api_settings = api_config_entry.get("settings", {})
        tool_name_for_llm = api_settings.get("name") # This is the user-facing name (e.g., "Получить ББ")
        tool_description_for_llm = api_settings.get("description", f"Calls the {tool_name_for_llm} API.")
        api_url = api_settings.get("apiUrl")
        
        if not tool_id or not tool_name_for_llm or not api_url:
            log_adapter.warning(f"Skipping apiRequest tool due to missing id, settings.name, or settings.apiUrl: {json.dumps(api_config_entry)[:200]}")
            continue

        # api_settings already contains apiUrl, params, method, headers, etc.
        # We pass the whole api_settings dict to make_api_request via partial.
        configured_request_func = partial(
            make_api_request,
            api_config=api_settings, # Pass the full settings dict for this tool
            log_adapter=log_adapter,
            agent_state=InjectedState() # Inject the full agent state
        )
        
        # Input schema for LLM can be defined in api_settings if needed, e.g., api_settings.get("args_schema")
        # For now, assuming no specific input schema from LLM beyond the general query.
        args_schema_pydantic = None # Placeholder if you want to generate Pydantic models later

        try:
            api_tool = Tool.from_function(
                func=configured_request_func,
                name=tool_id, # Use the unique ID from config for LangGraph routing
                description=tool_description_for_llm, # Use settings.description for LLM
                args_schema=args_schema_pydantic, # Pass Pydantic model if defined
            )
            safe_tools_list.append(api_tool)
            log_adapter.info(f"Configured dynamic API tool '{tool_name_for_llm}' (ID: {tool_id})")
        except Exception as e:
            log_adapter.error(f"Failed to create dynamic API tool '{tool_name_for_llm}' (ID: {tool_id}): {e}", exc_info=True)

    # --- Add Predefined Tools (if not overridden by dynamic config with same ID) ---
    predefined_tools_map = {
        "auth_tool": auth_tool,
        "get_user_info_tool": get_user_info_tool,
        "get_bonus_points": get_bonus_points 
    }
    configured_dynamic_ids = {t.name for t in safe_tools_list} # Names of dynamically configured tools are their IDs

    for predefined_id, tool_instance in predefined_tools_map.items():
        if predefined_id not in configured_dynamic_ids:
            safe_tools_list.append(tool_instance)
            log_adapter.info(f"Added predefined tool: {predefined_id} (Name: {tool_instance.name})")
        else:
            log_adapter.info(f"Predefined tool ID '{predefined_id}' was already configured dynamically. Skipping duplicate.")

    configured_tools.extend(safe_tools_list)
    configured_tools.extend(datastore_tool_list)

    log_adapter.info(f"Total tools: {len(configured_tools)}. Safe: {len(safe_tools_list)}, Datastore: {len(datastore_tool_list)}")
    return configured_tools, safe_tools_list, datastore_tool_names, current_max_rewrites
