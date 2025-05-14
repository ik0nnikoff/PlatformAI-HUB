from abc import ABC, abstractmethod
import asyncio
import json
import logging
from typing import List, Optional, Any, Dict
from datetime import datetime, timezone, timedelta

from app.core.base.runnable_component import RunnableComponent
from app.core.base.status_updater import StatusUpdater
from app.core.config import settings

logger = logging.getLogger(__name__)

class BaseWorker(RunnableComponent, StatusUpdater, ABC):
    """
    Abstract base class for workers.
    Combines RunnableComponent lifecycle with StatusUpdater capabilities.
    """
    def __init__(self, component_id: str, status_key_prefix: str = "worker_status:"): # Added colon for consistency
        RunnableComponent.__init__(self) 
        StatusUpdater.__init__(self) 
        
        self._component_id = component_id
        # Ensure status_key_prefix ends with a colon for namespacing
        self._status_key_prefix = status_key_prefix if status_key_prefix.endswith(':') else status_key_prefix + ':'


    async def setup(self):
        """
        Concrete implementation of RunnableComponent's setup.
        Initializes StatusUpdater and sets initial status.
        """
        self.logger.info(f"[{self._component_id}] Worker setup started.")
        # _component_id and _status_key_prefix are already set on the instance.
        # setup_status_updater will use them.
        await self.setup_status_updater(redis_url=str(settings.REDIS_URL))
        self.logger.info(f"[{self._component_id}] Worker setup completed.")

    async def cleanup(self):
        """
        Concrete implementation of RunnableComponent's cleanup.
        Marks worker as stopped and cleans up StatusUpdater (and RedisClientManager).
        """
        self.logger.info(f"[self._component_id] Worker cleanup started.")
        await self.mark_as_stopped(reason="Worker cleanup")
        # cleanup_status_updater handles Redis client closing
        await self.cleanup_status_updater(clear_status_on_cleanup=True) # Set to True to clear status from Redis
        self.logger.info(f"[self._component_id] Worker cleanup completed.")

    # run_loop is inherited as abstract from RunnableComponent and must be implemented
    # by concrete worker types (QueueWorker, ScheduledTaskWorker).
    # The main `run` method from RunnableComponent will call setup, run_loop, and cleanup.


class QueueWorker(BaseWorker):
    """
    A worker that processes tasks from one or more Redis queues.
    """
    def __init__(self, 
                 component_id: str, 
                 queue_names: List[str], 
                 process_timeout: float = 60.0, # Timeout for processing a single message
                 redis_block_timeout: int = 1,  # Timeout for blpop
                 status_key_prefix: str = "q_worker_status:"): # Added colon
        super().__init__(component_id, status_key_prefix)
        self.queue_names = queue_names
        self.queue_names_str = ", ".join(queue_names)
        self.process_timeout = process_timeout
        self.redis_block_timeout = redis_block_timeout
        self.logger.info(f"QueueWorker [{self._component_id}] initialized for queues: {self.queue_names_str}")

    async def setup(self):
        await super().setup()
        self.logger.info(f"[{self._component_id}] Listening to Redis queues: {self.queue_names_str}")
        await self.set_status("IDLE", {"listening_on": self.queue_names_str})

    async def run_loop(self):
        """
        Continuously listens to the Redis queue(s) and processes messages.
        """
        self.logger.info(f"[{self._component_id}] Starting queue listening loop for {self.queue_names_str}.")
        await self.mark_as_running()
        redis_cli = await self.redis_client # Get the actual Redis client instance

        while self._running:
            try:
                await self.set_status("LISTENING", {"queues": self.queue_names_str, "last_checked": datetime.now(timezone.utc).isoformat()})
                
                # blpop returns a tuple: (queue_name_bytes, message_bytes)
                message_tuple = await redis_cli.blpop(self.queue_names, timeout=self.redis_block_timeout)

                if not self._running: break # Exit if shutdown requested during blpop

                if message_tuple:
                    queue_name_raw, message_data_raw = message_tuple
                    queue_name = queue_name_raw.decode('utf-8') if isinstance(queue_name_raw, bytes) else queue_name_raw
                    
                    self.logger.debug(f"[{self._component_id}] Message received from queue '{queue_name}'")
                    await self.set_status("PROCESSING", {"queue": queue_name, "received_at": datetime.now(timezone.utc).isoformat()})
                    await self.update_last_active_time()

                    try:
                        # Attempt to decode if bytes, then parse if JSON string
                        if isinstance(message_data_raw, bytes):
                            message_data_str = message_data_raw.decode('utf-8')
                        else:
                            message_data_str = message_data_raw # Assume it's already a string
                        
                        message_data_dict = json.loads(message_data_str)
                        
                        # Process the message with a timeout
                        await asyncio.wait_for(
                            self.process_message(message_data_dict),
                            timeout=self.process_timeout
                        )
                        self.logger.debug(f"[{self._component_id}] Message from '{queue_name}' processed successfully.")
                    except json.JSONDecodeError as e:
                        self.logger.error(f"[{self._component_id}] Failed to decode JSON message from '{queue_name}': {e}. Message: {message_data_raw[:200]}...") # Log snippet
                        await self.set_status("ERROR", {"error": "JSONDecodeError", "queue": queue_name, "message_snippet": str(message_data_raw[:200])})
                    except asyncio.TimeoutError:
                        self.logger.error(f"[{self._component_id}] Timeout processing message from '{queue_name}'. Exceeded {self.process_timeout}s.")
                        await self.set_status("ERROR", {"error": "ProcessingTimeout", "queue": queue_name})
                    except Exception as e:
                        self.logger.exception(f"[{self._component_id}] Error processing message from '{queue_name}': {e}")
                        await self.set_status("ERROR", {"error": str(e), "queue": queue_name})
                    finally:
                        if self._running: # Avoid status update if shutting down
                             await self.set_status("IDLE", {"listening_on": self.queue_names_str})
                else:
                    # Timeout from blpop, means no message received
                    self.logger.debug(f"[{self._component_id}] No message in '{self.queue_names_str}' within timeout, continuing to listen...")
                    await self.update_last_active_time() # Still active, just no messages
                    # Optional: Add a small sleep if redis_block_timeout is very short to prevent tight loop on CPU
                    if self.redis_block_timeout == 0 and self._running: # Non-blocking, so add sleep
                        await asyncio.sleep(0.1)

            except Exception as e: # Catch-all for unexpected errors in the loop itself
                self.logger.exception(f"[{self._component_id}] Unexpected error in run_loop: {e}")
                await self.set_status("ERROR", {"error": f"RunLoopException: {str(e)}"})
                if self._running: # Avoid tight loop on continuous error
                    await asyncio.sleep(5) 
        
        self.logger.info(f"[{self._component_id}] Queue listening loop for {self.queue_names_str} finished.")

    @abstractmethod
    async def process_message(self, message_data: Dict[str, Any]) -> None:
        """
        Process a single message from the queue.
        This method must be implemented by subclasses.
        """
        pass


class ScheduledTaskWorker(BaseWorker):
    """
    A worker that executes a task periodically.
    """
    def __init__(self, 
                 component_id: str, 
                 interval_seconds: float, 
                 status_key_prefix: str = "sched_worker_status:"): # Added colon
        super().__init__(component_id, status_key_prefix)
        if interval_seconds <= 0:
            raise ValueError("Interval for ScheduledTaskWorker must be positive.")
        self.interval_seconds = interval_seconds
        self.logger.info(f"ScheduledTaskWorker [{self._component_id}] initialized with interval: {self.interval_seconds}s")

    async def setup(self):
        await super().setup()
        self.logger.info(f"[{self._component_id}] Scheduled task interval: {self.interval_seconds} seconds.")
        await self.set_status("IDLE", {"interval_seconds": self.interval_seconds})

    async def run_loop(self):
        """
        Periodically executes the scheduled task.
        """
        self.logger.info(f"[{self._component_id}] Starting scheduled task loop with interval {self.interval_seconds}s.")
        await self.mark_as_running()

        while self._running:
            try:
                await self.set_status("PENDING_TASK", {"next_run_approx": (datetime.now(timezone.utc) + timedelta(seconds=self.interval_seconds)).isoformat()})
                
                # Wait for the interval, but break early if shutdown is requested
                # Split sleep into smaller chunks to be more responsive to shutdown
                sleep_interval = 1.0 # Check for shutdown every 1 second
                remaining_sleep = self.interval_seconds
                while remaining_sleep > 0 and self._running:
                    await asyncio.sleep(min(sleep_interval, remaining_sleep))
                    remaining_sleep -= sleep_interval

                if not self._running: break # Exit if shutdown requested during sleep

                self.logger.info(f"[{self._component_id}] Executing scheduled task.")
                await self.set_status("RUNNING_TASK", {"task_started_at": datetime.now(timezone.utc).isoformat()})
                await self.update_last_active_time()
                
                await self.perform_task()
                
                self.logger.info(f"[{self._component_id}] Scheduled task completed.")
                if self._running: # Avoid status update if shutting down
                    await self.set_status("IDLE", {"interval_seconds": self.interval_seconds, "last_run_completed_at": datetime.now(timezone.utc).isoformat()})

            except Exception as e:
                self.logger.exception(f"[{self._component_id}] Error during scheduled task execution: {e}")
                await self.set_status("ERROR", {"error": str(e)})
                # If task fails, wait for the next interval to retry, unless it's a critical error
                # that requires stopping the worker (handled by self._running = False)
        
        self.logger.info(f"[{self._component_id}] Scheduled task loop for {self.interval_seconds}s interval finished.")

    @abstractmethod
    async def perform_task(self) -> None:
        """
        Perform the scheduled task.
        This method must be implemented by subclasses.
        """
        pass
