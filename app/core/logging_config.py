import os
import logging
import sys
from pathlib import Path

# Уровень логирования по умолчанию, можно переопределить из .env
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()

def setup_logging():
    """Настраивает базовую конфигурацию логирования."""
    # Создаем папку для логов если её нет
    log_dir = Path("logs")
    log_dir.mkdir(exist_ok=True)
    
    # Создаем обработчики для логирования
    handlers = [
        logging.StreamHandler(sys.stdout),  # Вывод в stdout
        logging.FileHandler(log_dir / "app.log", encoding='utf-8'),  # Запись в файл
    ]
    
    logging.basicConfig(
        level=LOG_LEVEL,
        # format="%(asctime)s - %(levelname)s - %(name)s - %(module)s - %(funcName)s - %(lineno)d - %(message)s",
        format="%(asctime)s - %(levelname)s - %(name)s - %(message)s",
        handlers=handlers,
        force=True  # Пересоздать конфигурацию если она уже существует
    )

    # Пример настройки логгера для конкретной библиотеки, если нужно другой уровень
    # logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)

    logger = logging.getLogger(__name__)
    logger.info(f"Logging configured with level: {LOG_LEVEL}")

# Вызов функции настройки при импорте модуля, чтобы логирование было доступно сразу
# Однако, лучше вызывать это явно в lifespan или main.py
# setup_logging()
