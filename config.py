import os
import logging
from dotenv import load_dotenv
from qdrant_client import QdrantClient
from langchain_openai import OpenAIEmbeddings, ChatOpenAI

# Загрузка переменных окружения
load_dotenv()

# Настройка логирования
logging.basicConfig(
    # level=logging.INFO,
    level=None,
    format="%(asctime)s - %(levelname)s - %(message)s",
)

# Инициализация моделей
embeddings = OpenAIEmbeddings()
llm = ChatOpenAI(model="gpt-4o-mini", temperature=0.2)

# Подключение к Qdrant
client = QdrantClient(os.getenv("QDRANT_URL", "http://qdrant:6333"))
search_limit = 5

# Константы
client_id = "client123"
datasource_id = "source_456"