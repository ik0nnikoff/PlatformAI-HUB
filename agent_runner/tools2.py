import os
import logging
import requests
from typing import Annotated, Dict, List, Tuple, Set
from pydantic import BaseModel, Field
from langchain_core.tools import tool, BaseTool
from langgraph.prebuilt import ToolNode, InjectedState
from langchain_openai import OpenAIEmbeddings
from qdrant_client import QdrantClient, models
from langchain_qdrant import QdrantVectorStore
from langchain.tools.retriever import create_retriever_tool
from langchain_community.tools.tavily_search import TavilySearchResults

logger = logging.getLogger(__name__)

# --- Tool Definitions ---

@tool
def auth_tool() -> str:
    """
    Call authorization function.

    Returns:
        str: Trigger to call external authorization function.
    """
    # This tool now just returns a marker. The actual auth flow
    # might be handled by the integration (e.g., Telegram bot asking for contact).
    # The agent uses this to indicate auth is needed.
    return "необходима авторизация. Допиши в ответе: [AUTH_REQUIRED]"

@tool
def get_bonus_points(state: Annotated[dict, InjectedState]) -> str:
    """
    Gets the user's bonus points balance if authenticated.

    Args:
        state (dict): The current agent state containing user_data.

    Returns:
        str: The bonus points balance or an authorization request.
    """
    log_adapter = logging.LoggerAdapter(logger, state.get('config', {}).get('metadata', {})) # Get agent_id if passed in config
    user_data = state.get("user_data", {})
    if user_data.get("is_authenticated"):
        user_phone = user_data.get("phone_number")
        if not user_phone:
            return "Номер телефона пользователя не найден для проверки баллов."
        try:
            # Consider making URL configurable via agent config or env var
            url = f"http://airsoft-rus.ru/obmen_rus/bals.php?type=info&tel={user_phone}"
            response = requests.get(url, timeout=10)
            response.raise_for_status()
            if response.text.strip().isdigit():
                return f"Количество бонусных баллов на балансе: {response.text}"
            else:
                log_adapter.warning(f"Bonus points API returned non-numeric text: {response.text}")
                return f"Could not get bonus points balance. API response: {response.text}"
        except requests.exceptions.Timeout:
            log_adapter.error("Timeout error fetching bonus points.")
            return "Error: Could not contact bonus points service (timeout)."
        except requests.exceptions.RequestException as e:
            log_adapter.error(f"Request exception fetching bonus points: {e}")
            return f"Error: Could not get bonus points ({e})."
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
    channel = state.get("channel", "unknown")
    user_data = state.get("user_data", {})
    if user_data.get("is_authenticated"):
        user_fname = user_data.get("first_name", "N/A")
        user_lname = user_data.get("last_name", "N/A")
        user_phone = user_data.get("phone_number", "N/A")
        user_id = user_data.get("user_id", "N/A")
        return (
            f"User Information:\n"
            f"First Name: {user_fname}\n"
            f"Last Name: {user_lname}\n"
            f"Phone Number: {user_phone}\n"
            f"User ID: {user_id}\n"
            f"Channel: {channel}"
        )
    else:
        return f"Channel: {channel}. Информация о пользователе недоступна без авторизации. Запусти авторизацию."

# --- Tool Configuration Logic ---

def configure_tools(agent_config: Dict, agent_id: str) -> Tuple[List[BaseTool], List[BaseTool], Set[str], int]:
    """
    Configures tools based on the agent configuration.

    Args:
        agent_config: The agent's configuration dictionary.
        agent_id: The ID of the agent for logging context.

    Returns:
        A tuple containing:
        - List of all configured tools.
        - List of safe tools (non-retriever).
        - Set of datastore tool names.
        - Maximum number of rewrite attempts configured.
    """
    log_adapter = logging.LoggerAdapter(logger, {'agent_id': agent_id})
    config_simple = agent_config.get("config", {}).get("simple", {})
    settings = config_simple.get("settings", {})
    tool_settings = settings.get("tools", [])

    configured_tools: List[BaseTool] = []
    safe_tools_list: List[BaseTool] = []
    datastore_tool_list: List[BaseTool] = []
    datastore_tool_names: Set[str] = set()
    max_rewrites = 3 # Default

    # Knowledge Base / Retriever Tools
    kb_tool_configs = [t for t in tool_settings if t.get("type") == "knowledgeBase"]
    if kb_tool_configs:
        embeddings = OpenAIEmbeddings() # Assuming OpenAI, could be configurable
        qdrant_url = os.getenv("QDRANT_URL")
        qdrant_collection = os.getenv("QDRANT_COLLECTION")
        if not qdrant_url or not qdrant_collection:
            log_adapter.error("QDRANT_URL or QDRANT_COLLECTION environment variables not set. Skipping KB tools.")
        else:
            try:
                client = QdrantClient(qdrant_url)
                vectorstore = QdrantVectorStore(
                    client, collection_name=qdrant_collection, embedding=embeddings
                )
                log_adapter.info(f"Connected to Qdrant at {qdrant_url}, collection: {qdrant_collection}")

                for kb_tool_config in kb_tool_configs:
                    kb_settings = kb_tool_config.get("settings", {})
                    knowledge_base_ids = kb_settings.get("knowledgeBaseIds", [])
                    retrieval_limit = kb_settings.get("retrievalLimit", 4)
                    # Use the rewriteAttempts from the *last* KB tool config found? Or first? Needs clarification.
                    max_rewrites = kb_settings.get("rewriteAttempts", max_rewrites)

                    if knowledge_base_ids:
                        must_conditions = [
                            models.FieldCondition(
                                key="metadata.datasource_id",
                                match=models.MatchAny(any=knowledge_base_ids)
                            )
                        ]
                        # TODO: Add client_id filter if needed based on agent_config or user_data

                        retriever = vectorstore.as_retriever(
                            search_kwargs={"k": retrieval_limit, "filter": models.Filter(must=must_conditions)}
                        )
                        tool_id = kb_tool_config.get("id", f"retrieve_kb_{'_'.join(knowledge_base_ids)}")
                        tool_name = kb_tool_config.get("name", f"Search KB ({', '.join(knowledge_base_ids)})")
                        retriever_tool = create_retriever_tool(
                            retriever, tool_id, tool_name, document_separator="\n---RETRIEVER_DOC---\n"
                        )
                        datastore_tool_list.append(retriever_tool)
                        datastore_tool_names.add(retriever_tool.name)
                        log_adapter.info(f"Configured retriever tool '{tool_name}' (ID: {tool_id}) for datasources: {knowledge_base_ids}")
                    else:
                        log_adapter.warning(f"KnowledgeBase tool '{kb_tool_config.get('id')}' configured but no knowledgeBaseIds provided.")
            except Exception as e:
                log_adapter.error(f"Failed to initialize Qdrant client or vector store: {e}", exc_info=True)

    if datastore_tool_list:
        configured_tools.extend(datastore_tool_list)

    # Web Search Tool
    web_search_config = next((t for t in tool_settings if t.get("type") == "webSearch"), None)
    if web_search_config:
        if not os.getenv("TAVILY_API_KEY"):
            log_adapter.warning("WebSearch tool configured but TAVILY_API_KEY not found. Skipping tool.")
        else:
            ws_settings = web_search_config.get("settings", {})
            search_limit = ws_settings.get("searchLimit", 3)
            tavily_tool = TavilySearchResults(max_results=int(search_limit))
            tavily_tool.name = web_search_config.get("id", "web_search")
            tavily_tool.description = web_search_config.get("name", "Search the web")
            safe_tools_list.append(tavily_tool)
            log_adapter.info(f"Configured Tavily web search tool '{tavily_tool.name}'")

    # API Request Tools
    api_request_configs = [t for t in tool_settings if t.get("type") == "apiRequest"]
    for api_config in api_request_configs:
        api_settings = api_config.get("settings", {})
        api_name = api_settings.get("name") # Use the user-facing name for matching
        # TODO: Implement dynamic API tool generation based on config (e.g., using OpenAPI spec)
        # Manual mapping for now:
        if api_name == "Получить ББ": # Match based on config name
            if get_bonus_points not in safe_tools_list:
                # Optionally override tool name/description from config?
                # get_bonus_points.name = api_config.get("id", get_bonus_points.name)
                # get_bonus_points.description = api_settings.get("description", get_bonus_points.description)
                safe_tools_list.append(get_bonus_points)
                log_adapter.info(f"Mapped API tool '{api_name}' to 'get_bonus_points'")
        else:
            log_adapter.warning(f"apiRequest tool '{api_name}' found in config, but no matching implementation found. Skipping.")

    # Add other predefined safe tools if not already added by API config mapping
    if auth_tool not in safe_tools_list:
        safe_tools_list.append(auth_tool)
        log_adapter.info(f"Added predefined tool: {auth_tool.name}")
    if get_user_info_tool not in safe_tools_list:
        safe_tools_list.append(get_user_info_tool)
        log_adapter.info(f"Added predefined tool: {get_user_info_tool.name}")

    if safe_tools_list:
        configured_tools.extend(safe_tools_list)

    log_adapter.info(f"Total tools configured: {len(configured_tools)}")
    return configured_tools, safe_tools_list, datastore_tool_names, max_rewrites
