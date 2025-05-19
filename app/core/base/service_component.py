from abc import ABC, abstractmethod
import logging
import asyncio # Add asyncio import
from typing import Optional, List, Coroutine # Add List and Coroutine
import os # <--- Добавлен этот импорт

from app.core.base.runnable_component import RunnableComponent
from app.core.base.status_updater import StatusUpdater
from app.core.config import settings # Added import for settings


class ServiceComponentBase(RunnableComponent, StatusUpdater, ABC):
    """
    Абстрактный базовый класс для сервисных компонентов, таких как AgentRunner или TelegramIntegrationBot.

    Объединяет жизненный цикл `RunnableComponent` с возможностями `StatusUpdater`
    для управления состоянием в Redis.
    Также предоставляет унифицированное управление основными задачами asyncio.
    """
    needs_restart: bool # Объявление атрибута класса
    _main_tasks: List[asyncio.Task] # Для хранения основных задач компонента

    def __init__(self,
                 component_id: str,
                 status_key_prefix: str,
                 logger_adapter: logging.LoggerAdapter):
        """
        Инициализатор ServiceComponentBase.

        Args:
            component_id: Уникальный идентификатор компонента.
            status_key_prefix: Префикс для ключей статуса в Redis.
            logger_adapter: Адаптер логгера для компонента.
        """
        RunnableComponent.__init__(self, logger=logger_adapter) # Pass logger_adapter
        StatusUpdater.__init__(self)

        self._component_id = component_id
        # Гарантируем, что status_key_prefix заканчивается двоеточием для пространства имен
        self._status_key_prefix = status_key_prefix if status_key_prefix.endswith(':') else status_key_prefix + ':'
        self.needs_restart = False # Инициализация флага перезапуска
        self._main_tasks = [] # Инициализация списка основных задач
        # Use self.logger which is set by RunnableComponent
        self.logger.info(f"ServiceComponentBase initialized.")

    def _register_main_task(self, coro: Coroutine, name: Optional[str] = None) -> asyncio.Task:
        """
        Регистрирует корутину как основную задачу компонента.

        Args:
            coro: Корутина для выполнения.
            name: Опциональное имя для задачи.

        Returns:
            Созданная задача asyncio.Task.
        """
        if name is None:
            name = f"{self._component_id}_task_{len(self._main_tasks) + 1}"
        
        task = asyncio.create_task(coro, name=name)
        self._main_tasks.append(task)
        self.logger.info(f"Registered main task: {name}")
        return task

    def request_restart(self) -> None:
        """Сигнализирует о необходимости перезапуска компонента."""
        self.logger.info(f"[self._component_id] Restart requested.")
        self.needs_restart = True
        self.initiate_shutdown() # Также инициируем штатное завершение текущего цикла run_loop

    def clear_restart_request(self) -> None:
        """Сбрасывает флаг запроса на перезапуск."""
        self.needs_restart = False

    async def setup(self) -> None:
        """
        Конкретная реализация метода `setup` из `RunnableComponent`.

        Инициализирует `StatusUpdater` (и, следовательно, `RedisClientManager`)
        для возможности обновления статуса компонента в Redis.
        Использует `settings.REDIS_URL` для подключения к Redis.
        Сбрасывает флаг `needs_restart` и очищает список предыдущих задач.
        """
        self.logger.info(f"ServiceComponent setup started.")
        self.clear_restart_request() # Сброс флага перед каждой настройкой
        
        # Очистка списка задач от предыдущих запусков (если были)
        # Задачи должны быть отменены в cleanup предыдущего цикла.
        if self._main_tasks:
            self.logger.warning(f"Main tasks list was not empty at setup. This might indicate an issue in cleanup. Clearing.")
            self._main_tasks.clear()

        await self.setup_status_updater(redis_url=str(settings.REDIS_URL))
        await self.mark_as_initializing() # Set initial status
        self.logger.info(f"ServiceComponent setup completed.")

    @abstractmethod
    async def run_loop(self) -> None:
        """
        Абстрактный метод, представляющий основной цикл работы компонента.
        Дочерние классы должны зарегистрировать свои основные задачи через `_register_main_task`
        (обычно в своем `setup` или в начале своего `run_loop` перед вызовом `super().run_loop()`),
        а затем вызвать `await super().run_loop()` для управления этими задачами.
        """
        if not self._running:
            self.logger.info(f"Run_loop called but component is not marked as running. Exiting loop.")
            return

        if not self._main_tasks:
            self.logger.warning(f"Run_loop started, but no main tasks were registered. The component might not do anything.")
            # Можно решить, что делать в этом случае:
            # 1. Просто выйти (как сейчас)
            # 2. Ждать, пока self._running не станет False (пассивное ожидание)
            # 3. Вызвать ошибку
            # Пока что, если нет задач, цикл просто завершится, если _running станет False.
            # Для активного компонента это обычно означает, что он ничего не будет делать.
            while self._running: # Пассивное ожидание, если нет задач
                await asyncio.sleep(0.1)
            self.logger.info(f"Exiting run_loop (no main tasks, _running is now False).")
            return

        self.logger.info(f"Starting run_loop with {len(self._main_tasks)} registered main tasks.")
        await self.mark_as_running(pid=os.getpid() if hasattr(os, 'getpid') else None)

        try:
            # Копируем список задач, так как asyncio.wait может модифицировать его (хотя обычно нет)
            # и для безопасности, если _main_tasks будет изменен извне (не должно происходить)
            active_tasks = list(self._main_tasks)
            
            done, pending = await asyncio.wait(active_tasks, return_when=asyncio.FIRST_COMPLETED)

            for task in done:
                task_name = task.get_name()
                try:
                    # Проверяем результат задачи, чтобы выявить исключения
                    result = task.result()
                    self.logger.info(f"Main task '{task_name}' completed. Result: {result}")
                except asyncio.CancelledError:
                    self.logger.info(f"Main task '{task_name}' was cancelled.")
                    # Если отмена была инициирована извне (не через self.initiate_shutdown()),
                    # то это может быть неожиданно. Но обычно отмена происходит во время cleanup.
                except Exception as e:
                    self.logger.error(f"Main task '{task_name}' failed: {e}", exc_info=True)
                    await self.mark_as_error(reason=f"Main task {task_name} failed: {e}")
                    self.clear_restart_request()  # Критическая ошибка, не перезапускать через runner_main
                    self.initiate_shutdown() # Инициируем остановку остальных задач и компонента
                    # _running будет False, что приведет к выходу из внешнего цикла в RunnableComponent.run()
                finally:
                    # Удаляем завершенную задачу из списка активных, если нужно отслеживать
                    # Но так как мы выходим при FIRST_COMPLETED, это не так важно здесь.
                    pass
            
            # Если мы здесь, значит, одна из задач завершилась (возможно, с ошибкой).
            # Мы должны инициировать остановку, если это не было сделано.
            if self._running: # Если еще не было команды на остановку (например, из-за ошибки выше)
                self.logger.info(f"A main task finished. Initiating shutdown of other tasks and component.")
                self.initiate_shutdown()

        except asyncio.CancelledError:
            self.logger.info(f"ServiceComponentBase run_loop itself was cancelled (e.g. during shutdown).")
            self.initiate_shutdown()
            # initiate_shutdown() должен был быть вызван ранее, чтобы это произошло.
            # Все задачи будут отменены в cleanup.
        
        # После выхода из asyncio.wait (или если run_loop был отменен),
        # нужно дождаться, пока self._running станет False, если это еще не так.
        # Это гарантирует, что cleanup будет вызван корректно.
        while self._running:
            self.logger.debug(f"Run_loop waiting for _running to become False...")
            await asyncio.sleep(0.05)
        
        self.logger.info(f"ServiceComponentBase run_loop finished.")

    async def cleanup(self) -> None:
        """
        Конкретная реализация метода `cleanup` из `RunnableComponent`.

        Помечает компонент как остановленный, отменяет все зарегистрированные основные задачи,
        очищает ресурсы `StatusUpdater` (включая закрытие соединения с Redis).
        """
        self.logger.info(f"ServiceComponent cleanup started. Cancelling {len(self._main_tasks)} main tasks.")
        
        # Отменяем все зарегистрированные задачи
        cancelled_tasks = []
        for task in self._main_tasks:
            if not task.done():
                task.cancel()
                cancelled_tasks.append(task)
        
        if cancelled_tasks:
            self.logger.info(f"Waiting for {len(cancelled_tasks)} tasks to process cancellation...")
            # Ожидаем завершения отмененных задач
            # return_exceptions=True, чтобы gather не прервался, если одна из задач при отмене выбросит исключение (кроме CancelledError)
            results = await asyncio.gather(*cancelled_tasks, return_exceptions=True)
            for task, result in zip(cancelled_tasks, results):
                task_name = task.get_name()
                if isinstance(result, asyncio.CancelledError):
                    self.logger.info(f"Task '{task_name}' confirmed cancelled.")
                elif isinstance(result, Exception):
                    self.logger.error(f"Task '{task_name}' raised an exception during cancellation: {result}", exc_info=isinstance(result, BaseException))
                else:
                    self.logger.info(f"Task '{task_name}' completed during cleanup with result: {result}")
        else:
            self.logger.info(f"No running main tasks to cancel.")

        self._main_tasks.clear() # Очищаем список задач
        self.logger.info(f"All main tasks processed and list cleared.")

        await self.mark_as_stopped(reason="Service component cleanup")
        await self.cleanup_status_updater(clear_status_on_cleanup=True)
        self.logger.info(f"ServiceComponent cleanup completed.")

    async def _pubsub_listener_loop(self):
        """Прослушивает сообщения Pub/Sub из Redis и обрабатывает их."""
        # self.redis_client is inherited from StatusUpdater -> RedisClientManager
        # and initialized by ServiceComponentBase.setup() -> StatusUpdater.setup_status_updater()
        
        # Ensure redis_client is available (it should be after setup)
        try:
            redis_cli = await self.redis_client 
        except RuntimeError as e:
            self.logger.critical(f"[{self._component_id}] Redis client not available for Pub/Sub listener: {e}. Listener cannot start.")
            await self.mark_as_error(reason=f"Redis client unavailable for Pub/Sub: {e}")
            return

        # The channel will be defined by the child class by setting self._pubsub_channel
        # For example: self._pubsub_channel = f"agent:{self._component_id}:input"
        # Or: self._pubsub_channel = f"bot:{self._component_id}:messages"
        if not hasattr(self, '_pubsub_channel') or not self._pubsub_channel:
            self.logger.error(f"[{self._component_id}] _pubsub_channel is not set. Pub/Sub listener cannot start.")
            await self.mark_as_error(reason="_pubsub_channel not set for listener")
            return

        channel = self._pubsub_channel
        pubsub = None
        self.logger.info(f"Pub/Sub listener starting for channel: {channel}")

        while self._running:  # self._running is from RunnableComponent
            try:
                if not await redis_cli.ping():
                    self.logger.warning(f"[{self._component_id}] Redis ping failed in listener. Re-establishing pubsub.")
                    if pubsub:
                        try:
                            await pubsub.aclose()
                        except Exception as e_close:
                            self.logger.error(f"[{self._component_id}] Error closing pubsub during reconnect: {e_close}")
                    pubsub = None
                    await asyncio.sleep(settings.REDIS_RECONNECT_INTERVAL) # settings needs to be available
                    continue

                if pubsub is None:
                    pubsub = redis_cli.pubsub()
                    await pubsub.subscribe(channel)
                    self.logger.info(f"Subscribed to Redis channel: {channel}")

                # Listen for messages
                message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
                
                if message and message['type'] == 'message':
                    self.logger.debug(f"Received message from {channel}: {message['data']}")
                    try:
                        # Child class must implement _handle_pubsub_message
                        await self._handle_pubsub_message(message['data'])
                    except Exception as e_handle:
                        self.logger.error(f"[{self._component_id}] Error handling pubsub message: {e_handle}", exc_info=True)
                elif message:
                    self.logger.debug(f"Received non-message from {channel}: {message}")

            except asyncio.CancelledError:
                self.logger.info(f"Pub/Sub listener task cancelled.")
                break
            except (ConnectionError, TimeoutError) as e_conn:
                self.logger.warning(f"[{self._component_id}] Pub/Sub connection error: {e_conn}. Attempting to reconnect...")
                if pubsub:
                    try:
                        await pubsub.aclose()
                    except Exception as e_close:
                        self.logger.error(f"[{self._component_id}] Error closing pubsub during connection error: {e_close}")
                pubsub = None
                await asyncio.sleep(settings.REDIS_RECONNECT_INTERVAL)
            except Exception as e:
                self.logger.error(f"[{self._component_id}] Unexpected error in Pub/Sub listener loop: {e}", exc_info=True)
                # In case of unexpected errors, also try to re-establish pubsub after a delay
                if pubsub:
                    try:
                        await pubsub.aclose()
                    except Exception as e_close:
                        self.logger.error(f"[{self._component_id}] Error closing pubsub after unexpected error: {e_close}")
                pubsub = None
                await asyncio.sleep(settings.REDIS_RECONNECT_INTERVAL * 2) # Longer delay for unexpected errors
        
        if pubsub:
            try:
                self.logger.info(f"Closing Pub/Sub connection for channel: {channel}")
                await pubsub.aclose()
            except Exception as e:
                self.logger.error(f"[{self._component_id}] Error closing pubsub on exit: {e}")
        self.logger.info(f"Pub/Sub listener loop for {channel} finished.")

    @abstractmethod
    async def _handle_pubsub_message(self, message_data: bytes) -> None:
        """
        Абстрактный метод для обработки входящих сообщений Pub/Sub.
        Дочерние классы должны реализовать этот метод.

        Args:
            message_data: Данные сообщения (сырые байты).
        """
        pass
