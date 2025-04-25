import logging
from typing import List, Optional
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from .models import AgentConfigInput, AgentConfigDB

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

async def db_update_agent_config(db: AsyncSession, agent_id: str, agent_config: AgentConfigInput) -> Optional[AgentConfigDB]:
    """Updates an existing agent configuration in the database."""
    logger.info(f"Updating agent config in DB for agent_id: {agent_id}")
    # Используем select().where() и scalars().first() для получения объекта ORM
    stmt = select(AgentConfigDB).where(AgentConfigDB.id == agent_id)
    result = await db.execute(stmt)
    db_agent = result.scalars().first() # Получаем экземпляр AgentConfigDB или None

    if not db_agent:
        logger.warning(f"Agent config not found in DB for update: {agent_id}")
        return None

    # Обновляем поля
    db_agent.name = agent_config.name
    db_agent.description = agent_config.description
    db_agent.user_id = agent_config.userId # Убедитесь, что userId обновляется
    # Преобразуем Pydantic config в JSON (словарь) для хранения
    # Используем model_dump() для Pydantic v2+
    if hasattr(agent_config.config, 'model_dump'):
        db_agent.config_json = agent_config.config.model_dump()
    else:
        # Fallback для Pydantic v1 или если это уже словарь
        db_agent.config_json = agent_config.config

    try:
        await db.commit()
        await db.refresh(db_agent)
        logger.info(f"Agent config updated in DB for agent_id: {agent_id}")
        return db_agent
    except Exception as e:
        await db.rollback()
        logger.error(f"Failed to update agent config in DB for {agent_id}: {e}", exc_info=True)
        raise # Перевыбрасываем исключение для обработки на уровне API
