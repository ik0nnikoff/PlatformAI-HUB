import asyncio
import json
import logging
import os
import signal
import subprocess # Keep for Popen if still used for integrations, or remove if all async
import sys # Remove if sys.executable is no longer used
import time
from typing import Optional, Dict, Any

import redis.asyncio as redis
from fastapi import HTTPException

from app.api.schemas.agent_schemas import AgentStatus
from app.api.schemas.common_schemas import IntegrationStatus, IntegrationType
from app.core.config import settings

logger = logging.getLogger(__name__)

# --- Environment/Config Variables ---
AGENT_INACTIVITY_TIMEOUT = settings.AGENT_INACTIVITY_TIMEOUT
AGENT_INACTIVITY_CHECK_INTERVAL = settings.AGENT_INACTIVITY_CHECK_INTERVAL
MANAGER_HOST = settings.MANAGER_HOST
MANAGER_PORT = settings.MANAGER_PORT
REDIS_URL = str(settings.REDIS_URL)

# --- Paths ---
APP_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
PROJECT_ROOT = os.path.abspath(os.path.join(APP_DIR, '..'))

AGENT_RUNNER_MODULE_PATH = settings.AGENT_RUNNER_MODULE_PATH
AGENT_RUNNER_FILE_PATH = settings.AGENT_RUNNER_SCRIPT_FULL_PATH

INTEGRATION_MODULE_PATHS = {
    IntegrationType.TELEGRAM: "app.integrations.telegram.telegram_bot_main"
}
INTEGRATION_FILE_PATHS = {
    IntegrationType.TELEGRAM: os.path.join(PROJECT_ROOT, "app", "integrations", "telegram", "telegram_bot_main.py")
}


# --- Agent Process Management ---

async def get_agent_status(agent_id: str, r: redis.Redis) -> AgentStatus:
    """Retrieves agent status from Redis, checking process/container existence if running."""
    status_key = f"agent_status:{agent_id}"
    status_data = await r.hgetall(status_key) # Changed: r.hgetall with decode_responses=True returns Dict[str, str]

    if not status_data:
        return AgentStatus(agent_id=agent_id, status="not_found", pid=None, last_active=None)

    current_status = status_data.get("status", "unknown")
    pid_val = status_data.get("pid")
    pid = int(pid_val) if pid_val and pid_val.isdigit() else None
    last_active_val = status_data.get("last_active")
    try:
        last_active = float(last_active_val) if last_active_val else None
    except (ValueError, TypeError):
        last_active = None
    
    runtime = status_data.get("runtime", "local")

    if pid and current_status in ["running", "starting", "initializing"]:
        if runtime == "local":
            try:
                os.kill(pid, 0)  # Check if host process exists
            except ProcessLookupError:
                logger.warning(f"Agent {agent_id} (local) status is '{current_status}' in Redis, but PID {pid} not found. Updating status to 'error_process_lost'.")
                current_status = "error_process_lost"
                pid = None  # Clear PID as it's gone
                async with r.pipeline(transaction=True) as pipe:
                    pipe.hset(status_key, "status", current_status)
                    pipe.hdel(status_key, "pid")
                    await pipe.execute()
            except OSError as e:  # Other errors like permission denied
                logger.error(f"Error checking PID {pid} for local agent {agent_id}: {e}. Status remains '{current_status}'.")
        elif runtime == "docker":
            container_name = status_data.get("container_name")
            if container_name:
                try:
                    check_cmd_args = ["docker", "ps", "-q", "-f", f"name=^{container_name}$"] # Anchor name
                    process = await asyncio.create_subprocess_exec(
                        *check_cmd_args,
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.PIPE
                    )
                    stdout, stderr = await process.communicate()
                    if process.returncode == 0 and stdout.decode().strip():
                        logger.debug(f"Docker container {container_name} for agent {agent_id} is running.")
                    else:
                        logger.warning(f"Agent {agent_id} (Docker container {container_name}) status is '{current_status}' in Redis, but container not found (or docker ps error). RC:{process.returncode}, stdout: {stdout.decode()}, stderr: {stderr.decode()}. Updating status to 'error_process_lost'.")
                        current_status = "error_process_lost"
                        async with r.pipeline(transaction=True) as pipe:
                            pipe.hset(status_key, "status", current_status)
                            # Optionally clear pid/container_name if desired
                            # pipe.hdel(status_key, "pid", "container_name")
                            await pipe.execute()
                except FileNotFoundError:
                    logger.error("'docker' command not found. Cannot check status of Dockerized agents.")
                except Exception as e_docker_ps:
                    logger.error(f"Error checking Docker container status for {container_name}: {e_docker_ps}", exc_info=True)
            else:
                logger.warning(f"Agent {agent_id} has runtime 'docker' but no container_name in Redis. Cannot verify status.")


    return AgentStatus(
        agent_id=agent_id,
        status=current_status,
        pid=pid,
        last_active=last_active,
    )

async def start_agent_process(agent_id: str, r: redis.Redis) -> bool:
    """
    Starts the agent runner subprocess, either locally or via Docker.
    Assumes agent configuration exists (checked by API layer).
    """
    status_key = f"agent_status:{agent_id}"
    try:
        current_agent_status = await get_agent_status(agent_id, r)
        if current_agent_status.status == "running" and (current_agent_status.pid or settings.RUN_AGENTS_WITH_DOCKER): # If docker, PID might not be there but container might
            # For docker, get_agent_status would have checked container. If still "running", it's likely fine.
            logger.warning(f"Agent {agent_id} already reported as running (status: {current_agent_status.status}). Skipping start.")
            return True
        elif current_agent_status.status == "starting":
            logger.warning(f"Agent {agent_id} is already starting. Skipping duplicate start request.")
            return True
    except Exception as e:
        logger.error(f"Error checking agent status before start for {agent_id}: {e}", exc_info=True)

    logger.info(f"Starting agent process for {agent_id}...")
    config_url = f"http://{MANAGER_HOST}:{MANAGER_PORT}{settings.API_V1_STR}/agents/{agent_id}/config"

    try:
        process_env = os.environ.copy()
        process_env["PYTHONPATH"] = PROJECT_ROOT + (os.pathsep + process_env.get("PYTHONPATH", "") if process_env.get("PYTHONPATH") else "")


        if settings.RUN_AGENTS_WITH_DOCKER:
            if not settings.AGENT_DOCKER_IMAGE:
                logger.error(f"RUN_AGENTS_WITH_DOCKER is true, but AGENT_DOCKER_IMAGE is not set. Cannot start agent {agent_id}.")
                await r.hset(status_key, "status", "error_config_missing")
                raise ValueError("AGENT_DOCKER_IMAGE not configured.")

            container_name = f"agent_runner_{agent_id}"
            cmd = [
                "docker", "run", "-d", "--rm",
                "--name", container_name,
                "--network", "host",
                "-e", f"AGENT_ID={agent_id}",
                "-e", f"CONFIG_URL={config_url}",
                "-e", f"REDIS_URL={REDIS_URL}",
                "-e", "PYTHONUNBUFFERED=1",
                settings.AGENT_DOCKER_IMAGE
            ]
            logger.info(f"Starting agent {agent_id} using Docker. Command: {' '.join(cmd)}")
            
            # Using asyncio.create_subprocess_exec for non-blocking execution
            docker_process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE, # Capture output to prevent blocking and for logging
                stderr=asyncio.subprocess.PIPE,
                cwd=PROJECT_ROOT, # cwd might not be relevant for docker run, but good practice
                env=process_env
            )
            # We don't wait for docker_process.communicate() here as it's detached (-d)
            # The PID is of the 'docker run -d' command itself, which exits quickly.
            # Storing it might have limited use, but we do for now.
            # The agent inside Docker is responsible for updating its status to "running".
            
            await r.hset(status_key, mapping={
                "status": "starting",
                "pid": str(docker_process.pid), 
                "container_name": container_name,
                "runtime": "docker",
                "last_active": str(time.time())
            })
            logger.info(f"Agent {agent_id} (Docker container {container_name}) initiated start. Docker command PID: {docker_process.pid}.")
            # We can optionally log the stdout/stderr from the docker run -d command if needed,
            # but it usually just prints the container ID.
            # asyncio.create_task(log_subprocess_output(docker_process, f"Docker run {container_name}"))

        else: # Local execution
            python_executable = settings.PYTHON_EXECUTABLE
            module_path = AGENT_RUNNER_MODULE_PATH

            if not os.path.exists(AGENT_RUNNER_FILE_PATH): # AGENT_RUNNER_FILE_PATH is full path
                logger.error(f"Agent runner script not found at: {AGENT_RUNNER_FILE_PATH}")
                await r.hset(status_key, "status", "error_script_not_found")
                raise FileNotFoundError(f"Agent runner script not found: {AGENT_RUNNER_FILE_PATH}")

            cmd = [
                python_executable,
                "-m", module_path,
                "--agent-id", agent_id,
                "--config-url", config_url,
                "--redis-url", REDIS_URL
            ]
            
            logger.debug(f"Executing local agent command: {' '.join(cmd)} in cwd: {PROJECT_ROOT} with PYTHONPATH: {process_env.get('PYTHONPATH')}")
            # Using subprocess.Popen for local script. Assumes script backgrounds itself or is managed.
            local_process = subprocess.Popen(cmd, cwd=PROJECT_ROOT, env=process_env)

            await r.hset(status_key, mapping={
                "status": "starting",
                "pid": str(local_process.pid),
                "runtime": "local",
                "last_active": str(time.time())
            })
            logger.info(f"Local agent process {agent_id} initiated start with PID {local_process.pid}")
        return True
    except FileNotFoundError as fnf_e: # Catch specifically for script/docker not found
        logger.error(f"Failed to start agent process {agent_id} due to FileNotFoundError: {fnf_e}", exc_info=True)
        await r.hset(status_key, "status", "error_script_not_found") # Or more generic error
        raise
    except Exception as e:
        logger.error(f"Failed to start agent process {agent_id}: {e}", exc_info=True)
        await r.hset(status_key, "status", "error_start_failed")
        # Consider re-raising a more specific HTTP error if this service is directly called by API
        raise RuntimeError(f"Failed to launch agent process {agent_id}: {e}")


async def stop_agent_process(agent_id: str, r: redis.Redis, force: bool = False) -> bool:
    """Stops the agent runner subprocess (local or Docker)."""
    status_key = f"agent_status:{agent_id}"
    try:
        status_data = await r.hgetall(status_key) # Changed: r.hgetall with decode_responses=True returns Dict[str, str]
        if not status_data:
            logger.info(f"Agent {agent_id} status not found in Redis. Assuming stopped.")
            return True

        current_status = status_data.get("status", "unknown")
        pid_to_stop_val = status_data.get("pid")
        pid_to_stop = int(pid_to_stop_val) if pid_to_stop_val and pid_to_stop_val.isdigit() else None
        runtime = status_data.get("runtime", "local")
        container_name = status_data.get("container_name")

        if current_status == "stopped":
            logger.info(f"Agent {agent_id} is already stopped.")
            # Ensure cleanup of relevant fields if any linger
            await r.hdel(status_key, "pid", "container_name", "runtime", "last_active")
            return True

        await r.hset(status_key, "status", "stopping")
        stopped_successfully = False

        if runtime == "docker" and container_name:
            logger.info(f"Stopping Docker agent {agent_id} (container: {container_name}). Force: {force}")
            try:
                stop_cmd_args = ["docker", "stop", container_name]
                logger.debug(f"Executing: {' '.join(stop_cmd_args)}")
                process = await asyncio.create_subprocess_exec(
                    *stop_cmd_args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
                stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=15) # docker stop default is 10s

                if process.returncode == 0:
                    logger.info(f"Docker container {container_name} stopped. Output: {stdout.decode().strip()} {stderr.decode().strip()}")
                    stopped_successfully = True
                else:
                    logger.warning(f"docker stop {container_name} failed. RC:{process.returncode}, stdout:{stdout.decode()}, stderr:{stderr.decode()}")
                    if force:
                        logger.warning(f"Forcing stop with docker kill for container {container_name}.")
                        kill_cmd_args = ["docker", "kill", container_name]
                        process_kill = await asyncio.create_subprocess_exec(
                            *kill_cmd_args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
                        stdout_kill, stderr_kill = await asyncio.wait_for(process_kill.communicate(), timeout=10)
                        if process_kill.returncode == 0:
                            logger.info(f"Docker container {container_name} killed. Output: {stdout_kill.decode().strip()} {stderr_kill.decode().strip()}")
                            stopped_successfully = True
                        else:
                            logger.error(f"docker kill {container_name} failed. RC:{process_kill.returncode}, stdout:{stdout_kill.decode()}, stderr:{stderr_kill.decode()}")
            except asyncio.TimeoutError:
                logger.warning(f"docker stop/kill command for {container_name} timed out.")
                if force: # If forced and timed out, assume it's gone or will be.
                    logger.warning(f"Assuming container {container_name} is stopped/gone due to forced timeout on command.")
                    stopped_successfully = True 
            except FileNotFoundError:
                logger.error("'docker' command not found. Cannot stop Dockerized agent.")
            except Exception as e:
                logger.error(f"Error stopping Docker agent {agent_id} (container: {container_name}): {e}", exc_info=True)

        elif runtime == "local" and pid_to_stop:
            logger.info(f"Stopping local agent process {agent_id} (PID: {pid_to_stop}). Force: {force}")
            try:
                os.kill(pid_to_stop, signal.SIGTERM)
                logger.info(f"SIGTERM sent to agent {agent_id} (PID: {pid_to_stop})")
                wait_time = 5
                try:
                    async def check_pid_terminated():
                        while True:
                            try: os.kill(pid_to_stop, 0); await asyncio.sleep(0.1)
                            except ProcessLookupError: return
                    await asyncio.wait_for(check_pid_terminated(), timeout=wait_time)
                    logger.info(f"Agent {agent_id} (PID: {pid_to_stop}) terminated gracefully.")
                    stopped_successfully = True
                except asyncio.TimeoutError:
                    logger.warning(f"Agent {agent_id} (PID: {pid_to_stop}) did not terminate after SIGTERM.")
                    if force:
                        logger.warning(f"Forcing stop with SIGKILL for agent {agent_id} (PID: {pid_to_stop}).")
                        try: os.kill(pid_to_stop, signal.SIGKILL); logger.info(f"SIGKILL sent."); stopped_successfully = True
                        except ProcessLookupError: logger.info(f"PID {pid_to_stop} already gone before SIGKILL."); stopped_successfully = True
                        except Exception as kill_e: logger.error(f"Error sending SIGKILL: {kill_e}")
            except ProcessLookupError:
                logger.warning(f"Local agent process {agent_id} (PID: {pid_to_stop}) not found (already stopped?).")
                stopped_successfully = True
            except Exception as e:
                logger.error(f"Error stopping local agent {agent_id} (PID: {pid_to_stop}): {e}", exc_info=True)
        else:
            logger.warning(f"Cannot stop agent {agent_id}: runtime '{runtime}', PID '{pid_to_stop}', container '{container_name}'. Marking as stopped.")
            stopped_successfully = True # Mark as stopped if no clear way to stop

        if stopped_successfully:
            await r.hset(status_key, "status", "stopped")
            await r.hdel(status_key, "pid", "container_name", "runtime", "last_active")
            logger.info(f"Agent {agent_id} marked as stopped in Redis.")
            return True
        else: # Stop attempt failed
            logger.warning(f"Failed to stop agent {agent_id}. Reverting status from 'stopping' to '{current_status}'.")
            await r.hset(status_key, "status", current_status) # Revert to pre-stop attempt status
            return False

    except Exception as e:
        logger.error(f"Overall error in stop_agent_process for {agent_id}: {e}", exc_info=True)
        if 'status_key' in locals() and 'current_status' in locals(): # Attempt to revert status if possible
             try: await r.hset(status_key, "status", current_status if current_status != "stopping" else "error_stop_failed")
             except: pass
        elif 'status_key' in locals():
             try: await r.hset(status_key, "status", "error_stop_failed")
             except: pass
        return False

async def restart_agent_process(agent_id: str, r: redis.Redis) -> bool:
    """Restarts the agent runner subprocess."""
    logger.info(f"Restarting agent process for {agent_id}...")
    stopped = await stop_agent_process(agent_id, r, force=True) # Force stop during restart
    if not stopped:
        logger.error(f"Failed to stop agent {agent_id} during restart. Aborting restart.")
        # Status should reflect the failure to stop, or be error_stop_failed
        return False

    await asyncio.sleep(1) # Brief pause
    logger.info(f"Agent {agent_id} stopped (or stop attempted). Proceeding to start...")
    try:
        started = await start_agent_process(agent_id, r)
        if started:
            logger.info(f"Agent {agent_id} restart sequence initiated successfully.")
            return True
        else:
            logger.error(f"Failed to start agent {agent_id} after stopping during restart.")
            # start_agent_process should set appropriate error status
            return False
    except Exception as e:
        logger.error(f"Exception during agent start phase of restart for {agent_id}: {e}", exc_info=True)
        await r.hset(f"agent_status:{agent_id}", "status", "error_restart_failed")
        return False

# --- Integration Process Management ---

def _get_integration_status_key(agent_id: str, integration_type: IntegrationType) -> str:
    return f"integration_status:{agent_id}:{integration_type.value}"

async def get_integration_status(agent_id: str, integration_type: IntegrationType, r: redis.Redis) -> IntegrationStatus:
    """Retrieves the status of a specific integration process from Redis."""
    status_key = _get_integration_status_key(agent_id, integration_type)
    status_data = await r.hgetall(status_key) # Changed: r.hgetall with decode_responses=True returns Dict[str, str]

    if not status_data:
        return IntegrationStatus(agent_id=agent_id, integration_type=integration_type, status="stopped", pid=None, last_active=None)

    current_status = status_data.get("status", "unknown")
    pid_val = status_data.get("pid")
    pid = int(pid_val) if pid_val and pid_val.isdigit() else None
    last_active_val = status_data.get("last_active")
    try:
        last_active = float(last_active_val) if last_active_val else None
    except (ValueError, TypeError):
        last_active = None
    
    # runtime = status_data.get("runtime", "local") # Assuming integrations are always local for now

    if pid and current_status in ["running", "starting"]: # Check PID for local integrations
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            logger.warning(f"Integration {integration_type.value} for agent {agent_id} status is '{current_status}' in Redis, but PID {pid} not found. Updating status to 'error_process_lost'.")
            current_status = "error_process_lost"
            pid = None
            async with r.pipeline(transaction=True) as pipe:
                pipe.hset(status_key, "status", current_status)
                pipe.hdel(status_key, "pid")
                await pipe.execute()
        except OSError as e:
            logger.error(f"Error checking PID {pid} for integration {integration_type.value} (agent {agent_id}): {e}. Status remains '{current_status}'.")

    return IntegrationStatus(
        agent_id=agent_id,
        integration_type=integration_type,
        status=current_status,
        pid=pid,
        last_active=last_active,
    )

async def start_integration_process(
    agent_id: str,
    integration_type: IntegrationType,
    r: redis.Redis,
    integration_settings: Optional[Dict[str, Any]] = None
) -> bool:
    """Starts a specific integration subprocess (assumed local)."""
    status_key = _get_integration_status_key(agent_id, integration_type)
    integration_name = integration_type.value

    try:
        current_integration_status = await get_integration_status(agent_id, integration_type, r)
        if current_integration_status.status == "running" and current_integration_status.pid:
            try:
                os.kill(current_integration_status.pid, 0)
                logger.warning(f"Integration {integration_name} for agent {agent_id} already running with PID {current_integration_status.pid}. Skipping start.")
                return True
            except ProcessLookupError:
                logger.warning(f"Integration {integration_name} for agent {agent_id} reported running but PID {current_integration_status.pid} not found. Attempting to start.")
            # ... other checks from original code
        elif current_integration_status.status == "starting":
             logger.warning(f"Integration {integration_name} for agent {agent_id} is already starting. Skipping.")
             return True
    except Exception as e:
        logger.error(f"Error checking integration status before start for {agent_id}/{integration_name}: {e}", exc_info=True)


    logger.info(f"Starting integration process {integration_name} for agent {agent_id}...")

    module_path = INTEGRATION_MODULE_PATHS.get(integration_type)
    script_file_path = INTEGRATION_FILE_PATHS.get(integration_type) # For existence check

    if not module_path or not script_file_path:
        logger.error(f"Integration type {integration_name} is not configured.")
        await r.hset(status_key, "status", "error_config_missing")
        raise ValueError(f"Integration type {integration_name} not configured.")

    if not os.path.exists(script_file_path):
        logger.error(f"Integration script for {integration_name} not found at: {script_file_path}")
        await r.hset(status_key, "status", "error_script_not_found")
        raise FileNotFoundError(f"Integration script for {integration_name} not found: {script_file_path}")

    try:
        python_executable = settings.PYTHON_EXECUTABLE
        cmd = [
            python_executable,
            "-m", module_path,
            "--agent-id", agent_id,
            "--redis-url", REDIS_URL
        ]
        if integration_settings:
            cmd.extend(["--integration-settings", json.dumps(integration_settings)])

        process_env = os.environ.copy()
        process_env["PYTHONPATH"] = PROJECT_ROOT + (os.pathsep + process_env.get("PYTHONPATH", "") if process_env.get("PYTHONPATH") else "")
        
        logger.debug(f"Executing integration command: {' '.join(cmd)} in cwd: {PROJECT_ROOT} with PYTHONPATH: {process_env.get('PYTHONPATH')}")
        # Using subprocess.Popen, assuming integration script manages its lifecycle (e.g. backgrounds)
        integration_process = subprocess.Popen(cmd, cwd=PROJECT_ROOT, env=process_env)

        await r.hset(status_key, mapping={
            "status": "starting",
            "pid": str(integration_process.pid),
            "runtime": "local", # Explicitly set runtime
            "last_active": str(time.time())
        })
        logger.info(f"Integration process {integration_name} for agent {agent_id} initiated start with PID {integration_process.pid}")
        return True
    except FileNotFoundError as fnf_e:
        logger.error(f"Failed to start integration {integration_name} for {agent_id} (FileNotFound): {fnf_e}", exc_info=True)
        await r.hset(status_key, "status", "error_script_not_found")
        raise
    except Exception as e:
        logger.error(f"Failed to start integration process {integration_name} for agent {agent_id}: {e}", exc_info=True)
        await r.hset(status_key, "status", "error_start_failed")
        raise RuntimeError(f"Failed to launch integration process {integration_name} for agent {agent_id}: {e}")

async def stop_integration_process(
    agent_id: str,
    integration_type: IntegrationType,
    r: redis.Redis,
    force: bool = False
) -> bool:
    """Stops a specific integration subprocess (assumed local)."""
    status_key = _get_integration_status_key(agent_id, integration_type)
    integration_name = integration_type.value
    try:
        status_data = await r.hgetall(status_key) # Changed: r.hgetall with decode_responses=True returns Dict[str, str]
        if not status_data:
            logger.info(f"Integration {integration_name} for agent {agent_id} not found in Redis. Assuming stopped.")
            return True

        current_status = status_data.get("status", "unknown")
        pid_to_stop_val = status_data.get("pid")
        pid_to_stop = int(pid_to_stop_val) if pid_to_stop_val and pid_to_stop_val.isdigit() else None
        # runtime = status_data.get("runtime", "local") # Assume local

        if current_status == "stopped":
            logger.info(f"Integration {integration_name} for agent {agent_id} is already stopped.")
            await r.hdel(status_key, "pid", "runtime", "last_active") # Cleanup
            return True

        if not pid_to_stop: # No PID to stop
            logger.warning(f"Attempted to stop integration {integration_name} for agent {agent_id}, but no PID found. Marking as stopped.")
            await r.hset(status_key, "status", "stopped")
            await r.hdel(status_key, "pid", "runtime", "last_active")
            return True

        await r.hset(status_key, "status", "stopping")
        stopped_successfully = False
        logger.info(f"Stopping local integration process {integration_name} for agent {agent_id} (PID: {pid_to_stop}). Force: {force}")
        try:
            os.kill(pid_to_stop, signal.SIGTERM)
            logger.info(f"SIGTERM sent to integration {integration_name} (PID: {pid_to_stop})")
            wait_time = 5
            try:
                async def check_pid_terminated():
                    while True:
                        try: os.kill(pid_to_stop, 0); await asyncio.sleep(0.1)
                        except ProcessLookupError: return
                await asyncio.wait_for(check_pid_terminated(), timeout=wait_time)
                logger.info(f"Integration {integration_name} (PID: {pid_to_stop}) terminated gracefully.")
                stopped_successfully = True
            except asyncio.TimeoutError:
                logger.warning(f"Integration {integration_name} (PID: {pid_to_stop}) did not terminate after SIGTERM.")
                if force:
                    logger.warning(f"Forcing stop with SIGKILL for integration {integration_name} (PID: {pid_to_stop}).")
                    try: os.kill(pid_to_stop, signal.SIGKILL); logger.info(f"SIGKILL sent."); stopped_successfully = True
                    except ProcessLookupError: logger.info(f"PID {pid_to_stop} already gone before SIGKILL."); stopped_successfully = True
                    except Exception as kill_e: logger.error(f"Error sending SIGKILL to integration: {kill_e}")
        except ProcessLookupError:
            logger.warning(f"Local integration process {integration_name} (PID: {pid_to_stop}) not found (already stopped?).")
            stopped_successfully = True
        except Exception as e:
            logger.error(f"Error stopping local integration {integration_name} (PID: {pid_to_stop}): {e}", exc_info=True)

        if stopped_successfully:
            await r.hset(status_key, "status", "stopped")
            await r.hdel(status_key, "pid", "runtime", "last_active")
            logger.info(f"Integration {integration_name} for agent {agent_id} marked as stopped.")
            return True
        else:
            logger.warning(f"Failed to stop integration {integration_name} for agent {agent_id}. Reverting status.")
            await r.hset(status_key, "status", current_status)
            return False
            
    except Exception as e:
        logger.error(f"Overall error in stop_integration_process for {agent_id}/{integration_name}: {e}", exc_info=True)
        if 'status_key' in locals() and 'current_status' in locals():
             try: await r.hset(status_key, "status", current_status if current_status != "stopping" else "error_stop_failed")
             except: pass
        elif 'status_key' in locals():
             try: await r.hset(status_key, "status", "error_stop_failed")
             except: pass
        return False

async def restart_integration_process(
    agent_id: str,
    integration_type: IntegrationType,
    r: redis.Redis,
    integration_settings: Optional[Dict[str, Any]] = None
) -> bool:
    """Restarts a specific integration subprocess."""
    integration_name = integration_type.value
    logger.info(f"Restarting integration process {integration_name} for agent {agent_id}...")
    stopped = await stop_integration_process(agent_id, integration_type, r, force=True)
    if not stopped:
        logger.error(f"Failed to stop integration {integration_name} for agent {agent_id} during restart. Aborting.")
        return False

    await asyncio.sleep(1)
    logger.info(f"Integration {integration_name} for agent {agent_id} stopped. Proceeding to start...")
    try:
        started = await start_integration_process(agent_id, integration_type, r, integration_settings=integration_settings)
        if started:
            logger.info(f"Integration {integration_name} for agent {agent_id} restarted successfully.")
            return True
        else:
            logger.error(f"Failed to start integration {integration_name} for agent {agent_id} after stopping during restart.")
            return False
    except Exception as e:
        logger.error(f"Exception during integration start phase of restart for {agent_id}/{integration_name}: {e}", exc_info=True)
        status_key = _get_integration_status_key(agent_id, integration_type)
        await r.hset(status_key, "status", "error_restart_failed")
        return False

# Removed placeholder for inactivity monitoring as it's in its own worker.
