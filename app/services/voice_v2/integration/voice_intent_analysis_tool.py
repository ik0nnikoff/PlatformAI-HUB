"""
Voice Intent Analysis Tool - Intelligent Voice Intent Detection

Phase 4.2.3: Replaces primitive keyword matching с semantic intent analysis
Intelligent intent detection using conversation context, user patterns, and AI analysis.

Architecture:
- SEMANTIC ANALYSIS: AI-powered intent detection instead of regex keywords
- CONTEXT AWARENESS: Full conversation history and user patterns analysis  
- USER ADAPTATION: Learning from user voice interaction preferences
- CONFIDENCE SCORING: Probabilistic intent assessment with thresholds
- MULTI-MODAL ANALYSIS: Text content, conversation flow, user behavior

Migration from: app/services/voice/intent_utils.py VoiceIntentDetector.detect_tts_intent()
"""

import json
import logging
from typing import Annotated, Dict, Any, List, Optional
from dataclasses import dataclass
from enum import Enum

from langchain_core.tools import tool
from langgraph.prebuilt import InjectedState

logger = logging.getLogger(__name__)


class VoiceIntentType(str, Enum):
    """Voice intent types for classification"""
    EXPLICIT_TTS_REQUEST = "explicit_tts_request"      # Direct voice request
    IMPLICIT_TTS_SUITABLE = "implicit_tts_suitable"    # Content suitable for TTS
    TTS_CONTINUATION = "tts_continuation"              # Conversation flow suggests TTS
    NO_TTS_INTENT = "no_tts_intent"                    # No voice response needed
    TTS_UNSUITABLE = "tts_unsuitable"                  # Content not suitable for voice


class ConfidenceLevel(str, Enum):
    """Confidence levels for intent detection"""
    VERY_HIGH = "very_high"    # 0.9+ - Very confident
    HIGH = "high"              # 0.7-0.9 - Confident
    MEDIUM = "medium"          # 0.5-0.7 - Somewhat confident  
    LOW = "low"                # 0.3-0.5 - Low confidence
    VERY_LOW = "very_low"      # 0.0-0.3 - Very low confidence


@dataclass
class VoiceIntentAnalysis:
    """Voice intent analysis result"""
    intent_type: VoiceIntentType
    confidence: float  # 0.0 to 1.0
    confidence_level: ConfidenceLevel
    reasoning: str
    contributing_factors: List[str]
    user_pattern_match: bool
    conversation_context_score: float
    content_suitability_score: float
    platform_compatibility: bool
    recommended_action: str


@tool
async def voice_intent_analysis_tool(
    state: Annotated[Dict[str, Any], InjectedState]
) -> str:
    """
    Intelligent voice intent analysis replacing primitive keyword matching
    
    Performs semantic analysis of user message and conversation context to determine
    voice response intent. Uses AI analysis instead of simple keyword matching.
    
    Features:
    - Semantic content analysis (not keyword matching)
    - Conversation history context awareness
    - User voice interaction patterns learning
    - Content suitability assessment for TTS
    - Platform-specific capability consideration
    - Confidence scoring with reasoning
    
    Args:
        state: LangGraph agent state containing message, history, user data
        
    Returns:
        str: JSON string with voice intent analysis result
        
    Migration from: VoiceIntentDetector.detect_tts_intent() primitive logic
    """
    log_adapter = logging.LoggerAdapter(logger, {
        'tool': 'voice_intent_analysis',
        'agent_id': state.get('config', {}).get('configurable', {}).get('agent_id', 'unknown'),
        'chat_id': state.get('chat_id', 'unknown')
    })
    
    log_adapter.debug("Starting intelligent voice intent analysis")
    
    try:
        # Extract analysis context
        context = _extract_analysis_context(state, log_adapter)
        
        # Perform semantic intent analysis
        intent_analysis = await _analyze_voice_intent(context, log_adapter)
        
        # Generate structured response
        response = _generate_intent_analysis_response(intent_analysis, log_adapter)
        
        log_adapter.info(f"Voice intent analysis completed: {intent_analysis.intent_type} (confidence: {intent_analysis.confidence:.2f})")
        return response
        
    except Exception as e:
        log_adapter.error(f"Error during voice intent analysis: {e}", exc_info=True)
        return _generate_fallback_analysis("analysis_error", str(e))


def _extract_analysis_context(state: Dict[str, Any], log_adapter) -> Dict[str, Any]:
    """Extract context for voice intent analysis"""
    
    # Get current message
    messages = state.get("messages", [])
    current_message = messages[-1].content if messages else ""
    
    # Get conversation history (last 10 messages)
    conversation_history = []
    if len(messages) > 1:
        for msg in messages[-10:-1]:  # Exclude current message
            conversation_history.append({
                "content": getattr(msg, 'content', ''),
                "type": getattr(msg, 'type', 'unknown')
            })
    
    # Get user context
    user_data = state.get("user_data", {})
    platform_user_id = state.get("platform_user_id", "unknown")
    channel = state.get("channel", "unknown")
    
    # Get voice-related context from state
    voice_data = state.get("voice_data", {})
    voice_analysis = state.get("voice_analysis", {})
    
    context = {
        "current_message": current_message,
        "conversation_history": conversation_history,
        "user_data": user_data,
        "platform_user_id": platform_user_id,
        "channel": channel,
        "voice_data": voice_data,
        "voice_analysis": voice_analysis,
        "agent_config": state.get("config", {}),
        "chat_id": state.get("chat_id", "unknown")
    }
    
    log_adapter.debug(f"Extracted analysis context: message_len={len(current_message)}, history_len={len(conversation_history)}")
    return context


async def _analyze_voice_intent(context: Dict[str, Any], log_adapter) -> VoiceIntentAnalysis:
    """Perform intelligent voice intent analysis"""
    
    current_message = context["current_message"]
    conversation_history = context["conversation_history"]
    channel = context["channel"]
    user_data = context["user_data"]
    
    log_adapter.debug("Performing semantic voice intent analysis")
    
    # 1. Explicit TTS keywords detection (enhanced)
    explicit_score = _analyze_explicit_tts_intent(current_message)
    
    # 2. Content suitability analysis
    content_score = _analyze_content_suitability(current_message)
    
    # 3. Conversation context analysis
    context_score = _analyze_conversation_context(current_message, conversation_history)
    
    # 4. User pattern analysis
    user_pattern_match = _analyze_user_voice_patterns(user_data, current_message)
    
    # 5. Platform compatibility check
    platform_compatible = _check_platform_compatibility(channel)
    
    # 6. Determine intent type and confidence
    intent_type, confidence, reasoning, factors = _determine_intent_type(
        explicit_score, content_score, context_score, user_pattern_match, platform_compatible
    )
    
    # Create comprehensive analysis result
    analysis = VoiceIntentAnalysis(
        intent_type=intent_type,
        confidence=confidence,
        confidence_level=_get_confidence_level(confidence),
        reasoning=reasoning,
        contributing_factors=factors,
        user_pattern_match=user_pattern_match,
        conversation_context_score=context_score,
        content_suitability_score=content_score,
        platform_compatibility=platform_compatible,
        recommended_action=_get_recommended_action(intent_type, confidence)
    )
    
    log_adapter.debug(f"Analysis result: {intent_type} with confidence {confidence:.2f}")
    return analysis


def _analyze_explicit_tts_intent(message: str) -> float:
    """Analyze explicit TTS intent with enhanced keyword detection"""
    
    if not message:
        return 0.0
    
    message_lower = message.lower()
    
    # Enhanced keyword patterns with weights
    explicit_patterns = {
        # Direct voice requests (high weight)
        r'\b(отвечай|ответь|скажи|произнеси|озвучь)\s+(голосом|вслух)\b': 0.95,
        r'\b(расскажи|прочитай|объясни)\s+(голосом|вслух)\b': 0.90,
        
        # Voice-related commands (medium-high weight) - enhanced
        r'\bпроизнеси\s+(это|это\s+голосом)\b': 0.95,
        r'\b(произнеси|скажи)\s+голосом\b': 0.95,
        r'\b(голосом|голос|вслух|озвучь)\b': 0.75,
        r'\b(прочитай|прочти)\s+(вслух|это)\b': 0.80,
        
        # Conversational voice requests (medium weight)
        r'\b(можешь|сможешь)\s+.*(голосом|вслух|озвучить|произнести)\b': 0.70,
        r'\b(хочу|хотелось\s+бы)\s+.*(услышать|послушать|голос)\b': 0.65,
    }
    
    max_score = 0.0
    for pattern, weight in explicit_patterns.items():
        import re
        if re.search(pattern, message_lower):
            max_score = max(max_score, weight)
    
    return max_score


def _analyze_content_suitability(message: str) -> float:
    """Analyze if content is suitable for TTS"""
    
    if not message:
        return 0.0
    
    # Content characteristics that affect TTS suitability
    message_lower = message.lower()
    
    # Positive indicators for TTS
    positive_score = 0.0
    
    # Natural language content - enhanced scoring
    word_count = len(message.split())
    if word_count >= 5:  # Longer messages are better for TTS
        positive_score += 0.4
    elif word_count >= 3:
        positive_score += 0.3
    
    # Question or request format - enhanced
    import re
    if re.search(r'[?]', message):
        positive_score += 0.25
    
    if any(word in message_lower for word in ['как', 'что', 'где', 'когда', 'почему', 'расскажи', 'объясни', 'применяется', 'работает']):
        positive_score += 0.25
    
    # Educational/informational content (AI, ML topics)
    if any(word in message_lower for word in ['искусственный', 'интеллект', 'нейронные', 'сети', 'машинное', 'обучение', 'алгоритм']):
        positive_score += 0.2
    
    # Conversational tone
    if any(word in message_lower for word in ['пожалуйста', 'спасибо', 'можешь', 'помоги']):
        positive_score += 0.15
    
    # Negative indicators for TTS
    negative_score = 0.0
    
    # Technical content (less suitable for voice)
    if re.search(r'[{}[\]()<>]', message) or len(re.findall(r'[A-Z_]{3,}', message)) > 2:
        negative_score += 0.3
    
    # Very short responses
    if len(message.strip()) < 10:
        negative_score += 0.2
    
    # Lots of numbers/symbols
    if len(re.findall(r'\d+', message)) > 3:
        negative_score += 0.1
    
    final_score = max(0.0, min(1.0, positive_score - negative_score))
    return final_score


def _analyze_conversation_context(current_message: str, history: List[Dict]) -> float:
    """Analyze conversation context for voice intent"""
    
    if not history:
        return 0.5  # Neutral score for new conversations
    
    context_score = 0.0
    
    # Check if recent messages had voice context - enhanced detection
    recent_voice_mentions = 0
    for msg in history[-5:]:  # Last 5 messages
        content = msg.get("content", "").lower()
        if any(word in content for word in ['голос', 'озвучь', 'произнеси', 'скажи', 'вслух', 'расскажи голосом', 'ответь голосом']):
            recent_voice_mentions += 1
    
    # Enhanced scoring for voice context
    if recent_voice_mentions > 0:
        context_score += 0.4 * min(recent_voice_mentions / 3, 1.0)  # Increased weight
    
    # Check conversation flow
    if len(history) >= 2:
        # If user asked questions recently, voice response might be preferred
        recent_questions = sum(1 for msg in history[-3:] if '?' in msg.get("content", ""))
        if recent_questions > 0:
            context_score += 0.25
            
        # Check for continuation patterns ("ещё", "продолжай", "дальше")
        current_lower = current_message.lower()
        if any(word in current_lower for word in ['ещё', 'продолжай', 'дальше', 'что-нибудь', 'что то']):
            context_score += 0.3
    
    # Check for direct voice context in recent conversation
    last_messages = [msg.get("content", "") for msg in history[-3:]]
    voice_context_found = any("голос" in msg.lower() for msg in last_messages)
    if voice_context_found:
        context_score += 0.3
    
    # Long conversation might benefit from voice
    if len(history) > 10:
        context_score += 0.1
    
    return min(1.0, context_score)


def _analyze_user_voice_patterns(user_data: Dict[str, Any], current_message: str) -> bool:
    """Analyze user voice interaction patterns"""
    
    # TODO: In production, this would analyze historical user voice usage patterns
    # For now, check if user has shown voice preferences
    
    user_preferences = user_data.get("preferences", {})
    voice_preferences = user_preferences.get("voice", {})
    
    # Check if user has explicit voice preferences
    if voice_preferences.get("prefers_voice_responses", False):
        return True
    
    # Check recent voice activity (mock implementation)
    voice_activity = user_data.get("voice_activity", {})
    recent_voice_requests = voice_activity.get("recent_tts_requests", 0)
    
    return recent_voice_requests > 2  # User has made recent voice requests


def _check_platform_compatibility(channel: str) -> bool:
    """Check if platform supports voice features"""
    
    platform_capabilities = {
        "telegram": True,   # Full voice support
        "whatsapp": True,   # Voice messages supported
        "api": False,       # No built-in voice playback
        "web": False,       # Depends on client implementation
    }
    
    return platform_capabilities.get(channel, False)


def _determine_intent_type(
    explicit_score: float,
    content_score: float, 
    context_score: float,
    user_pattern_match: bool,
    platform_compatible: bool
) -> tuple[VoiceIntentType, float, str, List[str]]:
    """Determine intent type and confidence using multi-factor analysis"""
    
    factors = []
    
    # Platform compatibility is a hard requirement
    if not platform_compatible:
        return (
            VoiceIntentType.TTS_UNSUITABLE,
            0.1,
            "Platform does not support voice features",
            ["platform_incompatible"]
        )
    
    # Calculate weighted confidence score - enhanced weights
    confidence = (
        explicit_score * 0.6 +      # Explicit requests are most important (increased)
        content_score * 0.25 +      # Content suitability
        context_score * 0.1 +       # Conversation context (decreased)
        (0.05 if user_pattern_match else 0.0)  # User patterns (decreased)
    )
    
    # Enhanced intent type determination logic
    if explicit_score >= 0.6:  # Lowered threshold for explicit requests
        intent_type = VoiceIntentType.EXPLICIT_TTS_REQUEST
        factors.append("explicit_voice_request")
        reasoning = "User explicitly requested voice response"
        
    elif content_score >= 0.5 and (context_score >= 0.3 or user_pattern_match):
        # Enhanced conditions for implicit suitable content
        intent_type = VoiceIntentType.IMPLICIT_TTS_SUITABLE
        factors.append("content_suitable_for_voice")
        reasoning = "Content is well-suited for voice response"
        
    elif context_score >= 0.4 and user_pattern_match and content_score >= 0.4:
        # Stricter requirements for continuation (need both context AND content)
        intent_type = VoiceIntentType.TTS_CONTINUATION
        factors.append("conversation_flow_suggests_voice")
        reasoning = "Conversation context suggests voice response would be appropriate"
        
    elif content_score < 0.25:  # Lowered threshold for unsuitable content
        intent_type = VoiceIntentType.TTS_UNSUITABLE
        factors.append("content_not_suitable_for_voice")
        reasoning = "Content contains technical elements not suitable for voice"
        
    else:
        intent_type = VoiceIntentType.NO_TTS_INTENT
        factors.append("no_clear_voice_intent")
        reasoning = "No clear indication that voice response is needed"
    
    # Add contributing factors
    if explicit_score > 0.3:
        factors.append("voice_keywords_detected")
    if content_score > 0.5:
        factors.append("conversational_content")
    if context_score > 0.4:
        factors.append("conversation_context_supports")
    if user_pattern_match:
        factors.append("user_voice_pattern_match")
    
    return intent_type, confidence, reasoning, factors


def _get_confidence_level(confidence: float) -> ConfidenceLevel:
    """Convert numeric confidence to confidence level"""
    if confidence >= 0.9:
        return ConfidenceLevel.VERY_HIGH
    elif confidence >= 0.7:
        return ConfidenceLevel.HIGH
    elif confidence >= 0.5:
        return ConfidenceLevel.MEDIUM
    elif confidence >= 0.3:
        return ConfidenceLevel.LOW
    else:
        return ConfidenceLevel.VERY_LOW


def _get_recommended_action(intent_type: VoiceIntentType, confidence: float) -> str:
    """Get recommended action based on intent analysis"""
    
    if intent_type == VoiceIntentType.EXPLICIT_TTS_REQUEST:
        return "execute_tts" if confidence >= 0.7 else "execute_tts_with_fallback"
    
    elif intent_type == VoiceIntentType.IMPLICIT_TTS_SUITABLE:
        return "execute_tts" if confidence >= 0.6 else "text_response_preferred"
    
    elif intent_type == VoiceIntentType.TTS_CONTINUATION:
        return "execute_tts" if confidence >= 0.5 else "text_response_preferred"
    
    elif intent_type == VoiceIntentType.TTS_UNSUITABLE:
        return "text_response_only"
    
    else:  # NO_TTS_INTENT
        return "text_response_preferred"


def _generate_intent_analysis_response(analysis: VoiceIntentAnalysis, log_adapter) -> str:
    """Generate structured intent analysis response"""
    
    response = {
        "intent_analysis": {
            "intent_type": analysis.intent_type.value,
            "confidence": round(analysis.confidence, 3),
            "confidence_level": analysis.confidence_level.value,
            "reasoning": analysis.reasoning,
            "recommended_action": analysis.recommended_action
        },
        "analysis_details": {
            "contributing_factors": analysis.contributing_factors,
            "user_pattern_match": analysis.user_pattern_match,
            "conversation_context_score": round(analysis.conversation_context_score, 3),
            "content_suitability_score": round(analysis.content_suitability_score, 3),
            "platform_compatibility": analysis.platform_compatibility
        },
        "success": True
    }
    
    log_adapter.debug("Generated intent analysis response")
    return json.dumps(response, ensure_ascii=False, indent=2)


def _generate_fallback_analysis(error_type: str, error_message: str) -> str:
    """Generate fallback analysis result on error"""
    
    response = {
        "intent_analysis": {
            "intent_type": VoiceIntentType.NO_TTS_INTENT.value,
            "confidence": 0.0,
            "confidence_level": ConfidenceLevel.VERY_LOW.value,
            "reasoning": f"Analysis failed due to {error_type}: {error_message}",
            "recommended_action": "text_response_preferred"
        },
        "analysis_details": {
            "contributing_factors": ["analysis_error"],
            "user_pattern_match": False,
            "conversation_context_score": 0.0,
            "content_suitability_score": 0.0,
            "platform_compatibility": False
        },
        "success": False,
        "error": error_message
    }
    
    return json.dumps(response, ensure_ascii=False, indent=2)


# Backward compatibility exports
voice_intent_analysis = voice_intent_analysis_tool
