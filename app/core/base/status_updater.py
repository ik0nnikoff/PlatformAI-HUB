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
    Абстрактный базовый класс или миксин для компонентов, которым необходимо
    обновлять свой статус в Redis.

    Этот класс предоставляет общую функциональность для взаимодействия с Redis
    с целью хранения и обновления информации о состоянии компонента. Он наследуется
    от `RedisClientManager` для управления соединением с Redis.

    Дочерние классы должны определить:
    - `_status_key_prefix` (str): Префикс ключа Redis для статусов (например, "agent_status:").
    - `_component_id` (str): Уникальный идентификатор компонента (например, ID агента
      или f"{agent_id}:{integration_type}").

    Также необходимо убедиться, что `RedisClientManager` правильно инициализирован
    (например, вызовом `super().__init__()` и `await self.setup_redis_client()`
    в методах инициализации и настройки дочернего класса).

    Атрибуты:
        _status_key_prefix (str): Префикс для ключа статуса в Redis.
        _component_id (str): Идентификатор компонента.

    Методы:
        _get_effective_logger: Возвращает подходящий логгер (экземпляра или модуля).
        _get_status_key: Формирует полный ключ Redis для статуса компонента.
        update_status_in_redis: Обновляет статус компонента в Redis.
        set_status: Устанавливает новый статус компонента с дополнительными деталями.
        get_current_status_from_redis: Получает текущий статус компонента из Redis.
        update_last_active_time: Обновляет время последней активности компонента.
        clear_specific_fields_in_redis: Удаляет указанные поля из хеша статуса в Redis.
        delete_status_key_from_redis: Удаляет весь ключ статуса компонента из Redis.
        mark_as_initializing: Устанавливает статус "initializing".
        mark_as_running: Устанавливает статус "running".
        mark_as_stopped: Устанавливает статус "stopped".
        mark_as_error: Устанавливает статус "error".
        mark_as_completed: Устанавливает статус "completed".
        setup_status_updater: Настраивает `StatusUpdater` (инициализация Redis).
        cleanup_status_updater: Очищает ресурсы `StatusUpdater` (закрытие Redis, опциональное удаление статуса).
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
        Возвращает логгер, специфичный для экземпляра, если он доступен и валиден,
        в противном случае возвращает логгер уровня модуля.

        Это позволяет использовать более специфичный логгер, если он был настроен
        в классе, использующем этот миксин (например, `RunnableComponent`), или
        безопасно использовать логгер модуля по умолчанию.

        Returns:
            logging.Logger: Эффективный логгер для использования.
        """
        if hasattr(self, 'logger') and isinstance(getattr(self, 'logger'), (logging.Logger, logging.LoggerAdapter)):
            return getattr(self, 'logger')
        return module_logger

    def _get_status_key(self) -> str:
        """Формирует и возвращает полный ключ Redis для статуса этого компонента."""
        if not hasattr(self, '_status_key_prefix') or not hasattr(self, '_component_id'):
            raise ValueError("_status_key_prefix and _component_id must be set on the instance.")
        if not self._status_key_prefix or not self._component_id:
            # This check is slightly redundant if the hasattr check passes and they are just empty strings,
            # but good for explicitness.
            raise ValueError("_status_key_prefix and _component_id must not be empty.")
        return f"{self._status_key_prefix}{self._component_id}"

    async def update_status_in_redis(self, status_data: Dict[str, Any]):
        """
        Асинхронно обновляет статус компонента в Redis, используя команду HSET.

        Автоматически добавляет поле 'last_updated_utc' с текущим временем в формате ISO.
        Все значения в `status_data` конвертируются в строки перед сохранением в Redis.
        Если клиент Redis недоступен или `status_data` (после добавления `last_updated_utc`)
        пуст, обновление не производится.

        Args:
            status_data (Dict[str, Any]): Словарь с данными статуса для обновления.
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
        Асинхронно устанавливает новый основной статус для компонента и опционально
        добавляет другие детали в Redis.

        Args:
            status_value (str): Новое значение статуса (например, "running", "stopped").
            details (Optional[Dict[str, Any]]): Словарь с дополнительными полями для сохранения
                                               вместе со статусом.
        """
        effective_logger = self._get_effective_logger()
        update_payload = details.copy() if details else {}
        update_payload["status"] = status_value
        await self.update_status_in_redis(update_payload)
        # effective_logger.info(f"Status for {getattr(self, '_component_id', 'UnknownComponent')} set to '{status_value}' with details: {details or {}}")

    async def get_current_status_from_redis(self) -> Dict[str, str]:
        """Асинхронно извлекает все поля для ключа статуса компонента из Redis."""
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
        """
        Асинхронно обновляет поле 'last_active' в Redis для этого компонента.

        Args:
            timestamp (Optional[float]): Временная метка Unix для установки. Если None,
                                       используется текущее время (`time.time()`).
        """
        effective_logger = self._get_effective_logger()
        ts = timestamp if timestamp is not None else time.time()
        await self.update_status_in_redis({"last_active": str(ts)})
        effective_logger.debug(f"Updated last_active_time for {getattr(self, '_component_id', 'UnknownComponent')} to {ts}")

    async def clear_specific_fields_in_redis(self, fields: List[str]):
        """Асинхронно удаляет указанные поля из хеша статуса компонента в Redis."""
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
        """Асинхронно удаляет весь ключ Redis, связанный со статусом этого компонента."""
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
        """
        Асинхронно устанавливает статус компонента как 'initializing'.

        Добавляет PID процесса и время начала попытки инициализации (`start_attempt_utc`)
        в детали статуса.

        Args:
            details (Optional[Dict[str, Any]]): Дополнительные детали для сохранения.
        """
        payload = details.copy() if details else {} # Ensure we don't modify the original dict
        payload.update({
            "pid": os.getpid() if hasattr(os, 'getpid') else None,
            "start_attempt_utc": datetime.now(timezone.utc).isoformat()
        })
        await self.set_status("initializing", payload)

    async def mark_as_running(self, pid: Optional[int] = None, details: Optional[Dict[str, Any]] = None):
        """
        Асинхронно помечает компонент как 'running'.

        Сохраняет PID процесса и текущее время как 'last_active'.

        Args:
            pid (Optional[int]): Идентификатор процесса. Если None, используется os.getpid().
            details (Optional[Dict[str, Any]]): Дополнительные детали для сохранения.
        """
        payload = details.copy() if details else {}
        payload["pid"] = pid if pid is not None else (os.getpid() if hasattr(os, 'getpid') else None)
        payload["last_active"] = str(time.time())
        await self.set_status("running", payload)

    async def mark_as_stopped(self, reason: Optional[str] = None, details: Optional[Dict[str, Any]] = None):
        """
        Асинхронно помечает компонент как 'stopped'.

        Очищает PID и другие связанные с запуском поля. Добавляет причину остановки, если указана.

        Args:
            reason (Optional[str]): Причина остановки.
            details (Optional[Dict[str, Any]]): Дополнительные детали для сохранения.
        """
        payload = details.copy() if details else {}
        if reason:
            payload["reason"] = reason
        
        fields_to_clear = ["pid", "error_detail", "start_attempt_utc", "container_name", "actual_container_id", "runtime"]
        
        await self.set_status("stopped", payload)
        await self.clear_specific_fields_in_redis(fields_to_clear)


    async def mark_as_error(self, error_message: str, details: Optional[Dict[str, Any]] = None):
        """
        Асинхронно помечает компонент как 'error'.

        Сохраняет сообщение об ошибке в поле 'error_detail'.

        Args:
            error_message (str): Сообщение об ошибке.
            details (Optional[Dict[str, Any]]): Дополнительные детали для сохранения.
        """
        payload = details.copy() if details else {}
        payload["error_detail"] = error_message
        await self.set_status("error", payload)

    async def mark_as_completed(self, details: Optional[Dict[str, Any]] = None):
        """
        Асинхронно помечает компонент как 'completed'.

        Args:
            details (Optional[Dict[str, Any]]): Дополнительные детали для сохранения.
        """
        await self.set_status("completed", details)
        
    async def setup_status_updater(self, redis_url: Optional[str] = None):
        """
        Настраивает часть компонента, отвечающую за обновление статуса (StatusUpdater).

        Этот метод должен вызываться в методе `setup` конкретного компонента.
        Он инициализирует соединение с Redis. Предполагается, что `_component_id`
        и `_status_key_prefix` уже установлены в экземпляре.

        Args:
            redis_url (Optional[str]): URL-адрес Redis. Если None, используется значение
                                       из `settings.REDIS_URL`.

        Raises:
            ValueError: Если `_component_id` или `_status_key_prefix` не установлены.
        """
        if not hasattr(self, '_component_id') or not self._component_id:
            raise ValueError("'_component_id' must be set on the instance before calling setup_status_updater.")
        if not hasattr(self, '_status_key_prefix') or not self._status_key_prefix:
            raise ValueError("'_status_key_prefix' must be set on the instance before calling setup_status_updater.")
        
        effective_redis_url = redis_url or str(settings.REDIS_URL)
        await self.setup_redis_client(effective_redis_url) # From RedisClientManager
        effective_logger = self._get_effective_logger()
        effective_logger.debug(f"StatusUpdater for {self._component_id} (prefix: {self._status_key_prefix}) initialized with Redis: {effective_redis_url}")

    async def cleanup_status_updater(self, clear_status_on_cleanup: bool = False):
        """
        Очищает ресурсы Redis для StatusUpdater.

        Опционально удаляет ключ статуса из Redis. Этот метод следует вызывать
        в методе `cleanup` компонента.

        Args:
            clear_status_on_cleanup (bool): Если True, ключ статуса будет удален из Redis.
                                            По умолчанию False.
        """
        effective_logger = self._get_effective_logger()
        component_name = getattr(self, '_component_id', 'UnknownComponent')
        if clear_status_on_cleanup:
            effective_logger.info(f"Clearing status key for {component_name} as part of cleanup.")
            await self.delete_status_key_from_redis()
            
        await self.close_redis_resources() # From RedisClientManager
        effective_logger.info(f"StatusUpdater for {component_name} cleaned up Redis resources.")

# ... (Example usage removed as it's illustrative and not part of the core class) ...
