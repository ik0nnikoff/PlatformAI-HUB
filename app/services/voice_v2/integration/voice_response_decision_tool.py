"""
Voice Response Decision Tool for LangGraph Integration

This module provides intelligent TTS decision-making capabilities for LangGraph agents,
replacing primitive AgentResponseProcessor logic with context-aware decisions.

Key Features:
- Intelligent TTS decision making based on voice intent analysis
- Dynamic provider selection with health/performance/cost considerations  
- User preference learning and adaptation
- Response content suitability analysis
- Platform-specific capability handling

Migration from: app/services/voice/intent_utils.py:AgentResponseProcessor
"""

import logging
import asyncio
from typing import Dict, Any, List, Optional, Tuple
from dataclasses import dataclass
from enum import Enum

from langchain.tools import tool
from langgraph.prebuilt import InjectedState
from typing_extensions import Annotated

from app.services.voice_v2.integration.voice_intent_analysis_tool import (
    voice_intent_analysis_tool, VoiceIntentType, ConfidenceLevel
)


class TTSDecisionType(Enum):
    """TTS decision types"""
    PROCEED_WITH_TTS = "proceed_with_tts"
    SKIP_TTS = "skip_tts"
    USER_PREFERENCE_REQUIRED = "user_preference_required"
    PLATFORM_UNSUPPORTED = "platform_unsupported"
    CONTENT_UNSUITABLE = "content_unsuitable"


class ProviderSelectionStrategy(Enum):
    """Provider selection strategies"""
    HEALTH_BASED = "health_based"
    PERFORMANCE_BASED = "performance_based"
    COST_OPTIMIZED = "cost_optimized"
    USER_PREFERENCE = "user_preference"
    FAILOVER = "failover"


@dataclass
class VoiceResponseDecision:
    """Voice response decision result"""
    decision_type: TTSDecisionType
    should_generate_tts: bool
    selected_provider: Optional[str]
    provider_config: Optional[Dict[str, Any]]
    confidence: float
    reasoning: str
    metadata: Dict[str, Any]
    fallback_providers: List[str]
    estimated_cost: Optional[float]
    estimated_duration: Optional[float]


@tool
def voice_response_decision_tool(
    response_text: Annotated[str, "The agent response text to analyze for TTS generation"],
    state: Annotated[Dict, InjectedState] = None
) -> str:
    """
    Intelligent voice response decision tool for LangGraph agents.
    
    Analyzes agent response and context to make intelligent TTS decisions,
    replacing primitive AgentResponseProcessor logic with context-aware analysis.
    
    Args:
        response_text: Agent response text to analyze
        state: LangGraph agent state with conversation context
        
    Returns:
        JSON string with voice response decision and provider recommendations
    """
    import json
    import asyncio
    
    # Get logger from state or create default
    log_adapter = logging.LoggerAdapter(
        logging.getLogger("voice_response_decision_tool"),
        {"agent_id": state.get("config", {}).get("configurable", {}).get("agent_id", "unknown")}
    )
    
    try:
        log_adapter.debug(f"Analyzing voice response decision for text length: {len(response_text)}")
        
        # Run async analysis
        loop = asyncio.get_event_loop()
        decision = loop.run_until_complete(
            _analyze_voice_response_decision(response_text, state, log_adapter)
        )
        
        # Generate comprehensive response
        response = _generate_decision_response(decision, log_adapter)
        
        log_adapter.debug(f"Voice response decision: {decision.decision_type.value}")
        return json.dumps(response, ensure_ascii=False)
        
    except Exception as e:
        log_adapter.error(f"Error in voice response decision analysis: {e}", exc_info=True)
        
        # Generate fallback decision
        fallback_decision = _generate_fallback_decision(response_text, state)
        return json.dumps(_generate_decision_response(fallback_decision, log_adapter), ensure_ascii=False)


async def _analyze_voice_response_decision(
    response_text: str, 
    state: Dict[str, Any], 
    log_adapter
) -> VoiceResponseDecision:
    """Perform intelligent voice response decision analysis"""
    
    log_adapter.debug("Starting voice response decision analysis")
    
    # Step 1: Get voice intent analysis first
    intent_analysis = await _get_voice_intent_analysis(state, log_adapter)
    
    # Step 2: Analyze response content suitability
    content_suitability = _analyze_response_content_suitability(response_text)
    
    # Step 3: Check platform compatibility
    platform_compatible = _check_platform_voice_compatibility(state.get("channel", "unknown"))
    
    # Step 4: Analyze user preferences and patterns
    user_preference_score = _analyze_user_voice_preferences(state.get("user_data", {}))
    
    # Step 5: Get agent voice configuration
    agent_voice_config = _extract_agent_voice_config(state.get("config", {}))
    
    # Step 6: Make intelligent decision
    decision = _make_tts_decision(
        intent_analysis=intent_analysis,
        content_suitability=content_suitability,
        platform_compatible=platform_compatible,
        user_preference_score=user_preference_score,
        agent_config=agent_voice_config,
        response_text=response_text,
        log_adapter=log_adapter
    )
    
    # Step 7: Select optimal provider if TTS should proceed
    if decision.should_generate_tts:
        provider_info = await _select_optimal_provider(
            agent_voice_config, 
            state.get("channel", "unknown"),
            user_preference_score,
            log_adapter
        )
        decision.selected_provider = provider_info["provider"]
        decision.provider_config = provider_info["config"]
        decision.fallback_providers = provider_info["fallbacks"]
        decision.estimated_cost = provider_info["estimated_cost"]
        decision.estimated_duration = provider_info["estimated_duration"]
    
    log_adapter.debug(f"Decision analysis complete: {decision.decision_type.value}")
    return decision


async def _get_voice_intent_analysis(state: Dict[str, Any], log_adapter) -> Dict[str, Any]:
    """Get voice intent analysis from previous tool call or analyze now"""
    
    # Check if we already have intent analysis in state
    existing_analysis = state.get("voice_intent_analysis")
    if existing_analysis:
        log_adapter.debug("Using existing voice intent analysis from state")
        return existing_analysis
    
    # Run voice intent analysis tool
    log_adapter.debug("Running voice intent analysis for response decision")
    
    try:
        # Import here to avoid circular imports
        from app.services.voice_v2.integration.voice_intent_analysis_tool import voice_intent_analysis_tool
        
        # Get the actual coroutine function
        analysis_func = voice_intent_analysis_tool.coroutine
        analysis_result = await analysis_func(state=state)
        
        # Parse JSON result
        import json
        return json.loads(analysis_result)
        
    except Exception as e:
        log_adapter.error(f"Error getting voice intent analysis: {e}", exc_info=True)
        return {
            "success": False,
            "intent_analysis": {
                "intent_type": VoiceIntentType.NO_TTS_INTENT.value,
                "confidence": 0.0,
                "reasoning": "Failed to analyze intent"
            }
        }


def _analyze_response_content_suitability(response_text: str) -> float:
    """Analyze if response content is suitable for TTS"""
    
    if not response_text:
        return 0.0
    
    suitability_score = 0.0
    
    # Length analysis - enhanced scoring
    text_length = len(response_text.strip())
    if 50 <= text_length <= 2000:  # Optimal length for TTS
        suitability_score += 0.3
    elif text_length < 50:
        suitability_score += 0.1  # Very short text
    else:
        suitability_score += 0.2  # Long text, still acceptable
    
    # Content type analysis
    import re
    
    # Natural language content (good for TTS) - enhanced
    word_count = len(response_text.split())
    if word_count >= 15:  # Longer responses are better for voice
        suitability_score += 0.3
    elif word_count >= 10:
        suitability_score += 0.25
    
    # Check for conversational elements
    if any(word in response_text.lower() for word in ['пожалуйста', 'конечно', 'безусловно', 'естественно']):
        suitability_score += 0.15
    
    # Educational/explanatory content (good for voice) - enhanced patterns
    educational_patterns = ['объясню', 'расскажу', 'покажу', 'например', 'во-первых', 'во-вторых', 'пошагово', 'основы', 'понять']
    if any(word in response_text.lower() for word in educational_patterns):
        suitability_score += 0.25  # Increased from 0.2
    
    # Technical content analysis (may be less suitable)
    code_blocks = len(re.findall(r'```[\s\S]*?```', response_text))
    if code_blocks > 0:
        suitability_score -= 0.2
    
    # URLs and links (less suitable for voice)
    urls = len(re.findall(r'http[s]?://\S+', response_text))
    if urls > 2:
        suitability_score -= 0.15
    
    # Lists and structured content
    list_items = len(re.findall(r'^\s*[-*]\s+', response_text, re.MULTILINE))
    if list_items > 5:
        suitability_score -= 0.1
    
    return max(0.0, min(1.0, suitability_score))


def _check_platform_voice_compatibility(platform: str) -> bool:
    """Check if platform supports voice features"""
    
    platform_capabilities = {
        "telegram": True,   # Full voice message support
        "whatsapp": True,   # Voice messages supported via wppconnect
        "api": False,       # API doesn't have built-in voice playback
        "web": False,       # Depends on client implementation
        "discord": True,    # Voice support available
        "slack": False,     # Limited voice support
    }
    
    return platform_capabilities.get(platform.lower(), False)


def _analyze_user_voice_preferences(user_data: Dict[str, Any]) -> float:
    """Analyze user voice interaction preferences and patterns"""
    
    voice_activity = user_data.get("voice_activity", {})
    preferences = user_data.get("preferences", {})
    
    preference_score = 0.5  # Neutral starting point
    
    # Explicit user preferences
    voice_prefs = preferences.get("voice", {})
    if voice_prefs.get("prefers_voice_responses", False):
        preference_score += 0.3
    elif voice_prefs.get("prefers_text_responses", False):
        preference_score -= 0.3
    
    # Historical voice activity
    recent_tts_requests = voice_activity.get("recent_tts_requests", 0)
    if recent_tts_requests > 3:
        preference_score += 0.2
    elif recent_tts_requests == 0:
        preference_score -= 0.1
    
    # Voice interaction patterns
    total_voice_interactions = voice_activity.get("total_voice_interactions", 0)
    total_interactions = user_data.get("total_interactions", 1)
    
    if total_interactions > 0:
        voice_ratio = total_voice_interactions / total_interactions
        if voice_ratio > 0.5:
            preference_score += 0.2
        elif voice_ratio < 0.1:
            preference_score -= 0.15
    
    # Time of day considerations (mock implementation)
    # Real implementation would check current time vs user activity patterns
    
    return max(0.0, min(1.0, preference_score))


def _extract_agent_voice_config(agent_config: Dict[str, Any]) -> Dict[str, Any]:
    """Extract voice configuration from agent config"""
    
    config = agent_config.get("configurable", {})
    simple_config = config.get("simple", {})
    settings = simple_config.get("settings", {})
    
    voice_settings = settings.get("voice_settings", {})
    
    return {
        "enabled": voice_settings.get("enabled", False),
        "providers": voice_settings.get("providers", []),
        "auto_tts": voice_settings.get("auto_tts", False),
        "quality_priority": voice_settings.get("quality_priority", "balanced"),
        "cost_optimization": voice_settings.get("cost_optimization", True)
    }


def _make_tts_decision(
    intent_analysis: Dict[str, Any],
    content_suitability: float,
    platform_compatible: bool,
    user_preference_score: float,
    agent_config: Dict[str, Any],
    response_text: str,
    log_adapter
) -> VoiceResponseDecision:
    """Make intelligent TTS decision based on all factors"""
    
    # Check basic requirements first
    if not platform_compatible:
        return VoiceResponseDecision(
            decision_type=TTSDecisionType.PLATFORM_UNSUPPORTED,
            should_generate_tts=False,
            selected_provider=None,
            provider_config=None,
            confidence=1.0,
            reasoning="Platform does not support voice features",
            metadata={"platform_compatible": False},
            fallback_providers=[],
            estimated_cost=None,
            estimated_duration=None
        )
    
    if not agent_config.get("enabled", False):
        return VoiceResponseDecision(
            decision_type=TTSDecisionType.SKIP_TTS,
            should_generate_tts=False,
            selected_provider=None,
            provider_config=None,
            confidence=1.0,
            reasoning="Voice features not enabled for this agent",
            metadata={"voice_enabled": False},
            fallback_providers=[],
            estimated_cost=None,
            estimated_duration=None
        )
    
    if content_suitability < 0.2:
        return VoiceResponseDecision(
            decision_type=TTSDecisionType.CONTENT_UNSUITABLE,
            should_generate_tts=False,
            selected_provider=None,
            provider_config=None,
            confidence=0.9,
            reasoning="Response content not suitable for voice delivery",
            metadata={"content_suitability": content_suitability},
            fallback_providers=[],
            estimated_cost=None,
            estimated_duration=None
        )
    
    # Analyze intent analysis results
    intent_data = intent_analysis.get("intent_analysis", {})
    intent_type = intent_data.get("intent_type", VoiceIntentType.NO_TTS_INTENT.value)
    intent_confidence = intent_data.get("confidence", 0.0)
    
    # Calculate overall decision confidence
    decision_confidence = (
        intent_confidence * 0.4 +           # Intent analysis is most important
        content_suitability * 0.25 +        # Content must be suitable
        user_preference_score * 0.25 +      # User preferences matter
        (0.1 if agent_config.get("auto_tts", False) else 0.0)  # Agent settings
    )
    
    # Make decision based on weighted factors
    if intent_type == VoiceIntentType.EXPLICIT_TTS_REQUEST.value:
        should_generate = True
        decision_type = TTSDecisionType.PROCEED_WITH_TTS
        reasoning = "User explicitly requested voice response"
        
    elif intent_type == VoiceIntentType.IMPLICIT_TTS_SUITABLE.value and decision_confidence >= 0.6:
        should_generate = True
        decision_type = TTSDecisionType.PROCEED_WITH_TTS
        reasoning = "Content suitable for voice and user context supports TTS"
        
    elif intent_type == VoiceIntentType.TTS_CONTINUATION.value and user_preference_score >= 0.6:
        should_generate = True
        decision_type = TTSDecisionType.PROCEED_WITH_TTS
        reasoning = "Conversation context suggests voice continuation"
        
    elif user_preference_score < 0.3 and intent_confidence < 0.5:
        should_generate = False
        decision_type = TTSDecisionType.SKIP_TTS
        reasoning = "Low user voice preference and unclear intent"
        
    elif decision_confidence < 0.4:
        should_generate = False
        decision_type = TTSDecisionType.USER_PREFERENCE_REQUIRED
        reasoning = "Unclear decision - user preference input needed"
        
    else:
        should_generate = decision_confidence >= 0.5
        decision_type = TTSDecisionType.PROCEED_WITH_TTS if should_generate else TTSDecisionType.SKIP_TTS
        reasoning = f"Decision based on weighted analysis (confidence: {decision_confidence:.2f})"
    
    log_adapter.debug(f"TTS decision: {decision_type.value} (confidence: {decision_confidence:.2f})")
    
    return VoiceResponseDecision(
        decision_type=decision_type,
        should_generate_tts=should_generate,
        selected_provider=None,  # Will be set later
        provider_config=None,
        confidence=decision_confidence,
        reasoning=reasoning,
        metadata={
            "intent_type": intent_type,
            "intent_confidence": intent_confidence,
            "content_suitability": content_suitability,
            "user_preference_score": user_preference_score,
            "response_length": len(response_text)
        },
        fallback_providers=[],
        estimated_cost=None,
        estimated_duration=None
    )


async def _select_optimal_provider(
    agent_config: Dict[str, Any],
    platform: str,
    user_preference_score: float,
    log_adapter
) -> Dict[str, Any]:
    """Select optimal TTS provider based on multiple factors"""
    
    providers = agent_config.get("providers", [])
    if not providers:
        log_adapter.warning("No TTS providers configured")
        return {
            "provider": None,
            "config": None,
            "fallbacks": [],
            "estimated_cost": None,
            "estimated_duration": None
        }
    
    # Sort providers by priority
    sorted_providers = sorted(providers, key=lambda p: p.get("priority", 999))
    
    # Select strategy based on agent configuration
    strategy = _determine_provider_selection_strategy(agent_config, user_preference_score)
    
    log_adapter.debug(f"Using provider selection strategy: {strategy.value}")
    
    if strategy == ProviderSelectionStrategy.HEALTH_BASED:
        selected = await _select_healthiest_provider(sorted_providers, log_adapter)
    elif strategy == ProviderSelectionStrategy.PERFORMANCE_BASED:
        selected = _select_fastest_provider(sorted_providers)
    elif strategy == ProviderSelectionStrategy.COST_OPTIMIZED:
        selected = _select_cheapest_provider(sorted_providers)
    else:  # Default to priority-based
        selected = sorted_providers[0] if sorted_providers else None
    
    if not selected:
        log_adapter.warning("No suitable provider found")
        return {
            "provider": None,
            "config": None,
            "fallbacks": [],
            "estimated_cost": None,
            "estimated_duration": None
        }
    
    # Prepare fallback providers
    fallbacks = [p.get("provider") for p in sorted_providers if p != selected and p.get("enabled", True)][:3]
    
    return {
        "provider": selected.get("provider"),
        "config": selected,
        "fallbacks": fallbacks,
        "estimated_cost": _estimate_tts_cost(selected),
        "estimated_duration": _estimate_tts_duration(selected)
    }


def _determine_provider_selection_strategy(
    agent_config: Dict[str, Any], 
    user_preference_score: float
) -> ProviderSelectionStrategy:
    """Determine the best provider selection strategy"""
    
    quality_priority = agent_config.get("quality_priority", "balanced")
    cost_optimization = agent_config.get("cost_optimization", True)
    
    if quality_priority == "performance" or user_preference_score > 0.8:
        return ProviderSelectionStrategy.PERFORMANCE_BASED
    elif cost_optimization and quality_priority == "cost":
        return ProviderSelectionStrategy.COST_OPTIMIZED
    else:
        return ProviderSelectionStrategy.HEALTH_BASED


async def _select_healthiest_provider(providers: List[Dict[str, Any]], log_adapter) -> Optional[Dict[str, Any]]:
    """Select provider based on health metrics"""
    
    # Mock implementation - in real system would check provider health
    # from health checker or monitoring system
    
    for provider in providers:
        if provider.get("enabled", True):
            provider_name = provider.get("provider", "unknown")
            # Mock health check
            health_score = 0.9  # Would be real health check
            
            if health_score > 0.7:
                log_adapter.debug(f"Selected healthy provider: {provider_name}")
                return provider
    
    log_adapter.warning("No healthy providers found, using first available")
    return providers[0] if providers else None


def _select_fastest_provider(providers: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Select provider based on performance metrics"""
    
    # Mock performance metrics - real implementation would use actual metrics
    performance_rankings = {
        "openai": 0.95,
        "elevenlabs": 0.90,
        "google": 0.85,
        "yandex": 0.80
    }
    
    best_provider = None
    best_score = 0.0
    
    for provider in providers:
        if not provider.get("enabled", True):
            continue
            
        provider_name = provider.get("provider", "")
        score = performance_rankings.get(provider_name, 0.5)
        
        if score > best_score:
            best_score = score
            best_provider = provider
    
    return best_provider


def _select_cheapest_provider(providers: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Select provider based on cost optimization"""
    
    # Mock cost metrics - real implementation would use actual pricing
    cost_rankings = {
        "yandex": 0.95,     # Cheapest
        "google": 0.80,
        "openai": 0.70,
        "elevenlabs": 0.50  # Most expensive
    }
    
    best_provider = None
    best_score = 0.0
    
    for provider in providers:
        if not provider.get("enabled", True):
            continue
            
        provider_name = provider.get("provider", "")
        score = cost_rankings.get(provider_name, 0.5)
        
        if score > best_score:
            best_score = score
            best_provider = provider
    
    return best_provider


def _estimate_tts_cost(provider_config: Dict[str, Any]) -> float:
    """Estimate TTS generation cost"""
    
    # Mock cost estimation - real implementation would use provider pricing
    provider_name = provider_config.get("provider", "")
    
    base_costs = {
        "openai": 0.015,    # Per 1K characters
        "elevenlabs": 0.30, # Per 1K characters
        "google": 0.016,    # Per 1K characters
        "yandex": 0.008     # Per 1K characters
    }
    
    return base_costs.get(provider_name, 0.01)


def _estimate_tts_duration(provider_config: Dict[str, Any]) -> float:
    """Estimate TTS generation duration"""
    
    # Mock duration estimation - real implementation would use historical metrics
    provider_name = provider_config.get("provider", "")
    
    base_durations = {
        "openai": 1.2,      # Seconds per request
        "elevenlabs": 2.0,  # Seconds per request
        "google": 1.5,      # Seconds per request
        "yandex": 1.8       # Seconds per request
    }
    
    return base_durations.get(provider_name, 1.5)


def _generate_decision_response(decision: VoiceResponseDecision, log_adapter) -> Dict[str, Any]:
    """Generate comprehensive decision response"""
    
    response = {
        "success": True,
        "voice_decision": {
            "decision_type": decision.decision_type.value,
            "should_generate_tts": decision.should_generate_tts,
            "confidence": decision.confidence,
            "reasoning": decision.reasoning,
            "metadata": decision.metadata
        }
    }
    
    if decision.should_generate_tts and decision.selected_provider:
        response["voice_decision"]["provider_info"] = {
            "selected_provider": decision.selected_provider,
            "provider_config": decision.provider_config,
            "fallback_providers": decision.fallback_providers,
            "estimated_cost": decision.estimated_cost,
            "estimated_duration": decision.estimated_duration
        }
    
    return response


def _generate_fallback_decision(response_text: str, state: Dict[str, Any]) -> VoiceResponseDecision:
    """Generate fallback decision when analysis fails"""
    
    return VoiceResponseDecision(
        decision_type=TTSDecisionType.SKIP_TTS,
        should_generate_tts=False,
        selected_provider=None,
        provider_config=None,
        confidence=0.1,
        reasoning="Analysis failed - defaulting to text response",
        metadata={"fallback": True, "error": "analysis_failed"},
        fallback_providers=[],
        estimated_cost=None,
        estimated_duration=None
    )
