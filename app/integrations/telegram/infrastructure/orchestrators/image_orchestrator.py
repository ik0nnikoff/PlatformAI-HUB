"""
Image orchestrator for Telegram integration infrastructure.

Handles image processing orchestration.
"""

import logging
from typing import Optional

from app.services.media.image_orchestrator import \
    ImageOrchestrator as MediaImageOrchestrator


class ImageOrchestrator:
    """Manages image processing orchestration for Telegram integration."""

    def __init__(self, logger: logging.LoggerAdapter):
        self.logger = logger
        self.orchestrator: Optional[MediaImageOrchestrator] = None

    async def initialize(self) -> bool:
        """Initialize image orchestrator."""
        try:
            self.orchestrator = MediaImageOrchestrator()
            await self.orchestrator.initialize()
            self.logger.info("Image orchestrator initialized for Telegram integration")
            return True

        except (ImportError, AttributeError, ConnectionError) as e:
            self.logger.warning("Failed to initialize image orchestrator: %s", e)
            self.orchestrator = None
            return False

    async def cleanup(self) -> None:
        """Cleanup image orchestrator."""
        if self.orchestrator:
            try:
                self.logger.info("Image orchestrator cleaned up")
            except AttributeError as e:
                self.logger.error("Error cleaning up image orchestrator: %s", e)
            finally:
                self.orchestrator = None

    def get_orchestrator(self) -> Optional[MediaImageOrchestrator]:
        """Get the image orchestrator instance."""
        return self.orchestrator
