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
import cloudscraper
import json
from typing import Annotated, Dict, List, Tuple, Set, Optional, Any, Union
from functools import partial

from langchain_core.tools import tool, BaseTool, Tool
from langgraph.prebuilt import InjectedState

# Import voice_v2 tools
try:
    # NOTE: voice_intent_analysis_tool and voice_response_decision_tool removed
    # as anti-patterns (Phase 4.8.1) - LangGraph should use native decision making

    # Phase 4.8.2: LangGraph native TTS tool
    from app.services.voice_v2.tools import generate_voice_response

    # Legacy capabilities tool (still needed)
    from app.services.voice_v2.integration.voice_capabilities_tool import voice_capabilities_tool

    VOICE_V2_AVAILABLE = True
except ImportError:
    VOICE_V2_AVAILABLE = False
    generate_voice_response = None
    voice_capabilities_tool = None

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


# @tool
# def get_bonus_points(state: Annotated[dict, InjectedState]) -> str:
#     """
#     Getting the user's bonus points balance

#     Args:
#         state (user_data): user data (phone number).

#     Returns:
#         str: The number of bonus points on the user's balance that can be spent or accumulated.
#     """
#     # Get logger adapter from state if passed, otherwise use default logger
#     # Try getting agent_id from config first for consistency
#     agent_id = state.get('config', {}).get('configurable', {}).get('agent_id', 'unknown_agent')
#     log_adapter = logging.LoggerAdapter(logger, {'agent_id': agent_id})

#     user_data = state.get("user_data", {})
#     if user_data.get("is_authenticated"):
#         user_phone = user_data.get("phone_number")
#         # Ensure phone number format if necessary
#         if not user_phone:
#             return "Номер телефона пользователя не найден для проверки баллов."
#         try:
#             # Consider making URL configurable
#             url = f"http://airsoft-rus.ru/obmen_rus/bals.php?type=info&tel={user_phone}"
#             response = requests.get(url, timeout=10) # Add timeout
#             response.raise_for_status()
#             # Basic check if response looks like a number
#             if response.text.strip().isdigit():
#                  return f"Количество бонусных баллов на балансе: {response.text}"
#             else:
#                  # Handle cases where the API might return error messages as text
#                  log_adapter.warning(f"Bonus points API returned non-numeric text: {response.text}")
#                  return f"Не удалось получить баланс бонусных баллов. Ответ API: {response.text}"

#         except requests.exceptions.Timeout:
#              log_adapter.error("Timeout error fetching bonus points.")
#              return "Ошибка: Не удалось связаться с сервисом бонусных баллов (таймаут)."
#         except requests.exceptions.RequestException as e:
#             log_adapter.error(f"Request exception fetching bonus points: {e}")
#             return f"Ошибка: Не удалось получить бонусные баллы ({e})."
#     return "Необходима авторизация для просмотра бонусных баллов. Запусти авторизацию."


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
        user_lname = user_data.get("last_name")  # Получаем значение без fallback
        user_phone = user_data.get("phone_number", "N/A")
        user_id = user_data.get("user_id", "N/A")

        # Формируем информацию о фамилии
        last_name_info = user_lname if user_lname else "Не указана"

        return f"""Информация о пользователе:
First Name: {user_fname}
Last Name: {last_name_info}
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
    # Arguments bound from the tool configuration using functools.partial
    api_config: Optional[Dict[str, Any]] = None,
    log_adapter: Optional[logging.LoggerAdapter] = None,
    # LangGraph state injection - provides access to current runtime state
    state: Annotated[Optional[dict], InjectedState] = None
) -> str:
    """
    Makes an HTTP request based on the provided API configuration and input arguments.
    Handles simple key-value parameters and placeholder replacement from LangGraph state.

    This is a centralized implementation that replaces multiple duplicated versions
    found across different tool files.

    Args:
        api_config: Configuration dictionary containing:
            - apiUrl: The API endpoint URL
            - method: HTTP method (GET, POST, etc.)
            - headers: Request headers
            - params: List of parameter configurations with placeholders
            - name: Tool name for logging
        log_adapter: Logger adapter for consistent logging
        state: LangGraph injected state for placeholder resolution

    Returns:
        str: API response as JSON string or plain text, or error message
    """
    if not api_config:
        return "Error: API tool called without configuration."

    # Use default logger if adapter not provided (should be passed via partial)
    effective_logger = log_adapter if log_adapter else logger

    url = api_config.get("apiUrl") # Use apiUrl from config
    method = api_config.get("method", "GET").upper() # Assume GET if not specified
    headers_raw = api_config.get("headers", {})
    params_config = api_config.get("params", []) # Config for expected params (list of dicts)
    tool_name = api_config.get('name', 'unnamed')

    if not url:
        return f"Error: API configuration for tool '{tool_name}' is missing 'apiUrl'."

    # Ensure URL starts with http:// or https://
    if not url.startswith("http://") and not url.startswith("https://"):
        url = "http://" + url # Assume http if scheme is missing

    # Normalize headers: convert list format to dict format if needed
    headers = {}
    effective_logger.debug(f"Raw headers type: {type(headers_raw)}, value: {headers_raw}")

    if isinstance(headers_raw, dict):
        headers = headers_raw.copy()
        effective_logger.debug(f"Using dict headers: {headers}")
    elif isinstance(headers_raw, list):
        # Convert list format [{"key": "Authorization", "value": "Bearer token"}] to dict
        effective_logger.debug(f"Converting list headers to dict format...")
        for header_item in headers_raw:
            if isinstance(header_item, dict) and "key" in header_item and "value" in header_item:
                headers[header_item["key"]] = header_item["value"]
                effective_logger.debug(f"Added header: {header_item['key']} = {header_item['value']}")
            else:
                effective_logger.warning(f"Invalid header format in API tool '{tool_name}': {header_item}")
        effective_logger.debug(f"Final converted headers: {headers}")
    else:
        effective_logger.warning(f"Unexpected headers format in API tool '{tool_name}': {type(headers_raw)}")
        # Ensure headers is always a dict for requests library
        headers = {}

    # Final safety check - ensure headers is always a dict
    if not isinstance(headers, dict):
        effective_logger.error(f"CRITICAL: Headers is not a dict after normalization! Type: {type(headers)}, Value: {headers}")
        headers = {}  # Force to empty dict for safety

    effective_logger.info(f"Executing API tool '{tool_name}'")
    # effective_logger.info(f"Function parameters: api_config={api_config is not None}, log_adapter={log_adapter is not None}, state={state is not None}")

    # Early debug logging
    try:
        # effective_logger.info(f"State type: {type(state)}")
        if state:
            # effective_logger.info(f"State keys: {list(state.keys())}")
            user_data = state.get("user_data", {})
            # effective_logger.info(f"User data extracted: {user_data}")
        else:
            # effective_logger.info("State is None or empty")
            user_data = {}
    except Exception as e:
        effective_logger.error(f"Error accessing state: {e}", exc_info=True)
        user_data = {}

    effective_logger.debug(f"API Config: {api_config}")
    if state:
        # Log state without messages to avoid verbose output
        state_summary = {k: v for k, v in state.items() if k != 'messages'}
        effective_logger.debug(f"Agent State (partial): {state_summary}")

    query_params = {}

    # --- Prepare Query Parameters with Placeholder Replacement ---
    # user_data already extracted above

    # Debug logging
    effective_logger.debug(f"Final user_data for API tool '{tool_name}': {user_data}")

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

    # --- Make Request with Cloudflare Bypass ---
    try:
        effective_logger.info(f"Making {method} request to {url}")
        effective_logger.debug(f"Headers type: {type(headers)}, value: {headers}")
        effective_logger.debug(f"Query Params: {query_params}")

        # Final safety check before making request
        if not isinstance(headers, dict):
            effective_logger.error(f"CRITICAL ERROR: Headers is not a dict before request! Type: {type(headers)}")
            headers = {}  # Force to empty dict

        # Try cloudscraper first (for Cloudflare bypass)
        try:
            effective_logger.info(f"Attempting Cloudflare bypass with cloudscraper for {url}")
            scraper = cloudscraper.create_scraper(
                browser={
                    'browser': 'chrome',
                    'platform': 'windows',
                    'desktop': True
                }
            )

            # Update headers to include realistic browser headers if not already present
            if not headers.get('User-Agent'):
                headers['User-Agent'] = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'

            response = scraper.request(
                method=method,
                url=url,
                headers=headers,
                params=query_params,
                timeout=15
            )
            effective_logger.debug(f"Cloudscraper request successful with status {response.status_code}")

        except Exception as cloudscraper_error:
            effective_logger.warning(f"Cloudscraper failed for {url}: {cloudscraper_error}")
            effective_logger.info(f"Falling back to standard requests library")

            # Fallback to standard requests if cloudscraper fails
            response = requests.request(
                method=method,
                url=url,
                headers=headers,
                params=query_params,
                timeout=15
            )

        response.raise_for_status()  # Raise HTTPError for bad responses (4xx or 5xx)

        # Log response details for debugging
        effective_logger.debug(f"API Response Status: {response.status_code}")
        effective_logger.debug(f"API Response Headers: {dict(response.headers)}")
        effective_logger.debug(f"API Response Content-Type: {response.headers.get('content-type', 'Not specified')}")
        effective_logger.debug(f"API Response Content Length: {len(response.content)} bytes")
        effective_logger.debug(f"API Response Encoding: {response.encoding}")

        # Get properly decoded text content (handles compression automatically)
        try:
            # Use response.text which automatically handles decompression and encoding
            decoded_content = response.text
            # effective_logger.info(f"Decoded content: {decoded_content[:100]}... (truncated for preview)")
            effective_logger.debug(f"Successfully got decoded text content, encoding: {response.encoding}")
        except Exception as e:
            effective_logger.warning(f"Failed to get response.text: {e}")
            # Fallback: manually decode content if response.text fails
            try:
                decoded_content = response.content.decode(response.encoding or 'utf-8')
                effective_logger.debug(f"Successfully manually decoded content")
            except Exception as e2:
                effective_logger.error(f"Manual decoding also failed: {e2}")
                decoded_content = str(response.content)

        # Log first 1000 characters of decoded content for debugging
        response_preview = decoded_content[:50] if len(decoded_content) > 50 else decoded_content
        effective_logger.info(f"API Response Content Preview (first 50 chars): {response_preview}")

        # Try to return JSON response if possible, otherwise text
        try:
            json_response = json.loads(decoded_content)
            # effective_logger.debug(f"Successfully parsed JSON response with {len(json_response)} top-level items")
            # Convert JSON to string for Langchain tool output
            return json.dumps(json_response, ensure_ascii=False, indent=2)
        except json.JSONDecodeError as json_error:
            effective_logger.warning(f"Failed to parse JSON response: {json_error}")
            effective_logger.debug("Returning raw text response")
            return decoded_content  # Return decoded text if not JSON

    except requests.exceptions.Timeout:
        effective_logger.error(f"Timeout error making {method} request to {url}")
        return f"Error: API request timed out for tool '{tool_name}'."
    except requests.exceptions.HTTPError as e:
        effective_logger.error(f"HTTP error making {method} request to {url}: {e.response.status_code} {e.response.text[:500]}")
        return f"Error: API request failed for tool '{tool_name}' with status {e.response.status_code}. Response: {e.response.text[:200]}"
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
        'voice_capabilities_tool': voice_capabilities_tool,
        # Vision tools will be imported lazily to avoid circular imports
        # 'analyze_images': analyze_images,
        # 'describe_image_content': describe_image_content,
        # 'get_bonus_points': get_bonus_points,
    }

    # Voice v2 tools (conditionally added if available)
    VOICE_V2_TOOLS = {}

    @classmethod
    def get_predefined(cls, tool_name: str) -> Optional[BaseTool]:
        """Get a predefined tool by name."""
        return cls.PREDEFINED_TOOLS.get(tool_name)

    @classmethod
    def get_all_predefined(cls) -> List[BaseTool]:
        """Get all predefined tools as a list, including voice_v2 tools if available."""
        all_tools = list(cls.PREDEFINED_TOOLS.values())

        # Add voice_v2 tools if available
        if VOICE_V2_AVAILABLE:
            if not hasattr(cls, '_voice_v2_initialized'):
                cls._init_voice_v2_tools()
                cls._voice_v2_initialized = True
            all_tools.extend(list(cls.VOICE_V2_TOOLS.values()))

        return all_tools

    @classmethod
    def _init_voice_v2_tools(cls):
        """Initialize voice_v2 tools registry."""
        if VOICE_V2_AVAILABLE:
            cls.VOICE_V2_TOOLS = {
                # Phase 4.8.2: LangGraph native TTS tool (replaces anti-patterns)
                'generate_voice_response': generate_voice_response,
                # Legacy capabilities tool (still needed for voice provider info)
                'voice_capabilities_tool': voice_capabilities_tool,
            }

    @classmethod
    def get_voice_v2_tools(cls) -> List[BaseTool]:
        """Get voice_v2 tools if available."""
        if not VOICE_V2_AVAILABLE:
            return []

        if not hasattr(cls, '_voice_v2_initialized'):
            cls._init_voice_v2_tools()
            cls._voice_v2_initialized = True

        return list(cls.VOICE_V2_TOOLS.values())

    @classmethod
    def get_predefined_names(cls) -> List[str]:
        """Get names of all predefined tools."""
        return list(cls.PREDEFINED_TOOLS.keys())

    @classmethod
    def get_vision_tools(cls) -> List[BaseTool]:
        """
        Get vision analysis tools with lazy import to avoid circular dependencies.

        Returns:
            List[BaseTool]: List of vision analysis tools
        """
        try:
            # Import vision tools lazily to avoid circular imports
            from app.agent_runner.langgraph.tools.tools import analyze_images, describe_image_content
            tools = [analyze_images, describe_image_content]
            logger.info(f"Successfully loaded {len(tools)} vision tools: {[t.name for t in tools]}")
            return tools
        except ImportError as e:
            logger.warning(f"Failed to import vision tools: {e}")
            return []
        except Exception as e:
            logger.error(f"Unexpected error loading vision tools: {e}", exc_info=True)
            return []

    @classmethod
    def create_api_tool(cls, api_config: Dict[str, Any],
                       log_adapter: logging.LoggerAdapter) -> BaseTool:
        """
        Create a dynamic API request tool using the centralized make_api_request function.

        Args:
            api_config: API configuration dictionary
            log_adapter: Logger adapter for consistent logging

        Returns:
            BaseTool: Configured API tool
        """
        tool_id = api_config.get("id", "api_tool")
        tool_name = api_config.get("name", tool_id)
        tool_description = api_config.get("description", f"API tool: {tool_name}")

        # Create API tool using @tool decorator pattern for proper InjectedState handling
        @tool
        def api_tool_func(state: Annotated[dict, InjectedState]) -> str:
            """
            Dynamic API request tool created from configuration.
            Makes HTTP requests with placeholder replacement from agent state.
            """
            return make_api_request(
                api_config=api_config,
                log_adapter=log_adapter,
                state=state
            )

        # Update the tool's metadata to match configuration
        api_tool_func.name = tool_id
        api_tool_func.description = tool_description

        return api_tool_func


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

        # Configure API request tools
        api_request_configs = [t for t in tool_settings if t.get("type") == "apiRequest"]
        for api_config_entry in api_request_configs:
            try:
                api_settings = api_config_entry.get("settings", {})

                # Merge tool-level configuration with settings for complete API config
                complete_api_config = {
                    "id": api_config_entry.get("id", "api_tool"),
                    "name": api_settings.get("name", api_config_entry.get("id", "API Tool")),
                    "description": api_settings.get("description", f"API tool: {api_settings.get('name', 'unnamed')}"),
                    "enabled": api_settings.get("enabled", True),
                    # Extract API configuration from settings
                    "apiUrl": api_settings.get("apiUrl", ""),
                    "method": api_settings.get("method", "GET"),
                    "headers": api_settings.get("headers", {}),
                    "params": api_settings.get("params", [])
                }

                # Skip disabled tools
                if not complete_api_config.get("enabled", True):
                    log_adapter.info(f"Skipping disabled API tool: {complete_api_config['id']}")
                    continue

                api_tool = ToolsRegistry.create_api_tool(
                    api_config=complete_api_config,
                    log_adapter=log_adapter
                )
                api_tools.append(api_tool)
                log_adapter.info(f"Configured API tool: {complete_api_config['id']} ({complete_api_config['name']}) (method: {complete_api_config['method']}) wirh description: {complete_api_config['description']})")
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