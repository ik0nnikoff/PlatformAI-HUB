"""
WhatsApp Integration Main Entry Point для PlatformAI-HUB

Запуск интеграции WhatsApp для конкретного агента.
Использует аргументы командной строки для настройки.
"""

import argparse
import asyncio
import logging
import sys
from pathlib import Path

from app.core.logging_config import setup_logging

# Добавляем корневую директорию проекта в Python path
project_root = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(project_root))


def setup_logging_for_agent(agent_id: str) -> logging.LoggerAdapter:
    """Настройка логирования для агента"""
    module_logger = logging.getLogger(f"WHATSAPP_BOT:{agent_id}")
    adapter = logging.LoggerAdapter(module_logger, {"agent_id": agent_id})
    return adapter


async def main_async_runner(
    agent_id: str, integration_settings: str, logger: logging.Logger
):
    """
    Main asynchronous runner for the WhatsApp integration.

    Args:
        agent_id: Agent identifier
        integration_settings: Integration configuration
        logger: Logger instance
    """
    # NOTE: WhatsAppRunner class is deprecated/removed
    # This is a placeholder for potential future implementation
    logger.warning(
        "WhatsApp integration runner not implemented - placeholder only. "
        "Agent ID: %s, Settings length: %d",
        agent_id,
        len(integration_settings),
    )
    return


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
