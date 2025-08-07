"""
Specialized processors for WhatsApp integration.

Contains focused processors for different message types:
- Text message processing
- Voice message processing
- Image message processing
"""

from .image_processor import ImageProcessor
from .text_processor import TextProcessor
from .voice_processor import VoiceProcessor

__all__ = [
    "TextProcessor",
    "VoiceProcessor",
    "ImageProcessor",
]
