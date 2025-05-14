import asyncio
import logging
import time
import os # Added for os.getpid()
from abc import ABC, abstractmethod
from typing import Dict, Any, Optional, List
from datetime import datetime, timezone

from app.core.base.redis_manager import RedisClientManager
from app.core.config import settings

module_logger = logging.getLogger(__name__) # Renamed for clarity

class StatusUpdater(RedisClientManager, ABC):
    """
    Abstract base class/mixin for components that need to update their status in Redis.
    
    Subclasses must define:
    - _status_key_prefix: str (e.g., "agent_status:")
    - _component_id: str (e.g., agent_id or f"{agent_id}:{integration_type}")
    
    And ensure RedisClientManager is properly initialized (e.g., by calling super().__init__()
    and await self.setup_redis_client()).
    """

    # These will be set by the concrete class, typically in its __init__
    _status_key_prefix: str 
    _component_id: str

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # self.logger might be initialized by a mixing class like RunnableComponent,
        # or it might not exist on this instance.

    def _get_effective_logger(self) -> logging.Logger:
        """
        Returns the instance-specific logger if available and valid, 
        otherwise falls back to the module-level logger.
        """
        if hasattr(self, 'logger') and isinstance(getattr(self, 'logger'), (logging.Logger, logging.LoggerAdapter)):
            return getattr(self, 'logger')
        return module_logger

    def _get_status_key(self) -> str:
        """Constructs the full Redis key for this component's status."""
        if not hasattr(self, '_status_key_prefix') or not hasattr(self, '_component_id'):
            raise ValueError("_status_key_prefix and _component_id must be set on the instance.")
        if not self._status_key_prefix or not self._component_id:
            # This check is slightly redundant if the hasattr check passes and they are just empty strings,
            # but good for explicitness.
            raise ValueError("_status_key_prefix and _component_id must not be empty.")
        return f"{self._status_key_prefix}{self._component_id}"

    async def update_status_in_redis(self, status_data: Dict[str, Any]):
        """
        Updates the component's status in Redis using HSET.
        Ensures 'last_updated_utc' is always included.
        Converts all values to strings for Redis storage.
        """
        effective_logger = self._get_effective_logger()
        if not await self.is_redis_client_available():
            effective_logger.error(f"Redis client not available. Cannot update status for {getattr(self, '_component_id', 'UnknownComponent')}.")
            return

        key = self._get_status_key()
        
        status_data["last_updated_utc"] = datetime.now(timezone.utc).isoformat()
        mapping = {k: str(v) for k, v in status_data.items() if v is not None}

        if not mapping:
            effective_logger.warning(f"Attempted to update status for key {key} with an empty mapping. Original dict: {status_data}")
            return

        redis_cli = await self.redis_client
        try:
            await redis_cli.hset(key, mapping=mapping)
            effective_logger.debug(f"Updated status for key {key} with: {mapping}")
        except Exception as e:
            effective_logger.error(f"Failed to update status in Redis for key {key}: {e}", exc_info=True)

    async def set_status(self, status_value: str, details: Optional[Dict[str, Any]] = None):
        """
        Sets a new status for the component, optionally including other details.
        """
        effective_logger = self._get_effective_logger()
        update_payload = details.copy() if details else {}
        update_payload["status"] = status_value
        await self.update_status_in_redis(update_payload)
        # effective_logger.info(f"Status for {getattr(self, '_component_id', 'UnknownComponent')} set to '{status_value}' with details: {details or {}}")

    async def get_current_status_from_redis(self) -> Dict[str, str]:
        """Fetches all fields for the component's status key from Redis."""
        effective_logger = self._get_effective_logger()
        if not await self.is_redis_client_available():
            effective_logger.error(f"Redis client not available. Cannot get status for {getattr(self, '_component_id', 'UnknownComponent')}.")
            return {}
            
        key = self._get_status_key()
        redis_cli = await self.redis_client
        try:
            status_dict = await redis_cli.hgetall(key)
            return status_dict
        except Exception as e:
            effective_logger.error(f"Failed to get status from Redis for key {key}: {e}", exc_info=True)
            return {}

    async def update_last_active_time(self, timestamp: Optional[float] = None):
        """Updates the 'last_active' field in Redis for this component."""
        effective_logger = self._get_effective_logger()
        ts = timestamp if timestamp is not None else time.time()
        await self.update_status_in_redis({"last_active": str(ts)})
        effective_logger.debug(f"Updated last_active_time for {getattr(self, '_component_id', 'UnknownComponent')} to {ts}")

    async def clear_specific_fields_in_redis(self, fields: List[str]):
        """Removes specified fields from the component's status hash in Redis."""
        effective_logger = self._get_effective_logger()
        component_name = getattr(self, '_component_id', 'UnknownComponent')
        if not await self.is_redis_client_available() or not fields:
            if not fields: effective_logger.warning(f"No fields specified to clear for {component_name}.")
            else: effective_logger.error(f"Redis client not available. Cannot clear fields for {component_name}.")
            return

        key = self._get_status_key()
        redis_cli = await self.redis_client
        try:
            await redis_cli.hdel(key, *fields)
            effective_logger.debug(f"Cleared fields {fields} for key {key}")
        except Exception as e:
            effective_logger.error(f"Failed to clear fields in Redis for key {key}: {e}", exc_info=True)
            
    async def delete_status_key_from_redis(self):
        """Deletes the entire Redis key for this component's status."""
        effective_logger = self._get_effective_logger()
        component_name = getattr(self, '_component_id', 'UnknownComponent')
        if not await self.is_redis_client_available():
            effective_logger.error(f"Redis client not available. Cannot delete status key for {component_name}.")
            return
            
        key = self._get_status_key()
        redis_cli = await self.redis_client
        try:
            await redis_cli.delete(key)
            effective_logger.info(f"Deleted status key {key} from Redis.")
        except Exception as e:
            effective_logger.error(f"Failed to delete status key {key} from Redis: {e}", exc_info=True)

    # --- Convenience methods for common status updates ---

    async def mark_as_initializing(self, details: Optional[Dict[str, Any]] = None):
        payload = details.copy() if details else {} # Ensure we don't modify the original dict
        payload.update({
            "pid": os.getpid() if hasattr(os, 'getpid') else None,
            "start_attempt_utc": datetime.now(timezone.utc).isoformat()
        })
        await self.set_status("initializing", payload)

    async def mark_as_running(self, pid: Optional[int] = None, details: Optional[Dict[str, Any]] = None):
        """Marks the component as 'running' with its PID and current time as last_active."""
        payload = details.copy() if details else {}
        payload["pid"] = pid if pid is not None else (os.getpid() if hasattr(os, 'getpid') else None)
        payload["last_active"] = str(time.time())
        await self.set_status("running", payload)

    async def mark_as_stopped(self, reason: Optional[str] = None, details: Optional[Dict[str, Any]] = None):
        """Marks the component as 'stopped', clearing PID and adding a reason if provided."""
        payload = details.copy() if details else {}
        if reason:
            payload["reason"] = reason
        
        fields_to_clear = ["pid", "error_detail", "start_attempt_utc", "container_name", "actual_container_id", "runtime"]
        
        await self.set_status("stopped", payload)
        await self.clear_specific_fields_in_redis(fields_to_clear)


    async def mark_as_error(self, error_message: str, details: Optional[Dict[str, Any]] = None):
        """Marks the component as 'error' with an error message."""
        payload = details.copy() if details else {}
        payload["error_detail"] = error_message
        await self.set_status("error", payload)

    async def mark_as_completed(self, details: Optional[Dict[str, Any]] = None):
        """Marks the component as 'completed'."""
        await self.set_status("completed", details)
        
    async def setup_status_updater(self, redis_url: Optional[str] = None):
        """
        Sets up the StatusUpdater part of the component.
        This method should be called in the `setup` method of the concrete component.
        It initializes Redis connection.
        Assumes _component_id and _status_key_prefix are already set on the instance.
        """
        if not hasattr(self, '_component_id') or not self._component_id:
            raise ValueError("'_component_id' must be set on the instance before calling setup_status_updater.")
        if not hasattr(self, '_status_key_prefix') or not self._status_key_prefix:
            raise ValueError("'_status_key_prefix' must be set on the instance before calling setup_status_updater.")
        
        effective_redis_url = redis_url or str(settings.REDIS_URL)
        await self.setup_redis_client(effective_redis_url) # From RedisClientManager
        effective_logger = self._get_effective_logger()
        effective_logger.info(f"StatusUpdater for {self._component_id} (prefix: {self._status_key_prefix}) initialized with Redis: {effective_redis_url}")

    async def cleanup_status_updater(self, clear_status_on_cleanup: bool = False):
        """
        Cleans up Redis resources for StatusUpdater.
        Optionally deletes the status key from Redis.
        Call this in the component's `cleanup` method.
        """
        effective_logger = self._get_effective_logger()
        component_name = getattr(self, '_component_id', 'UnknownComponent')
        if clear_status_on_cleanup:
            effective_logger.info(f"Clearing status key for {component_name} as part of cleanup.")
            await self.delete_status_key_from_redis()
            
        await self.close_redis_resources() # From RedisClientManager
        effective_logger.info(f"StatusUpdater for {component_name} cleaned up Redis resources.")

# ... (Example usage removed as it's illustrative and not part of the core class) ...
