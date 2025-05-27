"""
Centralized Tools Registry
==========================

This module provides a centralized registry for all predefined tools and API request handlers
to eliminate code duplication across the agent runner codebase.

Key Features:
- Unified tool definitions (auth_tool, get_user_info_tool, get_bonus_points)
- Centralized make_api_request function with enhanced error handling
- Tools registry class for managing tool configurations
- Centralized configuration function for standard tools

This replaces multiple duplicated implementations found in:
- /backup/agent_runner/langgraph_tools.py
- /OLD/hub/agent_runner/tools.py  
- /app/agent_runner/langgraph/tools.py
"""

import logging
import requests
import json
from typing import Annotated, Dict, List, Tuple, Set, Optional, Any, Union
from functools import partial

from langchain_core.tools import tool, BaseTool, Tool
from langgraph.prebuilt import InjectedState

logger = logging.getLogger(__name__)


# =============================================================================
# PREDEFINED TOOL DEFINITIONS (Centralized)
# =============================================================================

@tool
def auth_tool() -> str:
    """
    Call authorization function

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
        str: information about the user.
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

        return f"""Информация о пользователе:
First Name: {user_fname}
Last Name: {user_lname}
User phone number: {user_phone}
User ID: {user_id}
Channel: {channel}
Authorization status: Авторизован"""
    else:
        return f"""Информация о пользователе:
Channel: {channel}
Authorization status: Не авторизован
Для получения полной информации требуется авторизация."""


# =============================================================================
# CENTRALIZED API REQUEST HANDLER
# =============================================================================

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
    
    This is a centralized implementation that replaces multiple duplicated versions
    found across different tool files.
    
    Args:
        input_args: Arguments passed from the LLM call (currently not used)
        api_config: Configuration dictionary containing:
            - apiUrl: The API endpoint URL
            - method: HTTP method (GET, POST, etc.)
            - headers: Request headers
            - params: List of parameter configurations with placeholders
            - name: Tool name for logging
        agent_state: Current agent state for placeholder resolution
        log_adapter: Logger adapter for consistent logging
        
    Returns:
        str: API response as JSON string or plain text, or error message
    """
    if not api_config:
        return "Error: API tool called without configuration."
    
    # Use default logger if adapter not provided (should be passed via partial)
    effective_logger = log_adapter if log_adapter else logger

    url = api_config.get("apiUrl") # Use apiUrl from config
    method = api_config.get("method", "GET").upper() # Assume GET if not specified
    headers = api_config.get("headers", {})
    params_config = api_config.get("params", []) # Config for expected params (list of dicts)
    tool_name = api_config.get('name', 'unnamed')

    if not url:
        return f"Error: API configuration for tool '{tool_name}' is missing 'apiUrl'."

    # Ensure URL starts with http:// or https://
    if not url.startswith("http://") and not url.startswith("https://"):
        url = "http://" + url # Assume http if scheme is missing

    effective_logger.info(f"Executing API tool '{tool_name}'")
    effective_logger.debug(f"API Config: {api_config}")
    if agent_state:
        # Log state without messages to avoid verbose output
        state_summary = {k: v for k, v in agent_state.items() if k != 'messages'}
        effective_logger.debug(f"Agent State (partial): {state_summary}")

    query_params = {}

    # --- Prepare Query Parameters with Placeholder Replacement ---
    user_data = agent_state.get("user_data", {}) if agent_state else {}

    for param_conf in params_config:
        param_key = param_conf.get("key")
        param_value = param_conf.get("value")

        if param_key and param_value is not None:
            # Replace placeholders like {phone_number}
            try:
                param_value = str(param_value)
                
                # Enhanced placeholder replacement with better error handling
                placeholders_map = {
                    "{phone_number}": user_data.get("phone_number", ""),
                    "{user_id}": user_data.get("user_id", ""),
                    "{first_name}": user_data.get("first_name", ""),
                    "{last_name}": user_data.get("last_name", ""),
                }
                
                for placeholder, replacement in placeholders_map.items():
                    if placeholder in param_value:
                        param_value = param_value.replace(placeholder, str(replacement))

                # Check if any placeholders remain unresolved
                if "{" in param_value and "}" in param_value:
                    effective_logger.warning(f"Unresolved placeholder in API param '{param_key}': {param_value}")
                    # Skip this parameter if placeholder is unresolved
                    continue

                query_params[param_key] = param_value
                
            except Exception as e:
                effective_logger.error(f"Error processing parameter '{param_key}' with value '{param_value}': {e}")
                return f"Error processing parameter '{param_key}' for API tool '{tool_name}'."
        else:
            effective_logger.warning(f"Skipping invalid parameter config: {param_conf}")

    # --- Make Request ---
    try:
        effective_logger.info(f"Making {method} request to {url}")
        effective_logger.debug(f"Headers: {headers}")
        effective_logger.debug(f"Query Params: {query_params}")

        response = requests.request(
            method=method,
            url=url,
            headers=headers,
            params=query_params,
            timeout=15  # Reasonable timeout
        )
        response.raise_for_status()  # Raise HTTPError for bad responses (4xx or 5xx)

        # Try to return JSON response if possible, otherwise text
        try:
            json_response = response.json()
            # Convert JSON to string for Langchain tool output
            return json.dumps(json_response, ensure_ascii=False, indent=2)
        except json.JSONDecodeError:
            return response.text  # Return raw text if not JSON

    except requests.exceptions.Timeout:
        effective_logger.error(f"Timeout error making {method} request to {url}")
        return f"Error: API request timed out for tool '{tool_name}'."
    except requests.exceptions.HTTPError as e:
        effective_logger.error(f"HTTP error making {method} request to {url}: {e.response.status_code} {e.response.text}")
        return f"Error: API request failed for tool '{tool_name}' with status {e.response.status_code}. Response: {e.response.text}"
    except requests.exceptions.RequestException as e:
        effective_logger.error(f"Request exception making {method} request to {url}: {e}")
        return f"Error: Failed to make API request for tool '{tool_name}': {e}."
    except Exception as e:
        effective_logger.error(f"Unexpected error in API tool '{tool_name}': {e}", exc_info=True)
        return f"Error: An unexpected error occurred in API tool '{tool_name}': {e}."


# =============================================================================
# TOOLS REGISTRY CLASS
# =============================================================================

class ToolsRegistry:
    """
    Central registry for managing predefined tools and their configurations.
    
    This class provides a standardized way to access and configure tools
    across different parts of the agent runner system.
    """
    
    # Registry of predefined tools
    PREDEFINED_TOOLS = {
        'auth_tool': auth_tool,
        'get_user_info_tool': get_user_info_tool,
        'get_bonus_points': get_bonus_points,
    }
    
    @classmethod
    def get_predefined(cls, tool_name: str) -> Optional[BaseTool]:
        """Get a predefined tool by name."""
        return cls.PREDEFINED_TOOLS.get(tool_name)
    
    @classmethod
    def get_all_predefined(cls) -> List[BaseTool]:
        """Get all predefined tools as a list."""
        return list(cls.PREDEFINED_TOOLS.values())
    
    @classmethod
    def get_predefined_names(cls) -> List[str]:
        """Get names of all predefined tools."""
        return list(cls.PREDEFINED_TOOLS.keys())
    
    @classmethod
    def create_api_tool(cls, api_config: Dict[str, Any], agent_state: Dict[str, Any], 
                       log_adapter: logging.LoggerAdapter) -> BaseTool:
        """
        Create a dynamic API request tool using the centralized make_api_request function.
        
        Args:
            api_config: API configuration dictionary
            agent_state: Current agent state
            log_adapter: Logger adapter for consistent logging
            
        Returns:
            BaseTool: Configured API tool
        """
        tool_id = api_config.get("id", "api_tool")
        tool_name = api_config.get("name", tool_id)
        tool_description = api_config.get("description", f"API tool: {tool_name}")
        
        # Create a partial function with the configuration bound
        api_function = partial(
            make_api_request,
            api_config=api_config,
            agent_state=agent_state,
            log_adapter=log_adapter
        )
        
        # Create the tool
        return Tool(
            name=tool_id,
            description=tool_description,
            func=api_function
        )


# =============================================================================
# CENTRALIZED CONFIGURATION FUNCTION
# =============================================================================

def configure_tools_centralized(
    agent_config: Dict, 
    agent_id: str
) -> Tuple[List[BaseTool], List[BaseTool], List[BaseTool]]:
    """
    Centralized function to configure predefined tools and API request tools.
    
    This function provides a standardized way to set up tools that can be used
    across different tool configuration modules, reducing code duplication.
    
    Args:
        agent_config: The agent's configuration dictionary
        agent_id: The ID of the agent for logging context
        
    Returns:
        Tuple containing:
        - List of all configured centralized tools
        - List of safe tools (non-API tools)
        - List of API tools
    """
    log_adapter = logging.LoggerAdapter(logger, {'agent_id': agent_id})
    
    # Get all predefined tools
    predefined_tools = ToolsRegistry.get_all_predefined()
    safe_tools = predefined_tools.copy()  # Predefined tools are considered safe
    api_tools = []
    
    log_adapter.info(f"Configured {len(predefined_tools)} predefined tools from centralized registry")
    
    # Configure API request tools if present in config
    config_simple = agent_config.get("config", {}).get("simple", {})
    if config_simple:
        settings = config_simple.get("settings", {})
        tool_settings = settings.get("tools", [])
        
        # Create agent state for API tools
        # This is a simplified state - in practice, you might need more context
        agent_state = {
            'config': agent_config,
            'user_data': {},  # Will be populated at runtime
        }
        
        # Configure API request tools
        api_request_configs = [t for t in tool_settings if t.get("type") == "apiRequest"]
        for api_config_entry in api_request_configs:
            try:
                api_settings = api_config_entry.get("settings", {})
                api_tool = ToolsRegistry.create_api_tool(
                    api_config=api_settings,
                    agent_state=agent_state,
                    log_adapter=log_adapter
                )
                api_tools.append(api_tool)
                log_adapter.info(f"Configured API tool: {api_config_entry.get('id', 'unnamed')}")
            except Exception as e:
                log_adapter.error(f"Failed to configure API tool {api_config_entry.get('id', 'unnamed')}: {e}")
    
    all_tools = predefined_tools + api_tools
    
    return all_tools, safe_tools, api_tools


# =============================================================================
# UTILITY FUNCTIONS
# =============================================================================

def validate_tools_registry():
    """Validate that all tools in the registry are properly configured."""
    issues = []
    
    for tool_name, tool_func in ToolsRegistry.PREDEFINED_TOOLS.items():
        if not hasattr(tool_func, 'name'):
            issues.append(f"Tool {tool_name} missing 'name' attribute")
        if not hasattr(tool_func, 'description'):
            issues.append(f"Tool {tool_name} missing 'description' attribute")
    
    if issues:
        logger.warning(f"Tools registry validation issues: {issues}")
    else:
        logger.info("Tools registry validation passed")
    
    return len(issues) == 0


# Initialize validation on import
validate_tools_registry()