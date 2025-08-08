"""
LangGraph tools configuration and image analysis tools.

This module provides tools configuration for LangGraph agen    tool_id = kb_config.get("id",
                           f"kb_retriever_{'_'.join([str(kb_id) "
                           f"for kb_id in kb_ids])}")   kb_description = kb_settings.get("description",
                                    f"Searches and returns information "
                                    f"from the {qdrant_collection} knowledge base.")includi        logger.warning("KnowledgeBase tool '%s' configured but no "
                      "knowledgeBaseIds provided. Skipping.", tool_id):
- Knowledge Base / Retriever tools
- Web Search tools
- Internal tools (voice capabilities)
- Vision analysis tools for image processing
"""
import asyncio
import base64
import concurrent.futures
import logging
from typing import Annotated, Dict, List, Tuple, Set

import httpx
from langchain_core.tools import tool, BaseTool
from langgraph.prebuilt import InjectedState
from langchain_openai import OpenAIEmbeddings
from qdrant_client import QdrantClient, models
from langchain_qdrant import QdrantVectorStore
from langchain.tools.retriever import create_retriever_tool
from langchain_community.tools.tavily_search import TavilySearchResults

from app.core.config import settings as app_settings
from app.agent_runner.core.config_mixin import AgentConfigMixin
from app.agent_runner.langgraph.tools.tools_registry import (
    auth_tool,
    get_user_info_tool,
    voice_capabilities_tool,
    configure_tools_centralized,
    ToolsRegistry
)
from app.services.media.image_orchestrator import ImageOrchestrator


def _initialize_tool_lists():
    """Initialize tool lists."""
    safe_tools = []  # Tools that are safe to use in general conversation
    datastore_tools = []  # Tools that interact with knowledge bases/datastores
    datastore_names = set()  # Names of datastore tools
    return safe_tools, datastore_tools, datastore_names


def _add_centralized_tools(agent_config: Dict, agent_id: str, safe_tools: List, logger):
    """Add centralized tools to safe tools list."""
    try:
        # Get centralized tools configuration
        _, centralized_safe, centralized_api = configure_tools_centralized(
            agent_config, agent_id
        )

        # Add centralized tools to our lists
        safe_tools.extend(centralized_safe)
        safe_tools.extend(centralized_api)  # Add API tools to safe tools

        logger.info("Configured %d centralized tools",
                   len(centralized_safe) + len(centralized_api))
    except Exception as e:
        logger.error("Failed to configure centralized tools: %s", e, exc_info=True)


def _add_predefined_tools(safe_tools: List, logger):
    """
    Add predefined tools to safe tools list.

    🔶 PHASE 5.1.2 DEPRECATION: voice_capabilities_tool redirects to LangGraph decisions
    - Tool now educates agents to use contextual voice decisions
    - Eliminates keyword matching guidance
    - Encourages LangGraph workflow-based voice decisions
    """
    # 🔶 PHASE 5.1.2: voice_capabilities_tool updated to redirect to LangGraph decisions
    safe_tools.extend([auth_tool, get_user_info_tool, voice_capabilities_tool])
    logger.info("Added predefined tools: auth, user_info, voice_capabilities (REDIRECTS TO LANGGRAPH)")

    # ✅ PREFERRED: Add voice_v2 tools if available
    voice_v2_tools = ToolsRegistry.get_voice_v2_tools()
    if voice_v2_tools:
        safe_tools.extend(voice_v2_tools)
        logger.info(f"Added voice_v2 tools: {[tool.name for tool in voice_v2_tools]}")
    else:
        logger.info("Voice_v2 tools not available")


def _add_vision_tools(vision_settings: Dict, safe_tools: List, logger):
    """Add vision analysis tools - always enabled as core functionality."""
    safe_tools.append(analyze_images)
    safe_tools.append(describe_image_content)
    logger.info("Vision tools added as core functionality")


def _remove_duplicate_tools(tools: List) -> List:
    """Remove duplicate tools based on tool name."""
    seen_names = set()
    unique_tools = []
    
    for tool in tools:
        tool_name = getattr(tool, 'name', str(tool))
        if tool_name not in seen_names:
            seen_names.add(tool_name)
            unique_tools.append(tool)
    
    return unique_tools


def _add_tavily_tools(use_tavily_search: bool, safe_tools: List, logger):
    """Add Tavily search tools if enabled."""
    if use_tavily_search and app_settings.TAVILY_API_KEY:
        tavily_search = TavilySearchResults(max_results=5)
        safe_tools.append(tavily_search)
        logger.info("Tavily search tool added")
    elif use_tavily_search:
        logger.warning("Tavily search requested but TAVILY_API_KEY not configured")


def _setup_qdrant_connection(logger):
    """Setup Qdrant connection and vector store."""
    qdrant_url = app_settings.QDRANT_URL
    qdrant_collection = app_settings.QDRANT_COLLECTION

    if not qdrant_url or not qdrant_collection:
        logger.warning("QDRANT_URL or QDRANT_COLLECTION not set. Knowledge base tools disabled.")
        return None, None, None

    try:
        embeddings = OpenAIEmbeddings()
        qdrant_client = QdrantClient(url=qdrant_url, timeout=20)
        vector_store = QdrantVectorStore(
            client=qdrant_client,
            collection_name=qdrant_collection,
            embedding=embeddings,
        )
        logger.info("Connected to Qdrant at %s, collection: %s",
                   qdrant_url, qdrant_collection)
        return embeddings, qdrant_client, vector_store
    except Exception as e:
        logger.error("Failed to setup Qdrant connection: %s", e, exc_info=True)
        return None, None, None


def _create_kb_retriever_tool(kb_config: Dict, agent_config: Dict,
                             vector_store, qdrant_collection: str,
                             logger):
    """Create a single Knowledge Base retriever tool."""
    kb_settings = kb_config.get("settings", {})
    kb_ids = kb_settings.get("knowledgeBaseIds", [])
    search_limit = kb_settings.get("retrievalLimit", 4)
    tool_id = kb_config.get("id",
                           f"kb_retriever_{'_'.join([str(kb_id) for kb_id in kb_ids])}")
    tool_name = kb_settings.get("name", "Knowledge Base Search")
    kb_description = kb_settings.get("description",
                                    f"Searches and returns information from the {qdrant_collection} knowledge base.")
    tool_enabled = kb_settings.get("enabled", True)

    if not tool_enabled:
        logger.info("Skipping disabled Knowledge Base tool '%s' (ID: %s)",
                   tool_name, tool_id)
        return None, None

    if not kb_ids:
        logger.warning("KnowledgeBase tool '%s' configured but no knowledgeBaseIds provided. Skipping.",
                      tool_id)
        return None, None

    client_id = agent_config.get("ownerId")
    must_conditions = []

    if client_id:
        must_conditions.append(
            models.FieldCondition(
                key="metadata.client_id",
                match=models.MatchValue(value=str(client_id))
            )
        )

    must_conditions.append(
        models.FieldCondition(
            key="metadata.datastoreId",
            match=models.MatchAny(any=[str(kb_id) for kb_id in kb_ids])
        )
    )

    qdrant_filter = models.Filter(must=must_conditions)
    retriever = vector_store.as_retriever(
        search_kwargs={"k": search_limit, "filter": qdrant_filter}
    )

    retriever_tool = create_retriever_tool(
        retriever,
        tool_id,
        kb_description,
        document_separator="\n---RETRIEVER_DOC---\n",
    )

    logger.info("Configured Knowledge Base tool '%s' (ID: %s) for datastores: %s",
               tool_name, tool_id, kb_ids)
    return retriever_tool, tool_id


def _configure_knowledge_base_tools(kb_configs: List[Dict], agent_config: Dict, logger) -> Tuple[List[BaseTool], Set[str]]:
    """Configure Knowledge Base / Retriever Tools."""
    datastore_tools = []
    datastore_names = set()

    if not kb_configs:
        logger.info("No Knowledge Base tools configured.")
        return datastore_tools, datastore_names

    logger.info("Configuring %d Knowledge Base tool(s)...", len(kb_configs))

    # Setup Qdrant connection
    _, _, vector_store = _setup_qdrant_connection(logger)
    if not vector_store:
        return datastore_tools, datastore_names

    # Check for client_id once
    client_id = agent_config.get("ownerId")
    if not client_id:
        logger.warning("Missing 'ownerId' in agent_config, cannot filter KB by client.")

    # Process each Knowledge Base configuration
    for kb_config in kb_configs:
        try:
            retriever_tool, tool_id = _create_kb_retriever_tool(
                kb_config, agent_config, vector_store,
                app_settings.QDRANT_COLLECTION, logger
            )
            if retriever_tool and tool_id:
                datastore_tools.append(retriever_tool)
                datastore_names.add(tool_id)
        except Exception as e:
            logger.error("Failed to create KB tool: %s", e, exc_info=True)

    return datastore_tools, datastore_names


def _configure_web_search_tools(web_search_configs: List[Dict], logger) -> List[BaseTool]:
    """Configure Web Search Tools."""
    safe_tools_web = []

    if not web_search_configs:
        logger.info("No Web Search tools configured.")
        return safe_tools_web

    logger.info("Configuring %d Web Search tool(s)...", len(web_search_configs))
    tavily_api_key = app_settings.TAVILY_API_KEY

    if not tavily_api_key:
        logger.warning("TAVILY_API_KEY not set. Web search tools disabled.")
        return safe_tools_web

    for ws_config in web_search_configs:
        ws_settings = ws_config.get("settings", {})
        ws_id = ws_config.get("id", f"web_search_{web_search_configs.index(ws_config)}")
        ws_name = ws_settings.get("name", "Web Search")
        ws_description = ws_settings.get("description", "Performs a web search for recent information.")
        ws_enabled = ws_settings.get("enabled", True)
        search_limit = ws_settings.get("searchLimit", 3)
        include_domains = ws_settings.get("include_domains", [])
        exclude_domains = ws_settings.get("excludeDomains", [])

        if not ws_enabled:
            logger.info("Skipping disabled Web Search tool '%s' (ID: %s)",
                       ws_name, ws_id)
            continue

        try:
            web_search_tool = TavilySearchResults(
                max_results=int(search_limit),
                name=ws_id,
                description=ws_description,
                include_domains=include_domains,
                exclude_domains=exclude_domains,
            )
            safe_tools_web.append(web_search_tool)
            logger.info("Configured Web Search tool '%s' (ID: %s)", ws_name, ws_id)
        except Exception as e:
            logger.error("Failed to create Web Search tool '%s' (ID: %s): %s",
                        ws_name, ws_id, e, exc_info=True)

    return safe_tools_web


def _configure_internal_tools(internal_configs: List[Dict], safe_tools: List, logger):
    """Configure Internal Tools."""
    for internal_config in internal_configs:
        try:
            tool_id = internal_config.get("id")
            tool_settings_internal = internal_config.get("settings", {})
            tool_name = tool_settings_internal.get("name", "Internal Tool")
            tool_enabled = tool_settings_internal.get("enabled", True)

            if not tool_enabled:
                logger.info("Skipping disabled internal tool '%s' (ID: %s)",
                       tool_name, tool_id)
                continue

            if "voiceCapabilities" in tool_id:
                if voice_capabilities_tool not in safe_tools:
                    safe_tools.append(voice_capabilities_tool)
                    logger.info("Added internal tool: %s (ID: %s) - REDIRECTS TO LANGGRAPH", tool_name, tool_id)
                else:
                    logger.debug("Voice capabilities tool already configured (REDIRECTS TO LANGGRAPH)")
            else:
                logger.warning("Unknown internal tool type: %s", tool_id)

        except Exception as e:
            logger.error("Failed to configure internal tool: %s", e, exc_info=True)


def _validate_agent_config(agent_config: Dict, logger) -> Tuple[Dict, Dict, List]:
    """Validate and extract agent configuration settings."""
    config_simple = agent_config.get("config", {}).get("simple", {})
    if not config_simple:
        logger.warning("Agent configuration 'config.simple' not found. No tools will be configured.")
        return {}, {}, []

    settings = config_simple.get("settings", {})
    if not settings:
        logger.warning("Agent configuration 'config.simple.settings' not found. No tools will be configured.")
        return {}, {}, []

    tool_settings = settings.get("tools", [])
    if not tool_settings:
        logger.info("No tools specified in 'config.simple.settings.tools'.")
        return {}, {}, []

    return config_simple, settings, tool_settings


def _configure_basic_tools(agent_config: Dict, agent_id: str, settings: Dict, safe_tools: List, logger):
    """Configure basic tools (centralized, predefined, vision)."""
    # Add centralized tools
    _add_centralized_tools(agent_config, agent_id, safe_tools, logger)

    # Add predefined tools as fallback
    _add_predefined_tools(safe_tools, logger)

    # Add vision tools as core functionality (always enabled)
    _add_vision_tools(settings.get("vision", {}), safe_tools, logger)


def _configure_specialized_tools(tool_settings: List, agent_config: Dict, logger) -> Tuple[List[BaseTool], List[BaseTool], Set[str]]:
    """Configure specialized tools (KB, Web Search, Internal)."""
    datastore_tools = []
    datastore_names = set()
    web_tools = []

    # Configure Knowledge Base tools
    kb_configs = [t for t in tool_settings if t.get("type") == "knowledgeBase"]
    kb_tools, kb_names = _configure_knowledge_base_tools(kb_configs, agent_config, logger)
    datastore_tools.extend(kb_tools)
    datastore_names.update(kb_names)

    # Configure Web Search tools
    web_search_configs = [t for t in tool_settings if t.get("type") == "webSearch"]
    web_tools = _configure_web_search_tools(web_search_configs, logger)

    # Handle API Request tools (handled by centralized configuration)
    api_request_configs = [t for t in tool_settings if t.get("type") == "apiRequest"]
    if api_request_configs:
        logger.info("Found %d additional API request configurations",
                   len(api_request_configs))

    return datastore_tools, web_tools, datastore_names


def _finalize_tools_configuration(safe_tools: List, datastore_tools: List,
                                 datastore_names: Set, max_rewrites: int,
                                 logger) -> Tuple[List[BaseTool], List[BaseTool],
                                                List[BaseTool], Set[str], int]:
    """Finalize tools configuration and return results."""
    # Remove duplicates from safe_tools and datastore_tools
    safe_tools = _remove_duplicate_tools(safe_tools)
    datastore_tools = _remove_duplicate_tools(datastore_tools)
    
    tools_list = safe_tools + datastore_tools

    logger.info("Total tools configured: %d (%d safe, %d datastore) after deduplication.",
               len(tools_list), len(safe_tools), len(datastore_tools))
    logger.info("Tool names: %s", [tool.name for tool in tools_list])
    logger.info("Using max_rewrites: %d", max_rewrites)

    return tools_list, safe_tools, datastore_tools, datastore_names, max_rewrites


def configure_tools(agent_config: Dict, agent_id: str,
                   logger) -> Tuple[List[BaseTool], List[BaseTool],
                                  List[BaseTool], Set[str], int]:
    """Configure tools based on agent configuration (simple structure)."""
    # Initialize tool lists and variables
    safe_tools, datastore_tools, datastore_names = _initialize_tool_lists()
    max_rewrites = 3  # Default

    # Validate and extract configuration
    _, settings, tool_settings = _validate_agent_config(agent_config, logger)
    if not tool_settings:
        return [], [], [], set(), max_rewrites

    # Get max_rewrites setting
    config_mixin = AgentConfigMixin()
    max_rewrites = config_mixin.get_max_rewrites(agent_config)

    # Configure basic tools (centralized, predefined, vision)
    _configure_basic_tools(agent_config, agent_id, settings, safe_tools, logger)

    # Configure specialized tools (KB, Web Search, Internal)
    spec_datastore_tools, web_tools, spec_datastore_names = _configure_specialized_tools(
        tool_settings, agent_config, logger
    )

    # Combine results
    datastore_tools.extend(spec_datastore_tools)
    datastore_names.update(spec_datastore_names)
    safe_tools.extend(web_tools)

    # Configure Internal tools
    internal_configs = [t for t in tool_settings if t.get("type") == "internal"]
    _configure_internal_tools(internal_configs, safe_tools, logger)

    # Finalize and return results
    return _finalize_tools_configuration(safe_tools, datastore_tools, datastore_names, max_rewrites, logger)


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

        logger.info("Analyzing %d images with prompt: '%.100s...'",
                   len(image_urls), analysis_prompt)

        # Check IMAGE_VISION_MODE setting
        vision_mode = getattr(app_settings, 'IMAGE_VISION_MODE', 'binary')
        logger.info("Using vision mode: %s", vision_mode)

        # Since this is a sync tool, we need to handle async operations properly

        try:
            if vision_mode == "url":
                # URL mode: Pass URLs directly to Vision APIs (for production with public MinIO)
                result = _run_async_in_sync(_analyze_images_url_mode,
                                          image_urls, analysis_prompt, state, logger)
            else:
                # Binary mode: Download images and pass base64 data (for dev/local MinIO)
                result = _run_async_in_sync(_analyze_images_binary_mode,
                                          image_urls, analysis_prompt, state, logger)

            return result

        except Exception as e:
            logger.error("Error during image analysis: %s", e, exc_info=True)
            return f"Error analyzing images: {str(e)}"

    except Exception as e:
        logger.error("Unexpected error in analyze_images tool: %s", e, exc_info=True)
        return f"Unexpected error during image analysis: {str(e)}"


def _run_async_in_sync(async_func, *args):
    """
    Helper function to run async function in sync context
    """
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

        # No loop running, we can use it directly
        return loop.run_until_complete(async_func(*args))
    except RuntimeError:
        # No event loop, create a new one
        return asyncio.run(async_func(*args))


async def _analyze_images_url_mode(image_urls: List[str], analysis_prompt: str,
                                  state: Dict, logger) -> str:
    """
    URL mode: Pass URLs directly to Vision APIs
    Used when MinIO is publicly accessible (production)
    """
    try:
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

            logger.info("Image analysis completed using %s (URL mode)",
                       result.provider_name)
            return result.analysis

        error_msg = result.error_message or "Failed to analyze images"
        logger.warning("Image analysis failed (URL mode): %s", error_msg)
        return f"Image analysis failed: {error_msg}"

    except Exception as e:
        logger.error("Error during URL mode image analysis: %s", e, exc_info=True)
        return f"Error analyzing images in URL mode: {str(e)}"


async def _download_images_to_base64(image_urls: List[str], logger) -> List[str]:
    """Download images and convert to base64 data URLs."""
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

                    logger.debug("Downloaded and converted image: %d bytes",
                                len(image_bytes))
                else:
                    logger.warning("Failed to download image from %s: HTTP %d",
                                  image_url, response.status_code)
            except Exception as e:
                logger.error("Error downloading image from %s: %s", image_url, e)

    return image_data_urls


async def _store_analysis_in_state(state: Dict, analysis_prompt: str, image_urls: List[str], result, _logger):
    """Store analysis result in state."""
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


async def _analyze_images_binary_mode(image_urls: List[str], analysis_prompt: str,
                                     state: Dict, logger) -> str:
    """
    Binary mode: Download images locally and pass base64 data to Vision APIs
    Used when MinIO is not publicly accessible (localhost/dev)
    """
    try:
        # Initialize orchestrator
        orchestrator = ImageOrchestrator()
        await orchestrator.initialize()

        # Download images and convert to base64 data URLs
        image_data_urls = await _download_images_to_base64(image_urls, logger)

        if not image_data_urls:
            return "Failed to download any images for analysis."

        # Analyze images using data URLs instead of remote URLs
        result = await orchestrator.analyze_images(image_data_urls, analysis_prompt)

        if result.success and result.analysis:
            # Store analysis in state
            await _store_analysis_in_state(state, analysis_prompt, image_urls,
                                          result, logger)

            logger.info("Image analysis completed using %s (binary mode)",
                       result.provider_name)
            return result.analysis

        error_msg = result.error_message or "Failed to analyze images"
        logger.warning("Image analysis failed (binary mode): %s", error_msg)
        return f"Image analysis failed: {error_msg}"

    except Exception as e:
        logger.error("Error during image analysis: %s", e, exc_info=True)
        return f"Error analyzing images: {str(e)}"


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
            "text": ("Extract and transcribe any text visible in this image. "
                    "Include signs, labels, documents, or any written content."),
            "objects": ("Identify and describe all objects, items, and things "
                       "visible in this image. Be specific about their appearance, "
                       "location, and any notable features."),
            "people": ("Describe any people in this image, including their "
                      "appearance, clothing, actions, and positioning. "
                      "Be respectful and factual."),
            "scene": ("Describe the overall scene, setting, and environment "
                     "shown in this image. Include lighting, mood, and context."),
            "colors": ("Analyze the color palette, dominant colors, and color "
                      "relationships in this image."),
            "technical": ("Provide technical analysis of this image including "
                         "composition, lighting, style, and photographic aspects."),
            "general content": ("Provide a comprehensive description of "
                              "everything visible in this image.")
        }

        analysis_prompt = focus_prompts.get(focus.lower(),
                                           focus_prompts["general content"])
        if focus.lower() not in focus_prompts:
            analysis_prompt = f"Focus on {focus} in this image: {analysis_prompt}"

        logger.info("Describing image with focus on: %s", focus)

        # Use analyze_images tool for single image
        return analyze_images([image_url], analysis_prompt, state)

    except Exception as e:
        logger.error("Error in describe_image_content tool: %s", e, exc_info=True)
        return f"Error describing image: {str(e)}"
