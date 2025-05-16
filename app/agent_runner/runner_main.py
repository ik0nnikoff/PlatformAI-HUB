import os
import asyncio
import logging
import argparse
import sys # Keep sys for sys.exit if needed, though os._exit was used before

# Removed json, uuid, typing.Dict, Optional, Any, List (if not used by remaining code)
# Removed sqlalchemy.ext.asyncio related imports if db_session_factory is the only direct use
# Removed langchain_core.messages, datetime, requests, redis.asyncio as redis (now in AgentRunner)
# Removed time (now in AgentRunner)

# Removed app.agent_runner.langgraph_factory, app.agent_runner.langgraph_models (now in AgentRunner)
from app.db.session import get_async_session_factory, close_db_engine
# Removed app.db.alchemy_models, app.db.crud (now in AgentRunner)
from app.core.config import settings
from app.agent_runner.agent_runner import AgentRunner # Import the new AgentRunner class
from app.core.logging_config import setup_logging # Uncommented

# --- Custom Logging Filter ---
class AgentIdFilter(logging.Filter):
    """Добавляет `agent_id` по умолчанию, если он отсутствует в записи лога."""
    def __init__(self, default_agent_id="system"):
        super().__init__()
        self.default_agent_id = default_agent_id

    def filter(self, record):
        """Применяет фильтр к записи лога, добавляя `agent_id` при необходимости."""
        if not hasattr(record, 'agent_id'):
            record.agent_id = self.default_agent_id
        return True

def setup_logging_for_agent(agent_id: str) -> logging.LoggerAdapter:
    """
    Настраивает логирование для конкретного экземпляра исполнителя агента (agent runner).

    Предполагается, что базовая конфигурация логирования (`basicConfig`) уже была вызвана
    (например, через `app.core.logging_config`).
    Добавляет `AgentIdFilter` к корневым обработчикам, если он еще не добавлен,
    и возвращает `LoggerAdapter` для добавления `agent_id` ко всем сообщениям этого адаптера.

    Args:
        agent_id (str): Уникальный идентификатор агента, для которого настраивается логирование.

    Returns:
        logging.LoggerAdapter: Адаптер логгера, который автоматически добавляет `agent_id`
                               в записи логов.
    """
    # Ensure app.core.logging_config has been imported and configured basicConfig
    # For example, by importing it in the main entry point of your application that launches runners
    # Or, if this runner is the absolute entry point, ensure logging_config is imported here.
    # import app.core.logging_config # Uncomment if this is the very first point logging is needed

    root_logger = logging.getLogger()
    
    log_filter = AgentIdFilter(default_agent_id='-') 
    for handler in root_logger.handlers:
        if not any(isinstance(f, AgentIdFilter) for f in handler.filters):
            handler.addFilter(log_filter)
            
    module_logger = logging.getLogger(__name__) # Or a more specific logger name like "agent_runner.main"
    adapter = logging.LoggerAdapter(module_logger, {'agent_id': agent_id})
    return adapter

# Global flags and signal handler are removed, handled by RunnableComponent.
# Old functions (control_listener, fetch_config, update_redis_status, etc.) are removed.

async def main_async_runner(agent_id: str, config_url: str):
    """
    Основная асинхронная функция для инициализации и запуска `AgentRunner`.

    Эта функция выполняет следующие шаги:
    1. Настраивает логирование для указанного `agent_id` с помощью `setup_logging_for_agent`.
    2. Получает фабрику сессий базы данных, если `DATABASE_URL` задан в настройках.
    3. Создает экземпляр `AgentRunner` с предоставленными `agent_id`, `config_url`,
       фабрикой сессий БД и адаптером логгера.
    4. Запускает цикл, в котором вызывается `agent_runner.run()`. Этот метод `run()`
       обрабатывает полный жизненный цикл агента (настройка, основной цикл, очистка).
    5. Если `AgentRunner` запрашивает перезапуск (через атрибут `needs_restart`),
       цикл продолжается, позволяя `AgentRunner` переинициализироваться при следующем вызове `run()`.
    6. Если перезапуск не требуется или происходит исключение, цикл прерывается.
    7. В блоке `finally` выполняется закрытие соединения с базой данных (если оно было открыто)
       и логгируется завершение работы.

    Args:
        agent_id (str): Уникальный идентификатор агента для запуска.
        config_url (str): URL-адрес для загрузки конфигурации агента.
    """
    log_adapter = setup_logging_for_agent(agent_id)
    log_adapter.info(f"Starting agent runner for Agent ID: {agent_id}, Config URL: {config_url}")

    db_session_factory = None
    if settings.DATABASE_URL:
        db_session_factory = get_async_session_factory() # Убран аргумент
        log_adapter.info("Database session factory configured.")
    else:
        log_adapter.warning("DATABASE_URL not set. Database features will be unavailable.")

    agent_runner = AgentRunner(
        agent_id=agent_id,
        config_url=config_url,
        db_session_factory=db_session_factory,
        logger_adapter=log_adapter
    )

    try:
        while True:
            log_adapter.info(f"Calling AgentRunner.run() for agent {agent_id}...")
            await agent_runner.run() # This method now handles setup, run_loop, and cleanup

            if hasattr(agent_runner, 'needs_restart') and agent_runner.needs_restart:
                log_adapter.info(f"AgentRunner for {agent_id} requested restart. Re-initializing for another run cycle...")
                # The AgentRunner's setup() method (called by run()) should handle resetting its state.
                # If full re-instantiation were needed:
                # agent_runner = AgentRunner(...) 
            else:
                log_adapter.info(f"AgentRunner for {agent_id} finished execution or was shut down without a restart request.")
                break # Exit the loop, leading to shutdown
    except Exception as e:
        log_adapter.critical(f"Unhandled exception in main_async_runner for agent {agent_id}: {e}", exc_info=True)
        # Depending on desired behavior, could attempt a restart or ensure shutdown
    finally:
        log_adapter.info(f"Shutting down agent runner for {agent_id}.")
        if settings.DATABASE_URL: # Only close if it was initialized
            await close_db_engine()
            log_adapter.info("Database engine closed.")
        log_adapter.info(f"Agent runner for {agent_id} has been shut down.")

if __name__ == "__main__":
    # Ensure app.core.logging_config is imported and sets up basicConfig *before* any logging.
    # For example:
    # from app.core.logging_config import setup_logging # This is now uncommented above
    setup_logging() # Uncommented call

    parser = argparse.ArgumentParser(description="Agent Runner Main Program")
    parser.add_argument("--agent-id", required=True, help="Unique ID of the agent to run")
    parser.add_argument("--config-url", required=True, help="URL to fetch the agent's configuration")
    # --redis-url is no longer needed by runner_main.py directly, as AgentRunner gets it from settings
    # If ProcessManager still passes it, argparse will ignore it if not defined here.
    # To explicitly ignore unknown args: args, unknown = parser.parse_known_args()
    # However, it's cleaner if ProcessManager stops sending it.

    args = parser.parse_args()

    try:
        asyncio.run(main_async_runner(agent_id=args.agent_id, config_url=args.config_url))
    except KeyboardInterrupt:
        # Logging for KeyboardInterrupt can be done here if needed,
        # but individual components should handle their own graceful shutdown.
        logging.getLogger(__name__).info("Agent runner main received KeyboardInterrupt. Exiting.")
    except Exception as e:
        logging.getLogger(__name__).critical(f"Critical error in agent runner main: {e}", exc_info=True)
        sys.exit(1) # Or os._exit(1) if absolutely necessary
    finally:
        logging.getLogger(__name__).info("Agent runner main finished.")
