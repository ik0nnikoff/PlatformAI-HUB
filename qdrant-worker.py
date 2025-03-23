import logging
import os
import tempfile
from uuid import uuid4
from dotenv import load_dotenv
from minio import Minio, S3Error
from langchain_text_splitters import CharacterTextSplitter
from langchain_community.document_loaders import PyPDFLoader, TextLoader
from langchain_openai import OpenAIEmbeddings
from langchain_qdrant import Qdrant, QdrantVectorStore
from qdrant_client import QdrantClient, models
from langchain_core.documents import Document
from typing import List, Dict

load_dotenv()
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)

FILE_PATH = "test.pdf"
# FILE_PATH = "PPPoE_Debian_mt.pdf"
CLIENT_ID = "client123"
DATASOURCE_ID = "source_456"
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

def loader(file_path: str):
    if file_path.lower().endswith(".pdf"):
        loader = PyPDFLoader(file_path)
        docs = loader.load()
    else:
        loader = TextLoader(file_path, encoding='utf-8')
        docs = loader.load()
    return docs

def download_file(client_id: str, file_path: str):
    minio_client = Minio(
    os.getenv("MINIO_ENDPOINT", "127.0.0.1:9000"),
    access_key=os.getenv("MINIO_ACCESS_KEY", "MINIO_USER"),
    secret_key=os.getenv("MINIO_SECRET_KEY", "MINIO_PASS"),
    secure=False
    )
    bucket_name = os.getenv("MINIO_BUCKET", "test_collection")
    object_path = f"{client_id}/{file_path}"

    try:
        response = minio_client.get_object(bucket_name, object_path)
        with tempfile.NamedTemporaryFile(delete=False, suffix=os.path.splitext(file_path)[1]) as tmp_file:
            for data in response.stream(32*1024):
                tmp_file.write(data)
            tmp_file_path = tmp_file.name
        return loader(tmp_file_path)

    except S3Error as err:
        print(f"MinIO Error: {err}")
        raise
    except Exception as e:
        print(f"General Error: {e}")
        raise
    finally:
        response.close()
        os.unlink(tmp_file_path)

def process_documents(file_path: str, client_id: str, datasource_id: str) -> List[Dict]:
    document = download_file(client_id, file_path)
    
    text_splitter = CharacterTextSplitter(
        chunk_size=1000,
        chunk_overlap=200
    )
    texts = text_splitter.split_documents(document)
    
    documents = []
    for text in texts:
        metadata = text.metadata.copy()
        metadata.update({
            "client_id": client_id,
            "datasource_id": datasource_id,
            "file_path": file_path,
        })
        payload = {
            "page_content": text.page_content,
            "metadata": metadata,
            "client_id": client_id,
            "datasource_id": datasource_id,
        }
        documents.append(Document(**payload))
    return documents

processed_texts = process_documents(FILE_PATH, CLIENT_ID, DATASOURCE_ID)

embeddings = OpenAIEmbeddings(api_key=OPENAI_API_KEY)

qdrant = QdrantClient(
    url=os.getenv("QDRANT_URL", "http://qdrant:6333")  # Для облачной версии укажите облачный URL
)

collection_name = os.getenv("QDRANT_COLLECTION", "test_collection")

if not qdrant.collection_exists(collection_name=collection_name):
    qdrant.create_collection(
        collection_name=collection_name,
        vectors_config=models.VectorParams(
            size=1536,
            distance=models.Distance.COSINE
        )
    )

vector_store = QdrantVectorStore(
    client=qdrant,
    collection_name=collection_name,
    embedding=embeddings,
)

uuids = [str(uuid4()) for _ in range(len(processed_texts))]
vector_store.add_documents(documents=processed_texts, ids=uuids)