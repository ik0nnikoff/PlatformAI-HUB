"""
Базовые классы для процесс-менеджера.

Модуль содержит абстрактные базовые классы и общие типы
для управления процессами агентов и интеграций.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional, Dict, Any, Protocol

from app.core.base.redis_manager import RedisClientManager
from .config import ProcessConfiguration
from .exceptions import ProcessManagerError


# Типы данных для статусов процессов
@dataclass
class ProcessInfo:
    """Информация о процессе."""

    process_id: str
    status: str
    pid: Optional[int] = None
    last_active: Optional[float] = None
    error_detail: Optional[str] = None
    start_attempt_utc: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        """Преобразовать в словарь для Redis."""
        return {
            key: value for key, value in {
                "status": self.status,
                "pid": str(self.pid) if self.pid else "",
                "last_active": str(self.last_active) if self.last_active else "",
                "error_detail": self.error_detail or "",
                "start_attempt_utc": self.start_attempt_utc or ""
            }.items() if value
        }

    @classmethod
    def from_redis_data(cls, process_id: str, redis_data: Dict[str, str]) -> "ProcessInfo":
        """Создать объект из данных Redis."""
        pid_val = redis_data.get("pid")
        pid = int(pid_val) if pid_val and pid_val.isdigit() else None

        last_active_val = redis_data.get("last_active")
        try:
            last_active = float(last_active_val) if last_active_val else None
        except (ValueError, TypeError):
            last_active = None

        return cls(
            process_id=process_id,
            status=redis_data.get("status", "unknown"),
            pid=pid,
            last_active=last_active,
            error_detail=redis_data.get("error_detail"),
            start_attempt_utc=redis_data.get("start_attempt_utc")
        )


@dataclass
class AgentInfo(ProcessInfo):
    """Информация об агенте."""

    agent_id: str = ""

    def __post_init__(self):
        if not self.agent_id and self.process_id:
            self.agent_id = self.process_id


@dataclass
class IntegrationInfo(ProcessInfo):
    """Информация об интеграции."""

    agent_id: str = ""
    integration_type: str = ""

    def to_dict(self) -> Dict[str, Any]:
        """Преобразовать в словарь для Redis с дополнительными полями."""
        data = super().to_dict()
        data.update({
            "agent_id": self.agent_id,
            "integration_type": self.integration_type
        })
        return data


class ProcessManagerProtocol(Protocol):
    """Протокол для интерфейса ProcessManager."""

    async def start_process(self, process_id: str, **kwargs) -> bool:
        """Запустить процесс."""

    async def stop_process(self, process_id: str, force: bool = False) -> bool:
        """Остановить процесс."""

    async def get_process_status(self, process_id: str) -> ProcessInfo:
        """Получить статус процесса."""


class ProcessManagerBase(RedisClientManager, ABC):
    """Абстрактный базовый класс для менеджеров процессов."""

    def __init__(self, config: ProcessConfiguration):
        super().__init__()
        self.config = config

    @abstractmethod
    async def start_process(self, process_id: str, **kwargs) -> bool:
        """Запустить процесс."""

    @abstractmethod
    async def stop_process(self, process_id: str, force: bool = False) -> bool:
        """Остановить процесс."""

    @abstractmethod
    async def get_process_status(self, process_id: str) -> ProcessInfo:
        """Получить статус процесса."""

    async def setup(self):
        """Настройка менеджера."""
        await super().setup()
        await self.setup_redis_client()

    async def cleanup(self):
        """Очистка ресурсов менеджера."""
        if hasattr(super(), 'cleanup'):
            await super().cleanup()

    # Общие утилиты для работы с Redis
    async def _update_status_in_redis(self, key: str, status_dict: Dict[str, Any]):
        """Обновить статус процесса в Redis."""
        if not await self.is_redis_client_available():
            raise ProcessManagerError("Redis client not available")

        try:
            redis_cli = await self.redis_client
            await redis_cli.hset(key, mapping=status_dict)
        except Exception as e:
            raise ProcessManagerError(f"Failed to update status in Redis: {e}") from e

    async def _get_status_from_redis(self, key: str) -> Dict[str, str]:
        """Получить статус процесса из Redis."""
        if not await self.is_redis_client_available():
            return {}

        try:
            redis_cli = await self.redis_client
            raw_status_data = await redis_cli.hgetall(key)

            if not raw_status_data:
                return {}

            # Декодирование данных из bytes в str
            decoded_status_data: Dict[str, str] = {}
            for k_bytes, v_bytes in raw_status_data.items():
                try:
                    k_str = k_bytes.decode('utf-8')
                    v_str = v_bytes.decode('utf-8')
                    decoded_status_data[k_str] = v_str
                except UnicodeDecodeError:
                    continue

            return decoded_status_data

        except Exception as e:
            raise ProcessManagerError(f"Failed to get status from Redis: {e}") from e

    async def _delete_status_key_from_redis(self, key: str):
        """Удалить ключ статуса из Redis."""
        if not await self.is_redis_client_available():
            return

        try:
            redis_cli = await self.redis_client
            await redis_cli.delete(key)
        except Exception as e:
            raise ProcessManagerError(f"Failed to delete status key from Redis: {e}") from e


# Типы для обратной совместимости
AgentStatusInfo = Dict[str, Any]
IntegrationStatusInfo = Dict[str, Any]
IntegrationTypeStr = str
