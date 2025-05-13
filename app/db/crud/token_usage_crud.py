import logging
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.exc import SQLAlchemyError
from typing import List, Optional, Dict, Any

from app.db.alchemy_models import TokenUsageLogDB, ChatMessageDB
from app.api.schemas.common_schemas import SenderType

logger = logging.getLogger(__name__)

async def db_add_token_usage_log(db: AsyncSession, token_usage_data: Dict[str, Any]) -> Optional[TokenUsageLogDB]:
    """Adds a new token usage log entry to the database."""
    logger.debug(f"Adding token usage log for InteractionID: {token_usage_data.get('interaction_id')}")
    db_log_entry = TokenUsageLogDB(**token_usage_data)
    db.add(db_log_entry)
    try:
        await db.commit()
        await db.refresh(db_log_entry)
        logger.info(f"Token usage log entry added successfully (ID: {db_log_entry.id}) for InteractionID: {db_log_entry.interaction_id}")
        return db_log_entry
    except SQLAlchemyError as e:
        await db.rollback()
        logger.error(f"Error adding token usage log for InteractionID {token_usage_data.get('interaction_id')}: {e}", exc_info=True)
        # Not raising here, as this is often a background task, allow caller to decide
        return None 
    except Exception as e:
        await db.rollback()
        logger.error(f"Unexpected error adding token usage log for InteractionID {token_usage_data.get('interaction_id')}: {e}", exc_info=True)
        return None

async def db_get_token_usage_for_message(db: AsyncSession, message_id: int) -> List[TokenUsageLogDB]:
    """Retrieves all token usage logs associated with a specific chat message ID."""
    logger.debug(f"Fetching token usage logs for MessageID: {message_id}")
    try:
        # Изменено TokenUsageLogDB.chat_message_id на TokenUsageLogDB.message_id
        stmt = select(TokenUsageLogDB).where(TokenUsageLogDB.message_id == message_id).order_by(TokenUsageLogDB.timestamp.asc())
        result = await db.execute(stmt)
        logs = result.scalars().all()
        logger.debug(f"Fetched {len(logs)} token usage logs for MessageID: {message_id}")
        return logs
    except SQLAlchemyError as e:
        logger.error(f"Error fetching token usage for MessageID {message_id}: {e}", exc_info=True)
        return []
    except Exception as e:
        logger.error(f"Unexpected error fetching token usage for MessageID {message_id}: {e}", exc_info=True)
        return []

async def db_get_token_usage_for_interaction(db: AsyncSession, interaction_id: str) -> List[TokenUsageLogDB]:
    """Retrieves all token usage logs associated with a specific interaction ID."""
    logger.debug(f"Fetching token usage logs for InteractionID: {interaction_id}")
    try:
        stmt = select(TokenUsageLogDB).where(TokenUsageLogDB.interaction_id == interaction_id).order_by(TokenUsageLogDB.timestamp.asc())
        result = await db.execute(stmt)
        logs = result.scalars().all()
        logger.debug(f"Fetched {len(logs)} token usage logs for InteractionID: {interaction_id}")
        return logs
    except SQLAlchemyError as e:
        logger.error(f"Error fetching token usage for InteractionID {interaction_id}: {e}", exc_info=True)
        return []
    except Exception as e:
        logger.error(f"Unexpected error fetching token usage for InteractionID {interaction_id}: {e}", exc_info=True)
        return []

