"""
WhatsApp Integration Main Entry Point для PlatformAI-HUB

Запуск интеграции WhatsApp для конкретного агента.
Использует аргументы командной строки для настройки.
"""

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path
from typing import Any, Dict

from app.core.config import settings
from app.core.logging_config import setup_logging
from app.db.session import get_async_session_factory, close_db_engine

# Добавляем корневую директорию проекта в Python path
project_root = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(project_root))


def setup_logging_for_agent(agent_id: str) -> logging.LoggerAdapter:
    """Настройка логирования для агента"""
    module_logger = logging.getLogger(f"WHATSAPP_BOT:{agent_id}")
    adapter = logging.LoggerAdapter(module_logger, {"agent_id": agent_id})
    return adapter


def parse_integration_settings(settings_str: str) -> Dict[str, Any]:
    """
    Parse integration settings from JSON string.
    
    Args:
        settings_str: JSON string with WhatsApp integration settings
        
    Returns:
        Parsed settings dictionary
        
    Raises:
        ValueError: If settings cannot be parsed or are invalid
    """
    try:
        settings = json.loads(settings_str)
        
        # Validate required fields
        if not isinstance(settings, dict):
            raise ValueError("Settings must be a dictionary")
            
        session_name = settings.get("sessionName")
        if not session_name:
            raise ValueError("sessionName is required in integration settings")
            
        return settings
        
    except json.JSONDecodeError as e:
        raise ValueError(f"Invalid JSON in integration settings: {e}")


async def main_async_runner(
    agent_id: str, integration_settings: str, logger: logging.LoggerAdapter
) -> None:
    """Main asynchronous runner for the WhatsApp integration."""
    logger.info(f"Starting WhatsApp integration for Agent ID: {agent_id}")

    db_session_factory = None
    if settings.DATABASE_URL:
        try:
            db_session_factory = get_async_session_factory()
            logger.info("Database session factory configured for WhatsApp bot.")
        except Exception as e_db_setup:
            logger.error(f"Failed to setup database session factory: {e_db_setup}", exc_info=True)
    else:
        logger.warning("DATABASE_URL not set. Database features will be unavailable.")

    # Parse integration settings
    try:
        integration_settings_data = json.loads(integration_settings)
        if isinstance(integration_settings_data, dict):
            session_name = integration_settings_data.get("sessionName") or integration_settings_data.get("session_name", "")
            token = integration_settings_data.get("token", "")
        else:
            logger.error(
                f"Parsed --integration-settings is not a dictionary: {type(integration_settings_data)}. "
                f"Settings: '{integration_settings}'"
            )
            return
    except json.JSONDecodeError as e:
        logger.error(
            f"Failed to parse --integration-settings JSON: {e}. Settings: '{integration_settings}'",
            exc_info=True
        )
        return
    except Exception as e_parse:
        logger.error(
            f"Unexpected error parsing --integration-settings: {e_parse}. Settings: '{integration_settings}'",
            exc_info=True
        )
        return

    if not session_name:
        logger.critical("CRITICAL: WhatsApp session_name not found in --integration-settings. Bot cannot start.")
        sys.exit(1)

    # Import the bot after ensuring all dependencies are available
    from .whatsapp_bot import WhatsAppIntegrationBot

    # Prepare bot configuration
    bot_config = {
        "agent_id": agent_id,
        "session_name": session_name,
        "token": token,
        "db_session_factory": db_session_factory,
    }

    whatsapp_bot = WhatsAppIntegrationBot(bot_config, logger)

    try:
        while True:
            logger.info(f"Setting up and running WhatsApp integration bot for agent {agent_id}...")
            await whatsapp_bot.run()

            if hasattr(whatsapp_bot, 'needs_restart') and whatsapp_bot.needs_restart:
                logger.info(f"WhatsApp bot for {agent_id} requested restart. Re-initializing for another run cycle...")
                whatsapp_bot = WhatsAppIntegrationBot(bot_config, logger)
            else:
                logger.info(f"WhatsAppIntegrationBot for {agent_id} finished execution or was shut down without a restart request.")
                break
    except (KeyboardInterrupt, SystemExit):
        logger.info(f"WhatsApp bot {agent_id} process interrupted or exited.")
    except Exception as e:
        logger.critical(f"Unhandled exception in main_async_runner for WhatsApp bot {agent_id}: {e}", exc_info=True)
    finally:
        if settings.DATABASE_URL and db_session_factory:
            logger.info(f"Shutting down WhatsApp bot runner for {agent_id}.")
            try:
                await close_db_engine()
                logger.info("Database engine closed.")
            except Exception as e_db_close:
                logger.error(f"Error closing database engine: {e_db_close}", exc_info=True)
        logger.info(f"WhatsApp bot runner for {agent_id} has been shut down.")


if __name__ == "__main__":
    setup_logging()

    parser = argparse.ArgumentParser(
        description="WhatsApp Integration for PlatformAI-HUB"
    )
    parser.add_argument(
        "--agent-id", required=True, help="Unique ID of the agent to run"
    )
    parser.add_argument(
        "--integration-settings",
        type=str,
        required=True,
        help=(
            "JSON string with integration-specific settings "
            "(must include 'sessionName')"
        ),
    )
    args = parser.parse_args()

    logger_adapter = setup_logging_for_agent(args.agent_id)

    try:
        asyncio.run(
            main_async_runner(args.agent_id, args.integration_settings, logger_adapter)
        )
    except KeyboardInterrupt:
        logger_adapter.info(
            "WhatsApp bot runner main received KeyboardInterrupt. Exiting."
        )
    except (RuntimeError, OSError, ValueError) as e:
        logger_adapter.critical(
            f"Critical error in WhatsApp bot runner main: {e}", exc_info=True
        )
        sys.exit(1)
    finally:
        logger_adapter.info("WhatsApp bot runner main finished.")
