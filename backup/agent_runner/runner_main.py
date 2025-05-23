import asyncio
import logging
import argparse
import sys

from app.db.session import get_async_session_factory, close_db_engine
from app.core.config import settings
from app.agent_runner.agent_runner import AgentRunner
from app.core.logging_config import setup_logging


def setup_logging_for_agent(agent_id: str) -> logging.LoggerAdapter:
        
    module_logger = logging.getLogger(f"AGENT:{args.agent_id}")
    adapter = logging.LoggerAdapter(module_logger, {'agent_id': agent_id})
    return adapter

async def main_async_runner(agent_id: str):

    log_adapter = setup_logging_for_agent(agent_id)
    log_adapter.info(f"Starting agent runner for Agent ID: {agent_id}")

    db_session_factory = None
    if settings.DATABASE_URL:
        try:
            # Передаем строку URL базы данных напрямую из настроек
            db_session_factory = get_async_session_factory() # Убран аргумент
            log_adapter.info(f"Database session factory configured for Agent.")
        except Exception as e_db_setup:
            log_adapter.error(f"Failed to setup database session factory: {e_db_setup}", exc_info=True)
    else:
        log_adapter.warning("DATABASE_URL not set. Database features will be unavailable.")

    agent_runner = AgentRunner(
        agent_id=agent_id,
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
    except (KeyboardInterrupt, SystemExit):
        log_adapter.info(f"Agent {args.agent_id} process interrupted or exited.")
    except Exception as e:
        log_adapter.critical(f"Unhandled exception in main_async_runner for agent {agent_id}: {e}", exc_info=True)
        # Depending on desired behavior, could attempt a restart or ensure shutdown
    finally:
        if settings.DATABASE_URL and db_session_factory:
            log_adapter.info(f"Shutting down agent runner for {agent_id}.")
            try:
                await close_db_engine()
                log_adapter.info("Database engine closed.")
            except Exception as e_db_close:
                log_adapter.error(f"Error closing database engine: {e_db_close}", exc_info=True)
        log_adapter.info(f"Agent runner for {agent_id} has been shut down.")

if __name__ == "__main__":
    setup_logging()

    parser = argparse.ArgumentParser(description="Agent Runner Main Program")
    parser.add_argument("--agent-id", required=True, help="Unique ID of the agent to run")

    args = parser.parse_args()

    try:
        asyncio.run(main_async_runner(agent_id=args.agent_id))
    except KeyboardInterrupt:
        # Logging for KeyboardInterrupt can be done here if needed,
        # but individual components should handle their own graceful shutdown.
        logging.getLogger(__name__).info("Agent runner main received KeyboardInterrupt. Exiting.")
    except Exception as e:
        logging.getLogger(__name__).critical(f"Critical error in agent runner main: {e}", exc_info=True)
        sys.exit(1) # Or os._exit(1) if absolutely necessary
    finally:
        logging.getLogger(__name__).info("Agent runner main finished.")