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
    voice_capabilities_tool,
    ToolsRegistry,
    configure_tools_centralized
)


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
            "voice_capabilities_tool": voice_capabilities_tool,
        }
        for tool_name, tool_instance in predefined_tools_map.items():
            safe_tools.append(tool_instance)
            logger.info(f"Added predefined tool (fallback): {tool_name}")

    # Add vision tools if image processing is enabled (always try this, independent of centralized config)
    try:
        vision_tools = ToolsRegistry.get_vision_tools()
        if vision_tools:
            safe_tools.extend(vision_tools)
            logger.info(f"Added {len(vision_tools)} vision analysis tools: {[t.name for t in vision_tools]}")
        else:
            logger.warning("No vision tools returned from ToolsRegistry")
    except Exception as e:
        logger.warning(f"Failed to add vision tools: {e}")


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
                    timeout=20
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
                    tool_name = kb_settings.get("name", "Knowledge Base Search") # Name for LLM
                    kb_description = kb_settings.get("description", f"Searches and returns information from the {qdrant_collection} knowledge base.")
                    
                    # New configuration fields
                    return_to_agent = kb_settings.get("returnToAgent", True)
                    rewrite_query = kb_settings.get("rewriteQuery", True)
                    tool_enabled = kb_settings.get("enabled", True)

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
                            # key="metadata.datasource_id",
                            key="metadata.datastoreId", # Use datastoreId for consistency
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
                    logger.info(f"Configured Knowledge Base tool '{tool_name}' (ID: {tool_id}) for datastores: {kb_ids} with description: {kb_description}")

            except Exception as e:
                logger.error(f"Failed to configure Knowledge Base tools: {e}", exc_info=True)
    else:
        logger.info("No Knowledge Base tools configured.")


    logger.info(f"Using max_rewrites: {max_rewrites}")

    # --- Web Search Tools ---
    web_search_configs = [t for t in tool_settings if t.get("type") == "webSearch"]
    if web_search_configs:
        logger.info(f"Configuring {len(web_search_configs)} Web Search tool(s)...")
        tavily_api_key = app_settings.TAVILY_API_KEY
        if not tavily_api_key:
            logger.warning("TAVILY_API_KEY not set. Web search tools disabled.")
        else:
            for ws_config in web_search_configs:
                ws_settings = ws_config.get("settings", {})
                ws_id = ws_config.get("id", f"web_search_{web_search_configs.index(ws_config)}") # Use ID from config with fallback
                ws_name = ws_settings.get("name", "Web Search") # Name for LLM
                ws_description = ws_settings.get("description", "Performs a web search for recent information.") # Use description from config if available
                ws_enabled = ws_settings.get("enabled", True)
                search_limit = ws_settings.get("searchLimit", 3)
                include_domains = ws_settings.get("include_domains", [])
                exclude_domains = ws_settings.get("excludeDomains", [])

                if not ws_enabled:
                    logger.info(f"Skipping disabled Web Search tool '{ws_name}' (ID: {ws_id})")
                    continue

                try:
                    web_search_tool = TavilySearchResults(
                        max_results=int(search_limit),
                        name=ws_id, # Use ID from config
                        description=ws_description, # Use description from config
                        include_domains=include_domains, # Use domainLimit from config
                        exclude_domains=exclude_domains,
                    )
                    safe_tools.append(web_search_tool)
                    logger.info(f"Configured Web Search tool '{ws_name}' (ID: {ws_id}) with description: {ws_description}")
                except Exception as e:
                    logger.error(f"Failed to create Web Search tool '{ws_name}' (ID: {ws_id}): {e}", exc_info=True)
    else:
        logger.info("No Web Search tools configured.")


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


    # --- Internal Tools (like voice capabilities) ---
    internal_configs = [t for t in tool_settings if t.get("type") == "internal"]
    for internal_config in internal_configs:
        try:
            tool_id = internal_config.get("id")
            tool_settings_internal = internal_config.get("settings", {})
            tool_name = tool_settings_internal.get("name", "Internal Tool")
            tool_enabled = tool_settings_internal.get("enabled", True)
            
            if not tool_enabled:
                logger.info(f"Skipping disabled internal tool '{tool_name}' (ID: {tool_id})")
                continue
                
            # For voice capabilities tool specifically
            if "voiceCapabilities" in tool_id:
                # Add voice capabilities tool if it's not already added
                if voice_capabilities_tool not in safe_tools:
                    safe_tools.append(voice_capabilities_tool)
                    logger.info(f"Added internal tool: {tool_name} (ID: {tool_id})")
                else:
                    logger.debug(f"Voice capabilities tool already configured")
            else:
                logger.warning(f"Unknown internal tool type: {tool_id}")
                
        except Exception as e:
            logger.error(f"Failed to configure internal tool: {e}", exc_info=True)
    

    # --- Predefined tools are now handled by centralized configuration above ---
    # No need for manual predefined tool configuration as they are included
    # in the centralized_safe_tools from configure_tools_centralized()


    # Combine lists: safe tools first, then datastore tools
    tools_list.extend(safe_tools)
    tools_list.extend(datastore_tools) 

    logger.info(f"Total tools configured: {len(tools_list)} ({len(safe_tools)} safe, {len(datastore_tools)} datastore).")
    logger.info(f"Tool names: {[tool.name for tool in tools_list]}")
    return tools_list, safe_tools, datastore_tools, datastore_names, max_rewrites


# --- Vision Tools ---

@tool
def analyze_images(
    image_urls: Annotated[List[str], "List of image URLs to analyze"],
    analysis_prompt: Annotated[str, "Specific prompt for image analysis"] = "Describe what you see in these images",
    state: Annotated[Dict, InjectedState] = None
) -> str:
    """
    Analyzes images using available Vision API providers.
    
    This tool processes a list of image URLs and returns detailed analysis
    of their contents using computer vision models like GPT-4V, Google Vision API, or Claude.
    
    Args:
        image_urls: List of URLs pointing to images to be analyzed
        analysis_prompt: Custom prompt to guide the analysis (optional)
        state: LangGraph state (injected automatically)
    
    Returns:
        Detailed description of image contents or error message
    """
    logger = logging.getLogger(__name__)
    
    try:
        if not image_urls:
            return "No images provided for analysis."
        
        # Check if images are available in state
        state_image_urls = state.get("image_urls", []) if state else []
        
        # Use state images if no specific URLs provided, or validate provided URLs against state
        if not image_urls and state_image_urls:
            image_urls = state_image_urls
        elif image_urls and state_image_urls:
            # Validate that provided URLs are in state (security check)
            valid_urls = [url for url in image_urls if url in state_image_urls]
            if not valid_urls:
                return "Provided image URLs are not available in current context."
            image_urls = valid_urls
        
        if not image_urls:
            return "No valid images available for analysis."
        
        logger.info(f"Analyzing {len(image_urls)} images with prompt: '{analysis_prompt[:100]}...'")
        
        # Check IMAGE_VISION_MODE setting
        from app.core.config import settings as app_settings
        vision_mode = getattr(app_settings, 'IMAGE_VISION_MODE', 'binary')
        logger.info(f"Using vision mode: {vision_mode}")
        
        # Since this is a sync tool, we need to handle async operations properly
        import asyncio
        
        try:
            if vision_mode == "url":
                # URL mode: Pass URLs directly to Vision APIs (for production with public MinIO)
                result = _run_async_in_sync(_analyze_images_url_mode, image_urls, analysis_prompt, state, logger)
            else:
                # Binary mode: Download images and pass base64 data (for dev/local MinIO)
                result = _run_async_in_sync(_analyze_images_binary_mode, image_urls, analysis_prompt, state, logger)
            
            return result
            
        except Exception as e:
            logger.error(f"Error during image analysis: {e}", exc_info=True)
            return f"Error analyzing images: {str(e)}"
    
    except Exception as e:
        logger.error(f"Unexpected error in analyze_images tool: {e}", exc_info=True)
        return f"Unexpected error during image analysis: {str(e)}"


def _run_async_in_sync(async_func, *args):
    """
    Helper function to run async function in sync context
    """
    import asyncio
    import concurrent.futures
    import threading
    
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            # We're in an async context, run in a separate thread
            def run_in_thread():
                new_loop = asyncio.new_event_loop()
                asyncio.set_event_loop(new_loop)
                try:
                    return new_loop.run_until_complete(async_func(*args))
                finally:
                    new_loop.close()
            
            with concurrent.futures.ThreadPoolExecutor() as executor:
                future = executor.submit(run_in_thread)
                return future.result(timeout=60)  # 60 second timeout
        else:
            # No loop running, we can use it directly
            return loop.run_until_complete(async_func(*args))
    except RuntimeError:
        # No event loop, create a new one
        return asyncio.run(async_func(*args))


async def _analyze_images_url_mode(image_urls: List[str], analysis_prompt: str, state: Dict, logger) -> str:
    """
    URL mode: Pass URLs directly to Vision APIs
    Used when MinIO is publicly accessible (production)
    """
    try:
        from app.services.media.image_orchestrator import ImageOrchestrator
        orchestrator = ImageOrchestrator()
        await orchestrator.initialize()
        
        result = await orchestrator.analyze_images(image_urls, analysis_prompt)
        
        if result.success and result.analysis:
            # Store analysis in state
            if state is not None:
                if "image_analysis" not in state:
                    state["image_analysis"] = []
                state["image_analysis"].append({
                    "prompt": analysis_prompt,
                    "image_urls": image_urls,
                    "analysis": result.analysis,
                    "provider": result.provider_name,
                    "mode": "url",
                    "timestamp": getattr(result, 'timestamp', None)
                })
            
            logger.info(f"Image analysis completed using {result.provider_name} (URL mode)")
            return result.analysis
        else:
            error_msg = result.error_message or "Failed to analyze images"
            logger.warning(f"Image analysis failed (URL mode): {error_msg}")
            return f"Image analysis failed: {error_msg}"
            
    except Exception as e:
        logger.error(f"Error during URL mode image analysis: {e}", exc_info=True)
        return f"Error analyzing images in URL mode: {str(e)}"


async def _analyze_images_binary_mode(image_urls: List[str], analysis_prompt: str, state: Dict, logger) -> str:
    """
    Binary mode: Download images locally and pass base64 data to Vision APIs
    Used when MinIO is not publicly accessible (localhost/dev)
    """
    try:
        # Import ImageOrchestrator inside the tool to avoid circular imports
        from app.services.media.image_orchestrator import ImageOrchestrator
        import httpx
        import base64
        
        # Initialize orchestrator
        orchestrator = ImageOrchestrator()
        await orchestrator.initialize()
        
        # Download images and convert to base64 data URLs
        image_data_urls = []
        async with httpx.AsyncClient() as client:
            for image_url in image_urls:
                try:
                    response = await client.get(image_url)
                    if response.status_code == 200:
                        # Convert to base64 data URL
                        image_bytes = response.content
                        image_base64 = base64.b64encode(image_bytes).decode('utf-8')
                        
                        # Detect content type
                        content_type = response.headers.get('content-type', 'image/jpeg')
                        data_url = f"data:{content_type};base64,{image_base64}"
                        image_data_urls.append(data_url)
                        
                        logger.debug(f"Downloaded and converted image: {len(image_bytes)} bytes")
                    else:
                        logger.warning(f"Failed to download image from {image_url}: HTTP {response.status_code}")
                        return f"Failed to download image: HTTP {response.status_code}"
                except Exception as e:
                    logger.error(f"Error downloading image from {image_url}: {e}")
                    return f"Error downloading image: {str(e)}"
        
        if not image_data_urls:
            return "Failed to download any images for analysis."
        
        # Analyze images using data URLs instead of remote URLs
        result = await orchestrator.analyze_images(image_data_urls, analysis_prompt)
        
        if result.success and result.analysis:
            # Store analysis in state
            if state is not None:
                if "image_analysis" not in state:
                    state["image_analysis"] = []
                state["image_analysis"].append({
                    "prompt": analysis_prompt,
                    "image_urls": image_urls,  # Original URLs for reference
                    "analysis": result.analysis,
                    "provider": result.provider_name,
                    "mode": "binary",
                    "timestamp": getattr(result, 'timestamp', None)
                })
            
            logger.info(f"Image analysis completed using {result.provider_name} (binary mode)")
            return result.analysis
        else:
            error_msg = result.error_message or "Failed to analyze images"
            logger.warning(f"Image analysis failed (binary mode): {error_msg}")
            return f"Image analysis failed: {error_msg}"
            
    except Exception as e:
        logger.error(f"Error during binary mode image analysis: {e}", exc_info=True)
        return f"Error analyzing images in binary mode: {str(e)}"
        
    except Exception as e:
        logger.error(f"Unexpected error in analyze_images tool: {e}", exc_info=True)
        return f"Unexpected error during image analysis: {str(e)}"


@tool
def describe_image_content(
    image_url: Annotated[str, "URL of the image to describe"], 
    focus: Annotated[str, "What to focus on in the description"] = "general content",
    state: Annotated[Dict, InjectedState] = None
) -> str:
    """
    Provides detailed description of a single image's content.
    
    This tool is optimized for describing one image in detail, with options
    to focus on specific aspects like text content, objects, people, etc.
    
    Args:
        image_url: URL of the image to describe
        focus: What aspect to focus on (e.g., "text", "objects", "people", "general content")
        state: LangGraph state (injected automatically)
    
    Returns:
        Detailed description of the image content
    """
    logger = logging.getLogger(__name__)
    
    try:
        if not image_url:
            return "No image URL provided."
        
        # Check if image is available in state
        state_image_urls = state.get("image_urls", []) if state else []
        if state_image_urls and image_url not in state_image_urls:
            return "Requested image is not available in current context."
        
        # Create focused prompt based on the focus parameter
        focus_prompts = {
            "text": "Extract and transcribe any text visible in this image. Include signs, labels, documents, or any written content.",
            "objects": "Identify and describe all objects, items, and things visible in this image. Be specific about their appearance, location, and any notable features.",
            "people": "Describe any people in this image, including their appearance, clothing, actions, and positioning. Be respectful and factual.",
            "scene": "Describe the overall scene, setting, and environment shown in this image. Include lighting, mood, and context.",
            "colors": "Analyze the color palette, dominant colors, and color relationships in this image.",
            "technical": "Provide technical analysis of this image including composition, lighting, style, and photographic aspects.",
            "general content": "Provide a comprehensive description of everything visible in this image."
        }
        
        analysis_prompt = focus_prompts.get(focus.lower(), focus_prompts["general content"])
        if focus.lower() not in focus_prompts:
            analysis_prompt = f"Focus on {focus} in this image: {analysis_prompt}"
        
        logger.info(f"Describing image with focus on: {focus}")
        
        # Use analyze_images tool for single image
        return analyze_images([image_url], analysis_prompt, state)
        
    except Exception as e:
        logger.error(f"Error in describe_image_content tool: {e}", exc_info=True)
        return f"Error describing image: {str(e)}"
