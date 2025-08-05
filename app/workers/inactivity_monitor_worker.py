"""Модуль для мониторинга неактивных агентов и интеграций."""

import asyncio
import logging
import os
import time

import redis.exceptions

from app.api.schemas.common_schemas import IntegrationType
from app.core.config import settings
from app.db.crud import agent_crud
from app.db.session import SessionLocal
from app.services.process_management import ProcessManager
from app.workers.base_worker import ScheduledTaskWorker

# logger is inherited from BaseWorker/ScheduledTaskWorker

class InactivityMonitorWorker(ScheduledTaskWorker):
    """
    Периодический воркер для отслеживания активности процессов агентов и интеграций.

    Основные задачи:
    - Проверка неактивных агентов и их остановка
    - Проверка "упавших" процессов и их перезапуск
    """

    def __init__(self):
        """Инициализирует InactivityMonitorWorker."""
        super().__init__(
            component_id="inactivity_monitor_worker",
            interval_seconds=settings.AGENT_INACTIVITY_CHECK_INTERVAL,
            status_key_prefix="worker_status:inactivity_monitor:"
        )
        self.process_manager = ProcessManager()
        self.logger.debug(
            "[%s] Initialized with check interval: %ss.",
            self._component_id, self.interval_seconds
        )

    async def setup(self):
        """Выполняет настройку воркера перед запуском основного цикла."""
        await super().setup()
        if not self._running:
            self.logger.info(
                "[%s] Shutdown initiated during setup. Aborting further setup.",
                self._component_id
            )
            return

        try:
            await self.process_manager.setup_manager()
            self.logger.info(
                "[%s] ProcessManager initialized for InactivityMonitor.",
                self._component_id
            )
        except (ConnectionError, OSError) as e:
            self.logger.error(
                "[%s] Failed to initialize ProcessManager: %s",
                self._component_id, e, exc_info=True
            )
            self.initiate_shutdown()
            return

        if self._running:
            self.logger.info(
                "[%s] Delaying initial check for 10 seconds...",
                self._component_id
            )
            await asyncio.sleep(10)
            self.logger.info(
                "[%s] Performing initial check for crashed/stopped processes...",
                self._component_id
            )
            await self._check_and_restart_crashed_processes()

    async def perform_task(self) -> None:
        """Основная периодическая задача."""
        if not self._running:
            self.logger.info(
                "[%s] Shutdown in progress. Skipping periodict checks.",
                self._component_id
            )
            return

        if not await self._ensure_redis_available():
            return

        self.logger.debug(
            "[%s] Running periodict checks (inactivity, crashes)...",
            self._component_id
        )

        try:
            if self._running:
                await self._check_inactive_agents()
            if self._running:
                await self._check_and_restart_crashed_processes()
        except (ConnectionError, OSError) as e:
            self.logger.error(
                "[%s] Connection error during perform_task: %s",
                self._component_id, e, exc_info=True
            )
        except Exception as e:
            self.logger.error(
                "[%s] Unexpected error during perform_task: %s",
                self._component_id, e, exc_info=True
            )

    async def _ensure_redis_available(self) -> bool:
        """Проверяет доступность Redis и переподключается при необходимости."""
        if not await self.process_manager.is_redis_client_available():
            self.logger.warning(
                "[%s] ProcessManager Redis client not available. Skipping checks.",
                self._component_id
            )
            try:
                await self.process_manager.setup_redis_client()
                if not await self.process_manager.is_redis_client_available():
                    self.logger.error(
                        "[%s] Failed to re-establisth ProcessManager Redis client.",
                        self._component_id
                    )
                    return False
                self.logger.info(
                    "[%s] ProcessManager Redis client re-establisthed.",
                    self._component_id
                )
            except (ConnectionError, OSError) as e_redis_setup:
                self.logger.error(
                    "[%s] Redis connection error: %s",
                    self._component_id, e_redis_setup
                )
                return False
            except Exception as e_redis_setup:
                self.logger.error(
                    "[%s] Unexpected error re-establisthing Redis client: %s",
                    self._component_id, e_redis_setup
                )
                return False
        return True

    async def _check_inactive_agents(self):
        """
        Проверяет неактивные процессы агентов и останавливает их.

        Если воркер находится в процессе остановки, проверка прерывается.
        """
        if not self._running:
            self.logger.info(
                "[%s] Shutdown in progress. Skipping inactivity check.",
                self._component_id
            )
            return

        self.logger.debug(
            "[%s] Running inactivity check for agents...",
            self._component_id
        )

        pm_redis_clientt = await self.process_manager.redis_client
        if not pm_redis_clientt:
            self.logger.warning(
                "[%s] ProcessManager Redis client not available for inactivity check.",
                self._component_id
            )
            return

        await self._check_agent_inactivity(pm_redis_clientt)

    async def _check_agent_inactivity(self, redis_clientt) -> None:
        """Проверяет неактивность агентов."""
        current_time = time.time()
        agent_inactivity_timeout = settings.AGENT_INACTIVITY_TIMEOUT

        try:
            agent_keys = await redis_clientt.keys("agent_status:*")
            for key_bytes in agent_keys:
                if not self._running:
                    self.logger.info(
                        "[%s] Shutdown in progress during agent scan. Aborting.",
                        self._component_id
                    )
                    break

                await self._check_single_agent_activity(
                    redis_clientt, key_bytes.decode('utf-8'), current_time, agent_inactivity_timeout
                )

        except redis.exceptions.RedisError as e_redis:
            self.logger.error(
                "[%s] Redis error during agent inactivity check: %s",
                self._component_id, e_redis, exc_info=True
            )
        except Exception as e:
            self.logger.error(
                "[%s] Unexpected error during agent inactivity check: %s",
                self._component_id, e, exc_info=True
            )

    async def _check_single_agent_activity(
        self, redis_clientt, key: str, current_time: float, timeout: int
    ) -> None:
        """Проверяет активность одного агента."""
        agent_id = key.split(":", 1)[1]
        status = await redis_clientt.hgetall(key)
        status = {k.decode('utf-8'): v.decode('utf-8') for k, v in status.items()}

        last_active_str = status.get("last_active_time")
        current_status = status.get("status")

        if current_status == "running" and last_active_str:
            if self._should_stop_inactive_agent(last_active_str, current_time, timeout, agent_id):
                if self._running:
                    await self.process_manager.stop_agent_process(agent_id, force=False)
            else:
                self._log_agent_activity(last_active_str, agent_id)

    def _should_stop_inactive_agent(
        self, last_active_str: str, current_time: float, timeout: int, agent_id: str
    ) -> bool:
        """Определяет, нужно ли остановить неактивного агента."""
        try:
            last_active = float(last_active_str)
            if (current_time - last_active) > timeout:
                self.logger.warning(
                    "[%s] Agent %s is inactive (last active %.0f, current %.0f, "
                    "timeout %ss). Attempting to stop.",
                    self._component_id, agent_id, last_active, current_time, timeout
                )
                return True
            return False
        except ValueError:
            self.logger.error(
                "[%s] Could not parse last_active_time '%s' for agent %s.",
                self._component_id, last_active_str, agent_id
            )
            return False

    def _log_agent_activity(self, last_active_str: str, agent_id: str) -> None:
        """Логирует активность агента."""
        try:
            last_active = float(last_active_str)
            self.logger.debug(
                "[%s] Agent %s is active (last active %.0f).",
                self._component_id, agent_id, last_active
            )
        except ValueError:
            pass  # Уже логируется в _should_stop_inactive_agent


    def _is_process_alive(self, pid_str: str, context: str) -> bool:
        """
        Проверяет, существует ли процесс с заданным PID.

        Args:
            pid_str: Строка с PID процесса
            context: Контекст для логирования (например, "agent agent_id")

        Returns:
            bool: True если процесс жив, False иначе
        """
        if not pid_str:
            return False

        try:
            pid = int(pid_str)
            os.kill(pid, 0)
            return True
        except (ProcessLookupError, ValueError):
            return False
        except OSError as ose:
            self.logger.warning(
                "[%s] OSError checking PID %s for %s: %s. Assuming not alive for safety.",
                self._component_id, pid_str, context, ose
            )
            return False

    def _should_restart_agent(
        self, agent_status_info: dict
    ) -> bool:
        """
        Определяет, нужно ли перезапускать агента.

        Args:
            agent_status_info: Словарь с информацией об агенте
                (agent_id, status, pid_str, should_be_running)

        Returns:
            bool: True если нужен перезапуск
        """
        agent_id = agent_status_info["agent_id"]
        status = agent_status_info["status"]
        pid_str = agent_status_info["pid_str"]
        should_be_running = agent_status_info["should_be_running"]

        process_alive = self._is_process_alive(pid_str, f"agent {agent_id}")

        # Статус running/initializing но процесс мертв
        if status in ["running", "initializing"] and pid_str and not process_alive:
            self.logger.warning(
                "[%s] Agent %s has status '%s' with PID %s but process is not alive. "
                "Flagging for restart.",
                self._component_id, agent_id, status, pid_str
            )
            return True

        # Должен работать но процесс мертв
        if should_be_running and not process_alive:
            self.logger.warning(
                "[%s] Agent %s is marked as 'should_be_running' but process (PID: %s) "
                "is not alive. Status: '%s'. Flagging for restart.",
                self._component_id, agent_id, pid_str or 'N/A', status
            )
            return True

        return False

    def _parse_integration_key(self, key: str) -> tuple[str, str] | None:
        """
        Парсит ключ статуса интеграции.

        Args:
            key: Ключ Redis в формате integration_status:agent_id:type

        Returns:
            tuple[str, str] | None: (agent_id, integration_type) или None если ошибка
        """
        parts = key.split(":")
        valid_integration_types = [it.value for it in IntegrationType]

        if len(parts) != 3 or parts[0] != "integration_status":
            return self._handle_malformed_key(key, parts)

        return self._validate_integration_key_parts(key, parts, valid_integration_types)

    def _handle_malformed_key(self, key: str, parts: list) -> None:
        """Обрабатывает некорректные ключи интеграций."""
        if len(parts) == 4 and parts[0] == "integration_status" and parts[1] == "telegram_bot":
            self.logger.warning(
                "[%s] Detected old specific integration status key format "
                "(telegram_bot): %s. Skipping.",
                self._component_id, key
            )
        else:
            self.logger.warning(
                "[%s] Malformed integration status key (unexpected number of parts "
                "or prefix): %s. Parts: %s",
                self._component_id, key, parts
            )

    def _validate_integration_key_parts(
        self, key: str, parts: list, valid_types: list
    ) -> tuple[str, str] | None:
        """Валидирует части ключа интеграции."""
        potential_agent_id = parts[1]
        potential_integration_type = parts[2].lower()

        if potential_integration_type in valid_types:
            self.logger.debug(
                "[%s] Parsed new format key: %s -> agent_id=%s, type=%s",
                self._component_id, key, potential_agent_id, potential_integration_type
            )
            return potential_agent_id, potential_integration_type

        # Проверяем старый формат
        if parts[1].lower() in valid_types:
            self.logger.warning(
                "[%s] Detected potentially old integration status key format: %s. "
                "Skipping restart for this key during transition.",
                self._component_id, key
            )
            return None

        self.logger.warning(
            "[%s] Malformed integration status key (3 parts, but type '%s' "
            "not recognized): %s",
            self._component_id, parts[2], key
        )
        return None

    def _determine_integration_action(
        self, integration_info: dict
    ) -> str | None:
        """
        Определяет действие для интеграции (restart/start/None).

        Args:
            integration_info: Словарь с информацией об интеграции
                (agent_id, integration_type, status, pid_str, should_be_running)

        Returns:
            str | None: "restart", "start" или None
        """
        agent_id = integration_info["agent_id"]
        integration_type = integration_info["integration_type"]
        status = integration_info["status"]
        pid_str = integration_info["pid_str"]
        should_be_running = integration_info["should_be_running"]

        process_alive = self._is_process_alive(
            pid_str, f"integration {agent_id}/{integration_type}"
        )

        # Проверяем условия для перезапуска
        if self._should_restart_integration(status, pid_str, process_alive):
            return "restart"

        # Проверяем условия для старта
        if self._should_start_integration(status, pid_str, should_be_running):
            return "start"

        return None

    def _should_restart_integration(self, status: str, pid_str: str, process_alive: bool) -> bool:
        """Определяет нужен ли перезапуск интеграции."""
        if status in ["running", "initializing"] and pid_str and not process_alive:
            return True
        return not process_alive

    def _should_start_integration(self, status: str, pid_str: str, should_be_running: bool) -> bool:
        """Определяет нужен ли старт интеграции."""
        excluded_statuses = ["stopped", "not_found", "error_start_failed", "error_script_not_found"]
        return (not pid_str and status not in excluded_statuses and should_be_running)

    async def _get_integration_settings(
        self, agent_id: str, integration_type: str
    ) -> dict | None:
        """
        Получает настройки интеграции из БД.

        Args:
            agent_id: ID агента
            integration_type: Тип интеграции

        Returns:
            dict | None: Настройки интеграции или None если не найдены
        """
        async with SessionLocal() as db_session:
            try:
                agent_config_db = await agent_crud.db_get_agent_config(
                    db_session, agent_id=agent_id
                )
                self.logger.debug(
                    "[%s] Fetched agent_config_db for %s (integration %s): %s",
                    self._component_id, agent_id, integration_type, agent_config_db
                )
            except Exception as e:
                self.logger.error(
                    "[%s] DB error fetching agent config for %s to process "
                    "integration %s: %s",
                    self._component_id, agent_id, integration_type, e, exc_info=True
                )
                return None

            if not agent_config_db:
                self.logger.info(
                    "[%s] Agent %s not found in DB. Skipping integration %s.",
                    self._component_id, agent_id, integration_type
                )
                return None

            # Поиск настроек интеграции
            if agent_config_db.config_json and agent_config_db.config_json.get("integrations"):
                for integ_conf in agent_config_db.config_json["integrations"]:
                    if integ_conf.get("integration_type", "").upper() == integration_type.upper():
                        self.logger.info(
                            "[%s] Found settings for %s/%s.",
                            self._component_id, agent_id, integration_type
                        )
                        return integ_conf.get("settings")

            self.logger.info(
                "[%s] Agent %s found, but integration type %s is not configured "
                "or has no settings in DB.",
                self._component_id, agent_id, integration_type
            )
            return None

    async def _perform_integration_action(
        self, action: str, agent_id: str, integration_type: str, config: dict
    ) -> bool:
        """
        Выполняет действие с интеграцией (restart/start).

        Args:
            action: Действие ("restart" или "start")
            agent_id: ID агента
            integration_type: Тип интеграции
            config: Настройки интеграции

        Returns:
            bool: True если действие выполнено успешно
        """
        if action == "restart":
            return await self._restart_integration(agent_id, integration_type, config)
        if action == "start":
            return await self._start_integration(agent_id, integration_type, config)
        return False

    async def _restart_integration(
        self, agent_id: str, integration_type: str, config: dict
    ) -> bool:
        """Перезапускает интеграцию."""
        self.logger.info(
            "[%s] Attempting to restart integration %s/%s with retrieved config...",
            self._component_id, agent_id, integration_type
        )
        # Сначала останавливаем, потом запускаем
        stop_result = await self.process_manager.stop_integration_process(
            agent_id, integration_type, force=True
        )
        if stop_result:
            # Небольшая пауза перед запуском
            await asyncio.sleep(1)
            resultt = await self.process_manager.start_integration_process(
                agent_id, integration_type, integration_settings=config
            )
        else:
            resultt = False

        if resultt:
            self.logger.info(
                "[%s] Successfully initiated restart for integration %s/%s.",
                self._component_id, agent_id, integration_type
            )
        else:
            self.logger.error(
                "[%s] Failed to initiate restart for integration %s/%s.",
                self._component_id, agent_id, integration_type
            )
        return resultt

    async def _start_integration(
        self, agent_id: str, integration_type: str, config: dict
    ) -> bool:
        """Стартует интеграцию."""
        self.logger.info(
            "[%s] Attempting to start integration %s/%s with retrieved config...",
            self._component_id, agent_id, integration_type
        )
        resultt = await self.process_manager.start_integration_process(
            agent_id, integration_type, integration_settings=config
        )
        if resultt:
            self.logger.info(
                "[%s] Successfully initiated start for integration %s/%s.",
                self._component_id, agent_id, integration_type
            )
        else:
            self.logger.error(
                "[%s] Failed to initiate start for integration %s/%s.",
                self._component_id, agent_id, integration_type
            )
        return resultt

    async def _check_agents_for_restart(self) -> None:
        """Проверяет агентов на необходимость перезапуска."""
        pm_redis_clientt = await self.process_manager.redis_client
        if not pm_redis_clientt:
            self.logger.warning(
                "[%s] ProcessManager Redis client not available for agent crash check.",
                self._component_id
            )
            return

        await self._process_agent_keys(pm_redis_clientt)

    async def _process_agent_keys(self, redis_clientt) -> None:
        """Обрабатывает ключи агентов."""
        try:
            agent_keys = await redis_clientt.keys("agent_status:*")
            for key_bytes in agent_keys:
                if not self._running:
                    self.logger.info(
                        "[%s] Shutdown in progress during agent crash check. Aborting.",
                        self._component_id
                    )
                    break

                await self._process_single_agent(redis_clientt, key_bytes.decode('utf-8'))

        except redis.exceptions.RedisError as e_redis_agent:
            self.logger.error(
                "[%s] Redis error during agent crash check: %s",
                self._component_id, e_redis_agent, exc_info=True
            )
        except OSError as e_os:
            self.logger.error(
                "[%s] OS error scanning agent statuses: %s",
                self._component_id, e_os, exc_info=True
            )
        except Exception as e_agent_scan:
            self.logger.error(
                "[%s] Unexpected error scanning agent statuses for crash check: %s",
                self._component_id, e_agent_scan, exc_info=True
            )

    async def _process_single_agent(self, redis_clientt, key: str) -> None:
        """Обрабатывает одного агента."""
        agent_id = key.split(":", 1)[1]
        status_data = await redis_clientt.hgetall(key)
        status = {k.decode('utf-8'): v.decode('utf-8') for k, v in status_data.items()}

        current_status = status.get("status")
        pid_str = status.get("pid")
        process_should_be_running = (
            status.get("process_should_be_running", "false").lower() == "true"
        )

        agent_status_info = {
            "agent_id": agent_id,
            "status": current_status,
            "pid_str": pid_str,
            "should_be_running": process_should_be_running
        }

        if self._should_restart_agent(agent_status_info):
            if self._running:
                self.logger.info(
                    "[%s] Attempting to restart agent %s...",
                    self._component_id, agent_id
                )
                await self.process_manager.restart_agent_process(agent_id)
        elif current_status in ["stopped", "error"] and not process_should_be_running:
            self.logger.debug(
                "[%s] Agent %s is in status '%s' and not marked "
                "'process_should_be_running'. No restart action.",
                self._component_id, agent_id, current_status
            )

    async def _check_integrations_for_restart(self) -> None:
        """Проверяет интеграции на необходимость перезапуска."""
        if not self._running:
            self.logger.info(
                "[%s] Shutdown in progress after agent checks. Skipping integration checks.",
                self._component_id
            )
            return

        pm_redis_clientt = await self.process_manager.redis_client
        if not pm_redis_clientt:
            self.logger.warning(
                "[%s] ProcessManager Redis client not available for integration crash check.",
                self._component_id
            )
            return

        await self._process_integration_keys(pm_redis_clientt)

    async def _process_integration_keys(self, redis_clientt) -> None:
        """Обрабатывает все ключи интеграций."""
        try:
            integration_keys = await redis_clientt.keys("integration_status:*")
            for key_bytes in integration_keys:
                if not self._running:
                    self.logger.info(
                        "[%s] Shutdown in progress during integration crash check. Aborting.",
                        self._component_id
                    )
                    break

                await self._process_single_integration(redis_clientt, key_bytes.decode('utf-8'))

        except redis.exceptions.RedisError as e_redis_int:
            self.logger.error(
                "[%s] Redis error during integration crash check: %s",
                self._component_id, e_redis_int, exc_info=True
            )
        except OSError as e_os:
            self.logger.error(
                "[%s] OS error scanning integration statuses: %s",
                self._component_id, e_os, exc_info=True
            )
        except Exception as e_int_scan:
            self.logger.error(
                "[%s] Unexpected error scanning integration statuses for crash check: %s",
                self._component_id, e_int_scan, exc_info=True
            )

    async def _process_single_integration(self, redis_clientt, key: str) -> None:
        """Обрабатывает одну интеграцию."""
        parsed_resultt = self._parse_integration_key(key)
        if not parsed_resultt:
            return

        agent_id, integration_type_str = parsed_resultt
        status_data = await redis_clientt.hgetall(key)
        status = {k.decode('utf-8'): v.decode('utf-8') for k, v in status_data.items()}

        current_status = status.get("status")
        pid_str = status.get("pid")
        process_should_be_running = (
            status.get("process_should_be_running", "false").lower() == "true"
        )

        integration_info = {
            "agent_id": agent_id,
            "integration_type": integration_type_str,
            "status": current_status,
            "pid_str": pid_str,
            "should_be_running": process_should_be_running
        }

        action_to_take = self._determine_integration_action(integration_info)

        if action_to_take and self._running:
            await self._handle_integration_action(action_to_take, agent_id, integration_type_str)
        elif self._running:
            self._log_no_action_needed(agent_id, integration_type_str, current_status, pid_str)

    async def _handle_integration_action(
        self, action: str, agent_id: str, integration_type: str
    ) -> None:
        """Обрабатывает действие с интеграцией."""
        self.logger.info(
            "[%s] Action '%s' identified for integration %s/%s.",
            self._component_id, action, agent_id, integration_type
        )

        config = await self._get_integration_settings(agent_id, integration_type)
        if config is not None:
            await self._perform_integration_action(action, agent_id, integration_type, config)

    def _log_no_action_needed(
        self, agent_id: str, integration_type: str, status: str, pid_str: str
    ) -> None:
        """Логирует отсутствие необходимости в действии."""
        context = f"integration {agent_id}/{integration_type}"
        process_alive = self._is_process_alive(pid_str, context)
        self.logger.debug(
            "[%s] No action needed for integration %s/%s "
            "(Status: '%s', PID: %s, Alive: %s).",
            self._component_id, agent_id, integration_type,
            status, pid_str or 'N/A', process_alive
        )
    async def _check_and_restart_crashed_processes(self):
        """
        Проверяет "упавшие" или некорректно остановленные процессы агентов и интеграций
        и пытается их перезапустить или запустить.

        Если воркер находится в процессе остановки, проверка прерывается.
        """
        if not self._running:
            self.logger.info(
                "[%s] Shutdown in progress. Skipping crash check and restarts.",
                self._component_id
            )
            return

        self.logger.debug(
            "[%s] Checking for crashed or stopped agents and integrations to restart...",
            self._component_id
        )

        # Проверяем агентов
        await self._check_agents_for_restart()

        # Проверяем интеграции
        await self._check_integrations_for_restart()

    async def cleanup(self):
        """
        Выполняет очистку ресурсов при завершении работы InactivityMonitorWorker.
        """
        self.logger.info(
            "[%s] Cleaning up InactivityMonitorWorker...",
            self._component_id
        )
        if self.process_manager:
            try:
                await self.process_manager.cleanup_manager()
                self.logger.info(
                    "[%s] ProcessManager cleaned up.",
                    self._component_id
                )
            except (ConnectionError, OSError) as e_pm_cleanup:
                self.logger.error(
                    "[%s] Connection error cleaning up ProcessManager: %s",
                    self._component_id, e_pm_cleanup, exc_info=True
                )
            except Exception as e_pm_cleanup:
                self.logger.error(
                    "[%s] Unexpected error cleaning up ProcessManager: %s",
                    self._component_id, e_pm_cleanup, exc_info=True
                )
        await super().cleanup()
        self.logger.info(
            "[%s] InactivityMonitorWorker cleanup finished.",
            self._component_id
        )


if __name__ == "__main__":
    # Базовая настройка логирования для прямого запуска
    logging.basicConfig(
        level=settings.LOG_LEVEL,
        format='%(asctime)s - %(levelname)s - %(name)s - [%(component_id)s] - %(message)s'
    )

    if not logging.getLogger().hasHandlers():
        logging.getLogger().addHandler(logging.StreamHandler())

    main_logger = logging.getLogger("inactivity_monitor_main")
    main_logger = logging.LoggerAdapter(main_logger, {"component_id": "main"})

    main_logger.info("Initializing InactivityMonitorWorker...")

    worker = InactivityMonitorWorker()

    try:
        asyncio.run(worker.run())
    except KeyboardInterrupt:
        main_logger.info("InactivityMonitorWorker interrupted by user (KeyboardInterrupt).")
    except (ConnectionError, OSError) as e:
        main_logger.critical(
            "InactivityMonitorWorker failed due to connection/OS error: %s",
            e, exc_info=True
        )
    except Exception as e:
        main_logger.critical(
            "InactivityMonitorWorker failed due to unexpected error: %s",
            e, exc_info=True
        )
    finally:
        main_logger.info("InactivityMonitorWorker application finished.")
