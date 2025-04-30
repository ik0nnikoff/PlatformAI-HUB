import os
from dotenv import load_dotenv

# Load from the project root .env
dotenv_path = os.path.join(os.path.dirname(__file__), '..', '..', '.env')
if os.path.exists(dotenv_path):
    load_dotenv(dotenv_path=dotenv_path)
    print(f"Agent Manager: Loaded environment variables from {dotenv_path}")
else:
    print(f"Agent Manager: Warning! .env file not found at {dotenv_path}")

# Redis Configuration
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379")

# PostgreSQL Configuration (Placeholder)
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql+asyncpg://user:password@localhost:5432/agentdb") # Example URL

# Agent Runner Configuration
AGENT_RUNNER_SCRIPT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'agent_runner', 'runner.py'))
PYTHON_EXECUTABLE = "python" # Or use sys.executable

# Service Configuration
MANAGER_HOST = os.getenv("MANAGER_HOST", "localhost")
MANAGER_PORT = int(os.getenv("MANAGER_PORT", "8000"))

# Check if runner script exists
if not os.path.exists(AGENT_RUNNER_SCRIPT):
    print(f"Agent Manager: CRITICAL WARNING! Agent runner script not found at: {AGENT_RUNNER_SCRIPT}")

