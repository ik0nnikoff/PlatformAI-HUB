"""
Yandex STT Audio Processing Helper

Helper класс для обработки аудио данных для Yandex STT.
Extracted from yandex_stt.py для уменьшения размера файла.
"""

import asyncio
import logging
from pathlib import Path
from typing import Tuple

logger = logging.getLogger(__name__)


class YandexAudioProcessor:
    """
    Audio processing helper for Yandex STT.
    
    Handles audio format conversion and preparation for Yandex API.
    Single Responsibility: Audio format processing only.
    """

    def __init__(self):
        self.supported_formats = ["wav", "ogg", "opus", "mp3"]
        logger.debug("YandexAudioProcessor initialized")

    async def load_audio_file(self, audio_path: Path) -> bytes:
        """Load audio file from path"""
        try:
            async with asyncio.open_file(audio_path, "rb") as f:
                return await f.read()
        except Exception as e:
            logger.error(f"Failed to load audio file {audio_path}: {e}")
            raise

    async def process_audio_data(self, audio_data: bytes, audio_format: str) -> Tuple[bytes, str]:
        """
        Process audio data for Yandex STT API.
        
        Simplified processing logic with reduced complexity.
        """
        format_lower = audio_format.lower()
        
        # Direct passthrough for supported formats
        if format_lower in ["wav", "opus"]:
            return audio_data, format_lower
        
        # Convert OGG to WAV
        if format_lower == "ogg":
            return await self._convert_ogg_to_wav(audio_data, audio_format)
        
        # Convert MP3 to OPUS
        if format_lower == "mp3":
            return await self._convert_mp3_to_opus(audio_data)
        
        # Fallback to WAV conversion
        return await self._convert_to_wav(audio_data, audio_format)

    async def _convert_ogg_to_wav(self, audio_data: bytes, audio_format: str) -> Tuple[bytes, str]:
        """Convert OGG to WAV format"""
        try:
            import io
            from pydub import AudioSegment
            
            audio_io = io.BytesIO(audio_data)
            audio_segment = AudioSegment.from_ogg(audio_io)
            
            # Convert to WAV with optimal parameters for Yandex
            wav_io = io.BytesIO()
            audio_segment.export(
                wav_io,
                format="wav",
                parameters=["-ar", "16000", "-ac", "1", "-sample_fmt", "s16"]
            )
            
            converted_data = wav_io.getvalue()
            logger.debug(f"Converted OGG to WAV: {len(audio_data)} -> {len(converted_data)} bytes")
            
            return converted_data, "wav"
            
        except ImportError:
            logger.error("pydub not available for OGG conversion")
            raise RuntimeError("Audio conversion not available - pydub required")
        except Exception as e:
            logger.error(f"OGG to WAV conversion failed: {e}")
            raise

    async def _convert_mp3_to_opus(self, audio_data: bytes) -> Tuple[bytes, str]:
        """Convert MP3 to OPUS format"""
        try:
            import io
            from pydub import AudioSegment
            
            audio_io = io.BytesIO(audio_data)
            audio_segment = AudioSegment.from_mp3(audio_io)
            
            # Convert to OPUS
            opus_io = io.BytesIO()
            audio_segment.export(
                opus_io,
                format="opus",
                parameters=["-ar", "16000", "-ac", "1", "-b:a", "16k"]
            )
            
            converted_data = opus_io.getvalue()
            logger.debug(f"Converted MP3 to OPUS: {len(audio_data)} -> {len(converted_data)} bytes")
            
            return converted_data, "opus"
            
        except ImportError:
            logger.error("pydub not available for MP3 conversion")
            raise RuntimeError("Audio conversion not available - pydub required")
        except Exception as e:
            logger.error(f"MP3 to OPUS conversion failed: {e}")
            raise

    async def _convert_to_wav(self, audio_data: bytes, audio_format: str) -> Tuple[bytes, str]:
        """Generic conversion to WAV format"""
        try:
            import io
            from pydub import AudioSegment
            
            # Determine input format
            if audio_format.lower() == "mp3":
                audio_segment = AudioSegment.from_mp3(io.BytesIO(audio_data))
            elif audio_format.lower() == "ogg":
                audio_segment = AudioSegment.from_ogg(io.BytesIO(audio_data))
            else:
                # Try to auto-detect
                audio_segment = AudioSegment.from_file(io.BytesIO(audio_data))
            
            # Convert to WAV with Yandex-optimal parameters
            wav_io = io.BytesIO()
            audio_segment.export(
                wav_io,
                format="wav",
                parameters=["-ar", "16000", "-ac", "1", "-sample_fmt", "s16"]
            )
            
            converted_data = wav_io.getvalue()
            logger.debug(f"Converted {audio_format} to WAV: {len(audio_data)} -> {len(converted_data)} bytes")
            
            return converted_data, "wav"
            
        except ImportError:
            logger.error("pydub not available for audio conversion")
            raise RuntimeError("Audio conversion not available - pydub required")
        except Exception as e:
            logger.error(f"Audio conversion to WAV failed: {e}")
            raise

    def normalize_language(self, language: str) -> str:
        """Normalize language code for Yandex API"""
        # Language mapping for Yandex STT
        language_map = {
            "ru": "ru-RU",
            "en": "en-US", 
            "tr": "tr-TR",
            "uk": "uk-UA",
            "uz": "uz-UZ",
            "kk": "kk-KK",
            "de": "de-DE",
            "fr": "fr-FR",
            "es": "es-ES",
            "it": "it-IT",
            "he": "he-IL",
            "ar": "ar-AE"
        }
        
        # Handle full locale codes
        if "-" in language:
            return language
        
        # Map short codes to full locales
        return language_map.get(language.lower(), "ru-RU")  # Default to Russian

    def validate_audio_format(self, audio_format: str) -> bool:
        """Validate if audio format is supported"""
        return audio_format.lower() in self.supported_formats

    def get_optimal_format_for_size(self, audio_data: bytes) -> str:
        """Get optimal format based on audio data size"""
        # For large files, prefer OPUS for better compression
        if len(audio_data) > 5 * 1024 * 1024:  # 5MB
            return "opus"
        else:
            return "wav"  # For smaller files, WAV is simpler
