import json
import logging
from typing import List, Optional
import redis.asyncio as redis
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy import delete as sqlalchemy_delete

# Import models
from .models import AgentConfigInput, AgentConfigOutput, AgentConfigDB

logger = logging.getLogger(__name__)

# --- Database CRUD Operations ---

async def db_create_agent_config(db: AsyncSession, agent_config: AgentConfigInput, agent_id: str) -> AgentConfigDB:
    """Creates a new agent configuration in the database."""
    logger.info(f"Creating agent config in DB for agent_id: {agent_id}")
    # Convert Pydantic config structure to JSON for storage
    config_json_data = agent_config.config.model_dump(exclude_unset=True)

    db_agent = AgentConfigDB(
        id=agent_id,
        name=agent_config.name,
        description=agent_config.description,
        user_id=agent_config.userId,
        config_json=config_json_data
    )
    db.add(db_agent)
    try:
        await db.commit()
        await db.refresh(db_agent)
        logger.info(f"Agent config saved to DB for agent_id: {agent_id}")
        return db_agent
    except Exception as e:
        await db.rollback()
        logger.error(f"Failed to save agent config to DB for {agent_id}: {e}", exc_info=True)
        raise # Re-raise the exception to be handled by the API layer

async def db_get_agent_config(db: AsyncSession, agent_id: str) -> Optional[AgentConfigDB]:
    """Retrieves an agent configuration from the database by ID."""
    logger.debug(f"Fetching agent config for ID: '{agent_id}' from DB") # Используем кавычки для ясности
    if not db: # Добавим проверку на валидность сессии
        logger.error("db_get_agent_config received an invalid DB session (None).")
        return None
    try: # Добавим try/except для отлова ошибок сессии
        stmt = select(AgentConfigDB).where(AgentConfigDB.id == agent_id)
        result = await db.execute(stmt)
        agent = result.scalar_one_or_none()
        if agent:
            logger.debug(f"Found agent config for ID: '{agent_id}'")
        else:
            logger.warning(f"Agent config query returned None for ID: '{agent_id}'") # Уточненный лог
        return agent
    except Exception as e:
        logger.error(f"Exception during DB query for agent_id '{agent_id}': {e}", exc_info=True)
        return None # Возвращаем None при ошибке запроса

async def db_get_all_agents(db: AsyncSession, skip: int = 0, limit: int = 100) -> List[AgentConfigDB]:
    """Retrieves a list of agent configurations from the database."""
    logger.debug(f"Fetching all agent configs from DB (skip={skip}, limit={limit})")
    stmt = select(AgentConfigDB).offset(skip).limit(limit)
    result = await db.execute(stmt)
    agents = result.scalars().all()
    logger.debug(f"Fetched {len(agents)} agent configs from DB")
    return agents

async def db_delete_agent_config(db: AsyncSession, agent_id: str) -> bool:
    """Deletes an agent configuration from the database by ID."""
    logger.info(f"Deleting agent config from DB for agent_id: {agent_id}")
    db_agent = await db.get(AgentConfigDB, agent_id)
    if db_agent:
        try:
            await db.delete(db_agent)
            await db.commit()
            logger.info(f"Agent config deleted from DB for agent_id: {agent_id}")
            return True
        except Exception as e:
            await db.rollback()
            logger.error(f"Failed to delete agent config from DB for {agent_id}: {e}", exc_info=True)
            raise # Re-raise the exception
    else:
        logger.warning(f"Agent config not found in DB for deletion: {agent_id}")
        return False


# --- Redis Operations (Keep for status, potentially remove config ops later) ---

# Keep Redis functions for status management as they are more ephemeral
# Or decide later if status should also move to DB (less ideal for frequent updates)

# Remove Redis config functions or comment them out

# async def create_agent_config(r: redis.Redis, agent_config: AgentConfigInput, agent_id: str) -> AgentConfigOutput:
#     """Stores agent configuration in Redis."""
#     config_key = f"agent_config:{agent_id}"
#     status_key = f"agent_status:{agent_id}"
#     config_data = agent_config.model_dump()
#     try:
#         await r.set(config_key, json.dumps(config_data))
#         # Also add to a set for easy listing
#         await r.sadd("agents_set", agent_id)
#         # Set initial status
#         await r.hset(status_key, mapping={"status": "stopped"})
#         logger.info(f"Agent configuration stored in Redis for {agent_id}")
#         # Simulate DB timestamps for now
#         now = datetime.now()
#         return AgentConfigOutput(**config_data, id=agent_id, created_at=now, updated_at=now)
#     except Exception as e:
#         logger.error(f"Failed to store agent config in Redis for {agent_id}: {e}", exc_info=True)
#         raise

# async def get_agent_config(r: redis.Redis, agent_id: str) -> Optional[AgentConfigOutput]:
#     """Retrieves agent configuration from Redis."""
#     config_key = f"agent_config:{agent_id}"
#     config_json = await r.get(config_key)
#     if config_json:
#         try:
#             config_data = json.loads(config_json)
#             # Simulate DB timestamps for now
#             now = datetime.now() # Or fetch from status if stored?
#             return AgentConfigOutput(**config_data, id=agent_id, created_at=now, updated_at=now)
#         except json.JSONDecodeError:
#             logger.error(f"Failed to decode agent config JSON from Redis for {agent_id}")
#             return None
#         except Exception as e:
#              logger.error(f"Error creating AgentConfigOutput from Redis data for {agent_id}: {e}")
#              return None
#     return None

# async def get_all_agent_ids(r: redis.Redis) -> List[str]:
#     """Retrieves all agent IDs from the Redis set."""
#     agent_ids = await r.smembers("agents_set")
#     return sorted(list(agent_ids))

# async def delete_agent_config(r: redis.Redis, agent_id: str) -> bool:
#     """Deletes agent configuration and status from Redis."""
#     config_key = f"agent_config:{agent_id}"
#     status_key = f"agent_status:{agent_id}"
#     try:
#         deleted_count = await r.delete(config_key, status_key)
#         await r.srem("agents_set", agent_id)
#         logger.info(f"Deleted Redis keys for agent {agent_id}. Count: {deleted_count}")
#         return deleted_count > 0 # Return True if at least one key was deleted
#     except Exception as e:
#         logger.error(f"Failed to delete Redis keys for agent {agent_id}: {e}", exc_info=True)
#         return False

