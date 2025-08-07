"""
Voice orchestrator for WhatsApp integration infrastructure.

Handles voice processing orchestration with voice_v2 architecture.
"""

import logging
from typing import Optional

from app.core.config import settings
from app.services.voice_v2.core.orchestrator import VoiceServiceOrchestrator
from app.services.voice_v2.infrastructure.cache import VoiceCache
from app.services.voice_v2.infrastructure.minio_manager import MinioFileManager
from app.services.voice_v2.providers.unified_factory import \
    VoiceProviderFactory


class VoiceOrchestrator:
    """Manages voice processing orchestration for WhatsApp integration."""

    def __init__(self, logger: logging.LoggerAdapter):
        self.logger = logger
        self.orchestrator: Optional[VoiceServiceOrchestrator] = None

    async def initialize(self) -> bool:
        """Initialize voice orchestrator with voice_v2 architecture."""
        try:
            # Initialize components with enhanced voice_v2 architecture
            voice_factory = VoiceProviderFactory()
            cache_manager = VoiceCache()
            await cache_manager.initialize()

            file_manager = MinioFileManager(
                endpoint=settings.MINIO_ENDPOINT,
                access_key=settings.MINIO_ACCESS_KEY,
                secret_key=settings.MINIO_SECRET_KEY,
                bucket_name=settings.MINIO_VOICE_BUCKET_NAME,
                secure=settings.MINIO_SECURE,
            )
            await file_manager.initialize()

            self.orchestrator = VoiceServiceOrchestrator(
                enhanced_factory=voice_factory,
                cache_manager=cache_manager,
                file_manager=file_manager,
            )
            await self.orchestrator.initialize()
            self.logger.info(
                "Voice orchestrator v2 initialized for WhatsApp integration"
            )
            return True

        except (ImportError, AttributeError, ConnectionError) as e:
            self.logger.warning("Failed to initialize voice orchestrator v2: %s", e)
            self.orchestrator = None
            return False

    async def cleanup(self) -> None:
        """Cleanup voice orchestrator."""
        if self.orchestrator:
            try:
                await self.orchestrator.cleanup()
                self.logger.info("Voice orchestrator cleaned up")
            except (AttributeError, ConnectionError) as e:
                self.logger.error("Error cleaning up voice orchestrator: %s", e)
            finally:
                self.orchestrator = None

    def get_orchestrator(self) -> Optional[VoiceServiceOrchestrator]:
        """Get the voice orchestrator instance."""
        return self.orchestrator
