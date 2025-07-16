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
    adapter = logging.LoggerAdapter(module_logger, {'agent_id': agent_id})
    return adapter

def get_bot_token(integration_settings: str, log_adapter) -> str | None:
    bot_token: str | None = None
    try:
        integration_settings_data = json.loads(args.integration_settings)
        if isinstance(integration_settings_data, dict):
            bot_token = integration_settings_data.get("botToken") or integration_settings_data.get("bot_token")
        else:
            log_adapter.error(
                f"Parsed --integration-settings is not a dictionary: {type(integration_settings_data)}. "
                f"Settings: '{args.integration_settings}'"
            )
    except json.JSONDecodeError as e:
        log_adapter.error(
            f"Failed to parse --integration-settings JSON: {e}. Settings: '{args.integration_settings}'",
            exc_info=True
        )
    except Exception as e_parse: # Ловим любые другие ошибки парсинга
        log_adapter.error(
            f"Unexpected error parsing --integration-settings: {e_parse}. Settings: '{args.integration_settings}'",
            exc_info=True
        )

    if not bot_token:
        log_adapter.critical("CRITICAL: Telegram Bot Token not found in --integration-settings. Bot cannot start.")
        sys.exit(1)
    
    return bot_token

async def main_async_runner(agent_id: str, integration_settings: str):

    log_adapter = setup_logging_for_agent(agent_id)
    log_adapter.info(f"Starting Telegram bot for Agent ID: {agent_id}")

    db_session_factory = None
    if settings.DATABASE_URL:
        try:
            # Передаем строку URL базы данных напрямую из настроек
            db_session_factory = get_async_session_factory() # Убран аргумент
            log_adapter.info("Database session factory configured for Telegram bot.")
        except Exception as e_db_setup:
            log_adapter.error(f"Failed to setup database session factory: {e_db_setup}", exc_info=True)
    else:
        log_adapter.warning("DATABASE_URL not set. Database features will be unavailable.")

    bot_token = get_bot_token(integration_settings, log_adapter)

    telegram_bot = TelegramIntegrationBot(
        agent_id=args.agent_id,
        bot_token=bot_token,
        db_session_factory=db_session_factory,
        logger_adapter=log_adapter
    )

    try:
        while True:
            log_adapter.info(f"Calling TelegramIntegrationBot.run() for agent {agent_id}...")
            await telegram_bot.run()

            if hasattr(telegram_bot, 'needs_restart') and telegram_bot.needs_restart:
                log_adapter.info(f"Telegram bot for {agent_id} requested restart. Re-initializing for another run cycle...")
                telegram_bot = TelegramIntegrationBot(
                    agent_id=args.agent_id,
                    bot_token=bot_token,
                    db_session_factory=db_session_factory,
                    logger_adapter=log_adapter
                )
            else:
                log_adapter.info(f"TelegramIntegrationBot for {agent_id} finished execution or was shut down without a restart request.")
                break
    except (KeyboardInterrupt, SystemExit):
        log_adapter.info(f"Telegram bot {args.agent_id} process interrupted or exited.")
    except Exception as e:
        log_adapter.critical(f"Unhandled exception in main_async_runner for Telegram bot {agent_id}: {e}", exc_info=True)
        # Depending on desired behavior, could attempt a restart or ensure shutdown
    finally:
        if settings.DATABASE_URL and db_session_factory:
            log_adapter.info(f"Shutting down Telegram bot runner for {agent_id}.")
            try:
                await close_db_engine()
                log_adapter.info("Database engine closed.")
            except Exception as e_db_close:
                log_adapter.error(f"Error closing database engine: {e_db_close}", exc_info=True)
        log_adapter.info(f"Telegram bot runner for {agent_id} has been shut down.")

if __name__ == "__main__":
    setup_logging()

    parser = argparse.ArgumentParser(description="Telegram Bot Integration for Configurable Agent")
    parser.add_argument("--agent-id", required=True, help="Unique ID of the agent to run")
    parser.add_argument(
        "--integration-settings",
        type=str,
        required=True, # Делаем обязательным, так как bot_token необходим
        help="JSON string with integration-specific settings (must include 'bot_token')"
    )
    args = parser.parse_args()

    try:
        asyncio.run(main_async_runner(agent_id=args.agent_id, integration_settings=args.integration_settings))
    except KeyboardInterrupt:
        logging.getLogger(__name__).info("Telegram bot runner main received KeyboardInterrupt. Exiting.")
    except Exception as e:
        logging.getLogger(__name__).critical(f"Critical error in Telegram bot runner main: {e}", exc_info=True)
        sys.exit(1) # Or os._exit(1) if absolutely necessary
    finally:
        logging.getLogger(__name__).info("Telegram bot runner main finished.")