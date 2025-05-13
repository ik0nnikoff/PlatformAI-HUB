import logging
from sqlalchemy import select, delete, func as sql_func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.exc import SQLAlchemyError
from typing import List, Optional
from datetime import datetime

from app.db.alchemy_models import ChatMessageDB
from app.api.schemas.common_schemas import SenderType
from app.api.schemas.chat_schemas import ChatListItemOutput

logger = logging.getLogger(__name__)

async def db_add_chat_message(
    db: AsyncSession,
    agent_id: str,
    thread_id: str,
    sender_type: SenderType,
    content: str,
    channel: Optional[str],
    timestamp: datetime,
    interaction_id: Optional[str] = None
) -> ChatMessageDB:
    """Adds a new chat message to the database."""
    logger.debug(f"Adding chat message to DB: Agent={agent_id}, Thread={thread_id}, Sender={sender_type}, InteractionID={interaction_id}")
    db_message = ChatMessageDB(
        agent_id=agent_id,
        thread_id=thread_id,
        sender_type=sender_type,
        content=content,
        channel=channel,
        timestamp=timestamp,
        interaction_id=interaction_id
    )
    db.add(db_message)
    try:
        await db.commit()
        await db.refresh(db_message)
        logger.debug(f"Chat message added successfully (ID: {db_message.id})")
        return db_message
    except SQLAlchemyError as e:
        await db.rollback()
        logger.error(f"Error adding chat message for Agent={agent_id}, Thread={thread_id}: {e}", exc_info=True)
        raise
    except Exception as e:
        await db.rollback()
        logger.error(f"Unexpected error adding chat message for Agent={agent_id}, Thread={thread_id}: {e}", exc_info=True)
        raise

async def db_get_chat_history(db: AsyncSession, agent_id: str, thread_id: str, skip: int = 0, limit: int = 100) -> List[ChatMessageDB]:
    """Retrieves chat history for a specific agent and thread, ordered by timestamp."""
    logger.debug(f"Fetching chat history for Agent={agent_id}, Thread={thread_id} (skip={skip}, limit={limit})")
    try:
        stmt = (
            select(ChatMessageDB)
            .where(ChatMessageDB.agent_id == agent_id, ChatMessageDB.thread_id == thread_id)
            .order_by(ChatMessageDB.timestamp.asc())
            .offset(skip)
            .limit(limit)
        )
        result = await db.execute(stmt)
        messages = result.scalars().all()
        logger.debug(f"Fetched {len(messages)} messages for Agent={agent_id}, Thread={thread_id}")
        return messages
    except SQLAlchemyError as e:
        logger.error(f"Error fetching chat history for Agent={agent_id}, Thread={thread_id}: {e}", exc_info=True)
        return []
    except Exception as e:
        logger.error(f"Unexpected error fetching chat history for Agent={agent_id}, Thread={thread_id}: {e}", exc_info=True)
        return []

async def db_get_recent_chat_history(db: AsyncSession, agent_id: str, thread_id: str, limit: int) -> List[ChatMessageDB]:
    """
    Retrieves the most recent 'limit' chat messages for a specific agent and thread,
    ordered by timestamp ascending (oldest of the recent first).
    """
    logger.debug(f"Fetching recent chat history for context: Agent={agent_id}, Thread={thread_id}, Limit={limit}")
    try:
        subquery = (
            select(ChatMessageDB.id)
            .where(ChatMessageDB.agent_id == agent_id, ChatMessageDB.thread_id == thread_id)
            .order_by(ChatMessageDB.timestamp.desc())
            .limit(limit)
            .subquery()
        )
        stmt = (
            select(ChatMessageDB)
            .join(subquery, ChatMessageDB.id == subquery.c.id)
            .order_by(ChatMessageDB.timestamp.asc())
        )
        result = await db.execute(stmt)
        messages = result.scalars().all()
        logger.debug(f"Fetched {len(messages)} recent messages for context: Agent={agent_id}, Thread={thread_id}")
        return messages
    except SQLAlchemyError as e:
        logger.error(f"Error fetching recent chat history for context: Agent={agent_id}, Thread={thread_id}: {e}", exc_info=True)
        return []
    except Exception as e:
        logger.error(f"Unexpected error fetching recent chat history for context: Agent={agent_id}, Thread={thread_id}: {e}", exc_info=True)
        return []

async def db_get_agent_chats(db: AsyncSession, agent_id: str, skip: int = 0, limit: int = 100, channel: Optional[str] = None) -> List[ChatListItemOutput]:
    """
    Retrieves a list of unique threads for an agent with details of the first and last messages.
    Allows filtering by the channel of the last message.
    """
    logger.debug(f"Fetching chat list for Agent={agent_id} (skip={skip}, limit={limit}, channel={channel})")
    try:
        last_message_subquery_base = (
            select(
                ChatMessageDB.thread_id,
                ChatMessageDB.content.label("last_message_content"),
                ChatMessageDB.timestamp.label("last_message_timestamp"),
                ChatMessageDB.sender_type.label("last_message_sender_type"),
                ChatMessageDB.channel.label("last_message_channel"),
                sql_func.count(ChatMessageDB.id).over(partition_by=ChatMessageDB.thread_id).label("message_count"),
                sql_func.row_number().over(partition_by=ChatMessageDB.thread_id, order_by=ChatMessageDB.timestamp.desc()).label("rn_desc")
            )
            .where(ChatMessageDB.agent_id == agent_id)
        )
        if channel:
            last_message_subquery_base = last_message_subquery_base.where(ChatMessageDB.channel == channel)
        last_message_subquery = last_message_subquery_base.subquery('last_ranked_messages')

        first_message_subquery = (
            select(
                ChatMessageDB.thread_id,
                ChatMessageDB.content.label("first_message_content"),
                ChatMessageDB.timestamp.label("first_message_timestamp"),
                sql_func.row_number().over(partition_by=ChatMessageDB.thread_id, order_by=ChatMessageDB.timestamp.asc()).label("rn_asc")
            )
            .where(ChatMessageDB.agent_id == agent_id)
            .subquery('first_ranked_messages')
        )

        stmt = (
            select(
                last_message_subquery.c.thread_id,
                first_message_subquery.c.first_message_content,
                first_message_subquery.c.first_message_timestamp,
                last_message_subquery.c.last_message_content,
                last_message_subquery.c.last_message_timestamp,
                last_message_subquery.c.last_message_sender_type,
                last_message_subquery.c.last_message_channel,
                last_message_subquery.c.message_count
            )
            .join(
                first_message_subquery,
                last_message_subquery.c.thread_id == first_message_subquery.c.thread_id
            )
            .where(last_message_subquery.c.rn_desc == 1)
            .where(first_message_subquery.c.rn_asc == 1)
            .order_by(last_message_subquery.c.last_message_timestamp.desc())
            .offset(skip)
            .limit(limit)
        )

        result = await db.execute(stmt)
        chat_list = [
            ChatListItemOutput(
                thread_id=row.thread_id,
                first_message_content=row.first_message_content,
                first_message_timestamp=row.first_message_timestamp,
                last_message_content=row.last_message_content,
                last_message_timestamp=row.last_message_timestamp,
                last_message_sender_type=row.last_message_sender_type,
                last_message_channel=row.last_message_channel,
                message_count=row.message_count
            ) for row in result.mappings().all()
        ]
        logger.debug(f"Fetched {len(chat_list)} chat threads for Agent={agent_id} matching criteria (channel={channel})")
        return chat_list
    except SQLAlchemyError as e:
        logger.error(f"Error fetching agent chats for Agent={agent_id}: {e}", exc_info=True)
        return []
    except Exception as e:
        logger.error(f"Unexpected error fetching agent chats for Agent={agent_id}: {e}", exc_info=True)
        return []

async def db_delete_chat_thread(db: AsyncSession, agent_id: str, thread_id: str) -> int:
    """Deletes all chat messages for a specific agent and thread ID."""
    logger.info(f"Deleting chat thread for Agent={agent_id}, Thread={thread_id}")
    # Assuming ON DELETE CASCADE is set for token_usage_logs.message_id -> chat_messages.id
    stmt = (
        delete(ChatMessageDB)
        .where(ChatMessageDB.agent_id == agent_id)
        .where(ChatMessageDB.thread_id == thread_id)
    )
    try:
        result = await db.execute(stmt)
        await db.commit()
        deleted_count = result.rowcount
        if deleted_count > 0:
            logger.info(f"Successfully deleted {deleted_count} messages for Agent={agent_id}, Thread={thread_id}")
        else:
            logger.warning(f"No messages found or deleted for Agent={agent_id}, Thread={thread_id}")
        return deleted_count
    except SQLAlchemyError as e:
        await db.rollback()
        logger.error(f"Error deleting chat thread for Agent={agent_id}, Thread={thread_id}: {e}", exc_info=True)
        raise
    except Exception as e:
        await db.rollback()
        logger.error(f"Unexpected error deleting chat thread for Agent={agent_id}, Thread={thread_id}: {e}", exc_info=True)
        raise

async def db_get_chat_message_by_interaction_id(
    db: AsyncSession, 
    agent_id: str, 
    interaction_id: str, 
    sender_type: Optional[SenderType] = None
) -> Optional[ChatMessageDB]:
    """
    Retrieves a specific chat message by agent_id and interaction_id.
    Optionally filters by sender_type.
    If multiple messages match (e.g., user and AI for the same interaction_id),
    and sender_type is not specified, it will return the first one found (typically the user message if saved first,
    or AI message if sender_type=AI is specified and it exists).
    It's recommended to specify sender_type if a particular side of the interaction is needed.
    """
    logger.debug(f"Fetching chat message by InteractionID: Agent={agent_id}, InteractionID={interaction_id}, SenderType={sender_type}")
    try:
        stmt = select(ChatMessageDB).where(
            ChatMessageDB.agent_id == agent_id,
            ChatMessageDB.interaction_id == interaction_id
        )
        if sender_type:
            stmt = stmt.where(ChatMessageDB.sender_type == sender_type)
        
        # Order by timestamp or ID to get a consistent "first" if multiple exist without sender_type
        # Ordering by ID descending might get the AI message if it's saved after the user message for the same interaction.
        # If we specifically want the AI's response, sender_type=SenderType.AI should be used.
        # If no sender_type, and both user & AI messages have same interaction_id, this might need more specific ordering.
        # For now, let's order by id descending to potentially get the latest message for that interaction if sender_type is None.
        stmt = stmt.order_by(ChatMessageDB.id.desc()) 

        result = await db.execute(stmt)
        message = result.scalars().first()
        if message:
            logger.debug(f"Found chat message (ID: {message.id}) for InteractionID={interaction_id}, Agent={agent_id}, SenderType={sender_type}")
        else:
            logger.debug(f"No chat message found for InteractionID={interaction_id}, Agent={agent_id}, SenderType={sender_type}")
        return message
    except SQLAlchemyError as e:
        logger.error(f"Error fetching chat message by InteractionID for Agent={agent_id}, InteractionID={interaction_id}: {e}", exc_info=True)
        return None
    except Exception as e:
        logger.error(f"Unexpected error fetching chat message by InteractionID for Agent={agent_id}, InteractionID={interaction_id}: {e}", exc_info=True)
        return None
