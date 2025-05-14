import os
from typing import Optional # Added Optional
from dotenv import load_dotenv
from pydantic import SecretStr # Import SecretStr

# Load from the project root .env
dotenv_path = os.path.join(os.path.dirname(__file__), '..', '..', '.env')
if os.path.exists(dotenv_path):
    load_dotenv(dotenv_path=dotenv_path)
    # print(f"App Config: Loaded environment variables from {dotenv_path}")
else:
    print(f"App Config: Warning! .env file not found at {dotenv_path}")

class Settings:
    PROJECT_NAME: str = os.getenv("PROJECT_NAME", "PlatformAI Hub")
    PROJECT_VERSION: str = os.getenv("PROJECT_VERSION", "0.1.0")
    API_V1_STR: str = os.getenv("API_V1_STR", "/api/v1")

    # Redis Configuration
    REDIS_URL: str = os.getenv("REDIS_URL", "redis://localhost:6379")
    REDIS_USER_CACHE_TTL: int = int(os.getenv("REDIS_USER_CACHE_TTL", 3600))
    REDIS_HISTORY_QUEUE_NAME: str = os.getenv("REDIS_HISTORY_QUEUE_NAME", "history_queue") # Added
    REDIS_TOKEN_USAGE_QUEUE_NAME: str = os.getenv("REDIS_TOKEN_USAGE_QUEUE_NAME", "token_usage_queue") # Added

    # PostgreSQL Configuration
    DATABASE_URL: str = os.getenv("DATABASE_URL", "postgresql+asyncpg://user:password@localhost:5432/agentdb")

    # Qdrant Configuration
    QDRANT_URL: str = os.getenv("QDRANT_URL", "http://localhost:6333")
    _qdrant_api_key = os.getenv("QDRANT_API_KEY")
    QDRANT_API_KEY: SecretStr | None = SecretStr(_qdrant_api_key) if _qdrant_api_key else None
    QDRANT_COLLECTION: str = os.getenv("QDRANT_COLLECTION", "default_collection")

    # LLM Provider API Keys
    _openai_api_key = os.getenv("OPENAI_API_KEY")
    OPENAI_API_KEY: SecretStr | None = SecretStr(_openai_api_key) if _openai_api_key else None
    
    _openrouter_api_key = os.getenv("OPENROUTER_API_KEY")
    OPENROUTER_API_KEY: SecretStr | None = SecretStr(_openrouter_api_key) if _openrouter_api_key else None
    
    _tavily_api_key = os.getenv("TAVILY_API_KEY")
    TAVILY_API_KEY: SecretStr | None = SecretStr(_tavily_api_key) if _tavily_api_key else None

    # Agent Runner Configuration
    AGENT_RUNNER_SCRIPT_NAME: str = "runner_main.py" # Имя файла скрипта
    AGENT_RUNNER_MODULE_PATH: str = "app.agent_runner.runner_main" # Путь для запуска через python -m
    AGENT_RUNNER_HEARTBEAT_INTERVAL: float = float(os.getenv("AGENT_RUNNER_HEARTBEAT_INTERVAL", "10.0")) # seconds, for AgentRunner status updates
    
    # Полный путь к скрипту, если он нужен (например, для прямого запуска не как модуля)
    # Собирается относительно текущего файла config.py
    _agent_runner_script_path = os.path.abspath(
        os.path.join(os.path.dirname(__file__), '..', 'agent_runner', AGENT_RUNNER_SCRIPT_NAME)
    )
    AGENT_RUNNER_SCRIPT_FULL_PATH: str = _agent_runner_script_path

    PYTHON_EXECUTABLE: str = os.getenv("PYTHON_EXECUTABLE", "python") 

    # Docker settings for agent runner
    RUN_AGENTS_WITH_DOCKER: bool = os.getenv("RUN_AGENTS_WITH_DOCKER", "false").lower() == "true"
    AGENT_DOCKER_IMAGE: str = os.getenv("AGENT_DOCKER_IMAGE", "agent_runner_image")

    # Service Configuration
    MANAGER_HOST: str = os.getenv("MANAGER_HOST", "localhost")
    MANAGER_PORT: int = int(os.getenv("MANAGER_PORT", "8001"))

    # Logging Configuration
    LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO").upper()
    LOG_FORMAT: str = os.getenv("LOG_FORMAT", "%(asctime)s - %(levelname)s - %(name)s - %(message)s")
    
    # Process Manager Service Configuration
    PROCESS_CHECK_INTERVAL: int = int(os.getenv("PROCESS_CHECK_INTERVAL", "10")) # seconds
    MAX_RESTART_ATTEMPTS: int = int(os.getenv("MAX_RESTART_ATTEMPTS", "3"))
    RESTART_DELAY_SECONDS: int = int(os.getenv("RESTART_DELAY_SECONDS", "5"))

    # Inactivity Monitor Worker Configuration (новые настройки)
    AGENT_INACTIVITY_TIMEOUT: int = int(os.getenv("AGENT_INACTIVITY_TIMEOUT", "1800")) # seconds (30 minutes)
    AGENT_INACTIVITY_CHECK_INTERVAL: int = int(os.getenv("AGENT_INACTIVITY_CHECK_INTERVAL", "60")) # seconds (1 minute)

    # Telegram Integration Specific (может быть вынесено в настройки интеграции, но для примера здесь)
    TELEGRAM_BOT_REDIS_PUBSUB_URL: Optional[str] = os.getenv("TELEGRAM_BOT_REDIS_PUBSUB_URL", None) # Specific Redis for Telegram bot Pub/Sub, if different

settings = Settings()

# Check if runner script exists (if not running with Docker)
# and print warnings if paths or settings seem problematic.
if not settings.RUN_AGENTS_WITH_DOCKER:
    if not os.path.exists(settings.AGENT_RUNNER_SCRIPT_FULL_PATH):
        print(f"App Config: CRITICAL WARNING! Agent runner script not found at: {settings.AGENT_RUNNER_SCRIPT_FULL_PATH}")
        print(f"Ensure AGENT_RUNNER_SCRIPT_NAME ('{settings.AGENT_RUNNER_SCRIPT_NAME}') is correct and present in 'app/agent_runner/'.")
    # Дополнительно можно проверить PYTHON_EXECUTABLE, если он кастомный
elif settings.RUN_AGENTS_WITH_DOCKER:
    if not settings.AGENT_DOCKER_IMAGE:
        print(f"App Config: CRITICAL WARNING! RUN_AGENTS_WITH_DOCKER is true, but AGENT_DOCKER_IMAGE is not set.")
    else:
        print(f"App Config: Agent runners will be launched using Docker image: {settings.AGENT_DOCKER_IMAGE}")

# For debugging purposes, print out a few key settings
# print(f"App Config: MANAGER_PORT set to {settings.MANAGER_PORT}")
# print(f"App Config: REDIS_URL set to {settings.REDIS_URL}")
# print(f"App Config: DATABASE_URL set to {settings.DATABASE_URL}")
# print(f"App Config: RUN_AGENTS_WITH_DOCKER set to {settings.RUN_AGENTS_WITH_DOCKER}")
