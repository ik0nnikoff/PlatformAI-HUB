"""
Agent Runner Main Entry Point.

This module provides the main entry point for running individual agent instances.
It handles agent lifecycle management, database connectivity, and process coordination.
"""

import asyncio
import logging
import argparse
import sys

from app.db.session import get_async_session_factory, close_db_engine
from app.core.config import settings
from app.agent_runner.agent_runner import AgentRunner
from app.core.logging_config import setup_logging


def setup_logging_for_agent(agent_id: str) -> logging.LoggerAdapter:
    """
    Настраивает логирование для конкретного агента.
    
    Args:
        agent_id: Уникальный идентификатор агента
        
    Returns:
        LoggerAdapter с контекстом агента
    """
    module_logger = logging.getLogger(f"AGENT:{agent_id}")
    adapter = logging.LoggerAdapter(module_logger, {'agent_id': agent_id})
    return adapter


async def setup_database_factory(log_adapter: logging.LoggerAdapter):
    """
    Настраивает фабрику сессий базы данных.
    
    Args:
        log_adapter: Адаптер логирования для агента
        
    Returns:
        async_sessionmaker или None если БД недоступна
    """
    db_session_factory = None
    if settings.DATABASE_URL:
        try:
            # Передаем строку URL базы данных напрямую из настроек
            db_session_factory = get_async_session_factory()  # Убран аргумент
            log_adapter.info("Database session factory configured for Agent.")
        except Exception as e_db_setup:
            log_adapter.error(
                "Failed to setup database session factory: %s", 
                e_db_setup, exc_info=True
            )
    else:
        log_adapter.warning(
            "DATABASE_URL not set. Database features will be unavailable."
        )
    return db_session_factory


async def run_agent_loop(agent_runner, agent_id: str, log_adapter: logging.LoggerAdapter):
    """
    Основной цикл выполнения агента с поддержкой перезапуска.
    
    Args:
        agent_runner: Экземпляр AgentRunner
        agent_id: Идентификатор агента
        log_adapter: Адаптер логирования
    """
    try:
        while True:
            log_adapter.info(
                "Calling AgentRunner.run() for agent %s...", agent_id
            )
            # This method now handles setup, run_loop, and cleanup
            await agent_runner.run()

            if (hasattr(agent_runner, 'needs_restart') and
                agent_runner.needs_restart):
                log_adapter.info(
                    "AgentRunner for %s requested restart. "
                    "Re-initializing for another run cycle...", agent_id
                )
                # The AgentRunner's setup() method (called by run()) should
                # handle resetting its state.
                # If full re-instantiation were needed:
                # agent_runner = AgentRunner(...)
            else:
                log_adapter.info(
                    "AgentRunner for %s finished execution or was shut down "
                    "without a restart request.", agent_id
                )
                break  # Exit the loop, leading to shutdown
    except (KeyboardInterrupt, SystemExit):
        log_adapter.info("Agent %s process interrupted or exited.", agent_id)
    except Exception as e:
        log_adapter.critical(
            "Unhandled exception in main_async_runner for agent %s: %s", 
            agent_id, e, exc_info=True
        )
        # Depending on desired behavior, could attempt a restart or ensure shutdown


async def cleanup_database(db_session_factory, agent_id: str, log_adapter: logging.LoggerAdapter):
    """
    Очищает ресурсы базы данных.
    
    Args:
        db_session_factory: Фабрика сессий БД
        agent_id: Идентификатор агента
        log_adapter: Адаптер логирования
    """
    if settings.DATABASE_URL and db_session_factory:
        log_adapter.info("Shutting down agent runner for %s.", agent_id)
        try:
            await close_db_engine()
            log_adapter.info("Database engine closed.")
        except Exception as e_db_close:
            log_adapter.error(
                "Error closing database engine: %s", 
                e_db_close, exc_info=True
            )
    log_adapter.info("Agent runner for %s has been shut down.", agent_id)


async def main_async_runner(agent_id: str):
    """
    Основной асинхронный запускатель агента.
    
    Args:
        agent_id: Уникальный идентификатор агента для запуска
    """
    log_adapter = setup_logging_for_agent(agent_id)
    log_adapter.info("Starting agent runner for Agent ID: %s", agent_id)

    # Setup database
    db_session_factory = await setup_database_factory(log_adapter)

    # Create agent runner
    agent_runner = AgentRunner(
        agent_id=agent_id,
        db_session_factory=db_session_factory,
        logger_adapter=log_adapter
    )

    try:
        # Run agent loop
        await run_agent_loop(agent_runner, agent_id, log_adapter)
    finally:
        # Cleanup database resources
        await cleanup_database(db_session_factory, agent_id, log_adapter)


if __name__ == "__main__":
    setup_logging()

    parser = argparse.ArgumentParser(description="Agent Runner Main Program")
    parser.add_argument(
        "--agent-id", required=True, help="Unique ID of the agent to run"
    )

    args = parser.parse_args()

    try:
        asyncio.run(main_async_runner(agent_id=args.agent_id))
    except KeyboardInterrupt:
        # Logging for KeyboardInterrupt can be done here if needed,
        # but individual components should handle their own graceful shutdown.
        logging.getLogger(__name__).info(
            "Agent runner main received KeyboardInterrupt. Exiting."
        )
    except Exception as e:
        logging.getLogger(__name__).critical(
            "Critical error in agent runner main: %s", e, exc_info=True
        )
        sys.exit(1)  # Or os._exit(1) if absolutely necessary
    finally:
        logging.getLogger(__name__).info("Agent runner main finished.")
