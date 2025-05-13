import logging

from fastapi import FastAPI

from app.core.lifespan import lifespan
from app.core.config import settings
# Импорты роутеров
from app.api.routers import agent_api, chat_api, integration_api, sse_api, user_api, websocket_api

# Настройка логирования вызывается в lifespan, но можно получить логгер здесь
logger = logging.getLogger(__name__)

app = FastAPI(
    title=settings.PROJECT_NAME,
    version=settings.PROJECT_VERSION,
    openapi_url=f"{settings.API_V1_STR}/openapi.json",
    lifespan=lifespan
)

# Подключение роутеров
app.include_router(agent_api.router, prefix=settings.API_V1_STR + "/agents", tags=["Agents"])
app.include_router(integration_api.router, prefix=settings.API_V1_STR, tags=["Integrations"])
app.include_router(user_api.router, prefix=settings.API_V1_STR + "/users", tags=["Users"])
app.include_router(chat_api.router, prefix=settings.API_V1_STR + "/chat", tags=["Chat"])
app.include_router(sse_api.router, prefix=settings.API_V1_STR + "/agents", tags=["SSE"])
app.include_router(websocket_api.router, prefix=settings.API_V1_STR, tags=["WebSockets"])

@app.get("/", tags=["Root"])
async def read_root():
    logger.info("Root endpoint was called.")
    return {"message": f"Welcome to {settings.PROJECT_NAME} v{settings.PROJECT_VERSION}"}

# Для локального запуска (uvicorn app.main:app --reload)
if __name__ == "__main__":
    import uvicorn
    logger.info(f"Starting Uvicorn server for {settings.PROJECT_NAME} on http://{settings.SERVER_HOST}:{settings.SERVER_PORT}")
    uvicorn.run(
        "app.main:app", 
        host=settings.SERVER_HOST, 
        port=settings.SERVER_PORT, 
        reload=settings.DEBUG # Включаем reload только в DEBUG режиме
    )
