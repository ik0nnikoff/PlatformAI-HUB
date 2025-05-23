import logging
import requests
import json
from typing import Annotated, Dict, List, Tuple, Set, Optional, Any
from functools import partial

from langchain_core.tools import tool, BaseTool, Tool
from langgraph.prebuilt import InjectedState
from langchain_openai import OpenAIEmbeddings
from qdrant_client import QdrantClient, models
from langchain_qdrant import QdrantVectorStore
from langchain.tools.retriever import create_retriever_tool
from langchain_community.tools.tavily_search import TavilySearchResults

from app.core.config import settings as app_settings

logger = logging.getLogger(__name__)

# --- Predefined Tool Definitions ---

@tool
def auth_tool() -> str:
    """
    Сall authorization function

    Returns:
        str: Trigger to call external authorization function.
    """
    return "необходима авторизация. Допиши в ответе, в квадратных скобках не меняя содержимое: [AUTH_REQUIRED]"


@tool
def get_bonus_points(state: Annotated[dict, InjectedState]) -> str:
    """
    Getting the user's bonus points balance

    Args:
        state (user_data): user data (phone number).

    Returns:
        str: The number of bonus points on the user's balance that can be spent or accumulated.
    """
    # Get logger adapter from state if passed, otherwise use default logger
    # Try getting agent_id from config first for consistency
    agent_id = state.get('config', {}).get('configurable', {}).get('agent_id', 'unknown_agent')
    log_adapter = logging.LoggerAdapter(logger, {'agent_id': agent_id})

    user_data = state.get("user_data", {})
    if user_data.get("is_authenticated"):
        user_phone = user_data.get("phone_number")
        # Ensure phone number format if necessary
        if not user_phone:
            return "Номер телефона пользователя не найден для проверки баллов."
        try:
            # Consider making URL configurable
            url = f"http://airsoft-rus.ru/obmen_rus/bals.php?type=info&tel={user_phone}"
            response = requests.get(url, timeout=10) # Add timeout
            response.raise_for_status()
            # Basic check if response looks like a number
            if response.text.strip().isdigit():
                 return f"Количество бонусных баллов на балансе: {response.text}"
            else:
                 # Handle cases where the API might return error messages as text
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
    log_adapter = logging.LoggerAdapter(logger, {'agent_id': agent_id}) # Use agent_id from config

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
    # Arguments passed from the LLM call (tool input) - currently not used with this config structure
    input_args: Optional[Dict[str, Any]] = None,
    # Arguments bound from the tool configuration using functools.partial
    api_config: Dict[str, Any] = None,
    agent_state: Optional[Dict[str, Any]] = None, # Access to agent state if needed
    log_adapter: Optional[logging.LoggerAdapter] = None
) -> str:
    """
    Makes an HTTP request based on the provided API configuration and input arguments.
    Handles simple key-value parameters and placeholder replacement from agent_state.
    """
    if not api_config:
        return "Error: API tool called without configuration."
    # Use default logger if adapter not provided (should be passed via partial)
    effective_logger = log_adapter if log_adapter else logger

    url = api_config.get("apiUrl") # Use apiUrl from config
    method = api_config.get("method", "GET").upper() # Assume GET if not specified
    headers = api_config.get("headers", {})
    params_config = api_config.get("params", []) # Config for expected params (list of dicts)
    # body_config = api_config.get("body", {}) # Not used with current config structure

    if not url:
        return f"Error: API configuration for tool '{api_config.get('name', 'unnamed')}' is missing 'apiUrl'."

    # Ensure URL starts with http:// or https://
    if not url.startswith("http://") and not url.startswith("https://"):
        url = "http://" + url # Assume http if scheme is missing

    effective_logger.info(f"Executing API tool '{api_config.get('name', 'unnamed')}'")
    effective_logger.debug(f"API Config: {api_config}")
    effective_logger.debug(f"Agent State (partial): { {k: v for k, v in agent_state.items() if k != 'messages'} }") # Log state without messages

    query_params = {}
    request_body = None
    request_files = None # For file uploads, if needed later

    # --- Prepare Headers ---
    # Example: Add authentication from agent state if needed
    # if agent_state and agent_state.get("user_data", {}).get("api_key"):
    #     headers["Authorization"] = f"Bearer {agent_state['user_data']['api_key']}"

    # --- Prepare Query Parameters ---
    user_data = agent_state.get("user_data", {}) if agent_state else {}

    for param_conf in params_config:
        param_key = param_conf.get("key")
        param_value_template = param_conf.get("value")

        if param_key and param_value_template is not None:
            # Replace placeholders like {phone_number}
            try:
                # Simple replacement for known placeholders in user_data
                param_value = param_value_template
                if "{phone_number}" in param_value and "phone_number" in user_data:
                    param_value = param_value.replace("{phone_number}", str(user_data["phone_number"]))
                if "{user_id}" in param_value and "user_id" in user_data:
                    param_value = param_value.replace("{user_id}", str(user_data["user_id"]))
                # Add more placeholder replacements as needed

                # Check if any placeholders remain
                if "{" in param_value and "}" in param_value:
                     effective_logger.warning(f"Unresolved placeholder in API param '{param_key}': {param_value}")
                     # Decide whether to skip the parameter or send it as is
                     # return f"Error: Could not resolve placeholder in parameter '{param_key}' for API tool '{api_config.get('name', 'unnamed')}'."
                     continue # Skip this parameter if placeholder is unresolved

                query_params[param_key] = param_value
            except Exception as e:
                effective_logger.error(f"Error processing parameter '{param_key}' with value '{param_value_template}': {e}")
                return f"Error processing parameter '{param_key}' for API tool '{api_config.get('name', 'unnamed')}'."
        else:
            effective_logger.warning(f"Skipping invalid parameter config: {param_conf}")


    # --- Make Request ---
    try:
        effective_logger.info(f"Making {method} request to {url}")
        effective_logger.debug(f"Headers: {headers}")
        effective_logger.debug(f"Query Params: {query_params}")
        # effective_logger.debug(f"Request Body: {request_body}") # Not used

        response = requests.request(
            method=method,
            url=url,
            headers=headers,
            params=query_params,
            # json=request_body if headers.get("Content-Type") == "application/json" else None, # Not used
            # data=request_body if headers.get("Content-Type") != "application/json" else None, # Not used
            # files=request_files, # Add files if needed
            timeout=15 # Add a reasonable timeout
        )
        response.raise_for_status() # Raise HTTPError for bad responses (4xx or 5xx)

        # Try to return JSON response if possible, otherwise text
        try:
            json_response = response.json()
            # Convert JSON to string for Langchain tool output
            return json.dumps(json_response, ensure_ascii=False, indent=2)
        except json.JSONDecodeError:
            return response.text # Return raw text if not JSON

    except requests.exceptions.Timeout:
        effective_logger.error(f"Timeout error making {method} request to {url}")
        return f"Error: API request timed out for tool '{api_config.get('name', 'unnamed')}'."
    except requests.exceptions.HTTPError as e:
         effective_logger.error(f"HTTP error making {method} request to {url}: {e.response.status_code} {e.response.text}")
         return f"Error: API request failed for tool '{api_config.get('name', 'unnamed')}' with status {e.response.status_code}. Response: {e.response.text}"
    except requests.exceptions.RequestException as e:
        effective_logger.error(f"Request exception making {method} request to {url}: {e}")
        return f"Error: Failed to make API request for tool '{api_config.get('name', 'unnamed')}': {e}."
    except Exception as e:
        effective_logger.error(f"Unexpected error in API tool '{api_config.get('name', 'unnamed')}': {e}", exc_info=True)
        return f"Error: An unexpected error occurred in API tool '{api_config.get('name', 'unnamed')}': {e}."


# --- Tool Configuration Logic ---

def configure_tools(agent_config: Dict, agent_id: str) -> Tuple[List[BaseTool], List[BaseTool], List[BaseTool], Set[str], int]:
    """
    Configures tools based on the agent configuration (simple structure).

    Args:
        agent_config: The agent's configuration dictionary (expects simple structure).
        agent_id: The ID of the agent for logging context.

    Returns:
        A tuple containing:
        - List of all configured tools (instances of BaseTool).
        - List of safe tools (non-retriever).
        - List of datastore tools (retriever tools).
        - Set of datastore tool names (strings).
        - Maximum number of rewrite attempts configured (int).
    """
    log_adapter = logging.LoggerAdapter(logger, {'agent_id': agent_id})

    # --- Access configuration using the provided structure ---
    config_simple = agent_config.get("config", {}).get("simple", {})
    if not config_simple:
        log_adapter.warning("Agent configuration 'config.simple' not found. No tools will be configured.")
        return [], [], [], set(), 3 # Default max_rewrites

    settings = config_simple.get("settings", {})
    if not settings:
        log_adapter.warning("Agent configuration 'config.simple.settings' not found. No tools will be configured.")
        return [], [], [], set(), 3

    tool_settings = settings.get("tools", []) # List of tool configs
    if not tool_settings:
        log_adapter.info("No tools specified in 'config.simple.settings.tools'.")
        # return [], [], [], set(), settings.get("maxRewrites", 3) # Still return maxRewrites if set

    configured_tools_list: List[BaseTool] = []
    safe_tools_list: List[BaseTool] = []
    datastore_tool_list: List[BaseTool] = []
    datastore_tool_names: Set[str] = set()
    max_rewrites = 3 # Default
    
    # Default max_rewrites from model settings or a global default
    model_settings = settings.get("model", {})
    max_rewrites = model_settings.get("maxRewrites", 3) 


    # --- Knowledge Base / Retriever Tools ---
    kb_configs = [t for t in tool_settings if t.get("type") == "knowledgeBase"]
    if kb_configs:
        log_adapter.info(f"Configuring {len(kb_configs)} Knowledge Base tool(s)...")
        qdrant_url = app_settings.QDRANT_URL
        qdrant_collection = app_settings.QDRANT_COLLECTION
        
        if not qdrant_url or not qdrant_collection:
             log_adapter.warning("QDRANT_URL or QDRANT_COLLECTION not set. Knowledge base tools disabled.")
        else:
            try:
                embeddings = OpenAIEmbeddings()
                qdrant_client = QdrantClient(
                    url=qdrant_url,
                    timeout=20.0
                )
                vector_store = QdrantVectorStore(
                    client=qdrant_client,
                    collection_name=qdrant_collection,
                    embedding=embeddings,
                )
                log_adapter.info(f"Connected to Qdrant at {qdrant_url}, collection: {qdrant_collection}")

                for kb_config in kb_configs:
                    kb_settings = kb_config.get("settings", {})
                    kb_ids = kb_settings.get("knowledgeBaseIds", []) # List of datasource IDs
                    search_limit = kb_settings.get("retrievalLimit", 4) # Use retrievalLimit
                    max_rewrites = kb_settings.get("rewriteAttempts", max_rewrites)
                    kb_id = kb_config.get("id", f"kb_retriever_{'_'.join([str(kb_id) for kb_id in kb_ids])}") # Generate ID if missing
                    kb_description = kb_config.get("description", f"Searches and returns information from the {qdrant_collection} knowledge base.")

                    if not kb_ids:
                        log_adapter.warning(f"KnowledgeBase tool '{kb_id}' configured but no knowledgeBaseIds provided. Skipping.")
                        continue
                    
                    must_conditions = []
                    must_conditions.append(
                        models.FieldCondition(
                            key="metadata.datasource_id",
                            match=models.MatchAny(any=[str(kb_id) for kb_id in kb_ids]) # Use MatchAny for list of IDs
                        )
                    )

                    qdrant_filter = models.Filter(must=must_conditions)

                    retriever = vector_store.as_retriever(
                        search_kwargs={"k": search_limit, "filter": qdrant_filter}
                    )
                    
                    retriever_tool = create_retriever_tool(
                        retriever,
                        kb_id,
                        kb_description,
                        document_separator="\n---RETRIEVER_DOC---\n",
                    )
                    datastore_tool_list.append(retriever_tool)
                    datastore_tool_names.add(kb_id)
                    log_adapter.info(f"Configured Knowledge Base tool: {kb_id} for collection {qdrant_collection}")

            except Exception as e:
                log_adapter.error(f"Failed to configure Knowledge Base tools: {e}", exc_info=True)
    else:
        log_adapter.info("No Knowledge Base tools configured.")


    log_adapter.info(f"Using max_rewrites: {max_rewrites}")

    # --- Web Search Tool ---
    web_search_configs = [t for t in tool_settings if t.get("type") == "webSearch"]
    if web_search_configs:
        ws_config = web_search_configs[0] # Assume one web search tool
        ws_settings = ws_config.get("settings", {})
        ws_id = ws_config.get("id", "web_search") # Use ID from config
        ws_name = ws_config.get("name", "Web Search") # Name for LLM
        ws_description = ws_settings.get("description", "Performs a web search for recent information.") # Use description from config if available
        search_limit = ws_settings.get("searchLimit", 3)
        include_domains = ws_settings.get("include_domains", [])
        exclude_domains = ws_settings.get("excludeDomains", [])

        tavily_api_key = app_settings.TAVILY_API_KEY
        if not tavily_api_key:
            log_adapter.warning("TAVILY_API_KEY not set. Web search tool disabled.")
        else:
            try:
                web_search_tool = TavilySearchResults(
                    max_results=int(search_limit),
                    name=ws_id, # Use ID from config
                    description=ws_description, # Use description from config
                    include_domains=include_domains, # Use domainLimit from config
                    exclude_domains=exclude_domains,
                )
                safe_tools_list.append(web_search_tool)
                log_adapter.info(f"Configured Web Search tool '{ws_name}' (ID: {ws_id})")
            except Exception as e:
                log_adapter.error(f"Failed to create Web Search tool: {e}", exc_info=True)


    # --- Dynamic API Request Tools ---
    api_request_configs = [t for t in tool_settings if t.get("type") == "apiRequest"]
    for api_config_entry in api_request_configs:
        tool_id = api_config_entry.get("id")
        # tool_config_name = api_config_entry.get("name") # The name of the config entry itself
        api_settings = api_config_entry.get("settings", {}) # URL, method, params etc. are here
        tool_name = api_settings.get("name") # This is the user-facing name (e.g., "Получить ББ")
        tool_description = api_settings.get("description", f"Calls the {tool_name} API.") # Use settings.description
        api_url = api_settings.get("apiUrl")
        api_params = api_settings.get("params", [])

        if not tool_id or not tool_name or not api_url:
            log_adapter.warning(f"Skipping apiRequest tool due to missing id, settings.name, or settings.apiUrl: {api_config_entry}")
            continue

        # --- Create the partial function ---
        # Pass the necessary parts of api_settings to make_api_request
        bound_api_config = {
            "name": tool_name,
            "description": tool_description,
            "apiUrl": api_url,
            "params": api_params,
            # Add method, headers if they become configurable
        }
        # Bind the state using InjectedState for placeholder replacement
        configured_request_func = partial(
            make_api_request,
            api_config=bound_api_config,
            log_adapter=log_adapter,
            agent_state=InjectedState() # Inject the full state
        )

        # --- Define input schema (optional but recommended) ---
        # Since the current config doesn't define input args for the LLM, we don't need args_schema
        args_schema = None

        # --- Create the Langchain Tool ---
        try:
            # Use the config 'id' as the internal tool name for LangGraph routing
            api_tool = Tool.from_function(
                func=configured_request_func,
                name=tool_id, # Use ID from config entry
                description=tool_description, # Use settings.description for LLM
                args_schema=args_schema,
            )
            safe_tools_list.append(api_tool)
            log_adapter.info(f"Configured dynamic API tool '{tool_name}' (ID: {tool_id})")
        except Exception as e:
            log_adapter.error(f"Failed to create dynamic API tool '{tool_name}' (ID: {tool_id}): {e}", exc_info=True)


    # --- Add Predefined Tools (if not dynamically configured by the same name) ---
    predefined_safe_tools_map = {
        "auth_tool": auth_tool,
        "get_user_info_tool": get_user_info_tool,
        "get_bonus_points": get_bonus_points
    }
    # Get IDs of dynamically configured tools to avoid adding predefined ones with the same name
    configured_dynamic_names = {t.name for t in safe_tools_list} # Includes Tavily and API tools

    for tool_name, tool_instance in predefined_safe_tools_map.items():
        if tool_name not in configured_dynamic_names:
            safe_tools_list.append(tool_instance)
            log_adapter.info(f"Added predefined tool: {tool_name}")
        else:
            log_adapter.info(f"Predefined tool '{tool_name}' was already configured dynamically. Skipping.")


    # Combine lists: safe tools first, then datastore tools
    configured_tools_list.extend(safe_tools_list)
    configured_tools_list.extend(datastore_tool_list) 
    
    log_adapter.info(f"Total tools configured: {len(configured_tools_list)} ({len(safe_tools_list)} safe, {len(datastore_tool_list)} datastore).")
    return configured_tools_list, safe_tools_list, datastore_tool_list, datastore_tool_names, max_rewrites
