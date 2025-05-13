import logging
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.dependencies import get_db
from app.api.schemas.chat_schemas import ChatMessageOutput, ChatListItemOutput
from app.db.crud import agent_crud, chat_crud # chat_crud будет содержать db_get_agent_chats, db_get_chat_history, db_delete_chat_thread

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/{agent_id}/chats")

@router.get(
    "/",
    response_model=List[ChatListItemOutput],
    summary="List chat threads for an agent"
)
async def list_agent_chats_api(
    agent_id: str,
    skip: int = Query(0, ge=0, description="Number of threads to skip"),
    limit: int = Query(100, ge=1, le=500, description="Maximum number of threads to return"),
    channel: Optional[str] = Query(None, description="Filter by channel of the last message (e.g., 'telegram', 'websocket')"),
    db: AsyncSession = Depends(get_db)
):
    """
    Retrieves a list of unique chat threads for the specified agent,
    ordered by the timestamp of the last message in descending order.
    Includes details of the last message for each thread.
    Allows filtering by the channel of the last message.
    """
    db_agent = await agent_crud.db_get_agent_config(db, agent_id)
    if not db_agent:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent configuration not found")

    try:
        chats = await chat_crud.db_get_agent_chats(db, agent_id, skip=skip, limit=limit, channel=channel)
        return chats
    except Exception as e:
        logger.error(f"Error fetching chat list for agent {agent_id}: {e}", exc_info=True)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to retrieve chat list.")

@router.get(
    "/{thread_id}",
    response_model=List[ChatMessageOutput],
    summary="Get chat history for a specific thread"
)
async def get_chat_history_api(
    agent_id: str,
    thread_id: str,
    skip: int = Query(0, ge=0, description="Number of messages to skip"),
    limit: int = Query(100, ge=1, le=1000, description="Maximum number of messages to return"),
    db: AsyncSession = Depends(get_db)
):
    """
    Retrieves the chat message history for a specific agent and thread ID,
    ordered by timestamp in ascending order.
    """
    db_agent = await agent_crud.db_get_agent_config(db, agent_id) # Проверка существования агента
    if not db_agent:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent configuration not found")

    try:
        history = await chat_crud.db_get_chat_history(db, agent_id, thread_id, skip=skip, limit=limit)
        return history
    except Exception as e:
        logger.error(f"Error fetching chat history for agent {agent_id}, thread {thread_id}: {e}", exc_info=True)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to retrieve chat history.")

@router.delete(
    "/{thread_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a chat thread"
)
async def delete_chat_thread_api(
    agent_id: str,
    thread_id: str,
    db: AsyncSession = Depends(get_db)
):
    """
    Deletes all messages associated with a specific agent and thread ID.
    Returns 204 No Content on successful deletion, even if the thread didn't exist.
    Returns 404 if the agent itself does not exist.
    """
    db_agent = await agent_crud.db_get_agent_config(db, agent_id) # Проверка существования агента
    if not db_agent:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Agent configuration not found")

    try:
        await chat_crud.db_delete_chat_thread(db, agent_id, thread_id)
        return None # Для статуса 204 тело ответа должно быть пустым
    except Exception as e:
        logger.error(f"Error deleting chat thread for agent {agent_id}, thread {thread_id}: {e}", exc_info=True)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to delete chat thread.")
