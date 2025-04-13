import requests
import os
import time
import logging
from minio import Minio
from minio.error import S3Error
from pathlib import Path
from dotenv import load_dotenv

# --- Конфигурация ---
load_dotenv() # Загрузка переменных из .env файла

# API эндпоинты
API_BASE_URL = os.getenv("API_BASE_URL", "http://localhost:3001/api/v1")
MARKER_API_URL = os.getenv("MARKER_API_URL", "http://localhost:8000/marker/upload")
SOURCES_API_URL = f"{API_BASE_URL}/admin-sources/"

# MinIO конфигурация
MINIO_ENDPOINT = os.getenv("MINIO_ENDPOINT", "localhost:9000")
MINIO_ACCESS_KEY = os.getenv("MINIO_ACCESS_KEY", "minioadmin")
MINIO_SECRET_KEY = os.getenv("MINIO_SECRET_KEY", "minioadmin")
MINIO_BUCKET = os.getenv("MINIO_BUCKET", "datasources")
MINIO_SECURE = os.getenv("MINIO_SECURE", "False").lower() == "true"

# Прочие настройки
POLL_INTERVAL = int(os.getenv("POLL_INTERVAL", 60)) # Интервал опроса в секундах
TEMP_DIR = Path("./temp_files")

# Настройка логирования
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# --- Инициализация ---
TEMP_DIR.mkdir(exist_ok=True)

try:
    minio_client = Minio(
        MINIO_ENDPOINT,
        access_key=MINIO_ACCESS_KEY,
        secret_key=MINIO_SECRET_KEY,
        secure=MINIO_SECURE
    )
    # Проверка существования бакета
    found = minio_client.bucket_exists(MINIO_BUCKET)
    if not found:
        logging.error(f"MinIO bucket '{MINIO_BUCKET}' не найден.")
        exit(1)
    else:
        logging.info(f"Успешное подключение к MinIO бакету '{MINIO_BUCKET}'.")
except S3Error as e:
    logging.error(f"Ошибка подключения к MinIO: {e}")
    exit(1)
except Exception as e:
    logging.error(f"Непредвиденная ошибка при инициализации MinIO: {e}")
    exit(1)


# --- Функции ---

def get_pending_files():
    """Получает список файлов со статусом 'pending'."""
    try:
        params = {'type': 'file', 'status': 'pending'}
        response = requests.get(SOURCES_API_URL, params=params, timeout=30)
        response.raise_for_status() # Проверка на HTTP ошибки
        return response.json()
    except requests.exceptions.RequestException as e:
        logging.error(f"Ошибка получения списка файлов: {e}")
        return []
    except Exception as e:
        logging.error(f"Непредвиденная ошибка при получении списка файлов: {e}")
        return []

def download_file_from_minio(user_id, source_id, filename):
    """Скачивает файл из MinIO."""
    object_name = f"{user_id}/{source_id}/{filename}"
    local_filepath = TEMP_DIR / f"{source_id}_{filename}"
    try:
        minio_client.fget_object(MINIO_BUCKET, object_name, str(local_filepath))
        logging.info(f"Файл '{object_name}' успешно скачан в '{local_filepath}'.")
        return local_filepath
    except S3Error as e:
        logging.error(f"Ошибка скачивания файла '{object_name}' из MinIO: {e}")
        return None
    except Exception as e:
        logging.error(f"Непредвиденная ошибка при скачивании файла '{object_name}': {e}")
        return None

def process_with_marker(filepath):
    """Отправляет файл в Marker API и возвращает результат."""
    if not filepath or not filepath.exists():
        logging.error(f"Файл для отправки в Marker не найден: {filepath}")
        return None
    try:
        with open(filepath, 'rb') as f:
            files = {'file': (filepath.name, f)}
            response = requests.post(MARKER_API_URL, files=files, timeout=300) # Таймаут 5 минут
            response.raise_for_status()
        result = response.json()
        logging.info(f"Файл '{filepath.name}' успешно обработан Marker.")
        return result
    except requests.exceptions.RequestException as e:
        logging.error(f"Ошибка отправки файла '{filepath.name}' в Marker: {e}")
        return None
    except Exception as e:
        logging.error(f"Непредвиденная ошибка при обработке файла '{filepath.name}' в Marker: {e}")
        return None

def upload_markdown_to_minio(user_id, source_id, markdown_content, original_filename):
    """Загружает markdown контент в MinIO."""
    original_path = Path(original_filename)
    markdown_filename = f"{original_path.stem}.md"
    object_name = f"{user_id}/{source_id}/{markdown_filename}"
    local_md_path = TEMP_DIR / f"{source_id}_{markdown_filename}"

    try:
        # Сохраняем markdown локально временно
        with open(local_md_path, 'w', encoding='utf-8') as f:
            f.write(markdown_content)

        # Загружаем в MinIO
        minio_client.fput_object(
            MINIO_BUCKET, object_name, str(local_md_path), content_type='text/markdown'
        )
        logging.info(f"Markdown файл '{object_name}' успешно загружен в MinIO.")
        return True
    except S3Error as e:
        logging.error(f"Ошибка загрузки markdown файла '{object_name}' в MinIO: {e}")
        return False
    except IOError as e:
        logging.error(f"Ошибка записи временного markdown файла '{local_md_path}': {e}")
        return False
    except Exception as e:
        logging.error(f"Непредвиденная ошибка при загрузке markdown файла '{object_name}': {e}")
        return False
    finally:
        # Удаляем временный локальный markdown файл
        if local_md_path.exists():
            try:
                os.remove(local_md_path)
            except OSError as e:
                logging.warning(f"Не удалось удалить временный файл {local_md_path}: {e}")


def update_source_status(source_id):
    """Обновляет статус источника данных."""
    url = f"{SOURCES_API_URL}{source_id}"
    payload = {
        "markdown": True,
        "status": "unsync"
    }
    try:
        response = requests.patch(url, json=payload, timeout=30)
        response.raise_for_status()
        logging.info(f"Статус источника {source_id} успешно обновлен на 'unsync'.")
        return True
    except requests.exceptions.RequestException as e:
        logging.error(f"Ошибка обновления статуса источника {source_id}: {e}")
        return False
    except Exception as e:
        logging.error(f"Непредвиденная ошибка при обновлении статуса источника {source_id}: {e}")
        return False

def cleanup_temp_file(filepath):
    """Удаляет временный файл."""
    if filepath and filepath.exists():
        try:
            os.remove(filepath)
            logging.info(f"Временный файл '{filepath}' удален.")
        except OSError as e:
            logging.warning(f"Не удалось удалить временный файл '{filepath}': {e}")

# --- Основной цикл ---
def main():
    logging.info("Воркер запущен.")
    while True:
        logging.info("Проверка наличия новых файлов...")
        pending_files = get_pending_files()

        if not pending_files:
            logging.info(f"Нет файлов для обработки. Ожидание {POLL_INTERVAL} секунд.")
            time.sleep(POLL_INTERVAL)
            continue

        logging.info(f"Найдено {len(pending_files)} файлов для обработки.")

        for file_data in pending_files:
            source_id = file_data.get("id")
            user_id = file_data.get("userId")
            original_filename = file_data.get("name")

            if not all([source_id, user_id, original_filename]):
                logging.warning(f"Пропуск записи из-за отсутствия данных: {file_data}")
                continue

            logging.info(f"Обработка файла ID: {source_id}, Имя: {original_filename}")

            # 1. Скачать файл из MinIO
            local_filepath = download_file_from_minio(user_id, source_id, original_filename)
            if not local_filepath:
                # Ошибка уже залогирована в функции
                continue # Переходим к следующему файлу

            # 2. Отправить в Marker
            marker_result = process_with_marker(local_filepath)

            if not marker_result:
                # Ошибка уже залогирована, удаляем временный файл
                cleanup_temp_file(local_filepath)
                continue # Переходим к следующему файлу

            # 3. Проверить результат Marker и загрузить Markdown
            if marker_result.get("success"):
                markdown_output = marker_result.get("output")
                if markdown_output:
                    # 4. Загрузить Markdown в MinIO
                    upload_success = upload_markdown_to_minio(user_id, source_id, markdown_output, original_filename)

                    if upload_success:
                        # 5. Обновить статус источника
                        update_success = update_source_status(source_id)
                        if not update_success:
                             logging.error(f"Не удалось обновить статус для источника {source_id} после успешной обработки.")
                        # Статус обновлен (или не удалось), но обработка завершена успешно
                    else:
                        logging.error(f"Не удалось загрузить markdown для источника {source_id}.")
                        # Не обновляем статус, т.к. markdown не сохранен
                else:
                    logging.warning(f"Marker вернул success:true, но поле 'output' пустое для файла {original_filename} (ID: {source_id}).")
            else:
                logging.error(f"Marker вернул ошибку для файла {original_filename} (ID: {source_id}): {marker_result}")

            # 6. Очистить временный скачанный файл
            cleanup_temp_file(local_filepath)

            # Небольшая пауза между обработкой файлов, чтобы не перегружать системы
            time.sleep(1)

        logging.info("Цикл обработки завершен.")
        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        logging.info("Воркер остановлен вручную.")
    except Exception as e:
        logging.critical(f"Критическая ошибка в главном цикле воркера: {e}", exc_info=True)
        exit(1)

