import logging
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_db
from app.api.schemas import user_schemas
from app.db.crud import user_crud

logger = logging.getLogger(__name__)
router = APIRouter(tags=["Users"])

@router.get("/", response_model=List[user_schemas.UserOutput])
async def read_users_api(
    skip: int = Query(0, ge=0, description="Number of users to skip"),
    limit: int = Query(100, ge=1, le=500, description="Maximum number of users to return"),
    platform: Optional[str] = Query(None, description="Filter by platform (e.g., 'telegram')"),
    search: Optional[str] = Query(None, description="Search query for platform ID, phone, name, or username"),
    db: AsyncSession = Depends(get_db)
):
    """
    Получить список пользователей с возможностью фильтрации и поиска.
    """
    users = await user_crud.get_users(db, skip=skip, limit=limit, platform=platform, search_query=search)
    return users

@router.get("/{user_id}", response_model=user_schemas.UserOutput)
async def read_user_api(
    user_id: int,
    db: AsyncSession = Depends(get_db)
):
    """
    Получить пользователя по ID.
    """
    db_user = await user_crud.get_user(db, user_id=user_id)
    if db_user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Пользователь не найден.")
    return db_user

# TODO: Реализовать аутентификацию и авторизацию для этих эндпоинтов.
# TODO: Эндпоинты для обновления и удаления пользователей могут быть добавлены позже, если потребуется.
