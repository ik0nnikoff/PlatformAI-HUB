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
from typing import Any, Dict, Optional

from app.core.config import settings
from app.core.logging_config import setup_logging
from app.db.session import close_db_engine, get_async_session_factory

# Добавляем корневую директорию проекта в Python path
project_root = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(project_root))


def setup_logging_for_agent(agent_id: str) -> logging.LoggerAdapter:
    """Настройка логирования для агента"""
    module_logger = logging.getLogger(f"WHATSAPP_BOT:{agent_id}")
    adapter = logging.LoggerAdapter(module_logger, {"agent_id": agent_id})
    return adapter


async def _setup_database_session(logger: logging.LoggerAdapter):
    """Setup database session factory with error handling."""
    if not settings.DATABASE_URL:
        logger.warning("DATABASE_URL not set. Database features will be unavailable.")
        return None

    try:
        db_session_factory = get_async_session_factory()
        logger.info("Database session factory configured for WhatsApp bot.")
        return db_session_factory
    except Exception as e_db_setup:  # pylint: disable=broad-exception-caught
        logger.error(
            "Failed to setup database session factory: %s", e_db_setup, exc_info=True
        )
        return None


async def _parse_integration_settings(
    integration_settings: str, logger: logging.LoggerAdapter
) -> tuple[Optional[str], Optional[str]]:
    """Parse integration settings and extract session_name and token."""
    try:
        integration_settings_data = json.loads(integration_settings)
        if not isinstance(integration_settings_data, dict):
            logger.error(
                "Parsed --integration-settings is not a dictionary: %s. "
                "Settings: '%s'",
                type(integration_settings_data),
                integration_settings,
            )
            return None, None

        session_name = integration_settings_data.get(
            "sessionName"
        ) or integration_settings_data.get("session_name", "")
        token = integration_settings_data.get("token", "")

        return session_name, token

    except json.JSONDecodeError as e:
        logger.error(
            "Failed to parse --integration-settings JSON: %s. Settings: '%s'",
            e,
            integration_settings,
            exc_info=True,
        )
        return None, None
    except Exception as e_parse:  # pylint: disable=broad-exception-caught
        logger.error(
            "Unexpected error parsing --integration-settings: %s. Settings: '%s'",
            e_parse,
            integration_settings,
            exc_info=True,
        )
        return None, None


async def _run_bot_loop(
    whatsapp_bot,
    agent_id: str,
    bot_config: Dict[str, Any],
    logger: logging.LoggerAdapter,
) -> None:
    """Run the main bot loop with restart handling."""
    current_bot = whatsapp_bot

    while True:
        logger.info(
            "Setting up and running WhatsApp integration bot for agent %s...", agent_id
        )
        await current_bot.run()

        if hasattr(current_bot, "needs_restart") and current_bot.needs_restart:
            logger.info(
                "WhatsApp bot for %s requested restart. Re-initializing for another run cycle...",
                agent_id,
            )
            # Import inside function to avoid circular imports
            from .whatsapp_bot import \
                WhatsAppIntegrationBot  # pylint: disable=import-outside-toplevel

            current_bot = WhatsAppIntegrationBot(bot_config, logger)
        else:
            logger.info(
                "WhatsAppIntegrationBot for %s finished execution or was shut down "
                "without a restart request.",
                agent_id,
            )
            break


async def _cleanup_resources(
    db_session_factory, agent_id: str, logger: logging.LoggerAdapter
) -> None:
    """Cleanup database and other resources."""
    if settings.DATABASE_URL and db_session_factory:
        logger.info("Shutting down WhatsApp bot runner for %s.", agent_id)
        try:
            await close_db_engine()
            logger.info("Database engine closed.")
        except Exception as e_db_close:  # pylint: disable=broad-exception-caught
            logger.error("Error closing database engine: %s", e_db_close, exc_info=True)
    logger.info("WhatsApp bot runner for %s has been shut down.", agent_id)


async def main_async_runner(
    agent_id: str, integration_settings: str, logger: logging.LoggerAdapter
) -> None:
    """Main asynchronous runner for the WhatsApp integration."""
    logger.info("Starting WhatsApp integration for Agent ID: %s", agent_id)

    # Setup database session
    db_session_factory = await _setup_database_session(logger)

    # Parse integration settings
    session_name, token = await _parse_integration_settings(
        integration_settings, logger
    )
    if not session_name:
        logger.critical(
            "CRITICAL: WhatsApp session_name not found in --integration-settings. "
            "Bot cannot start."
        )
        sys.exit(1)

    # Import the bot after ensuring all dependencies are available
    from .whatsapp_bot import \
        WhatsAppIntegrationBot  # pylint: disable=import-outside-toplevel

    # Prepare bot configuration
    bot_config = {
        "agent_id": agent_id,
        "session_name": session_name,
        "token": token,
        "db_session_factory": db_session_factory,
    }

    whatsapp_bot = WhatsAppIntegrationBot(bot_config, logger)

    try:
        await _run_bot_loop(whatsapp_bot, agent_id, bot_config, logger)
    except (KeyboardInterrupt, SystemExit):
        logger.info("WhatsApp bot %s process interrupted or exited.", agent_id)
    except Exception as e:  # pylint: disable=broad-exception-caught
        logger.critical(
            "Unhandled exception in main_async_runner for WhatsApp bot %s: %s",
            agent_id,
            e,
            exc_info=True,
        )
    finally:
        await _cleanup_resources(db_session_factory, agent_id, logger)


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
            "Critical error in WhatsApp bot runner main: %s", e, exc_info=True
        )
        sys.exit(1)
    finally:
        logger_adapter.info("WhatsApp bot runner main finished.")
