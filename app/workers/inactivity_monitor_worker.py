import asyncio
import logging
import signal
import time
import os
import json # Added

import redis.asyncio as redis
from redis.exceptions import ConnectionError as RedisConnectionError # Added
from sqlalchemy.ext.asyncio import AsyncSession # Keep for potential future use with DB

from app.core.config import settings
from app.api.schemas.common_schemas import IntegrationType # Added
from app.services import process_manager_service as pms # Added
# from app.db.session import get_async_session_factory # If direct DB access needed in future
# from app.db.crud.agent_crud import db_get_agent_config_by_id # Example if needed
# from app.services.process_manager_service import stop_agent_process_via_api # If we call an API endpoint

logger = logging.getLogger(__name__)

# Graceful shutdown handling
shutdown_requested = False

def signal_handler(signum, frame):
    global shutdown_requested
    logger.info(f"Signal {signum} received, initiating graceful shutdown for inactivity_monitor_worker.")
    shutdown_requested = True

async def stop_agent_process_directly(agent_id: str, pid: int, redis_client: redis.Redis):
    """
    Attempts to stop an agent process directly using os.kill.
    This is a simplified version for the worker, assuming it has PID.
    Updates Redis status upon stopping.
    """
    status_key = f"agent_status:{agent_id}"
    logger.info(f"Attempting to stop inactive agent {agent_id} (PID: {pid}) directly.")
    try:
        os.kill(pid, signal.SIGTERM) # Send SIGTERM
        logger.info(f"SIGTERM sent to agent {agent_id} (PID: {pid}).")
        # Basic wait, more robust checking might be needed if SIGTERM is ignored
        await asyncio.sleep(5) 
        try:
            os.kill(pid, 0) # Check if process still exists
            logger.warning(f"Agent {agent_id} (PID: {pid}) did not terminate after SIGTERM. Sending SIGKILL.")
            os.kill(pid, signal.SIGKILL)
            logger.info(f"SIGKILL sent to agent {agent_id} (PID: {pid}).")
            await asyncio.sleep(1) # Give time for SIGKILL to take effect
        except ProcessLookupError:
            logger.info(f"Agent {agent_id} (PID: {pid}) terminated successfully after SIGTERM/SIGKILL.")
        except OSError as e:
            logger.error(f"Error during SIGKILL for agent {agent_id} (PID: {pid}): {e}")

    except ProcessLookupError:
        logger.warning(f"Agent {agent_id} (PID: {pid}) process not found when trying to stop (already stopped?).")
    except Exception as e:
        logger.error(f"Error stopping agent process {agent_id} (PID: {pid}): {e}", exc_info=True)
    finally:
        # Update Redis status to 'stopped'
        try:
            await redis_client.hset(status_key, mapping={
                "status": "stopped_inactive", # Specific status for inactive shutdown
                "pid": "", # Clear PID
                "last_active": "" # Clear last_active
            })
            logger.info(f"Updated Redis status for agent {agent_id} to 'stopped_inactive'.")
        except Exception as e:
            logger.error(f"Failed to update Redis status for agent {agent_id} after stop attempt: {e}")

async def check_and_restart_crashed_agents(r: redis.Redis):
    logger.debug("Checking for crashed or stopped agents to restart...")
    try:
        async for config_key_b in r.scan_iter("agent_config:*"):
            if shutdown_requested: break
            config_key = config_key_b.decode('utf-8')
            agent_id_from_key = ""
            try:
                agent_id_from_key = config_key.split(":")[1]
            except IndexError:
                logger.warning(f"Malformed agent_config key found: {config_key}")
                continue

            current_status = await pms.get_agent_status(agent_id_from_key, r)
            
            # Define statuses that warrant a restart
            # Excludes "stopped" to avoid restarting explicitly stopped agents.
            # "error_process_lost" covers cases where agent was running but PID/container disappeared.
            # "not_found" covers cases where config exists but status record is missing.
            restart_worthy_statuses = [
                "error_process_lost", "error_start_failed", 
                "error_script_not_found", "error_config_missing", 
                "not_found", "unknown",
                "error_restart_failed", # Added from integration logic, seems relevant
                "error_restart_stop_failed", # Added from integration logic
                "error_restart_start_failed" # Added from integration logic
            ]
            
            if current_status.status in restart_worthy_statuses:
                logger.info(f"Agent {agent_id_from_key} needs restart (current status: {current_status.status}). Attempting restart.")
                try:
                    success = await pms.restart_agent_process(agent_id_from_key, r)
                    if success:
                        logger.info(f"Successfully initiated restart for agent {agent_id_from_key}.")
                    else:
                        logger.warning(f"Restart attempt failed for agent {agent_id_from_key} (restart_agent_process returned False).")
                except Exception as e_restart:
                    logger.error(f"Error during restart attempt for agent {agent_id_from_key}: {e_restart}", exc_info=True)
            await asyncio.sleep(0.01) # Yield control during long scans
    except RedisConnectionError: # Restored
        logger.error("Redis connection error during agent restart check.")
        # Reconnection is handled in the main_loop
    except Exception as e: # Restored
        logger.error(f"Unexpected error in check_and_restart_crashed_agents: {e}", exc_info=True)

async def check_and_restart_crashed_integrations(r: redis.Redis):
    logger.debug("Checking for crashed or stopped integrations to restart...")
    try:
        async for config_key_b in r.scan_iter("integration_config:*:*"):
            if shutdown_requested: break
            config_key = config_key_b.decode('utf-8')
            agent_id_from_key = ""
            integration_type_str = ""
            try:
                parts = config_key.split(":")
                if len(parts) != 3:
                    logger.warning(f"Malformed integration config key found: {config_key}")
                    continue
                _, agent_id_from_key, integration_type_str = parts
                integration_type = IntegrationType(integration_type_str)
            except ValueError:
                logger.warning(f"Unknown integration type '{integration_type_str}' in key {config_key}. Skipping.")
                continue
            except IndexError:
                 logger.warning(f"Malformed integration_config key (IndexError): {config_key}")
                 continue


            current_status = await pms.get_integration_status(agent_id_from_key, integration_type, r)
            
            # Define statuses that warrant a restart for integrations
            # Excludes "stopped" to avoid restarting explicitly stopped integrations.
            restart_worthy_statuses_integration = [
                "error_process_lost", "error_start_failed",
                "error_script_not_found", "error_config_missing", 
                "not_found", "unknown",
                "error_restart_stop_failed", "error_restart_start_failed", "error_restart_failed"
            ]

            if current_status.status in restart_worthy_statuses_integration:
                logger.info(f"Integration {integration_type.value} for agent {agent_id_from_key} needs restart (current status: {current_status.status}). Attempting restart.")
                try:
                    integration_settings_json_b = await r.get(config_key)
                    if not integration_settings_json_b:
                        logger.error(f"Integration config data not found for {config_key} despite key existing. Cannot restart.")
                        continue
                    
                    integration_settings = json.loads(integration_settings_json_b.decode('utf-8'))
                    
                    success = await pms.restart_integration_process(
                        agent_id_from_key, 
                        integration_type, 
                        r, 
                        integration_settings=integration_settings
                    )
                    if success:
                        logger.info(f"Successfully initiated restart for integration {integration_type.value} of agent {agent_id_from_key}.")
                    else:
                        logger.warning(f"Restart attempt failed for integration {integration_type.value} of agent {agent_id_from_key} (restart_integration_process returned False).")
                except json.JSONDecodeError:
                    logger.error(f"Failed to parse integration settings from {config_key} (value: {integration_settings_json_b[:100] if integration_settings_json_b else 'None'}). Cannot restart.")
                except Exception as e_restart_int:
                    logger.error(f"Error during restart attempt for integration {integration_type.value} of agent {agent_id_from_key}: {e_restart_int}", exc_info=True)
            await asyncio.sleep(0.01) # Yield control
    except RedisConnectionError: # Restored
        logger.error("Redis connection error during integration restart check.")
    except Exception as e: # Restored
        logger.error(f"Unexpected error in check_and_restart_crashed_integrations: {e}", exc_info=True)

async def main_loop():
    global shutdown_requested
    logger.info("Starting inactivity monitor worker.")
    
    redis_url = settings.REDIS_URL
    if not redis_url:
        logger.critical("REDIS_URL not configured. Inactivity monitor worker cannot start.")
        return
        
    redis_client = None
    try:
        redis_client = await redis.from_url(redis_url, decode_responses=True)
        await redis_client.ping()
        logger.info(f"Successfully connected to Redis at {redis_url}")
    except Exception as e:
        logger.critical(f"Could not connect to Redis: {e}", exc_info=True)
        return

    agent_inactivity_timeout = settings.AGENT_INACTIVITY_TIMEOUT
    agent_inactivity_check_interval = settings.AGENT_INACTIVITY_CHECK_INTERVAL

    logger.info(f"Agent inactivity timeout set to {agent_inactivity_timeout} seconds.")
    logger.info(f"Agent inactivity check interval set to {agent_inactivity_check_interval} seconds.")

    # async_session_factory = get_async_session_factory() # If DB interaction is needed

    # Initial check for crashed/stopped processes shortly after startup
    # Wait a bit for the application to stabilize if Redis was just connected
    if redis_client and not shutdown_requested:
        logger.info("Performing initial check for crashed/stopped agents and integrations...")
        await asyncio.sleep(5) # Brief pause before initial aggressive checks
        await check_and_restart_crashed_agents(redis_client)
        await check_and_restart_crashed_integrations(redis_client)
        logger.info("Initial check for crashed/stopped processes completed.")

    while not shutdown_requested:
        try:
            await asyncio.sleep(agent_inactivity_check_interval)
            if shutdown_requested:
                break
            
            logger.debug("Running inactivity check...")
            current_time = time.time()
            
            async for status_key in redis_client.scan_iter("agent_status:*"):
                if shutdown_requested:
                    break
                agent_id = status_key.split(":")[-1] # Get agent_id from key (e.g., agent_status:some_agent_id)
                try:
                    status_data = await redis_client.hgetall(status_key)
                    status = status_data.get("status")
                    last_active_str = status_data.get("last_active")
                    pid_str = status_data.get("pid")

                    if status == "running" and last_active_str and pid_str:
                        try:
                            last_active = float(last_active_str)
                            pid = int(pid_str)
                            if (current_time - last_active) > agent_inactivity_timeout:
                                logger.info(f"Agent {agent_id} (PID: {pid}) inactive for more than {agent_inactivity_timeout} seconds. Scheduling stop.")
                                try:
                                    os.kill(pid, 0) # Check if process exists before attempting to stop
                                    # Instead of calling an API, we can attempt to stop it directly
                                    # or signal another service. For simplicity, direct stop here.
                                    await stop_agent_process_directly(agent_id, pid, redis_client)
                                except ProcessLookupError:
                                    logger.warning(f"Agent {agent_id} (PID: {pid}) process not found during inactivity check, but status was 'running'. Cleaning up status.")
                                    await redis_client.hset(status_key, mapping={"status": "error_process_lost", "pid": "", "last_active": ""})
                                except Exception as check_err:
                                    logger.error(f"Error checking PID {pid} for inactive agent {agent_id}: {check_err}. Skipping stop.")
                        except (ValueError, TypeError) as e:
                            logger.warning(f"Invalid last_active ('{last_active_str}') or pid ('{pid_str}') for agent {agent_id}: {e}")
                        except Exception as stop_err:
                            logger.error(f"Error processing inactive agent {agent_id}: {stop_err}", exc_info=True)
                except Exception as e:
                    logger.error(f"Error processing agent status key {status_key} during inactivity check: {e}", exc_info=True)

            # Periodically check for crashed/stopped agents and integrations to restart
            if not shutdown_requested and redis_client: # Ensure client is still valid
                logger.debug("Running periodic check for crashed/stopped agents and integrations.")
                await check_and_restart_crashed_agents(redis_client)
                await check_and_restart_crashed_integrations(redis_client)

        except RedisConnectionError as e: # Changed redis.exceptions.ConnectionError
            logger.error(f"Redis connection error: {e}. Attempting to reconnect...")
            if redis_client:
                try:
                    await redis_client.close()
                except Exception as close_exc:
                    logger.error(f"Error closing old Redis connection: {close_exc}")
            
            reconnected = False
            for i in range(settings.REDIS_RECONNECT_ATTEMPTS):
                if shutdown_requested: break
                await asyncio.sleep(settings.REDIS_RECONNECT_DELAY * (i + 1))
                try:
                    redis_client = await redis.from_url(redis_url, decode_responses=True)
                    await redis_client.ping()
                    logger.info("Successfully reconnected to Redis.")
                    reconnected = True
                    break
                except Exception as recon_e:
                    logger.error(f"Redis reconnection attempt {i+1} failed: {recon_e}")
            
            if not reconnected and not shutdown_requested:
                logger.critical("Failed to reconnect to Redis after multiple attempts. Worker will exit.")
                shutdown_requested = True # Trigger shutdown
        
        except asyncio.CancelledError:
            logger.info("Inactivity monitor worker task was cancelled.")
            break # Exit loop on cancellation
        except Exception as e:
            logger.error(f"An unexpected error occurred in the inactivity monitor main loop: {e}", exc_info=True)
            if not shutdown_requested:
                await asyncio.sleep(30) # Avoid busy-looping on unexpected errors

    if redis_client:
        try:
            await redis_client.close()
            logger.info("Redis client closed for inactivity monitor.")
        except Exception as e:
            logger.error(f"Error closing Redis client during inactivity monitor shutdown: {e}")
            
    logger.info("Inactivity monitor worker has shut down.")

if __name__ == "__main__":
    # Basic logging setup for standalone execution
    logging.basicConfig(
        level=settings.LOG_LEVEL,
        format='%(asctime)s - %(levelname)s - %(name)s - %(message)s',
        handlers=[logging.StreamHandler()]
    )
    
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    try:
        asyncio.run(main_loop())
    except KeyboardInterrupt:
        logger.info("Inactivity monitor worker interrupted by user (KeyboardInterrupt).")
    except Exception as e:
        logger.critical(f"Inactivity monitor worker failed to start or run: {e}", exc_info=True)
    finally:
        logger.info("Inactivity monitor worker application finished.")

