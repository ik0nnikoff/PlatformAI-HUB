"""
Voice V2 Tools Package

LangGraph native tools following best practices for voice functionality.
Replaces anti-pattern forced tool chains with elegant LLM autonomous decision making.
"""

from .tts_tool import generate_voice_response

__all__ = [
    'generate_voice_response',
]
