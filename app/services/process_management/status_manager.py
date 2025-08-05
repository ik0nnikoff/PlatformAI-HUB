"""
Менеджер статусов процессов.

Модуль содержит классы для управления статусами процессов в Redis,
включая валидацию, проверку существования процессов и обновление статусов.
"""

import os
import logging
import time
from typing import Dict, Any, List
from datetime import datetime, timezone

from .base import ProcessManagerBase, ProcessInfo, AgentInfo, IntegrationInfo
from .exceptions import ProcessManagerError


logger = logging.getLogger(__name__)


class ProcessValidationConfig:
    """Конфигурация для валидации процессов."""

    def __init__(self, process_id: str, status_key: str,
                 running_statuses: List[str] = None):
        self.process_id = process_id
        self.status_key = status_key
        self.running_statuses = running_statuses or [
            "running", "starting", "initializing"
        ]

    def is_running_status(self, status: str) -> bool:
        """Проверить, является ли статус "работающим"."""
        return status in self.running_statuses

    def get_full_id(self) -> str:
        """Получить полный идентификатор процесса."""
        return f"{self.process_id}@{self.status_key}"


class ProcessStatusManager(ProcessManagerBase):
    """Менеджер для управления статусами процессов в Redis."""

    async def start_process(self, process_id: str, **kwargs) -> bool:
        """Базовая реализация - должна быть переопределена в наследниках."""
        raise NotImplementedError("Must be implemented by subclasses")

    async def stop_process(self, process_id: str, force: bool = False) -> bool:
        """Базовая реализация - должна быть переопределена в наследниках."""
        raise NotImplementedError("Must be implemented by subclasses")

    async def get_process_status(self, process_id: str) -> ProcessInfo:
        """Базовая реализация - должна быть переопределена в наследниках."""
        raise NotImplementedError("Must be implemented by subclasses")

    async def validate_process_status_unified(
        self, validation_config: ProcessValidationConfig
    ) -> ProcessInfo:
        """Унифицированная валидация статуса процесса."""
        status_data = await self._get_status_from_redis(validation_config.status_key)

        if not status_data:
            return ProcessInfo(
                process_id=validation_config.process_id,
                status=self.config.defaults.STATUS_NOT_FOUND
            )

        process_info = ProcessInfo.from_redis_data(
            validation_config.process_id, status_data
        )

        # Проверить существование процесса, если статус предполагает работу
        if (process_info.pid and
            validation_config.is_running_status(process_info.status)):

            if not await self._validate_process_existence(process_info.pid):
                # Процесс не найден - обновить статус
                await self._update_lost_process_status(
                    validation_config.status_key, process_info
                )
                process_info.status = self.config.defaults.STATUS_ERROR_PROCESS_LOST
                process_info.pid = None

        return process_info

    async def _validate_process_existence(self, pid: int) -> bool:
        """Проверить существование процесса по PID."""
        try:
            os.kill(pid, 0)  # Сигнал 0 только проверяет существование
            return True
        except ProcessLookupError:
            return False
        except OSError:
            # Другие ошибки (например, нет прав доступа) считаем как существующий процесс
            return True

    async def _update_lost_process_status(self, status_key: str, process_info: ProcessInfo):
        """Обновить статус потерянного процесса."""
        error_detail = f"Process PID {process_info.pid} not found"
        update_data = {
            "status": self.config.defaults.STATUS_ERROR_PROCESS_LOST,
            "pid": "",
            "error_detail": error_detail
        }

        try:
            await self._update_status_in_redis(status_key, update_data)
            logger.warning(
                "Process %s PID %s not found. Updated status to %s",
                process_info.process_id, process_info.pid,
                self.config.defaults.STATUS_ERROR_PROCESS_LOST
            )
        except (OSError, ValueError, RuntimeError) as e:
            logger.error(
                "Failed to update lost process status for %s: %s",
                process_info.process_id, e
            )

    async def initialize_starting_status_unified(
        self, status_key: str, process_type: str, process_id: str
    ) -> Dict[str, Any]:
        """Унифицированная инициализация статуса "starting"."""
        initial_status_data = {
            "status": self.config.defaults.STATUS_STARTING,
            f"{process_type}_id": process_id,
            "start_attempt_utc": datetime.now(timezone.utc).isoformat(),
            "last_active": str(time.time())
        }

        try:
            await self._update_status_in_redis(status_key, initial_status_data)
            logger.debug("Initialized starting status for %s %s", process_type, process_id)
            return initial_status_data
        except Exception as e:
            logger.error("Failed to initialize starting status: %s", e)
            raise ProcessManagerError("Failed to initialize starting status") from e

    async def cleanup_stopped_process_status_unified(self, status_key: str,
                                                   process_type: str, process_id: str):
        """Унифицированная очистка статуса остановленного процесса."""
        cleanup_data = {
            "status": self.config.defaults.STATUS_STOPPED,
            "pid": "",
            "stop_utc": datetime.now(timezone.utc).isoformat(),
            "last_active": str(time.time())
        }

        try:
            await self._update_status_in_redis(status_key, cleanup_data)
            logger.debug("Cleaned up stopped status for %s %s", process_type, process_id)
        except (OSError, ValueError, RuntimeError) as e:
            logger.error("Failed to cleanup stopped status: %s", e)

    async def get_agent_status_info(self, agent_id: str) -> AgentInfo:
        """Получить информацию о статусе агента."""
        status_key = self.config.get_agent_status_key(agent_id)
        validation_config = ProcessValidationConfig(agent_id, status_key)

        process_info = await self.validate_process_status_unified(validation_config)

        return AgentInfo(
            process_id=agent_id,
            agent_id=agent_id,
            status=process_info.status,
            pid=process_info.pid,
            last_active=process_info.last_active,
            error_detail=process_info.error_detail,
            start_attempt_utc=process_info.start_attempt_utc
        )

    async def get_integration_status_info(self, agent_id: str,
                                        integration_type: str) -> IntegrationInfo:
        """Получить информацию о статусе интеграции."""
        status_key = self.config.get_integration_status_key(agent_id, integration_type)
        validation_config = ProcessValidationConfig(
            f"{agent_id}_{integration_type}", status_key
        )

        process_info = await self.validate_process_status_unified(validation_config)

        return IntegrationInfo(
            process_id=f"{agent_id}_{integration_type}",
            agent_id=agent_id,
            integration_type=integration_type,
            status=process_info.status,
            pid=process_info.pid,
            last_active=process_info.last_active,
            error_detail=process_info.error_detail,
            start_attempt_utc=process_info.start_attempt_utc
        )

    async def delete_agent_status_completely(self, agent_id: str):
        """Полностью удалить статус агента из Redis."""
        status_key = self.config.get_agent_status_key(agent_id)
        await self._delete_status_key_from_redis(status_key)
        logger.info("Completely deleted agent status for %s", agent_id)

    async def delete_integration_status_completely(self, agent_id: str, integration_type: str):
        """Полностью удалить статус интеграции из Redis."""
        status_key = self.config.get_integration_status_key(agent_id, integration_type)
        await self._delete_status_key_from_redis(status_key)
        logger.info("Completely deleted integration status for %s/%s", agent_id, integration_type)

    async def delete_fields_from_redis_status(self, key: str, fields: List[str]):
        """Удалить конкретные поля из статуса в Redis."""
        if not await self.is_redis_client_available():
            logger.warning("Redis client not available for deleting fields from %s", key)
            return

        try:
            redis_cli = await self.redis_client
            await redis_cli.hdel(key, *fields)
            logger.debug("Deleted fields %s from Redis key %s", fields, key)
        except (OSError, ValueError, RuntimeError) as e:
            logger.error("Failed to delete fields from Redis status: %s", e)
            raise ProcessManagerError("Failed to delete fields from Redis") from e
