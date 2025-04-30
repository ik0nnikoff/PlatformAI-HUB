import logging
from fastapi import APIRouter, Depends, HTTPException, status, Query
from typing import List, Optional
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_db
from ..models import UserOutput # Импортируем модель для ответа
from .. import crud

logger = logging.getLogger(__name__)
router = APIRouter()

@router.get(
    "/",
    response_model=List[UserOutput],
    summary="List users with optional filters",
    tags=["Users"] # Добавляем тег Users
)
async def list_users(
    platform: Optional[str] = Query(None, description="Filter by platform (e.g., 'telegram')"),
    search: Optional[str] = Query(None, description="Search query for platform ID, phone, name, or username"),
    skip: int = Query(0, ge=0, description="Number of users to skip"),
    limit: int = Query(100, ge=1, le=500, description="Maximum number of users to return"),
    db: AsyncSession = Depends(get_db)
):
    """
    Retrieves a list of users, allowing filtering by platform and a general search query
    across multiple fields. Supports pagination. Authorization status is no longer a filter.
    """
    try:
        users = await crud.db_get_users(
            db=db,
            platform=platform,
            search_query=search,
            skip=skip,
            limit=limit
        )
        # FastAPI автоматически преобразует список UserDB в список UserOutput благодаря response_model
        return users
    except Exception as e:
        # Логирование происходит в CRUD
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to retrieve user list.")

# Можно добавить другие эндпоинты для пользователей здесь (например, GET /users/{user_id}, DELETE /users/{user_id})
