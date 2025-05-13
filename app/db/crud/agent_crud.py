import logging
from sqlalchemy import select, update, delete
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.exc import SQLAlchemyError
from typing import List, Optional, Dict, Any

from app.db.alchemy_models import AgentConfigDB
from app.api.schemas.agent_schemas import AgentConfigInput

logger = logging.getLogger(__name__)

async def db_create_agent_config(db: AsyncSession, agent_config: AgentConfigInput, agent_id: str) -> AgentConfigDB:
    """Creates a new agent configuration in the database."""
    logger.info(f"Creating agent config in DB for agent_id: {agent_id}")
    config_json_data = agent_config.config_json

    db_agent = AgentConfigDB(
        id=agent_id,
        name=agent_config.name,
        description=agent_config.description,
        owner_id=agent_config.userId,
        config_json=config_json_data
    )
    db.add(db_agent)
    try:
        await db.commit()
        await db.refresh(db_agent)
        logger.info(f"Agent config created successfully for agent_id: {agent_id}")
        return db_agent
    except SQLAlchemyError as e:
        await db.rollback()
        logger.error(f"Error creating agent config for agent_id {agent_id}: {e}", exc_info=True)
        raise
    except Exception as e:
        await db.rollback()
        logger.error(f"Unexpected error creating agent config for agent_id {agent_id}: {e}", exc_info=True)
        raise

async def db_get_agent_config(db: AsyncSession, agent_id: str) -> Optional[AgentConfigDB]:
    """Retrieves an agent configuration from the database by ID."""
    logger.debug(f"Fetching agent config for ID: '{agent_id}' from DB")
    if not db:
        logger.error("Database session is not available.")
        return None
    try:
        result = await db.get(AgentConfigDB, agent_id)
        if result:
            logger.debug(f"Agent config found for ID: '{agent_id}'")
        else:
            logger.debug(f"Agent config NOT found for ID: '{agent_id}'")
        return result
    except SQLAlchemyError as e:
        logger.error(f"Error fetching agent config for ID '{agent_id}': {e}", exc_info=True)
        return None
    except Exception as e:
        logger.error(f"Unexpected error fetching agent config for ID '{agent_id}': {e}", exc_info=True)
        return None


async def db_get_all_agents(db: AsyncSession, skip: int = 0, limit: int = 100) -> List[AgentConfigDB]:
    """Retrieves a list of agent configurations from the database."""
    logger.debug(f"Fetching all agent configs from DB (skip={skip}, limit={limit})")
    try:
        stmt = select(AgentConfigDB).order_by(AgentConfigDB.created_at.desc()).offset(skip).limit(limit)
        result = await db.execute(stmt)
        agents = result.scalars().all()
        logger.debug(f"Fetched {len(agents)} agent configs from DB")
        return agents
    except SQLAlchemyError as e:
        logger.error(f"Error fetching all agent configs: {e}", exc_info=True)
        return []
    except Exception as e:
        logger.error(f"Unexpected error fetching all agent configs: {e}", exc_info=True)
        return []

async def db_delete_agent_config(db: AsyncSession, agent_id: str) -> bool:
    """Deletes an agent configuration from the database by ID."""
    logger.info(f"Deleting agent config from DB for agent_id: {agent_id}")
    try:
        db_agent = await db.get(AgentConfigDB, agent_id)
        if db_agent:
            await db.delete(db_agent)
            await db.commit()
            logger.info(f"Agent config deleted successfully for agent_id: {agent_id}")
            return True
        else:
            logger.warning(f"Agent config not found for deletion, agent_id: {agent_id}")
            return False
    except SQLAlchemyError as e:
        await db.rollback()
        logger.error(f"Error deleting agent config for agent_id {agent_id}: {e}", exc_info=True)
        return False
    except Exception as e:
        await db.rollback()
        logger.error(f"Unexpected error deleting agent config for agent_id {agent_id}: {e}", exc_info=True)
        return False

async def db_update_agent_config(db: AsyncSession, agent_id: str, agent_config: AgentConfigInput) -> Optional[AgentConfigDB]:
    """Updates an existing agent configuration in the database."""
    logger.info(f"Updating agent config in DB for agent_id: {agent_id}")
    try:
        stmt = select(AgentConfigDB).where(AgentConfigDB.id == agent_id)
        result = await db.execute(stmt)
        db_agent = result.scalars().first()

        if not db_agent:
            logger.warning(f"Agent config not found for update, agent_id: {agent_id}")
            return None

        db_agent.name = agent_config.name
        db_agent.description = agent_config.description
        db_agent.owner_id = agent_config.userId
        db_agent.config_json = agent_config.config_json

        await db.commit()
        await db.refresh(db_agent)
        logger.info(f"Agent config updated successfully for agent_id: {agent_id}")
        return db_agent
    except SQLAlchemyError as e:
        await db.rollback()
        logger.error(f"Error updating agent config for agent_id {agent_id}: {e}", exc_info=True)
        raise
    except Exception as e:
        await db.rollback()
        logger.error(f"Unexpected error updating agent config for agent_id {agent_id}: {e}", exc_info=True)
        raise
