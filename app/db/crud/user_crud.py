import logging
from sqlalchemy import select, update, or_ # Добавляем or_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.exc import SQLAlchemyError
from typing import List, Optional, Dict, Any
from datetime import datetime, timezone

from app.db.alchemy_models import UserDB, AgentUserAuthorizationDB, AgentConfigDB

logger = logging.getLogger(__name__)

async def get_user_by_platform_id(db: AsyncSession, platform: str, platform_user_id: str) -> Optional[UserDB]:
    """Retrieves a user by their platform and platform-specific ID."""
    logger.debug(f"Fetching user by platform ID: {platform}/{platform_user_id}")
    try:
        result = await db.execute(
            select(UserDB).where(
                UserDB.platform == platform,
                UserDB.platform_user_id == platform_user_id
            )
        )
        user = result.scalar_one_or_none()
        if user:
            logger.debug(f"User found: DB_ID={user.id}, Platform={platform}, PlatformID={platform_user_id}")
        else:
            logger.debug(f"User not found: Platform={platform}, PlatformID={platform_user_id}")
        return user
    except SQLAlchemyError as e:
        logger.error(f"Error getting user by platform ID ({platform}/{platform_user_id}): {e}", exc_info=True)
        return None
    except Exception as e:
        logger.error(f"Unexpected error getting user by platform ID ({platform}/{platform_user_id}): {e}", exc_info=True)
        return None

async def create_or_update_user(
    db: AsyncSession,
    platform: str,
    platform_user_id: str,
    user_details: Dict[str, Any]
) -> Optional[UserDB]:
    """
    Creates a new user or updates an existing one based on platform and platform_user_id.
    Authorization status is NOT handled here.
    """
    logger.info(f"Creating or updating user: Platform={platform}, PlatformID={platform_user_id}")
    try:
        existing_user = await get_user_by_platform_id(db, platform, platform_user_id)

        if existing_user:
            logger.debug(f"Updating existing user: DB_ID={existing_user.id}")
            update_data = {k: v for k, v in user_details.items() if v is not None and k != 'is_authorized'}
            if not update_data:
                logger.debug(f"No fields to update for user: DB_ID={existing_user.id}")
                return existing_user # Return existing user if no data to update
            
            update_data['updated_at'] = datetime.now(timezone.utc)
            
            await db.execute(
                update(UserDB)
                .where(UserDB.id == existing_user.id)
                .values(**update_data)
            )
            await db.commit()
            await db.refresh(existing_user, attribute_names=list(update_data.keys()))
            logger.info(f"Updated user: DB_ID={existing_user.id}")
            return existing_user
        else:
            logger.debug(f"Creating new user: Platform={platform}, PlatformID={platform_user_id}")
            new_user_data = {k: v for k, v in user_details.items() if v is not None and k != 'is_authorized'}
            new_user = UserDB(
                platform=platform,
                platform_user_id=platform_user_id,
                **new_user_data
            )
            db.add(new_user)
            await db.commit()
            await db.refresh(new_user)
            logger.info(f"Created new user: DB_ID={new_user.id}")
            return new_user

    except SQLAlchemyError as e:
        await db.rollback()
        logger.error(f"Database error creating/updating user ({platform}/{platform_user_id}): {e}", exc_info=True)
        return None
    except Exception as e:
        await db.rollback()
        logger.error(f"Unexpected error creating/updating user ({platform}/{platform_user_id}): {e}", exc_info=True)
        return None

async def get_agent_user_authorization(db: AsyncSession, agent_id: str, user_id: int) -> Optional[AgentUserAuthorizationDB]:
    """Retrieves the authorization status of a user for a specific agent."""
    logger.debug(f"Fetching authorization for AgentID={agent_id}, UserID={user_id}")
    try:
        result = await db.execute(
            select(AgentUserAuthorizationDB).where(
                AgentUserAuthorizationDB.agent_id == agent_id,
                AgentUserAuthorizationDB.user_id == user_id
            )
        )
        auth = result.scalar_one_or_none()
        if auth:
            logger.debug(f"Authorization found: AgentID={agent_id}, UserID={user_id}, Authorized={auth.is_authorized}")
        else:
            logger.debug(f"No authorization record found for AgentID={agent_id}, UserID={user_id}")
        return auth
    except SQLAlchemyError as e:
        logger.error(f"Error fetching agent user authorization for AgentID={agent_id}, UserID={user_id}: {e}", exc_info=True)
        return None
    except Exception as e:
        logger.error(f"Unexpected error fetching agent user authorization for AgentID={agent_id}, UserID={user_id}: {e}", exc_info=True)
        return None

async def update_agent_user_authorization(
    db: AsyncSession, 
    agent_id: str, 
    user_id: int, 
    is_authorized: bool
) -> Optional[AgentUserAuthorizationDB]:
    """Updates or creates an agent-user authorization status."""
    logger.info(f"Updating authorization for AgentID={agent_id}, UserID={user_id} to {is_authorized}")
    try:
        existing_auth = await get_agent_user_authorization(db, agent_id, user_id)
        if existing_auth:
            if existing_auth.is_authorized == is_authorized:
                logger.debug(f"Authorization status unchanged for AgentID={agent_id}, UserID={user_id}. No update needed.")
                return existing_auth
            
            existing_auth.is_authorized = is_authorized
            existing_auth.updated_at = datetime.now(timezone.utc)
            db.add(existing_auth) # Add to session to track changes
            await db.commit()
            await db.refresh(existing_auth)
            logger.info(f"Updated authorization for AgentID={agent_id}, UserID={user_id} to {is_authorized}")
            return existing_auth
        else:
            new_auth = AgentUserAuthorizationDB(
                agent_id=agent_id,
                user_id=user_id,
                is_authorized=is_authorized
            )
            db.add(new_auth)
            await db.commit()
            await db.refresh(new_auth)
            logger.info(f"Created new authorization for AgentID={agent_id}, UserID={user_id}, Authorized={is_authorized}")
            return new_auth
    except SQLAlchemyError as e:
        await db.rollback()
        logger.error(f"Database error updating/creating agent user authorization for AgentID={agent_id}, UserID={user_id}: {e}", exc_info=True)
        return None
    except Exception as e:
        await db.rollback()
        logger.error(f"Unexpected error updating/creating agent user authorization for AgentID={agent_id}, UserID={user_id}: {e}", exc_info=True)
        return None

async def db_get_authorized_users_for_agent(db: AsyncSession, agent_id: str) -> List[UserDB]:
    """Retrieves all users authorized for a specific agent."""
    logger.debug(f"Fetching authorized users for AgentID={agent_id}")
    try:
        stmt = (
            select(UserDB)
            .join(AgentUserAuthorizationDB, UserDB.id == AgentUserAuthorizationDB.user_id)
            .where(AgentUserAuthorizationDB.agent_id == agent_id, AgentUserAuthorizationDB.is_authorized == True)
        )
        result = await db.execute(stmt)
        users = result.scalars().all()
        logger.debug(f"Fetched {len(users)} authorized users for AgentID={agent_id}")
        return users
    except SQLAlchemyError as e:
        logger.error(f"Error fetching authorized users for AgentID={agent_id}: {e}", exc_info=True)
        return []
    except Exception as e:
        logger.error(f"Unexpected error fetching authorized users for AgentID={agent_id}: {e}", exc_info=True)
        return []

async def db_get_agents_for_user(db: AsyncSession, user_id: int, only_authorized: bool = True) -> List[AgentConfigDB]:
    """Retrieves all agents associated with a user, optionally filtering by authorization."""
    logger.debug(f"Fetching agents for UserID={user_id}, OnlyAuthorized={only_authorized}")
    try:
        stmt = select(AgentConfigDB).join(AgentUserAuthorizationDB, AgentConfigDB.id == AgentUserAuthorizationDB.agent_id)\
                                 .where(AgentUserAuthorizationDB.user_id == user_id)
        if only_authorized:
            stmt = stmt.where(AgentUserAuthorizationDB.is_authorized == True)
        
        result = await db.execute(stmt)
        agents = result.scalars().all()
        logger.debug(f"Fetched {len(agents)} agents for UserID={user_id} (Authorized: {only_authorized})")
        return agents
    except SQLAlchemyError as e:
        logger.error(f"Error fetching agents for UserID={user_id}: {e}", exc_info=True)
        return []
    except Exception as e:
        logger.error(f"Unexpected error fetching agents for UserID={user_id}: {e}", exc_info=True)
        return []

async def get_users(
    db: AsyncSession,
    skip: int = 0,
    limit: int = 100,
    platform: Optional[str] = None,
    search_query: Optional[str] = None
) -> List[UserDB]:
    """Получает список пользователей с необязательной фильтрацией и поиском."""
    logger.debug(f"Получение пользователей: skip={skip}, limit={limit}, platform={platform}, search_query={search_query}")
    try:
        stmt = select(UserDB)
        if platform:
            stmt = stmt.where(UserDB.platform == platform)
        if search_query:
            search_ilike = f"%{search_query}%"
            stmt = stmt.where(
                or_(
                    UserDB.platform_user_id.ilike(search_ilike),
                    UserDB.phone_number.ilike(search_ilike),
                    UserDB.first_name.ilike(search_ilike),
                    UserDB.last_name.ilike(search_ilike),
                    UserDB.username.ilike(search_ilike)
                )
            )
        stmt = stmt.order_by(UserDB.updated_at.desc()).offset(skip).limit(limit)
        result = await db.execute(stmt)
        users = result.scalars().all()
        logger.debug(f"Получено {len(users)} пользователей.")
        return list(users)
    except SQLAlchemyError as e:
        logger.error(f"Ошибка при получении пользователей: {e}", exc_info=True)
        return []
    except Exception as e:
        logger.error(f"Непредвиденная ошибка при получении пользователей: {e}", exc_info=True)
        return []

async def get_user(db: AsyncSession, user_id: int) -> Optional[UserDB]:
    """Получает пользователя по его внутреннему ID базы данных."""
    logger.debug(f"Получение пользователя по ID БД: {user_id}")
    try:
        result = await db.execute(select(UserDB).where(UserDB.id == user_id))
        user = result.scalar_one_or_none()
        if user:
            logger.debug(f"Пользователь найден: DB_ID={user.id}")
        else:
            logger.debug(f"Пользователь не найден: DB_ID={user_id}")
        return user
    except SQLAlchemyError as e:
        logger.error(f"Ошибка при получении пользователя по ID БД ({user_id}): {e}", exc_info=True)
        return None
    except Exception as e:
        logger.error(f"Непредвиденная ошибка при получении пользователя по ID БД ({user_id}): {e}", exc_info=True)
        return None
