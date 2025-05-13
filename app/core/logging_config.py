import os
import logging
import sys

# Уровень логирования по умолчанию, можно переопределить из .env
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()

def setup_logging():
    """Настраивает базовую конфигурацию логирования."""
    logging.basicConfig(
        level=LOG_LEVEL,
        # format="%(asctime)s - %(levelname)s - %(name)s - %(module)s - %(funcName)s - %(lineno)d - %(message)s",
        format="%(asctime)s - %(levelname)s - %(name)s - %(message)s",
        handlers=[
            logging.StreamHandler(sys.stdout)  # Вывод в stdout
            # Можно добавить другие обработчики, например, для записи в файл:
            # logging.FileHandler("app.log"),
        ]
    )

    # Пример настройки логгера для конкретной библиотеки, если нужно другой уровень
    # logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)

    logger = logging.getLogger(__name__)
    logger.info(f"Logging configured with level: {LOG_LEVEL}")

# Вызов функции настройки при импорте модуля, чтобы логирование было доступно сразу
# Однако, лучше вызывать это явно в lifespan или main.py
# setup_logging()
