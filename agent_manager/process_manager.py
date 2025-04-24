import os
import subprocess
import logging
import signal
import asyncio
import time # Добавляем time для сравнения
from typing import Optional
import redis.asyncio as redis
from fastapi import HTTPException, status as fastapi_status

from .models import AgentStatus, IntegrationStatus, IntegrationType
# Импортируем переменные из config, если они там определены, или используем os.getenv напрямую
# from .config import AGENT_RUNNER_SCRIPT, PYTHON_EXECUTABLE, MANAGER_HOST, MANAGER_PORT, REDIS_URL
# Предполагаем, что таймауты берутся из окружения
AGENT_INACTIVITY_TIMEOUT = int(os.getenv("AGENT_INACTIVITY_TIMEOUT", "1800")) # 30 минут по умолчанию
AGENT_INACTIVITY_CHECK_INTERVAL = int(os.getenv("AGENT_INACTIVITY_CHECK_INTERVAL", "300")) # 5 минут по умолчанию

logger = logging.getLogger(__name__)

# --- Agent Process Management ---

# Определяем корневой каталог проекта (предполагаем, что process_manager.py находится в agent_manager/)
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
RUNNER_SCRIPT_PATH = os.path.join(PROJECT_ROOT, 'agent_runner', 'runner.py')

async def get_agent_status(agent_id: str, r: redis.Redis) -> AgentStatus:
    """Retrieves agent status from Redis, checking process existence if running."""
    status_key = f"agent_status:{agent_id}"
    status_data = await r.hgetall(status_key)

    if not status_data:
        # Check if config exists to differentiate between stopped and non-existent
        config_exists = await r.exists(f"agent_config:{agent_id}")
        if config_exists:
            return AgentStatus(agent_id=agent_id, status="stopped")
        else:
            # Raise specific error or return a distinct status? Let's return status for now.
            # Consider raising HTTPException if called directly from API layer.
            return AgentStatus(agent_id=agent_id, status="not_found")

    current_status = status_data.get("status", "unknown")
    pid_val = status_data.get("pid")
    pid = int(pid_val) if pid_val and pid_val.isdigit() else None
    last_active_val = status_data.get("last_active")
    try:
        last_active = float(last_active_val) if last_active_val else None
    except (ValueError, TypeError):
        last_active = None

    # If status is 'running' or 'starting', verify the process is actually alive
    if pid and current_status in ["running", "starting", "initializing"]:
        try:
            os.kill(pid, 0) # Check if process exists
        except ProcessLookupError:
            logger.warning(f"Agent {agent_id} status is '{current_status}' in Redis, but PID {pid} not found. Updating status to 'error_process_lost'.")
            current_status = "error_process_lost"
            pid = None # Clear PID as it's invalid
            # Update Redis status
            async with r.pipeline(transaction=True) as pipe:
                 pipe.hset(status_key, "status", current_status)
                 pipe.hdel(status_key, "pid")
                 await pipe.execute()
        except OSError as e:
            # Permission error or other OS issue checking PID
            logger.error(f"Error checking PID {pid} for agent {agent_id}: {e}. Status remains '{current_status}'.")

    return AgentStatus(
        agent_id=agent_id,
        status=current_status,
        pid=pid,
        last_active=last_active,
    )

async def start_agent_process(agent_id: str, r: redis.Redis) -> bool:
    """
    Starts the agent runner subprocess.

    Returns:
        bool: True if start was successful or agent already running, False otherwise.
    Raises:
        FileNotFoundError: If the agent runner script is not found.
        ValueError: If agent configuration is missing or invalid (requires DB check).
        RuntimeError: If the process fails to launch for other reasons.
    """
    status_key = f"agent_status:{agent_id}"
    try:
        status = await get_agent_status(agent_id, r)
        if status.status == "running" and status.pid:
            try:
                os.kill(status.pid, 0)
                logger.warning(f"Agent {agent_id} already running with PID {status.pid}. Skipping start.")
                return True
            except ProcessLookupError:
                logger.warning(f"Agent {agent_id} reported running but PID {status.pid} not found. Attempting to start.")
            except OSError as e:
                logger.error(f"Error checking PID {status.pid} for agent {agent_id}: {e}. Attempting to start anyway.")
        elif status.status == "starting":
            logger.warning(f"Agent {agent_id} is already starting. Skipping duplicate start request.")
            return True
        elif status.status == "not_found":
             # This implies config might be missing if we rely solely on Redis status
             # A DB check here would be more robust before attempting start
             # For now, proceed but log a warning. A DB check should raise ValueError earlier.
             logger.warning(f"Agent status key not found for {agent_id}. Config existence not verified here. Proceeding with start attempt.")
             # raise ValueError(f"Agent configuration for {agent_id} not found.") # Uncomment if DB check added

    except Exception as e:
        logger.error(f"Error checking agent status before start for {agent_id}: {e}")
        # Don't prevent start attempt based on status check error, but log it.

    logger.info(f"Starting agent process for {agent_id}...")
    manager_host = os.getenv("MANAGER_HOST", "localhost") # Use localhost if run locally
    manager_port = os.getenv("MANAGER_PORT", "8000")
    config_url = f"http://{manager_host}:{manager_port}/agents/{agent_id}/config"
    redis_url_for_agent = os.getenv("REDIS_URL", "redis://localhost:6379")

    try:
        python_executable = sys.executable # Используем тот же python, что и менеджер
        # Используем рассчитанный путь к скрипту
        script_path = RUNNER_SCRIPT_PATH

        if not os.path.exists(script_path):
            logger.error(f"Agent runner script not found at calculated path: {script_path}")
            await r.hset(status_key, "status", "error_script_not_found")
            raise FileNotFoundError(f"Agent runner script not found: {script_path}")

        cmd = [
            python_executable,
            "-m", # Use the -m flag to run as a module
            "agent_runner.runner", # Specify the module path
            "--agent-id", agent_id,
            "--config-url", config_url,
            "--redis-url", redis_url_for_agent
        ]

        process = subprocess.Popen(cmd)

        await r.hset(status_key, mapping={
            "status": "starting",
            "pid": process.pid
        })
        logger.info(f"Agent process {agent_id} started with PID {process.pid}")
        return True
    except FileNotFoundError as e:
         raise e # Re-raise specific error
    except Exception as e:
        logger.error(f"Failed to start agent process {agent_id}: {e}", exc_info=True)
        await r.hset(status_key, "status", "error_start_failed")
        # Raise a generic runtime error
        raise RuntimeError(f"Failed to launch agent process {agent_id}: {e}")

async def stop_agent_process(agent_id: str, r: redis.Redis, force: bool = False) -> bool:
    """
    Stops the agent runner subprocess using PID from Redis.

    Args:
        agent_id: The ID of the agent to stop.
        r: Async Redis client instance.
        force: If True, sends SIGKILL if SIGTERM fails or after a timeout.

    Returns:
        bool: True if the process was stopped or already stopped, False otherwise.
    """
    status_key = f"agent_status:{agent_id}"
    try:
        status = await get_agent_status(agent_id, r)
        pid_to_stop = status.pid

        if status.status == "stopped":
             logger.info(f"Agent {agent_id} is already stopped.")
             if pid_to_stop: await r.hdel(status_key, "pid") # Clean up stale PID if present
             return True

        if not pid_to_stop:
            logger.warning(f"Attempted to stop agent {agent_id}, but no PID found. Setting status to stopped.")
            await r.hset(status_key, "status", "stopped")
            return True

        logger.info(f"Stopping agent process {agent_id} (PID: {pid_to_stop}). Force: {force}")
        await r.hset(status_key, "status", "stopping")

        try:
            # Send SIGTERM
            os.kill(pid_to_stop, signal.SIGTERM)
            logger.info(f"SIGTERM sent to agent {agent_id} (PID: {pid_to_stop})")

            # Wait for process to terminate
            wait_time = 5 # seconds
            try:
                # Use asyncio.wait_for with a helper that checks os.kill
                async def check_pid():
                    while True:
                        try:
                            os.kill(pid_to_stop, 0) # Check if process exists
                            await asyncio.sleep(0.5) # Wait before next check
                        except ProcessLookupError:
                            return True # Process is gone
                await asyncio.wait_for(check_pid(), timeout=wait_time)
                logger.info(f"Agent {agent_id} (PID: {pid_to_stop}) terminated gracefully after SIGTERM.")

            except asyncio.TimeoutError:
                logger.warning(f"Agent {agent_id} (PID: {pid_to_stop}) did not terminate within {wait_time}s after SIGTERM.")
                if force:
                    logger.warning(f"Forcing stop with SIGKILL for agent {agent_id} (PID: {pid_to_stop}).")
                    try:
                        os.kill(pid_to_stop, signal.SIGKILL)
                        logger.info(f"SIGKILL sent to agent {agent_id} (PID: {pid_to_stop}).")
                        # Assume killed, update status
                    except ProcessLookupError:
                         logger.info(f"Agent {agent_id} (PID: {pid_to_stop}) already terminated before SIGKILL.")
                    except Exception as kill_e:
                         logger.error(f"Error sending SIGKILL to agent {agent_id} (PID: {pid_to_stop}): {kill_e}")
                         # Status remains 'stopping', manual intervention might be needed
                         return False # Indicate stop failed
                else:
                    # Not forcing, process might still be running
                    logger.warning(f"Agent {agent_id} (PID: {pid_to_stop}) might still be running. Set force=True to use SIGKILL.")
                    return False # Indicate graceful stop failed

            # If process terminated (gracefully or via SIGKILL)
            await r.hset(status_key, "status", "stopped")
            await r.hdel(status_key, "pid")
            await r.hdel(status_key, "last_active") # Clear last active time
            return True

        except ProcessLookupError:
            logger.warning(f"Agent process {agent_id} (PID: {pid_to_stop}) not found when trying to stop (already stopped?).")
            await r.hset(status_key, "status", "stopped")
            await r.hdel(status_key, "pid")
            await r.hdel(status_key, "last_active")
            return True
        except Exception as e:
            logger.error(f"Error stopping agent process {agent_id} (PID: {pid_to_stop}): {e}")
            # Status remains 'stopping'
            return False

    except Exception as e:
        # Handle errors during status check etc.
        logger.error(f"Could not stop agent {agent_id}: Error during status check or pre-stop phase: {e}")
        return False

async def restart_agent_process(agent_id: str, r: redis.Redis) -> bool:
    """Restarts the agent runner subprocess."""
    logger.info(f"Restarting agent process for {agent_id}...")
    # Stop forcefully first to ensure the old process is gone
    stopped = await stop_agent_process(agent_id, r, force=True)
    if not stopped:
        logger.error(f"Failed to stop agent {agent_id} during restart. Aborting restart.")
        # Status might be 'stopping' or 'error', leave it as is
        return False

    # Short delay to allow OS/Redis updates
    await asyncio.sleep(1)

    logger.info(f"Agent {agent_id} stopped. Proceeding to start...")
    try:
        started = await start_agent_process(agent_id, r)
        if started:
            logger.info(f"Agent {agent_id} restarted successfully.")
            return True
        else:
            logger.error(f"Failed to start agent {agent_id} after stopping during restart.")
            # start_agent_process should have set an error status
            return False
    except Exception as e:
        logger.error(f"Exception during agent start phase of restart for {agent_id}: {e}", exc_info=True)
        # Ensure status reflects the failure if start_agent_process didn't raise a specific error handled by API
        status_key = f"agent_status:{agent_id}"
        await r.hset(status_key, "status", "error_start_failed")
        return False

async def check_process_termination(pid: int):
    """Polls to check if a process with the given PID has terminated."""
    while True:
        try:
            os.kill(pid, 0)
            await asyncio.sleep(0.5) # Wait before checking again
        except ProcessLookupError:
            return # Process is gone
        except OSError as e:
             logger.error(f"Error checking PID {pid} during termination wait: {e}")
             return # Assume terminated on error to avoid infinite loop

# --- Background Task for Inactivity Check ---
async def check_inactive_agents(r: redis.Redis):
    """Periodically checks for inactive agents and stops them."""
    logger.info(f"Starting background task to check for inactive agents every {AGENT_INACTIVITY_CHECK_INTERVAL} seconds.")
    logger.info(f"Agent inactivity timeout set to {AGENT_INACTIVITY_TIMEOUT} seconds.")
    while True:
        try:
            await asyncio.sleep(AGENT_INACTIVITY_CHECK_INTERVAL)
            logger.debug("Running inactivity check...")
            current_time = time.time()
            async for status_key in r.scan_iter("agent_status:*"):
                agent_id = status_key.split(":")[1] # Получаем agent_id из ключа
                try:
                    status_data = await r.hgetall(status_key)
                    status = status_data.get("status")
                    last_active_str = status_data.get("last_active")
                    pid_str = status_data.get("pid")

                    if status == "running" and last_active_str and pid_str:
                        try:
                            last_active = float(last_active_str)
                            pid = int(pid_str)
                            if (current_time - last_active) > AGENT_INACTIVITY_TIMEOUT:
                                logger.info(f"Agent {agent_id} (PID: {pid}) inactive for more than {AGENT_INACTIVITY_TIMEOUT} seconds. Stopping.")
                                # Проверяем, существует ли процесс перед остановкой
                                try:
                                     os.kill(pid, 0)
                                     await stop_agent_process(agent_id, r, force=False) # Пытаемся остановить штатно
                                except ProcessLookupError:
                                     logger.warning(f"Agent {agent_id} (PID: {pid}) process not found during inactivity check, but status was 'running'. Cleaning up status.")
                                     await r.hset(status_key, "status", "stopped")
                                     await r.hdel(status_key, "pid", "last_active")
                                except Exception as check_err:
                                     logger.error(f"Error checking PID {pid} for inactive agent {agent_id}: {check_err}. Skipping stop.")

                        except (ValueError, TypeError) as e:
                            logger.warning(f"Invalid last_active ('{last_active_str}') or pid ('{pid_str}') for agent {agent_id}: {e}")
                        except Exception as stop_err:
                             logger.error(f"Error stopping inactive agent {agent_id}: {stop_err}", exc_info=True)
                except Exception as e:
                    logger.error(f"Error processing agent status key {status_key} during inactivity check: {e}", exc_info=True)

        except asyncio.CancelledError:
            logger.info("Inactivity check task cancelled.")
            break
        except redis.exceptions.ConnectionError as e:
             logger.error(f"Redis connection error in inactivity check task: {e}. Retrying after delay...")
             await asyncio.sleep(60) # Ждем минуту перед повторной попыткой
        except Exception as e:
            logger.error(f"Unexpected error in inactivity check task: {e}", exc_info=True)
            # Продолжаем работу после небольшой паузы
            await asyncio.sleep(30)

# --- Integration Process Management ---

def _get_integration_status_key(agent_id: str, integration_type: IntegrationType) -> str:
    """Helper to get the Redis key for integration status."""
    return f"integration_status:{agent_id}:{integration_type.value}"

async def get_integration_status(agent_id: str, integration_type: IntegrationType, r: redis.Redis) -> IntegrationStatus:
    """Retrieves the status of a specific integration process from Redis."""
    status_key = _get_integration_status_key(agent_id, integration_type)
    # Assuming redis client 'r' uses decode_responses=True, hgetall returns Dict[str, str]
    status_data = await r.hgetall(status_key)

    if not status_data:
        # The existence of the agent config is already checked in the API layer (using the DB).
        # If no status data exists in Redis for this integration, assume it's stopped.
        return IntegrationStatus(agent_id=agent_id, integration_type=integration_type, status="stopped")

    # Use string keys since decode_responses=True is expected
    pid_val = status_data.get("pid")
    pid = int(pid_val) if pid_val and pid_val.isdigit() else None

    last_active_val = status_data.get("last_active")
    try:
        last_active = float(last_active_val) if last_active_val else None
    except (ValueError, TypeError):
        logger.warning(f"Invalid last_active value '{last_active_val}' for {agent_id}/{integration_type.value}, setting to None.")
        last_active = None

    # Status is already a string, no need to decode. Use string key.
    current_status = status_data.get("status", "unknown")

    # Optional: Add PID check here as well, similar to get_agent_status
    if pid and current_status in ["running", "starting"]:
        try:
            os.kill(pid, 0) # Check if process exists
        except ProcessLookupError:
            logger.warning(f"Integration {agent_id}/{integration_type.value} status is '{current_status}' in Redis, but PID {pid} not found. Updating status to 'error_process_lost'.")
            current_status = "error_process_lost"
            pid = None # Clear PID as it's invalid
            # Update Redis status (consider if this should be done here or rely on next start/stop)
            async with r.pipeline(transaction=True) as pipe:
                 pipe.hset(status_key, "status", current_status)
                 pipe.hdel(status_key, "pid")
                 await pipe.execute()
        except OSError as e:
            logger.error(f"Error checking PID {pid} for integration {agent_id}/{integration_type.value}: {e}. Status remains '{current_status}'.")


    return IntegrationStatus(
        agent_id=agent_id,
        integration_type=integration_type,
        status=current_status, # Already a string
        pid=pid,
        last_active=last_active,
    )

async def start_integration_process(agent_id: str, integration_type: IntegrationType, r: redis.Redis) -> bool:
    """Starts a specific integration subprocess (e.g., Telegram bot)."""
    status_key = _get_integration_status_key(agent_id, integration_type)

    # Check current status
    try:
        status = await get_integration_status(agent_id, integration_type, r)
        if status.status == "running" and status.pid:
            try:
                os.kill(status.pid, 0) # Check if process exists
                logger.warning(f"{integration_type.value} integration for agent {agent_id} already running (PID {status.pid}). Skipping.")
                return True
            except ProcessLookupError:
                logger.warning(f"{integration_type.value} integration for {agent_id} reported running but PID {status.pid} not found. Attempting start.")
            except OSError as e:
                logger.error(f"Error checking PID {status.pid} for {integration_type.value} integration {agent_id}: {e}. Starting anyway.")
        elif status.status == "starting":
            logger.warning(f"{integration_type.value} integration for {agent_id} is already starting. Skipping.")
            return True
    except HTTPException as e: # Handles potential 404 if status key doesn't exist but agent does
        logger.info(f"No existing status found for {integration_type.value} integration for {agent_id}. Proceeding with start. (Detail: {e.detail})")
        # This is expected if the integration was never started or was properly stopped.

    logger.info(f"Starting {integration_type.value} integration process for agent {agent_id}...")

    python_executable = sys.executable # Используем тот же python, что и менеджер
    module_path = None # Имя модуля для -m
    script_to_check = None # Путь к файлу для проверки существования

    if integration_type == IntegrationType.TELEGRAM:
        # Путь к файлу для проверки
        script_to_check = os.path.join(PROJECT_ROOT, 'integrations', 'telegram_bot.py')
        # Имя модуля для запуска через -m
        module_path = 'integrations.telegram_bot'
        # args = ["--agent-id", agent_id] # Аргументы передаются ниже
    # Add other integration types here
    # elif integration_type == IntegrationType.WEBCHAT:
    #     script_to_check = ...
    #     module_path = ...
    else:
        logger.error(f"Starting integration type '{integration_type.value}' is not implemented.")
        await r.hset(status_key, "status", f"error_unsupported_type")
        return False

    # Проверяем существование файла скрипта
    if not script_to_check or not os.path.exists(script_to_check):
        logger.error(f"{integration_type.value} integration script not found at expected location: {script_to_check}")
        await r.hset(status_key, "status", "error_script_not_found")
        return False

    try:
        # Define redis_url for the integration process
        redis_url = os.getenv("REDIS_URL", "redis://localhost:6379")

        cmd = [
            python_executable,
            "-m", # Use -m
            module_path, # Specify the module path (e.g., integrations.telegram_bot)
            "--agent-id", agent_id,
            # Add other necessary args like --redis-url if needed by the integration script
            "--redis-url", redis_url
        ]
        # Добавляем рабочую директорию, чтобы python мог найти модуль
        process_cwd = PROJECT_ROOT

        # Log the command being executed for debugging
        logger.debug(f"Executing integration command: {' '.join(cmd)} in cwd: {process_cwd}")

        process = subprocess.Popen(cmd, cwd=process_cwd) # Указываем cwd
        await r.hset(status_key, mapping={
            "status": "starting",
            "pid": process.pid
        })
        logger.info(f"{integration_type.value} integration process for {agent_id} started with PID {process.pid}")
        # The integration process itself should update status to 'running' if possible,
        # or we rely on this 'starting' status.
        # For telegram bot, it doesn't update status itself, so we might set it to running here after a delay,
        # or just rely on PID check. Let's set it to running after a short delay for now.
        async def update_to_running_delayed():
             await asyncio.sleep(3) # Give it a few seconds to potentially fail
             current_status_val = await r.hget(status_key, "status") # hget can return bytes or str
             current_status = None
             if isinstance(current_status_val, bytes):
                 current_status = current_status_val.decode('utf-8')
             elif isinstance(current_status_val, str):
                 current_status = current_status_val # Already a string

             if current_status == "starting": # Only update if still starting
                 await r.hset(status_key, "status", "running")
                 logger.info(f"Set {integration_type.value} integration status to 'running' for {agent_id} (PID: {process.pid})")
        asyncio.create_task(update_to_running_delayed())

        return True
    except Exception as e:
        logger.error(f"Failed to start {integration_type.value} integration process for {agent_id}: {e}", exc_info=True)
        await r.hset(status_key, "status", "error_start_failed")
        return False

async def stop_integration_process(agent_id: str, integration_type: IntegrationType, r: redis.Redis, force: bool = False) -> bool:
    """Stops a specific integration subprocess using PID from Redis."""
    status_key = _get_integration_status_key(agent_id, integration_type)

    try:
        status = await get_integration_status(agent_id, integration_type, r)
        pid_to_stop = status.pid

        if not pid_to_stop:
            logger.warning(f"Attempted to stop {integration_type.value} integration for {agent_id}, but no PID found. Setting status to stopped.")
            await r.hset(status_key, "status", "stopped")
            await r.hdel(status_key, "pid")
            return True

        signal_to_send = signal.SIGKILL if force else signal.SIGTERM
        signal_name = "SIGKILL" if force else "SIGTERM"
        logger.info(f"Stopping {integration_type.value} integration process for {agent_id} (PID: {pid_to_stop}) using {signal_name}...")

        try:
            os.kill(pid_to_stop, signal_to_send)
            logger.info(f"{signal_name} signal sent to {integration_type.value} integration {agent_id} (PID: {pid_to_stop})")

            if force:
                await r.hset(status_key, "status", "stopped")
                await r.hdel(status_key, "pid")
                logger.info(f"Force stopped {integration_type.value} integration {agent_id} (PID: {pid_to_stop}). Status set to stopped.")
            else:
                await r.hset(status_key, "status", "stopping")
                # Check after delay if not forced
                async def check_termination():
                    await asyncio.sleep(3)
                    try:
                        os.kill(pid_to_stop, 0) # Check if still alive
                        logger.warning(f"{integration_type.value} integration {agent_id} (PID: {pid_to_stop}) still alive after {signal_name}. Manual check or force stop may be needed.")
                    except ProcessLookupError:
                        logger.info(f"{integration_type.value} integration {agent_id} (PID: {pid_to_stop}) terminated successfully.")
                        await r.hset(status_key, "status", "stopped")
                        await r.hdel(status_key, "pid")
                asyncio.create_task(check_termination())

            return True

        except ProcessLookupError:
            logger.warning(f"{integration_type.value} integration process {agent_id} (PID: {pid_to_stop}) not found when trying to stop.")
            await r.hset(status_key, "status", "stopped")
            await r.hdel(status_key, "pid")
            return True
        except Exception as e:
            logger.error(f"Error stopping {integration_type.value} integration process {agent_id} (PID: {pid_to_stop}): {e}")
            return False

    except HTTPException as e:
        logger.warning(f"Could not stop {integration_type.value} integration for {agent_id}: {e.detail}")
        return False

async def restart_integration_process(agent_id: str, integration_type: IntegrationType, r: redis.Redis) -> bool:
    """Restarts a specific integration subprocess."""
    logger.info(f"Restarting {integration_type.value} integration for agent {agent_id}...")
    # Stop forcefully first
    stopped = await stop_integration_process(agent_id, integration_type, r, force=True)
    if not stopped:
        logger.error(f"Failed to stop {integration_type.value} integration for {agent_id} during restart. Aborting restart.")
        return False

    # Short delay
    await asyncio.sleep(1)

    logger.info(f"{integration_type.value} integration for {agent_id} stopped. Proceeding to start...")
    try:
        started = await start_integration_process(agent_id, integration_type, r)
        if started:
            logger.info(f"{integration_type.value} integration for {agent_id} restarted successfully.")
            return True
        else:
            logger.error(f"Failed to start {integration_type.value} integration for {agent_id} after stopping during restart.")
            return False
    except Exception as e:
        logger.error(f"Exception during integration start phase of restart for {agent_id}/{integration_type.value}: {e}", exc_info=True)
        status_key = _get_integration_status_key(agent_id, integration_type)
        await r.hset(status_key, "status", "error_start_failed")
        return False

import sys
