import asyncio
import logging
import argparse
import json
import sys

from app.core.logging_config import setup_logging
from app.core.config import settings # Для DATABASE_URL
from app.db.session import get_async_session_factory, close_db_engine
from app.integrations.telegram.telegram_bot import TelegramIntegrationBot

# Все старые глобальные переменные (bot, dp, redis_client, и т.д.) удалены.
# Все старые функции (обработчики, слушатели, lifespan, main_bot_runner, и т.д.) удалены.

if __name__ == "__main__":
    # 1. Настройка стандартного логирования
    setup_logging()

    # 2. Парсинг аргументов командной строки
    parser = argparse.ArgumentParser(description="Telegram Bot Integration for Configurable Agent")
    parser.add_argument("--agent-id", required=True, help="Unique ID of the agent this bot interacts with")
    parser.add_argument("--redis-url", required=True, help="URL for Redis connection for this bot instance")
    parser.add_argument(
        "--integration-settings",
        type=str,
        required=True, # Делаем обязательным, так как bot_token необходим
        help="JSON string with integration-specific settings (must include 'bot_token')"
    )
    args = parser.parse_args()

    # 3. Создание логгера для экземпляра Telegram-бота этого агента
    logger = logging.getLogger(f"telegram_bot:{args.agent_id}")
    logger.info(
        f"Initializing Telegram bot for Agent ID: {args.agent_id} with Redis URL: {args.redis_url}"
    )

    # 4. Извлечение bot_token из integration_settings
    bot_token_to_use: str | None = None
    try:
        integration_settings_data = json.loads(args.integration_settings)
        if isinstance(integration_settings_data, dict):
            bot_token_to_use = integration_settings_data.get("botToken") or integration_settings_data.get("bot_token")
        else:
            logger.error(
                f"Parsed --integration-settings is not a dictionary: {type(integration_settings_data)}. "
                f"Settings: '{args.integration_settings}'"
            )
    except json.JSONDecodeError as e:
        logger.error(
            f"Failed to parse --integration-settings JSON: {e}. Settings: '{args.integration_settings}'",
            exc_info=True
        )
    except Exception as e_parse: # Ловим любые другие ошибки парсинга
        logger.error(
            f"Unexpected error parsing --integration-settings: {e_parse}. Settings: '{args.integration_settings}'",
            exc_info=True
        )

    if not bot_token_to_use:
        logger.critical("CRITICAL: Telegram Bot Token not found in --integration-settings. Bot cannot start.")
        sys.exit(1) # Выход, если токена нет

    logger.info(f"Telegram Bot Token obtained (length: {len(bot_token_to_use)}).")

    # 5. Настройка фабрики сессий базы данных (если используется база данных)
    db_session_factory = None
    if settings.DATABASE_URL:
        try:
            # Передаем строку URL базы данных напрямую из настроек
            db_session_factory = get_async_session_factory() # Убран аргумент
            logger.info("Database session factory configured for Telegram bot.")
        except Exception as e_db_setup:
            logger.error(f"Failed to setup database session factory: {e_db_setup}", exc_info=True)
            # В зависимости от требований, можно выйти или продолжить без функций БД
            # Пока что он продолжит, и TelegramIntegrationBot должен обрабатывать db_session_factory равным None.
    else:
        logger.warning("DATABASE_URL not set. Database features will be unavailable for the Telegram bot.")

    # 6. Инициализация и запуск TelegramIntegrationBot
    telegram_bot_instance = TelegramIntegrationBot(
        agent_id=args.agent_id,
        bot_token=bot_token_to_use,
        redis_url=args.redis_url, # Для StatusUpdater и как fallback для операций бота
        # redis_pubsub_url=args.redis_url, # Можно явно указать, если URL для Pub/Sub отличается
        db_session_factory=db_session_factory,
        logger_adapter=logger # Изменено с logger на logger_adapter
    )

    try:
        asyncio.run(telegram_bot_instance.run())
    except (KeyboardInterrupt, SystemExit):
        logger.info(f"Telegram bot for agent {args.agent_id} process interrupted or exited.")
    except Exception as e:
        # Ловит исключения из telegram_bot_instance.run(), если они не обработаны внутри
        # или исключения из самого asyncio.run().
        logger.critical(
            f"Unhandled exception in Telegram bot main execution for agent {args.agent_id}: {e}",
            exc_info=True
        )
    finally:
        logger.info(f"Telegram bot application for agent {args.agent_id} is shutting down.")
        if settings.DATABASE_URL and db_session_factory: # Убедимся, что была попытка настройки
            logger.info("Closing database engine...")
            try:
                # close_db_engine - асинхронная функция
                asyncio.run(close_db_engine())
                logger.info("Database engine closed successfully.")
            except Exception as e_db_close:
                logger.error(f"Error closing database engine: {e_db_close}", exc_info=True)
        logger.info(f"Telegram bot for agent {args.agent_id} finished.")
