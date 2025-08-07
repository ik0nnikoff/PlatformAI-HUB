"""
Telegram Bot Integration Main Runner.

This module provides the main entry point for running Telegram bot integrations
for PlatformAI agents. It handles bot initialization, configuration parsing,
and lifecycle management with proper error handling and cleanup.
"""

import asyncio
import logging
import argparse
import json
import sys

from app.core.logging_config import setup_logging
from app.core.config import settings
from app.db.session import get_async_session_factory, close_db_engine
from app.integrations.telegram.telegram_bot import TelegramIntegrationBot


def setup_logging_for_agent(agent_id: str) -> logging.LoggerAdapter:
    """Настройка логирования для Telegram бота"""
    module_logger = logging.getLogger(f"TELEGRAM_BOT:{agent_id}")
    adapter = logging.LoggerAdapter(module_logger, {"agent_id": agent_id})
    return adapter


def _parse_integration_settings(
    integration_settings: str, log_adapter: logging.LoggerAdapter
) -> dict:
    """Parse integration settings JSON string."""
    try:
        settings_data = json.loads(integration_settings)
        if not isinstance(settings_data, dict):
            log_adapter.error(
                "Parsed --integration-settings is not a dictionary: %s. "
                "Settings: '%s'",
                type(settings_data),
                integration_settings,
            )
            sys.exit(1)
        return settings_data
    except json.JSONDecodeError as e:
        log_adapter.error(
            "Failed to parse --integration-settings JSON: %s. Settings: '%s'",
            e,
            integration_settings,
            exc_info=True,
        )
        sys.exit(1)
    except Exception as e_parse:
        log_adapter.error(
            "Unexpected error parsing --integration-settings: %s. Settings: '%s'",
            e_parse,
            integration_settings,
            exc_info=True,
        )
        sys.exit(1)


def get_bot_token(integration_settings: str, log_adapter: logging.LoggerAdapter) -> str:
    """Extract bot token from integration settings."""
    settings_data = _parse_integration_settings(integration_settings, log_adapter)
    bot_token = settings_data.get("botToken") or settings_data.get("bot_token")

    if not bot_token:
        log_adapter.critical(
            "CRITICAL: Telegram Bot Token not found in --integration-settings. "
            "Bot cannot start."
        )
        sys.exit(1)

    return bot_token


def _setup_database_session(log_adapter: logging.LoggerAdapter):
    """Setup database session factory if DATABASE_URL is available."""
    if not settings.DATABASE_URL:
        log_adapter.warning(
            "DATABASE_URL not set. Database features will be unavailable."
        )
        return None

    try:
        db_session_factory = get_async_session_factory()
        log_adapter.info("Database session factory configured for Telegram bot.")
        return db_session_factory
    except Exception as e_db_setup:
        log_adapter.error(
            "Failed to setup database session factory: %s", e_db_setup, exc_info=True
        )
        return None


def _create_bot_config(agent_id: str, bot_token: str, db_session_factory) -> dict:
    """Create consolidated config for TelegramIntegrationBot."""
    return {
        "agent_id": agent_id,
        "bot_token": bot_token,
        "db_session_factory": db_session_factory,
    }


async def _run_bot_loop(
    telegram_bot, log_adapter: logging.LoggerAdapter, agent_id: str, config: dict
) -> None:
    """Run the main bot execution loop with restart handling."""
    while True:
        log_adapter.info(
            "Calling TelegramIntegrationBot.run() for agent %s...", agent_id
        )
        await telegram_bot.run()

        if hasattr(telegram_bot, "needs_restart") and telegram_bot.needs_restart:
            log_adapter.info(
                "Telegram bot for %s requested restart. Re-initializing for another run cycle...",
                agent_id,
            )
            telegram_bot = TelegramIntegrationBot(
                config=config, logger_adapter=log_adapter
            )
        else:
            log_adapter.info(
                "TelegramIntegrationBot for %s finished execution or was shut down "
                "without a restart request.",
                agent_id,
            )
            break


async def _cleanup_database(
    log_adapter: logging.LoggerAdapter, db_session_factory
) -> None:
    """Cleanup database resources."""
    if settings.DATABASE_URL and db_session_factory:
        log_adapter.info("Shutting down database resources.")
        try:
            await close_db_engine()
            log_adapter.info("Database engine closed.")
        except Exception as e_db_close:
            log_adapter.error(
                "Error closing database engine: %s", e_db_close, exc_info=True
            )


async def main_async_runner(agent_id: str, integration_settings: str):
    """Main async runner for Telegram bot with clean error handling."""
    log_adapter = setup_logging_for_agent(agent_id)
    log_adapter.info("Starting Telegram bot for Agent ID: %s", agent_id)

    # Setup components
    db_session_factory = _setup_database_session(log_adapter)
    bot_token = get_bot_token(integration_settings, log_adapter)
    config = _create_bot_config(agent_id, bot_token, db_session_factory)

    telegram_bot = TelegramIntegrationBot(config=config, logger_adapter=log_adapter)

    try:
        await _run_bot_loop(telegram_bot, log_adapter, agent_id, config)
    except (KeyboardInterrupt, SystemExit):
        log_adapter.info("Telegram bot %s process interrupted or exited.", agent_id)
    except Exception as e:
        log_adapter.critical(
            "Unhandled exception in main_async_runner for Telegram bot %s: %s",
            agent_id,
            e,
            exc_info=True,
        )
    finally:
        await _cleanup_database(log_adapter, db_session_factory)
        log_adapter.info("Telegram bot runner for %s has been shut down.", agent_id)


if __name__ == "__main__":
    setup_logging()

    parser = argparse.ArgumentParser(
        description="Telegram Bot Integration for Configurable Agent"
    )
    parser.add_argument(
        "--agent-id", required=True, help="Unique ID of the agent to run"
    )
    parser.add_argument(
        "--integration-settings",
        type=str,
        required=True,  # Делаем обязательным, так как bot_token необходим
        help="JSON string with integration-specific settings (must include 'bot_token')",
    )
    args = parser.parse_args()

    try:
        asyncio.run(
            main_async_runner(
                agent_id=args.agent_id, integration_settings=args.integration_settings
            )
        )
    except KeyboardInterrupt:
        logging.getLogger(__name__).info(
            "Telegram bot runner main received KeyboardInterrupt. Exiting."
        )
    except Exception as e:
        logging.getLogger(__name__).critical(
            "Critical error in Telegram bot runner main: %s", e, exc_info=True
        )
        sys.exit(1)  # Or os._exit(1) if absolutely necessary
    finally:
        logging.getLogger(__name__).info("Telegram bot runner main finished.")
