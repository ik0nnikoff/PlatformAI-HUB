"""
Voice orchestrator initialization for Telegram integration.

Provides voice processing capabilities through voice_v2 orchestrator.
Handles TTS/STT operations with proper dependency injection.
"""

import logging
from typing import Optional

from app.services.voice_v2.core.orchestrator import VoiceServiceOrchestrator
from app.services.voice_v2.providers.unified_factory import VoiceProviderFactory
from app.services.voice_v2.infrastructure.cache import VoiceCache
from app.services.voice_v2.infrastructure.minio_manager import MinioFileManager
from app.core.config import settings


class VoiceOrchestrator:
    """Manages voice processing orchestration for Telegram integration."""

    def __init__(self, logger: logging.LoggerAdapter):
        self.logger = logger
        self.orchestrator: Optional[VoiceServiceOrchestrator] = None

    async def initialize(self) -> bool:
        """Initialize voice orchestrator with voice_v2 architecture."""
        try:
            # Create voice_v2 dependencies following WhatsApp pattern
            voice_factory = VoiceProviderFactory()
            
            cache_manager = VoiceCache()
            await cache_manager.initialize()
            
            file_manager = MinioFileManager(
                endpoint=settings.MINIO_ENDPOINT,
                access_key=settings.MINIO_ACCESS_KEY,
                secret_key=settings.MINIO_SECRET_KEY,
                bucket_name=settings.MINIO_VOICE_BUCKET_NAME,
                secure=settings.MINIO_SECURE
            )
            await file_manager.initialize()

            # Create orchestrator instance with proper dependencies
            self.orchestrator = VoiceServiceOrchestrator(
                enhanced_factory=voice_factory,
                cache_manager=cache_manager,
                file_manager=file_manager,
            )

            await self.orchestrator.initialize()
            self.logger.info("Voice orchestrator initialized for Telegram integration")
            return True

        except (ImportError, AttributeError, ConnectionError) as e:
            self.logger.warning(f"Failed to initialize voice orchestrator: {e}")
            self.orchestrator = None
            return False

    async def cleanup(self) -> None:
        """Cleanup voice orchestrator."""
        if self.orchestrator:
            try:
                await self.orchestrator.cleanup()
                self.logger.info("Voice orchestrator cleaned up")
            except AttributeError as e:
                self.logger.error(f"Error cleaning up voice orchestrator: {e}")
            finally:
                self.orchestrator = None

    def get_orchestrator(self) -> Optional[VoiceServiceOrchestrator]:
        """Get the voice orchestrator instance."""
        return self.orchestrator

    def is_initialized(self) -> bool:
        """Check if orchestrator is initialized."""
        return self.orchestrator is not None
