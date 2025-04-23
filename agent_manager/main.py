import os
import subprocess
import json
import logging
import signal # Import signal for os.kill
from fastapi import FastAPI, HTTPException, Depends, WebSocket, WebSocketDisconnect, status as fastapi_status, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ValidationError, Field as PydanticField
from typing import List, Dict, Any, Optional
import redis.asyncio as redis
import asyncio
from contextlib import asynccontextmanager
import time # Import time module

# Импортируем зависимости и модули
from .redis_client import init_redis_pool, close_redis_pool, get_redis
from .db import init_db, close_db_engine, get_db # Импортируем DB функции
from .process_manager import check_inactive_agents # Импортируем фоновую задачу
from .api import agents as agents_api # Импортируем новый роутер для агентов
from .api import websocket as websocket_api # Импортируем роутер для WebSocket

# --- Configuration & Globals ---
# Load .env - adjust path as needed
from dotenv import load_dotenv
dotenv_path = '/Users/jb/Projects/experiments/.env'
if os.path.exists(dotenv_path):
    load_dotenv(dotenv_path=dotenv_path)
else:
    print(f"Warning: .env file not found at {dotenv_path}")


REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379")
# TODO: Add PostgreSQL setup (SQLAlchemy models, database URL, session management)
# from . import crud, models, schemas
# from .database import SessionLocal, engine
# models.Base.metadata.create_all(bind=engine)

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

redis_client: redis.Redis = None # Глобальный клиент для фоновой задачи
inactivity_check_task: Optional[asyncio.Task] = None

# --- Pydantic Models for API ---
class AgentConfigInput(BaseModel):
    # Define structure matching the input JSON config more closely
    name: str
    description: str
    userId: str # Assuming this relates to the owner
    private: Optional[str] = None # Or bool?
    type: str # e.g., "simple"
    config: Dict[str, Any] # Keep flexible for now

class AgentConfigOutput(AgentConfigInput):
     id: str = PydanticField(..., description="Unique Agent ID assigned by the service")

class AgentStatus(BaseModel):
    agent_id: str
    status: str = PydanticField(..., description="e.g., 'stopped', 'running', 'starting', 'error', 'error_config', 'error_app_create'")
    pid: Optional[int] = None
    last_active: Optional[float] = None

# --- FastAPI Lifecycle ---
@asynccontextmanager
async def lifespan(app: FastAPI):
    """Handles application startup and shutdown events."""
    global redis_client, inactivity_check_task # Объявляем глобальные переменные
    # Startup
    logger.info("Agent Manager Service starting up...")
    redis_client = await init_redis_pool() # Инициализируем и сохраняем клиент
    await init_db() # Initialize DB connection

    # Start the background task
    if redis_client: # Запускаем задачу только если Redis доступен
        # Передаем redis_client в фоновую задачу
        inactivity_check_task = asyncio.create_task(check_inactive_agents(redis_client))
    else:
        logger.warning("Redis client not initialized, inactivity check task will not start.")


    yield
    # Shutdown
    logger.info("Agent Manager Service shutting down...")

    # Cancel the background task
    if inactivity_check_task and not inactivity_check_task.done(): # Проверяем, что задача существует и не завершена
        logger.info("Cancelling inactivity check task...")
        inactivity_check_task.cancel()
        try:
            await inactivity_check_task
        except asyncio.CancelledError:
            logger.info("Inactivity check task successfully cancelled.")
        except Exception as e:
             logger.error(f"Error during inactivity check task shutdown: {e}")


    await close_redis_pool()
    await close_db_engine() # Close DB connection pool


app = FastAPI(
    title="Agent Management Service",
    description="API for creating, managing, and interacting with configurable agents.",
    version="0.1.0",
    lifespan=lifespan
)

# --- Middleware (Optional) ---
# Example: Add timing middleware
# @app.middleware("http")
# async def add_process_time_header(request: Request, call_next):
#     start_time = time.time()
#     response = await call_next(request)
#     process_time = time.time() - start_time
#     response.headers["X-Process-Time"] = str(process_time)
#     return response

# --- Exception Handlers (Optional) ---
# Example: Generic exception handler
@app.exception_handler(Exception)
async def generic_exception_handler(request: Request, exc: Exception):
    logger.error(f"Unhandled exception for request {request.url}: {exc}", exc_info=True)
    return JSONResponse(
        status_code=500,
        content={"message": "An internal server error occurred."},
    )

# --- Include Routers ---
# Используем роутер из agents.py
app.include_router(agents_api.router, prefix="/agents", tags=["Agents"])
app.include_router(websocket_api.router, tags=["WebSocket"]) # Добавляем роутер WebSocket

# --- Root Endpoint ---
@app.get("/", tags=["Status"])
async def read_root():
    """Root endpoint providing basic service status."""
    redis_status = "disconnected"
    db_status = "disconnected"
    try:
        # Check Redis
        r = await get_redis().__anext__() # Get a connection to test
        await r.ping()
        redis_status = "connected"
        await r.close()
    except Exception:
        pass # Status remains disconnected

    try:
        # Check DB
        async with get_db() as db: # Use get_db context manager
            if db:
                # Perform a simple query like SELECT 1
                from sqlalchemy import text
                await db.execute(text("SELECT 1"))
                db_status = "connected"
            else:
                 db_status = "disabled" # If get_db yields None
    except Exception as e:
        logger.warning(f"DB connection check failed: {e}")
        pass # Status remains disconnected

    return {
        "service": "Agent Management Service",
        "status": "running",
        "redis_status": redis_status,
        "db_status": db_status # Add DB status
    }

# --- Main execution (for running with uvicorn directly) ---
# This part is usually not needed if using `make manager` target
# if __name__ == "__main__":
#     import uvicorn
#     uvicorn.run(
#         "agent_manager.main:app",
#         host=config.MANAGER_HOST,
#         port=config.MANAGER_PORT,
#         reload=True # Enable reload for development
#     )
