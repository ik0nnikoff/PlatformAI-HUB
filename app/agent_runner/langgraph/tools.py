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
from app.agent_runner.common.config_mixin import AgentConfigMixin
from app.agent_runner.common.tools_registry import (
    auth_tool,
    get_user_info_tool, 
    # get_bonus_points,
    make_api_request,
    ToolsRegistry,
    configure_tools_centralized
)

# logger = logging.getLogger(__name__)

# --- NOTE: Predefined tools are now imported from centralized tools_registry ---
# auth_tool, get_user_info_tool, get_bonus_points are imported from tools_registry
# make_api_request is also imported from tools_registry with enhanced error handling


# --- Tool Configuration Logic ---
# NOTE: make_api_request is now imported from centralized tools_registry

def configure_tools(agent_config: Dict, agent_id: str, logger) -> Tuple[List[BaseTool], List[BaseTool], List[BaseTool], Set[str], int]:
    """
    Configures tools based on the agent configuration (simple structure).
    
    This function now uses the centralized tools registry for predefined tools
    and standardized API request handling.

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
    # logger = logging.LoggerAdapter(logger, {'agent_id': agent_id})

    # --- Access configuration using the provided structure ---
    config_simple = agent_config.get("config", {}).get("simple", {})
    if not config_simple:
        logger.warning("Agent configuration 'config.simple' not found. No tools will be configured.")
        return [], [], [], set(), 3 # Default max_rewrites

    settings = config_simple.get("settings", {})
    if not settings:
        logger.warning("Agent configuration 'config.simple.settings' not found. No tools will be configured.")
        return [], [], [], set(), 3

    tool_settings = settings.get("tools", []) # List of tool configs
    if not tool_settings:
        logger.info("No tools specified in 'config.simple.settings.tools'.")
        return [], [], [], set(), 3
    
    tools_list: List[BaseTool] = []
    safe_tools: List[BaseTool] = []
    datastore_tools: List[BaseTool] = []
    datastore_names: Set[str] = set()
    max_rewrites = 3 # Default
    
    # Default max_rewrites from model settings or a global default
    config_mixin = AgentConfigMixin()
    max_rewrites = config_mixin.get_max_rewrites(agent_config) 

    # --- Use centralized tools configuration for predefined tools and API requests ---
    try:
        # Get centralized tools configuration
        centralized_tools, centralized_safe, centralized_api = configure_tools_centralized(
            agent_config, agent_id
        )
        
        # Add centralized tools to our lists
        safe_tools.extend(centralized_safe)
        safe_tools.extend(centralized_api)  # Add API tools to safe tools
        
        logger.info(f"Added {len(centralized_safe + centralized_api)} tools from centralized registry")
        
    except Exception as e:
        logger.error(f"Failed to configure centralized tools: {e}", exc_info=True)
        # Continue with local configuration as fallback 
        
        # Fallback: Add predefined tools manually if centralized configuration fails
        predefined_tools_map = {
            "auth_tool": auth_tool,
            "get_user_info_tool": get_user_info_tool,
            # "get_bonus_points": get_bonus_points
        }
        for tool_name, tool_instance in predefined_tools_map.items():
            safe_tools.append(tool_instance)
            logger.info(f"Added predefined tool (fallback): {tool_name}")


    # --- Knowledge Base / Retriever Tools ---
    kb_configs = [t for t in tool_settings if t.get("type") == "knowledgeBase"]
    if kb_configs:
        logger.info(f"Configuring {len(kb_configs)} Knowledge Base tool(s)...")
        qdrant_url = app_settings.QDRANT_URL
        qdrant_collection = app_settings.QDRANT_COLLECTION
        
        if not qdrant_url or not qdrant_collection:
             logger.warning("QDRANT_URL or QDRANT_COLLECTION not set. Knowledge base tools disabled.")
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
                logger.info(f"Connected to Qdrant at {qdrant_url}, collection: {qdrant_collection}")

                # Get client_id from the top level of agent_config
                client_id = agent_config.get("ownerId")
                if not client_id:
                    logger.warning("Missing 'ownerId' in agent_config, cannot filter KB by client.")
                    # Decide if KB should be disabled or proceed without client filter

                for kb_config in kb_configs:
                    kb_settings = kb_config.get("settings", {})
                    kb_ids = kb_settings.get("knowledgeBaseIds", []) # List of datasource IDs
                    search_limit = kb_settings.get("retrievalLimit", 4) # Use retrievalLimit
                    max_rewrites = kb_settings.get("rewriteAttempts", max_rewrites)
                    tool_id = kb_config.get("id", f"kb_retriever_{'_'.join([str(kb_id) for kb_id in kb_ids])}") # Generate ID if missing
                    tool_name = kb_config.get("name", "Knowledge Base Search") # Name for LLM
                    kb_description = kb_config.get("description", f"Searches and returns information from the {qdrant_collection} knowledge base.")
                    
                    # New configuration fields
                    return_to_agent = kb_settings.get("returnToAgent", True)
                    rewrite_query = kb_settings.get("rewriteQuery", True)
                    tool_enabled = kb_config.get("enabled", True)

                    # Skip disabled tools
                    if not tool_enabled:
                        logger.info(f"Skipping disabled Knowledge Base tool '{tool_name}' (ID: {tool_id})")
                        continue

                    if not kb_ids:
                        logger.warning(f"KnowledgeBase tool '{tool_id}' configured but no knowledgeBaseIds provided. Skipping.")
                        continue
                    
                    must_conditions = []
                    # Add client_id filter if available
                    if client_id:
                         must_conditions.append(
                             models.FieldCondition(key="metadata.client_id", match=models.MatchValue(value=str(client_id)))
                         )
                    # Add datasource_id filter using MatchAny
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
                        tool_id, # Use tool_id instead of kb_id
                        kb_description, # Use kb_description for LLM to understand the tool purpose
                        document_separator="\n---RETRIEVER_DOC---\n",
                    )
                    datastore_tools.append(retriever_tool)
                    datastore_names.add(tool_id) # Use tool_id instead of kb_id
                    logger.info(f"Configured Knowledge Base tool '{tool_name}' (ID: {tool_id}) for datasources: {kb_ids}")

            except Exception as e:
                logger.error(f"Failed to configure Knowledge Base tools: {e}", exc_info=True)
    else:
        logger.info("No Knowledge Base tools configured.")


    logger.info(f"Using max_rewrites: {max_rewrites}")

    # --- Web Search Tool ---
    web_search_configs = [t for t in tool_settings if t.get("type") == "webSearch"]
    if web_search_configs:
        ws_config = web_search_configs[0] # Assume one web search tool
        ws_settings = ws_config.get("settings", {})
        ws_id = ws_config.get("id", "web_search") # Use ID from config
        ws_name = ws_config.get("name", "Web Search") # Name for LLM
        ws_description = ws_settings.get("description", "Performs a web search for recent information.") # Use description from config if available
        ws_enabled = ws_config.get("enabled", True)
        search_limit = ws_settings.get("searchLimit", 3)
        include_domains = ws_settings.get("include_domains", [])
        exclude_domains = ws_settings.get("excludeDomains", [])

        if not ws_enabled:
            logger.info(f"Skipping disabled Web Search tool '{ws_name}' (ID: {ws_id})")
        else:
            tavily_api_key = app_settings.TAVILY_API_KEY
            if not tavily_api_key:
                logger.warning("TAVILY_API_KEY not set. Web search tool disabled.")
            else:
                try:
                    web_search_tool = TavilySearchResults(
                        max_results=int(search_limit),
                        name=ws_id, # Use ID from config
                        description=ws_description, # Use description from config
                        include_domains=include_domains, # Use domainLimit from config
                        exclude_domains=exclude_domains,
                    )
                    safe_tools.append(web_search_tool)
                    logger.info(f"Configured Web Search tool '{ws_name}' (ID: {ws_id})")
                except Exception as e:
                    logger.error(f"Failed to create Web Search tool: {e}", exc_info=True)


    # --- Dynamic API Request Tools ---
    # Note: Basic API tools are now handled by centralized configuration above.
    # This section handles any additional specialized API tool configuration that 
    # might not be covered by the centralized approach.
    api_request_configs = [t for t in tool_settings if t.get("type") == "apiRequest"]
    if api_request_configs:
        logger.info(f"Found {len(api_request_configs)} additional API request configurations")
        # Additional specialized API handling can be added here if needed
        # For now, rely on centralized configuration
    else:
        logger.debug("No additional API request tools to configure")


    # --- Predefined tools are now handled by centralized configuration above ---
    # No need for manual predefined tool configuration as they are included
    # in the centralized_safe_tools from configure_tools_centralized()


    # Combine lists: safe tools first, then datastore tools
    tools_list.extend(safe_tools)
    tools_list.extend(datastore_tools) 

    logger.info(f"Total tools configured: {len(tools_list)} ({len(safe_tools)} safe, {len(datastore_tools)} datastore).")
    return tools_list, safe_tools, datastore_tools, datastore_names, max_rewrites
