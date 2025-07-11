#!/usr/bin/env python3
"""
WhatsApp Integration Main Entry Point для PlatformAI-HUB

Запуск интеграции WhatsApp для конкретного агента.
Использует аргументы командной строки для настройки.
"""

import asyncio
import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Optional

# Добавляем корневую директорию проекта в Python path
project_root = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(project_root))

from app.integrations.whatsapp.whatsapp_bot import WhatsAppIntegrationBot
from app.core.config import settings
from app.core.logging_config import setup_logging
from app.db.session import get_async_session_factory, close_db_engine


def setup_logging_for_agent(agent_id: str) -> logging.LoggerAdapter:
    """Настройка логирования для агента"""
    module_logger = logging.getLogger(f"WHATSAPP_BOT:{agent_id}")
    adapter = logging.LoggerAdapter(module_logger, {'agent_id': agent_id})
    return adapter


def get_whatsapp_settings(integration_settings: str, log_adapter) -> tuple[str, str]:
    """
    Извлекает настройки WhatsApp из JSON строки integration_settings
    
    Returns:
        tuple: (session_name, token)
    """
    session_name: str = ""
    token: str = ""
    
    try:
        integration_settings_data = json.loads(integration_settings)
        if isinstance(integration_settings_data, dict):
            session_name = integration_settings_data.get("sessionName") or integration_settings_data.get("session_name", "")
            token = integration_settings_data.get("token", "")
        else:
            log_adapter.error(
                f"Parsed --integration-settings is not a dictionary: {type(integration_settings_data)}. "
                f"Settings: '{integration_settings}'"
            )
    except json.JSONDecodeError as e:
        log_adapter.error(
            f"Failed to parse --integration-settings JSON: {e}. Settings: '{integration_settings}'",
            exc_info=True
        )
    except Exception as e_parse:
        log_adapter.error(
            f"Unexpected error parsing --integration-settings: {e_parse}. Settings: '{integration_settings}'",
            exc_info=True
        )

    if not session_name:
        log_adapter.critical("CRITICAL: WhatsApp session_name not found in --integration-settings. Bot cannot start.")
        sys.exit(1)
    
    return session_name, token


async def main_async_runner(agent_id: str, integration_settings: str):
    """Основная асинхронная функция для запуска WhatsApp интеграции"""
    
    log_adapter = setup_logging_for_agent(agent_id)
    log_adapter.info(f"Starting WhatsApp integration for Agent ID: {agent_id}")

    db_session_factory = None
    if settings.DATABASE_URL:
        try:
            db_session_factory = get_async_session_factory()
            log_adapter.info("Database session factory configured for WhatsApp bot.")
        except Exception as e_db_setup:
            log_adapter.error(f"Failed to setup database session factory: {e_db_setup}", exc_info=True)
    else:
        log_adapter.warning("DATABASE_URL not set. Database features will be unavailable.")

    session_name, token = get_whatsapp_settings(integration_settings, log_adapter)

    whatsapp_bot = WhatsAppIntegrationBot(
        agent_id=agent_id,
        session_name=session_name,
        token=token,
        db_session_factory=db_session_factory,
        logger_adapter=log_adapter
    )

    try:
        while True:
            log_adapter.info(f"Calling WhatsAppIntegrationBot.run() for agent {agent_id}...")
            await whatsapp_bot.run()

            if hasattr(whatsapp_bot, 'needs_restart') and whatsapp_bot.needs_restart:
                log_adapter.info(f"WhatsApp bot for {agent_id} requested restart. Re-initializing for another run cycle...")
                whatsapp_bot = WhatsAppIntegrationBot(
                    agent_id=agent_id,
                    session_name=session_name,
                    token=token,
                    db_session_factory=db_session_factory,
                    logger_adapter=log_adapter
                )
            else:
                log_adapter.info(f"WhatsAppIntegrationBot for {agent_id} finished execution or was shut down without a restart request.")
                break
    except (KeyboardInterrupt, SystemExit):
        log_adapter.info(f"WhatsApp bot {agent_id} process interrupted or exited.")
    except Exception as e:
        log_adapter.critical(f"Unhandled exception in main_async_runner for WhatsApp bot {agent_id}: {e}", exc_info=True)
    finally:
        if settings.DATABASE_URL and db_session_factory:
            log_adapter.info(f"Shutting down WhatsApp bot runner for {agent_id}.")
            try:
                await close_db_engine()
                log_adapter.info("Database engine closed.")
            except Exception as e_db_close:
                log_adapter.error(f"Error closing database engine: {e_db_close}", exc_info=True)
        log_adapter.info(f"WhatsApp bot runner for {agent_id} has been shut down.")


if __name__ == "__main__":
    setup_logging()

    parser = argparse.ArgumentParser(description="WhatsApp Integration for PlatformAI-HUB")
    parser.add_argument("--agent-id", required=True, help="Unique ID of the agent to run")
    parser.add_argument(
        "--integration-settings",
        type=str,
        required=True,
        help="JSON string with integration-specific settings (must include 'sessionName')"
    )
    args = parser.parse_args()

    try:
        asyncio.run(main_async_runner(agent_id=args.agent_id, integration_settings=args.integration_settings))
    except KeyboardInterrupt:
        logging.getLogger(__name__).info("WhatsApp bot runner main received KeyboardInterrupt. Exiting.")
    except Exception as e:
        logging.getLogger(__name__).critical(f"Critical error in WhatsApp bot runner main: {e}", exc_info=True)
        sys.exit(1)
    finally:
        logging.getLogger(__name__).info("WhatsApp bot runner main finished.")
