import os
from dotenv import load_dotenv
from minio import Minio

load_dotenv()

# Инициализация клиента MinIO
minio_client = Minio(
    os.getenv("MINIO_ENDPOINT", "127.0.0.1:9000"),
    access_key=os.getenv("MINIO_ACCESS_KEY", "MINIO_USER"),
    secret_key=os.getenv("MINIO_SECRET_KEY", "MINIO_PASS"),
    secure=False
)

bucket_name = os.getenv("MINIO_BUCKET", "test_collection")  # имя бакета для хранения файлов
file_path = "client123/test.pdf"  # пример: файл PDF для клиента "client123"
response = minio_client.get_object(bucket_name, file_path)
file_data = response.read()  # чтение файла в память

text_content = ""

# Если PDF
import fitz  # PyMuPDF
if file_path.lower().endswith(".pdf"):
    with fitz.open(stream=file_data, filetype="pdf") as doc:
        for page in doc:
            text_content += page.get_text()

# Если DOCX
import io
from docx import Document
if file_path.lower().endswith(".docx"):
    file_stream = io.BytesIO(file_data)
    doc = Document(file_stream)
    text_content = "\n".join([para.text for para in doc.paragraphs])

# Если XLSX
import pandas as pd
if file_path.lower().endswith(".xlsx"):
    file_stream = io.BytesIO(file_data)
    df = pd.read_excel(file_stream)
    text_content = df.to_csv(index=False)  # конвертируем всю таблицу в CSV-строку

# Если CSV
if file_path.lower().endswith(".csv"):
    text_content = file_data.decode("utf-8")

# Если TXT
if file_path.lower().endswith(".txt"):
    text_content = file_data.decode("utf-8")

response.close()

from openai import OpenAI

client = OpenAI()
client.api_key = os.getenv("OPENAI_API_KEY")
model = "text-embedding-ada-002"

# Разбиваем текст на куски, например, по 1000 символов (или по количеству токенов, учитывая 8191 токенов max для ada-002)
chunk_size = 1000
text_chunks = [text_content[i:i+chunk_size] for i in range(0, len(text_content), chunk_size)]

embeddings = []
for chunk in text_chunks:
    if chunk.strip():  # пропускаем пустые фрагменты
        response = client.embeddings.create(input=chunk, model=model)
        # vec = response['data'][0]['embedding']
        vec = response.data[0].embedding
        embeddings.append(vec)

from qdrant_client import QdrantClient, models
import uuid

qdrant = QdrantClient(
        url=os.getenv("QDRANT_URL", "http://qdrant:6333")
        )

collection_name = os.getenv("QDRANT_COLLECTION", "test_collection")  # имя коллекции в Qdrant

# Создаём коллекцию только если она не существует
# if not qdrant.collection_exists(collection_name):
if not qdrant.collection_exists(collection_name=collection_name):
    qdrant.create_collection(
        collection_name=collection_name,
        vectors_config=models.VectorParams(
            size=len(embeddings[0]),
            distance=models.Distance.COSINE
        )
    )

# Сохраняем эмбеддинги в коллекцию
points = []
for idx, vector in enumerate(embeddings):
    # point_id = f"{os.path.basename(file_path)}_{idx}"  # уникальный ID точки (например, имя файла + номер фрагмента)
    point_id = str(uuid.uuid4())
    payload = {
        "client_id": "client123",
        "source_file": file_path,
        "original_id": f"{os.path.basename(file_path)}_{idx}"
        }
    
    points.append(models.PointStruct(
        id=point_id,
        vector=vector,
        payload=payload
        ))

qdrant.upsert(collection_name=collection_name, points=points)
