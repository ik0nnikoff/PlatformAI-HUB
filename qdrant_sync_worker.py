import requests
import os
import time
import logging
import tempfile
from minio import Minio
from minio.error import S3Error
from pathlib import Path
from dotenv import load_dotenv
from uuid import uuid4
import traceback # Добавляем импорт для трассировки ошибок
import tiktoken # Добавляем импорт для подсчета токенов

from langchain_text_splitters import CharacterTextSplitter
from langchain_community.document_loaders import TextLoader
from langchain_openai import OpenAIEmbeddings
from langchain_qdrant import QdrantVectorStore
from qdrant_client import QdrantClient, models
from langchain_core.documents import Document
from typing import List, Dict

# --- Конфигурация ---
load_dotenv() # Загрузка переменных из .env файла

# API эндпоинты
API_BASE_URL = os.getenv("API_BASE_URL", "http://localhost:3001/api/v1")
SOURCES_API_URL = f"{API_BASE_URL}/admin-sources/"

# MinIO конфигурация
MINIO_ENDPOINT = os.getenv("MINIO_ENDPOINT", "localhost:9000")
MINIO_ACCESS_KEY = os.getenv("MINIO_ACCESS_KEY", "minioadmin")
MINIO_SECRET_KEY = os.getenv("MINIO_SECRET_KEY", "minioadmin")
MINIO_BUCKET = os.getenv("MINIO_BUCKET", "datasources")
MINIO_SECURE = os.getenv("MINIO_SECURE", "False").lower() == "true"

# Qdrant конфигурация
QDRANT_URL = os.getenv("QDRANT_URL", "http://qdrant:6333")
QDRANT_COLLECTION = os.getenv("QDRANT_COLLECTION", "test_collection")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

# Прочие настройки
POLL_INTERVAL = int(os.getenv("POLL_INTERVAL", 60)) # Интервал опроса в секундах
TEMP_DIR = Path("./temp_files_qdrant")

# Настройка логирования
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# Инициализация кодировщика токенов (используем стандартный для ada-002)
try:
    tokenizer = tiktoken.get_encoding("cl100k_base")
except Exception as e:
    logging.error(f"Не удалось инициализировать токенизатор tiktoken: {e}")
    tokenizer = None # Устанавливаем в None, если не удалось

# --- Инициализация ---
TEMP_DIR.mkdir(exist_ok=True)

# Инициализация клиента MinIO
try:
    minio_client = Minio(
        MINIO_ENDPOINT,
        access_key=MINIO_ACCESS_KEY,
        secret_key=MINIO_SECRET_KEY,
        secure=MINIO_SECURE
    )
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

# Инициализация клиента Qdrant и Embeddings
try:
    embeddings = OpenAIEmbeddings(api_key=OPENAI_API_KEY)
    qdrant_client = QdrantClient(url=QDRANT_URL)

    # Проверка и создание коллекции, если не существует
    try:
        qdrant_client.get_collection(collection_name=QDRANT_COLLECTION)
        logging.info(f"Подключение к существующей коллекции Qdrant '{QDRANT_COLLECTION}'.")
    except Exception:
        logging.info(f"Коллекция Qdrant '{QDRANT_COLLECTION}' не найдена. Создание новой коллекции.")
        qdrant_client.create_collection(
            collection_name=QDRANT_COLLECTION,
            vectors_config=models.VectorParams(
                size=1536,
                distance=models.Distance.COSINE
            )
        )
        logging.info(f"Коллекция Qdrant '{QDRANT_COLLECTION}' успешно создана.")

    vector_store = QdrantVectorStore(
        client=qdrant_client,
        collection_name=QDRANT_COLLECTION,
        embedding=embeddings,
    )
    logging.info(f"Успешная инициализация Qdrant Vector Store для коллекции '{QDRANT_COLLECTION}'.")

except Exception as e:
    logging.error(f"Ошибка инициализации Qdrant или OpenAI Embeddings: {e}")
    exit(1)


# --- Функции ---

def get_unsynced_files():
    """Получает список файлов со статусом 'unsync' и markdown=true."""
    try:
        params = {'status': 'unsync', 'markdown': 'true'}
        response = requests.get(SOURCES_API_URL, params=params, timeout=30)
        response.raise_for_status() # Проверка на HTTP ошибки
        return response.json()
    except requests.exceptions.RequestException as e:
        logging.error(f"Ошибка получения списка файлов для синхронизации: {e}")
        return []
    except Exception as e:
        logging.error(f"Непредвиденная ошибка при получении списка файлов для синхронизации: {e}")
        return []

def download_markdown_from_minio(user_id, source_id, original_filename):
    """Скачивает markdown файл из MinIO."""
    markdown_filename = f"{Path(original_filename).stem}.md"
    object_name = f"{user_id}/{source_id}/{markdown_filename}"
    # Используем NamedTemporaryFile для автоматического управления временным файлом
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=".md", dir=TEMP_DIR) as tmp_file:
            local_filepath = Path(tmp_file.name)
            minio_client.fget_object(MINIO_BUCKET, object_name, str(local_filepath))
            logging.info(f"Markdown файл '{object_name}' успешно скачан в '{local_filepath}'.")
            return local_filepath
    except S3Error as e:
        logging.error(f"Ошибка скачивания файла '{object_name}' из MinIO: {e}")
        # Удаляем временный файл, если он был создан, но скачивание не удалось
        if 'local_filepath' in locals() and local_filepath.exists():
            os.remove(local_filepath)
        return None
    except Exception as e:
        logging.error(f"Непредвиденная ошибка при скачивании файла '{object_name}': {e}")
        if 'local_filepath' in locals() and local_filepath.exists():
            os.remove(local_filepath)
        return None

def process_and_vectorize(local_filepath: Path, client_id: str, datasource_id: str, original_filename: str) -> tuple[bool, int, int]:
    """
    Загружает, обрабатывает markdown файл, добавляет векторы в Qdrant
    и возвращает статус успеха, количество чанков и количество токенов.
    """
    nb_chunks = 0
    nb_tokens = 0
    try:
        # 1. Загрузка документа
        loader = TextLoader(str(local_filepath), encoding='utf-8')
        document = loader.load()
        if not document:
            logging.warning(f"Не удалось загрузить содержимое из файла: {local_filepath}")
            # Считаем успешным, если файл пуст, чтобы обновить статус, но чанков и токенов 0
            return True, 0, 0

        # 2. Разбиение на чанки
        text_splitter = CharacterTextSplitter(
            separator="\n\n", # Разделитель для markdown
            chunk_size=1000,
            chunk_overlap=200,
            length_function=len,
            is_separator_regex=False,
        )
        texts = text_splitter.split_documents(document)
        nb_chunks = len(texts) # Подсчет количества чанков

        if not texts:
            logging.warning(f"Не удалось разбить на чанки документ: {local_filepath}")
            # Возможно, файл пустой или содержит только разделители
            return True, 0, 0 # Считаем успешным, если файл пуст

        # 3. Подготовка данных для Qdrant и подсчет токенов
        documents_to_add = []
        markdown_filename = f"{Path(original_filename).stem}.md"
        minio_file_path = f"{client_id}/{datasource_id}/{markdown_filename}"

        for text in texts:
            # Подсчет токенов для чанка, если токенизатор доступен
            if tokenizer:
                try:
                    nb_tokens += len(tokenizer.encode(text.page_content))
                except Exception as token_error:
                    logging.warning(f"Ошибка подсчета токенов для чанка в файле {local_filepath}: {token_error}")
                    # Продолжаем без подсчета токенов для этого чанка или всего файла,
                    # можно установить nb_tokens в -1 или другое значение для индикации ошибки

            # Копируем метаданные из загрузчика и добавляем свои
            metadata = text.metadata.copy()
            metadata.update({
                "client_id": str(client_id),
                "datasource_id": str(datasource_id),
                "file_path": minio_file_path, # Путь в MinIO
                "original_filename": original_filename,
            })
            # Создаем объект Document Langchain
            payload = {
                "page_content": text.page_content,
                "metadata": metadata,
            }
            documents_to_add.append(Document(**payload))

        # 4. Добавление в Qdrant
        if documents_to_add:
            uuids = [str(uuid4()) for _ in range(len(documents_to_add))]
            vector_store.add_documents(documents=documents_to_add, ids=uuids)
            logging.info(f"Успешно добавлено {len(documents_to_add)} векторов для файла {markdown_filename} (ID: {datasource_id}). Чанков: {nb_chunks}, Токенов: {nb_tokens if tokenizer else 'N/A'}")
        else:
             logging.info(f"Нет чанков для добавления в Qdrant для файла {markdown_filename} (ID: {datasource_id})")

        return True, nb_chunks, nb_tokens

    except Exception as e:
        logging.error(f"Ошибка при обработке и векторизации файла {local_filepath} (ID: {datasource_id}): {e}", exc_info=True)
        return False, 0, 0 # Возвращаем 0 чанков/токенов при ошибке

def update_source_status(source_id, status: str, error_message: str = None, nb_chunks: int = None, nb_tokens: int = None):
    """Обновляет статус источника данных, опционально сообщение об ошибке, количество чанков и токенов."""
    url = f"{SOURCES_API_URL}{source_id}"
    payload = {
        "status": status,
    }

    # Добавляем поля в зависимости от статуса
    if status == "sync":
        payload["lastSynch"] = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
        if nb_chunks is not None:
            payload["nbChunks"] = nb_chunks
        if nb_tokens is not None and tokenizer: # Добавляем токены только если они были посчитаны
            payload["nbTokens"] = nb_tokens
        # Убираем сообщение об ошибке при успешной синхронизации
        payload["errorMessage"] = None
    elif status == "error" and error_message:
        payload["errorMessage"] = error_message
        # Можно обнулить или не трогать nbChunks/nbTokens при ошибке
        # payload["nbChunks"] = 0
        # payload["nbTokens"] = 0
    elif status == "running":
        # Можно сбросить сообщение об ошибке при запуске
        payload["errorMessage"] = None


    try:
        logging.debug(f"Отправка PATCH запроса на {url} с payload: {payload}")
        response = requests.patch(url, json=payload, timeout=30)
        response.raise_for_status()
        logging.info(f"Статус источника {source_id} успешно обновлен на '{status}'.")
        return True
    except requests.exceptions.RequestException as e:
        logging.error(f"Ошибка обновления статуса источника {source_id} на '{status}': {e}")
        # Логируем тело ответа, если есть
        try:
            logging.error(f"Тело ответа API: {response.text}")
        except Exception:
            pass # Игнорируем ошибки при логировании тела ответа
        return False
    except Exception as e:
        logging.error(f"Непредвиденная ошибка при обновлении статуса источника {source_id} на '{status}': {e}")
        return False

def cleanup_temp_file(filepath):
    """Удаляет временный файл."""
    if filepath and filepath.exists():
        try:
            os.remove(filepath)
            logging.debug(f"Временный файл '{filepath}' удален.")
        except OSError as e:
            logging.warning(f"Не удалось удалить временный файл '{filepath}': {e}")

# --- Основной цикл ---
def main():
    logging.info("Qdrant Sync Worker запущен.")
    if not tokenizer:
        logging.warning("Токенизатор tiktoken не инициализирован. Подсчет токенов будет недоступен.")

    while True:
        logging.info("Проверка наличия файлов для синхронизации с Qdrant...")
        unsynced_files = get_unsynced_files()

        if not unsynced_files:
            logging.info(f"Нет файлов для синхронизации. Ожидание {POLL_INTERVAL} секунд.")
            time.sleep(POLL_INTERVAL)
            continue

        logging.info(f"Найдено {len(unsynced_files)} файлов для синхронизации.")

        for file_data in unsynced_files:
            source_id = file_data.get("id")
            user_id = file_data.get("userId")
            original_filename = file_data.get("name") # Имя оригинального файла (не markdown)
            local_filepath = None # Инициализация переменной
            error_occurred = False
            error_msg = ""
            nb_chunks = 0 # Инициализация
            nb_tokens = 0 # Инициализация

            if not all([source_id, user_id, original_filename]):
                logging.warning(f"Пропуск записи из-за отсутствия данных: {file_data}")
                continue

            logging.info(f"Обработка файла для Qdrant. Source ID: {source_id}, User ID: {user_id}, Original Filename: {original_filename}")

            try:
                # 0. Обновить статус на 'running'
                if not update_source_status(source_id, "running"):
                    logging.warning(f"Не удалось обновить статус на 'running' для {source_id}. Пропуск обработки.")
                    continue # Пропускаем этот файл, если не удалось обновить статус

                # 1. Скачать markdown файл из MinIO
                local_filepath = download_markdown_from_minio(user_id, source_id, original_filename)
                if not local_filepath:
                    error_occurred = True
                    error_msg = f"Ошибка скачивания файла из MinIO: {user_id}/{source_id}/{Path(original_filename).stem}.md"
                    logging.error(error_msg)
                    # Статус 'error' будет установлен в блоке finally
                    continue # Переходим к finally для установки статуса error и очистки

                # 2. Обработать и векторизовать
                vectorize_success, nb_chunks, nb_tokens = process_and_vectorize(
                    local_filepath, str(user_id), str(source_id), original_filename
                )

                if not vectorize_success:
                    error_occurred = True
                    # Сообщение об ошибке уже залогировано внутри process_and_vectorize
                    error_msg = f"Ошибка векторизации файла {original_filename} (ID: {source_id})" # Краткое сообщение для API
                    # Статус 'error' будет установлен в блоке finally
                    continue # Переходим к finally

                # 3. Обновить статус источника на 'sync' (если не было ошибок)
                # Передаем посчитанные значения
                if not update_source_status(source_id, "sync", nb_chunks=nb_chunks, nb_tokens=nb_tokens):
                    # Ошибка обновления статуса после УСПЕШНОЙ векторизации - это проблема, но векторизация прошла
                    logging.error(f"Не удалось обновить статус на 'synced' для источника {source_id} после успешной векторизации.")
                    # Не устанавливаем error_occurred = True, т.к. основная работа сделана
                else:
                     logging.info(f"Источник {source_id} успешно синхронизирован (Чанков: {nb_chunks}, Токенов: {nb_tokens if tokenizer else 'N/A'}).")


            except Exception as e:
                 error_occurred = True
                 error_msg = f"Непредвиденная ошибка в цикле обработки файла {source_id}: {traceback.format_exc()}"
                 logging.error(error_msg)
                 # Статус 'error' будет установлен в блоке finally

            finally:
                # 4. Установить статус 'error' если были ошибки на любом этапе
                if error_occurred:
                    # Передаем сообщение об ошибке, но не чанки/токены
                    if not update_source_status(source_id, "error", error_message=error_msg[:1000]): # Ограничиваем длину сообщения
                        logging.error(f"Критическая ошибка: не удалось обновить статус на 'error' для источника {source_id} после сбоя.")

                # 5. Очистить временный скачанный файл
                if local_filepath:
                    cleanup_temp_file(local_filepath)

            # Небольшая пауза между обработкой файлов, чтобы не перегружать системы
            time.sleep(1)

        logging.info("Цикл синхронизации завершен.")
        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        logging.info("Qdrant Sync Worker остановлен вручную.")
    except Exception as e:
        logging.critical(f"Критическая ошибка в главном цикле Qdrant Sync Worker: {e}", exc_info=True)
        exit(1)

