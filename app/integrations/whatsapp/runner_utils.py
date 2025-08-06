"""
Runner utilities for WhatsApp integration main process.

Handles setup, lifecycle management and cleanup operations
to reduce complexity in main runner function.
"""

import logging
import json
from typing import Optional, Tuple
from sqlalchemy.ext.asyncio import async_sessionmaker, AsyncSession

from app.core.config import settings
from app.db.session import get_async_session_factory, close_db_engine

from .whatsapp_bot import WhatsAppIntegrationBot


class WhatsAppRunner:
    """Manages WhatsApp integration lifecycle with reduced complexity."""

    def __init__(self, agent_id: str, logger: logging.LoggerAdapter):
        self.agent_id = agent_id
        self.logger = logger
        self.db_session_factory: Optional[async_sessionmaker[AsyncSession]] = None

    async def setup_database(self) -> None:
        """Setup database session factory with error handling."""
        if settings.DATABASE_URL:
            try:
                self.db_session_factory = get_async_session_factory()
                self.logger.info("Database session factory configured for WhatsApp bot.")
            except (ImportError, ConnectionError, OSError) as e_db_setup:
                self.logger.error(
                    f"Failed to setup database session factory: {e_db_setup}", exc_info=True
                )
        else:
            self.logger.warning("DATABASE_URL not set. Database features will be unavailable.")

    def extract_whatsapp_settings(self, integration_settings: str) -> Tuple[str, str]:
        """Extract WhatsApp settings from integration settings."""
        try:
            settings_data = json.loads(integration_settings)
            session_name = settings_data.get("session_name", "default")
            token = settings_data.get("token", "")

            self.logger.info(f"WhatsApp settings: session_name={session_name}")
            return session_name, token
        except (json.JSONDecodeError, KeyError, ValueError) as e:
            self.logger.error(f"Error parsing WhatsApp settings: {e}")
            return "default", ""

    def create_bot_instance(self, session_name: str, token: str) -> "WhatsAppIntegrationBot":
        """Create new WhatsApp bot instance."""
        return WhatsAppIntegrationBot(
            agent_id=self.agent_id,
            session_name=session_name,
            token=token,
            db_session_factory=self.db_session_factory,
            logger_adapter=self.logger
        )

    async def run_bot_lifecycle(self, whatsapp_bot: "WhatsAppIntegrationBot") -> bool:
        """
        Run bot and handle restart requests.

        Returns:
            True if restart is needed, False if should stop
        """
        self.logger.info(f"Calling WhatsAppIntegrationBot.run() for agent {self.agent_id}...")
        await whatsapp_bot.run()

        if hasattr(whatsapp_bot, 'needs_restart') and whatsapp_bot.needs_restart:
            self.logger.info(
                f"WhatsApp bot for {self.agent_id} requested restart. "
                "Re-initializing for another run cycle..."
            )
            return True
        
        self.logger.info(
            f"WhatsAppIntegrationBot for {self.agent_id} finished execution "
            "or was shut down without a restart request."
        )
        return False

    async def cleanup_database(self) -> None:
        """Cleanup database resources."""
        if settings.DATABASE_URL and self.db_session_factory:
            self.logger.info(f"Shutting down WhatsApp bot runner for {self.agent_id}.")
            try:
                await close_db_engine()
                self.logger.info("Database engine closed.")
            except (ConnectionError, OSError, RuntimeError) as e_db_close:
                self.logger.error(f"Error closing database engine: {e_db_close}", exc_info=True)

    async def run_integration(self, integration_settings: str) -> None:
        """
        Run complete WhatsApp integration lifecycle with reduced complexity.

        Args:
            integration_settings: Integration configuration string
        """
        # Setup phase
        await self.setup_database()
        session_name, token = self.extract_whatsapp_settings(integration_settings)

        # Main execution loop
        whatsapp_bot = self.create_bot_instance(session_name, token)

        try:
            while True:
                needs_restart = await self.run_bot_lifecycle(whatsapp_bot)

                if needs_restart:
                    # Create new bot instance for restart
                    whatsapp_bot = self.create_bot_instance(session_name, token)
                else:
                    # Normal termination
                    break

        except (KeyboardInterrupt, SystemExit):
            self.logger.info(f"WhatsApp bot {self.agent_id} process interrupted or exited.")
        except (RuntimeError, ConnectionError, OSError) as e:
            self.logger.critical(
                f"Unhandled exception in main_async_runner for WhatsApp bot {self.agent_id}: {e}",
                exc_info=True
            )
        finally:
            await self.cleanup_database()
            self.logger.info(f"WhatsApp bot runner for {self.agent_id} has been shut down.")
