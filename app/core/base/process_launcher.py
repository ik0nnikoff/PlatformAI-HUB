import asyncio
import subprocess
import logging
import shlex
from typing import List, Optional, Tuple, Dict, Any
import os

logger = logging.getLogger(__name__)

class ProcessLauncher:
    """
    Управляет запуском и мониторингом дочерних процессов.
    """

    def __init__(self, default_env: Optional[Dict[str, str]] = None):
        """
        Инициализирует ProcessLauncher.

        Args:
            default_env: Переменные окружения по умолчанию для всех запускаемых процессов.
                         Могут быть переопределены при вызове launch_process.
        """
        self.default_env = default_env if default_env is not None else os.environ.copy()

    async def launch_process(
        self, 
        command: List[str],
        process_id: str, # Для идентификации процесса, например, в логах или статусе
        cwd: Optional[str] = None,
        env_vars: Optional[Dict[str, str]] = None,
        capture_output: bool = True,
        log_output: bool = True,
        **kwargs: Any # Дополнительные аргументы для asyncio.create_subprocess_exec
    ) -> Tuple[Optional[asyncio.subprocess.Process], Optional[str], Optional[str]]:
        """
        Асинхронно запускает дочерний процесс.

        Args:
            command: Команда и ее аргументы в виде списка строк.
            process_id: Уникальный идентификатор для этого экземпляра процесса.
            cwd: Рабочий каталог для процесса. Если None, используется текущий рабочий каталог.
            env_vars: Словарь переменных окружения для процесса. Переопределяет default_env.
            capture_output: Если True, stdout и stderr процесса будут захвачены.
            log_output: Если True и capture_output True, выводит stdout/stderr в логгер.
            **kwargs: Дополнительные аргументы, передаваемые в asyncio.create_subprocess_exec.

        Returns:
            Кортеж (Process, stdout, stderr).
            Process: Объект asyncio.subprocess.Process, если процесс успешно запущен.
            stdout: Захваченный stdout в виде строки, если capture_output=True, иначе None.
            stderr: Захваченный stderr в виде строки, если capture_output=True, иначе None.
            В случае ошибки запуска возвращает (None, None, None) и логирует ошибку.
        """
        effective_env = self.default_env.copy()
        if env_vars:
            effective_env.update(env_vars)

        # Преобразование команды в строку для логирования, если это список
        command_str = shlex.join(command) if isinstance(command, list) else command

        logger.info(f"[{process_id}] Launching process: {command_str} in {cwd or '.'}")
        logger.debug(f"[{process_id}] Environment: {effective_env}")

        stdout_pipe = asyncio.subprocess.PIPE if capture_output else None
        stderr_pipe = asyncio.subprocess.PIPE if capture_output else None

        try:
            process = await asyncio.create_subprocess_exec(
                *command,
                stdout=stdout_pipe,
                stderr=stderr_pipe,
                cwd=cwd,
                env=effective_env,
                **kwargs
            )
            logger.info(f"[{process_id}] Process started with PID: {process.pid}")
            
            stdout_bytes: Optional[bytes] = None
            stderr_bytes: Optional[bytes] = None

            if capture_output:
                # Ожидаем завершения процесса для захвата вывода
                # В реальном приложении это может быть сделано более гибко,
                # например, чтение потоков по мере поступления данных.
                try:
                    # Устанавливаем таймаут, чтобы избежать бесконечного ожидания
                    # Таймаут должен быть достаточно большим для выполнения команды
                    # или None, если мы хотим ждать неограниченно.
                    # Для длительных процессов, возможно, лучше не ждать здесь, 
                    # а обрабатывать потоки отдельно.
                    stdout_bytes, stderr_bytes = await asyncio.wait_for(process.communicate(), timeout=None) 
                except asyncio.TimeoutError:
                    logger.warning(f"[{process_id}] Timeout while waiting for process output. Process may still be running.")
                    # Можно попытаться убить процесс, если это необходимо
                    # process.terminate()
                    # await process.wait()
                    return process, None, None # Возвращаем процесс, но без полного вывода
                except Exception as comm_exc:
                    logger.error(f"[{process_id}] Error during process.communicate(): {comm_exc}", exc_info=True)
                    return process, None, None # Процесс запущен, но вывод получить не удалось

                stdout_str = stdout_bytes.decode('utf-8', errors='replace') if stdout_bytes else None
                stderr_str = stderr_bytes.decode('utf-8', errors='replace') if stderr_bytes else None

                if log_output:
                    if stdout_str:
                        logger.info(f"[{process_id}] STDOUT:\n{stdout_str}")
                    if stderr_str:
                        logger.error(f"[{process_id}] STDERR:\n{stderr_str}")
                
                # Проверка кода возврата после communicate()
                if process.returncode != 0:
                    logger.warning(f"[{process_id}] Process exited with code {process.returncode}.")
                else:
                    logger.info(f"[{process_id}] Process exited successfully (code 0).")

                return process, stdout_str, stderr_str
            else:
                # Если вывод не захватывается, мы не вызываем process.communicate() здесь,
                # так как это заблокирует до завершения процесса.
                # Управление таким процессом (ожидание, чтение потоков) должно
                # осуществляться вызывающим кодом.
                logger.info(f"[{process_id}] Process launched without output capture. PID: {process.pid}")
                return process, None, None

        except FileNotFoundError:
            logger.error(f"[{process_id}] Command not found: {command[0]}. Ensure it's in PATH or provide full path.", exc_info=True)
            return None, None, None
        except Exception as e:
            logger.error(f"[{process_id}] Failed to launch process {command_str}: {e}", exc_info=True)
            return None, None, None

    async def run_command_and_wait(
        self, 
        command: List[str],
        process_id: str,
        cwd: Optional[str] = None,
        env_vars: Optional[Dict[str, str]] = None,
        log_output: bool = True,
        **kwargs: Any
    ) -> Tuple[int, Optional[str], Optional[str]]:
        """
        Запускает команду, ожидает ее завершения и возвращает код выхода, stdout и stderr.
        Это обертка над launch_process для случаев, когда нужно дождаться завершения.

        Args:
            command: Команда и ее аргументы.
            process_id: Идентификатор процесса.
            cwd: Рабочий каталог.
            env_vars: Переменные окружения.
            log_output: Логировать ли stdout/stderr.
            **kwargs: Дополнительные аргументы для asyncio.create_subprocess_exec.

        Returns:
            Кортеж (return_code, stdout, stderr).
            return_code: Код завершения процесса. -1 в случае ошибки запуска.
            stdout: Захваченный stdout.
            stderr: Захваченный stderr.
        """
        process, stdout, stderr = await self.launch_process(
            command=command,
            process_id=process_id,
            cwd=cwd,
            env_vars=env_vars,
            capture_output=True, # Всегда захватываем вывод для этой функции
            log_output=log_output,
            **kwargs
        )

        if process is None:
            return -1, None, None # Ошибка запуска
        
        # Если launch_process уже вызвал communicate(), то returncode должен быть установлен.
        # Если capture_output был False в launch_process (что не должно быть здесь),
        # то нам нужно было бы вызвать await process.wait() здесь.
        if process.returncode is None:
            # Этого не должно произойти, если capture_output=True в launch_process
            # и communicate() был успешно вызван.
            # Но на всякий случай, если процесс еще работает (например, из-за таймаута в communicate):
            logger.warning(f"[{process_id}] Process return code is None after launch_process. Waiting...")
            try:
                await asyncio.wait_for(process.wait(), timeout=30) # Дополнительный таймаут
            except asyncio.TimeoutError:
                logger.error(f"[{process_id}] Timeout waiting for process to complete after output capture.")
                if process.returncode is None: # Если все еще None
                    try:
                        process.terminate()
                        await asyncio.wait_for(process.wait(), timeout=5)
                    except Exception as term_exc:
                        logger.error(f"[{process_id}] Error terminating process: {term_exc}")
                        return -2, stdout, stderr # -2 означает ошибку завершения
            except Exception as wait_exc:
                logger.error(f"[{process_id}] Error waiting for process: {wait_exc}")
                return -3, stdout, stderr # -3 означает ошибку ожидания

        return process.returncode if process.returncode is not None else -4, stdout, stderr # -4 если код все еще None

# Пример использования:
async def example_main():
    launcher = ProcessLauncher()

    # Пример 1: Запуск простой команды и ожидание
    print("--- Example 1: Running 'ls -la' ---")
    return_code, stdout, stderr = await launcher.run_command_and_wait(
        ['ls', '-la'], 
        process_id="ls_test"
    )
    print(f"'ls -la' exited with code: {return_code}")
    if stdout:
        print(f"STDOUT:\n{stdout[:200]}...") # Печатаем часть вывода
    if stderr:
        print(f"STDERR:\n{stderr}")

    # Пример 2: Запуск Python скрипта
    print("\n--- Example 2: Running a Python script ---")
    # Создадим временный скрипт
    script_content = "import time\nprint('Hello from Python script')\ntime.sleep(1)\nprint('Python script finishing')"
    script_path = "./temp_script.py"
    with open(script_path, "w") as f:
        f.write(script_content)
    
    # Используем sys.executable для гарантии использования того же интерпретатора Python
    import sys
    python_executable = sys.executable

    return_code, stdout, stderr = await launcher.run_command_and_wait(
        [python_executable, script_path], 
        process_id="python_script_test"
    )
    print(f"Python script exited with code: {return_code}")
    if stdout:
        print(f"STDOUT:\n{stdout}")
    if stderr:
        print(f"STDERR:\n{stderr}")
    os.remove(script_path) # Удаляем временный скрипт

    # Пример 3: Запуск процесса без ожидания (только запуск)
    # Для этого примера мы не будем ждать завершения в example_main,
    # а просто запустим и получим объект процесса.
    print("\n--- Example 3: Launching 'sleep 3' without immediate wait ---")
    # Запускаем 'sleep 3' без захвата вывода, чтобы он не ждал communicate()
    process_obj, _, _ = await launcher.launch_process(
        ['sleep', '3'], 
        process_id="sleep_test",
        capture_output=False # Важно: не ждем вывода здесь
    )
    if process_obj:
        print(f"'sleep 3' launched with PID: {process_obj.pid}. It will run in background.")
        # В реальном приложении здесь можно сохранить process_obj для дальнейшего управления
        # Например, дождаться его позже: await process_obj.wait()
        # Или периодически проверять process_obj.returncode
        # Для этого примера мы просто дадим ему время завершиться
        await asyncio.sleep(0.1) # Небольшая пауза, чтобы 'sleep 3' точно запустился
        if process_obj.returncode is None:
            print(f"Process {process_obj.pid} is still running (as expected).")
        
        # Дождемся его завершения, чтобы пример был чистым
        print(f"Waiting for 'sleep 3' (PID: {process_obj.pid}) to complete...")
        await process_obj.wait()
        print(f"'sleep 3' (PID: {process_obj.pid}) completed with code: {process_obj.returncode}")
    else:
        print("'sleep 3' failed to launch.")

    # Пример 4: Команда с ошибкой
    print("\n--- Example 4: Running a non-existent command ---")
    return_code, stdout, stderr = await launcher.run_command_and_wait(
        ['non_existent_command_12345'], 
        process_id="error_test"
    )
    print(f"'non_existent_command' exited with code: {return_code}") # Должно быть -1
    if stderr:
        print(f"STDERR:\n{stderr}") # Может быть пустым, т.к. ошибка на уровне запуска

if __name__ == "__main__":
    # Настройка базового логирования для вывода сообщений от ProcessLauncher
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    # Чтобы видеть DEBUG сообщения от ProcessLauncher, измените INFO на DEBUG
    # logging.getLogger("__main__").setLevel(logging.DEBUG) # Если ProcessLauncher в том же файле
    # logging.getLogger("app.core.base.process_launcher").setLevel(logging.DEBUG) # Если импортируется
    
    # asyncio.run(example_main()) # Раскомментируйте для запуска примера
    pass
