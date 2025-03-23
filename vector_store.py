from langchain_qdrant import QdrantVectorStore
from config import client, embeddings, search_limit
import os

# Создание хранилища векторов
vectorstore = QdrantVectorStore(
    client,
    collection_name=os.getenv("QDRANT_COLLECTION", "test_collection"),
    embedding=embeddings
)

# Ретривер
retriever = vectorstore.as_retriever(search_kwargs={"k": search_limit})