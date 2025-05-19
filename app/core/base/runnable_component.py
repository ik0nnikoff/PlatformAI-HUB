import asyncio
import signal
import logging
from abc import ABC, abstractmethod
from typing import Optional, Set

class RunnableComponent(ABC):
    """
    Абстрактный базовый класс для компонентов, которыми можно управлять и запускать.

    Этот класс предоставляет стандартизированный жизненный цикл для компонентов,
    включая этапы настройки (`setup`), основного цикла выполнения (`run_loop`)
    и очистки (`cleanup`).

    Завершение работы компонента должно инициироваться вызовом метода
    `initiate_shutdown()`. Этот метод обеспечивает корректное завершение
    основного цикла и освобождение ресурсов.

    Атрибуты:
        logger (logging.Logger): Логгер для компонента.
        _running (bool): Флаг, указывающий, активен ли основной цикл компонента.
        _main_task (Optional[asyncio.Task]): Задача asyncio для основного цикла.
        _shutdown_initiated (bool): Флаг, указывающий, был ли инициирован процесс завершения работы.

    Методы:
        setup(): Абстрактный метод для подготовки ресурсов компонента.
                 Должен быть реализован в дочерних классах.
        run_loop(): Абстрактный метод, представляющий основной цикл работы компонента.
                    Должен быть реализован в дочерних классах.
        cleanup(): Абстрактный метод для освобождения ресурсов компонента.
                   Должен быть реализован в дочерних классах.
        initiate_shutdown(): Инициирует корректное завершение работы компонента.
                             Устанавливает флаг `_running` в False и отменяет основную задачу.
                             Безопасен для многократного вызова.
        run(): Запускает полный жизненный цикл компонента: настройка, выполнение основного цикла и очистка.
               Управляет состоянием выполнения и обрабатывает исключения.
    """
    def __init__(self, logger: Optional[logging.Logger] = None):
        self.logger = logger if logger else logging.getLogger(self.__class__.__name__)
        self._running = False
        self._main_task: Optional[asyncio.Task] = None
        self._shutdown_initiated = False # New flag to track if shutdown has been called

    @abstractmethod
    async def setup(self) -> None:
        """Подготавливает ресурсы, необходимые для работы компонента."""
        pass # pragma: no cover

    @abstractmethod
    async def run_loop(self) -> None:
        """Основной цикл операций компонента."""
        pass # pragma: no cover

    @abstractmethod
    async def cleanup(self) -> None:
        """Освобождает ресурсы, используемые компонентом."""
        pass # pragma: no cover

    def initiate_shutdown(self) -> None:
        """
        Инициирует корректное завершение работы компонента.

        Устанавливает флаг `_running` в `False` и отменяет основную задачу (`_main_task`).
        Этот метод разработан таким образом, чтобы его можно было безопасно вызывать несколько раз.
        """
        if self._shutdown_initiated:
            self.logger.info(f"Shutdown already initiated for {self.__class__.__name__} (instance: {id(self)}).")
            # Still ensure _running is False and task is cancelled if it's somehow still running
            self._running = False
            if self._main_task and not self._main_task.done():
                self._main_task.cancel()
            return

        self.logger.info(f"Component {self.__class__.__name__} (instance: {id(self)}) received shutdown request. Initiating graceful shutdown...")
        self._shutdown_initiated = True
        self._running = False # Primary signal for run_loop to stop gracefully if it polls this

        if self._main_task and not self._main_task.done():
            self.logger.info(f"Cancelling main task (_run_loop) for {self.__class__.__name__} (instance: {id(self)})...")
            self._main_task.cancel()
        elif self._main_task and self._main_task.done():
            self.logger.info(f"Main task for {self.__class__.__name__} (instance: {id(self)}) already done.")
        else: # No main task was set or it's None
            self.logger.info(f"No active main task to cancel for {self.__class__.__name__} (instance: {id(self)}). Component might not have fully started or already stopped.")

    async def run(self) -> None:
        """
        Запускает полный жизненный цикл компонента.

        Выполняет последовательно `setup()`, `run_loop()` и `cleanup()`.
        Управляет состоянием выполнения, обрабатывает исключения и управляет флагами `_running` и `_shutdown_initiated`.
        Если `run()` вызывается для уже запущенного компонента, вызов будет проигнорирован.
        При возникновении необработанной ошибки во время выполнения, инициирует завершение работы.
        """
        if self._running: # Basic check if run is called again on an already running instance
            self.logger.warning(f"Component {self.__class__.__name__} (instance: {id(self)}) run() called while already considered running. Ignoring.")
            return

        # Reset flags for a fresh run, in case component is reused (though typically not)
        self._running = True
        self._shutdown_initiated = False
        self._main_task = None

        self.logger.debug(f"Component {self.__class__.__name__} (instance: {id(self)}) starting. Performing setup...")
        try:
            await self.setup()
            # If setup initiated shutdown (e.g. by calling self.initiate_shutdown())
            if not self._running or self._shutdown_initiated:
                 self.logger.info(f"Component {self.__class__.__name__} (instance: {id(self)}) shutdown initiated during or immediately after setup. Skipping run_loop.")
            else:
                self.logger.info(f"Component {self.__class__.__name__} (instance: {id(self)}) setup complete. Starting run loop.")
                self._main_task = asyncio.create_task(self.run_loop(), name=f"{self.__class__.__name__}MainLoop-{id(self)}")
                await self._main_task # This is where cancellation will be caught if run_loop is cancelled
        except asyncio.CancelledError:
            # This means self._main_task (the run_loop task) was cancelled.
            self.logger.info(f"Run_loop for {self.__class__.__name__} (instance: {id(self)}) was cancelled.")
        except Exception as e:
            self.logger.error(f"Unhandled error in component {self.__class__.__name__} (instance: {id(self)}) run phase: {e}", exc_info=True)
            # Ensure shutdown is initiated on unhandled error to proceed to cleanup
            if not self._shutdown_initiated:
                self.initiate_shutdown()
        finally:
            self.logger.info(f"Component {self.__class__.__name__} (instance: {id(self)}) stopping. Performing cleanup...")
            # Ensure _running is false before cleanup, initiate_shutdown should have done this.
            self._running = False
            await self.cleanup()
            self._shutdown_initiated = True # Mark as fully processed for shutdown
            self.logger.info(f"Component {self.__class__.__name__} (instance: {id(self)}) cleanup complete.")

# Example Usage (for illustration, would be in a specific component's module)
if __name__ == '__main__': # pragma: no cover
    class MyComponent(RunnableComponent):
        async def setup(self) -> None:
            self.logger.info("MyComponent setup complete.")
            # Simulate resource allocation
            await asyncio.sleep(0.1)

        async def run_loop(self) -> None:
            self.logger.info("MyComponent run_loop started.")
            try:
                while self._running:
                    self.logger.info("MyComponent is running...")
                    await asyncio.sleep(1)
                self.logger.info("MyComponent run_loop exiting due to _running=False.")
            except asyncio.CancelledError:
                self.logger.info("MyComponent run_loop was cancelled.")
                # Perform any immediate cancellation cleanup if necessary
                raise # Re-raise to be handled by the main run method

        async def cleanup(self) -> None:
            self.logger.info("MyComponent cleanup started.")
            # Simulate resource deallocation
            await asyncio.sleep(0.5)
            self.logger.info("MyComponent cleanup complete.")

    async def main():
        logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
        component = MyComponent()
        await component.run()

    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logging.getLogger(__name__).info("Application interrupted by KeyboardInterrupt.")
