"""
LangGraph Native TTS Tool - Phase 4.8.2 Implementation

This tool follows LangGraph best practices for LLM autonomous decision making.
Replaces 1200+ lines of anti-pattern forced tool chains with ~50 lines of elegant solution.

Based on:
- MD/Reports/LangGraph_Voice_Intent_Decision_Patterns_Analysis.md
- MD/Reports/Voice_v2_Architecture_Deep_Analysis_Report.md
"""

import logging
from typing import Optional, Dict, Any

from langchain_core.tools import tool
from pydantic import BaseModel, Field

from app.core.config import settings
from app.services.voice_v2.core.orchestrator.base_orchestrator import VoiceServiceOrchestrator

logger = logging.getLogger(__name__)
# settings already imported and available


class VoiceSettings(BaseModel):
    """Voice generation settings."""
    voice_id: Optional[str] = Field(default=None, description="Specific voice ID to use")
    speed: Optional[float] = Field(default=1.0, description="Speech speed (0.5-2.0)")
    stability: Optional[float] = Field(default=0.7, description="Voice stability (0.0-1.0)")


@tool
def generate_voice_response(
    text: str,
    voice_settings: Optional[Dict[str, Any]] = None
) -> str:
    """
    Generate voice response when appropriate for user interaction.
    
    LLM should use this tool when:
    - User explicitly requests voice response
    - Content is suitable for audio (questions, explanations, stories, conversations)
    - Context suggests voice would enhance user experience
    - Response is interactive or personal in nature
    
    LLM should avoid for:
    - Code snippets, tables, complex formatting, structured data
    - Very long texts (>500 words) - break into chunks instead
    - Technical documentation, API references
    - JSON/XML/CSV or other structured formats
    - Mathematical formulas or equations
    
    Args:
        text: The text to convert to speech (max 500 words recommended)
        voice_settings: Optional voice configuration (voice_id, speed, stability)
    
    Returns:
        String with voice response status and audio URL, or error message
    """
    try:
        # Validate input
        if not text or not text.strip():
            return "❌ Cannot generate voice: Empty text provided"
        
        if len(text) > 2000:  # Reasonable limit
            return f"❌ Text too long for voice generation ({len(text)} chars). Please break into smaller chunks (max 2000 chars)."
        
        # Initialize voice orchestrator
        orchestrator = VoiceServiceOrchestrator()
        
        # Parse voice settings if provided
        voice_id = None
        speed = 1.0
        if voice_settings:
            voice_id = voice_settings.get('voice_id')
            speed = voice_settings.get('speed', 1.0)
        
        # Create TTS request
        from app.services.voice_v2.core.schemas import TTSRequest
        import asyncio
        
        tts_request = TTSRequest(
            text=text,
            voice=voice_id,
            speed=speed
        )
        
        # Run async synthesize_speech
        async def synthesize():
            return await orchestrator.synthesize_speech(tts_request)
        
        # Execute async call
        audio_result = asyncio.run(synthesize())
        
        if audio_result and audio_result.get('success'):
            audio_url = audio_result.get('audio_url')
            duration = audio_result.get('duration', 'unknown')
            
            return f"🎤 Voice response generated successfully!\n" \
                   f"📎 Audio URL: {audio_url}\n" \
                   f"⏱️ Duration: {duration} seconds\n" \
                   f"📝 Text: {text[:100]}{'...' if len(text) > 100 else ''}"
        else:
            error_msg = audio_result.get('error', 'Unknown error') if audio_result else 'No result returned'
            return f"❌ Voice generation failed: {error_msg}"
            
    except Exception as e:
        logger.error(f"TTS tool error: {e}", exc_info=True)
        return f"❌ Voice generation error: {str(e)}"


# Export for registry
__all__ = ['generate_voice_response']
