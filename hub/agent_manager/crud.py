import logging
# --- ИЗМЕНЕНИЕ: Добавляем select, update, delete ---
from sqlalchemy import select, update, delete, desc, func as sql_func, or_ # Добавляем delete, func и or_
# --- КОНЕЦ ИЗМЕНЕНИЯ ---
from sqlalchemy.ext.asyncio import AsyncSession
# --- НОВОЕ: Импортируем SQLAlchemyError ---
from sqlalchemy.exc import SQLAlchemyError
# --- КОНЕЦ НОВОГО ---
# --- ИЗМЕНЕНИЕ: Добавляем UserDB и AgentUserAuthorizationDB ---
from .models import AgentConfigDB, AgentConfigInput, ChatListItemOutput, ChatMessageDB, SenderType, UserDB, AgentUserAuthorizationDB # Добавляем UserDB и AgentUserAuthorizationDB
# --- КОНЕЦ ИЗМЕНЕНИЯ ---
# --- УДАЛЕНО: Убираем неиспользуемый импорт ---
# from .schemas import AgentConfigStructure # Используем схему для валидации
# --- КОНЕЦ УДАЛЕНИЯ ---
from typing import List, Optional, Dict, Any
from datetime import datetime, timezone
from sqlalchemy.orm import aliased # Добавляем aliased

logger = logging.getLogger(__name__)

# --- Database CRUD Operations ---

async def db_create_agent_config(db: AsyncSession, agent_config: AgentConfigInput, agent_id: str) -> AgentConfigDB:
    """Creates a new agent configuration in the database."""
    logger.info(f"Creating agent config in DB for agent_id: {agent_id}")
    # Convert Pydantic config structure to JSON for storage
    config_json_data = agent_config.config_json # Сохраняем как есть из входа

    db_agent = AgentConfigDB(
        id=agent_id,
        name=agent_config.name,
        description=agent_config.description,
        # --- ИЗМЕНЕНИЕ: Маппинг userId на owner_id ---
        owner_id=agent_config.userId,
        # --- КОНЕЦ ИЗМЕНЕНИЯ ---
        config_json=config_json_data
    )
    db.add(db_agent)
    try:
        await db.commit()
        await db.refresh(db_agent)
        logger.info(f"Agent config created successfully for agent_id: {agent_id}")
        return db_agent
    except Exception as e:
        await db.rollback()
        logger.error(f"Error creating agent config for agent_id {agent_id}: {e}", exc_info=True)
        raise # Передаем исключение дальше


async def db_get_agent_config(db: AsyncSession, agent_id: str) -> Optional[AgentConfigDB]:
    """Retrieves an agent configuration from the database by ID."""
    logger.debug(f"Fetching agent config for ID: '{agent_id}' from DB") # Используем кавычки для ясности
    if not db:
        logger.error("Database session is not available.")
        return None
    try:
        # Используем get для поиска по первичному ключу
        result = await db.get(AgentConfigDB, agent_id)
        if result:
            logger.debug(f"Agent config found for ID: '{agent_id}'")
        else:
            logger.debug(f"Agent config NOT found for ID: '{agent_id}'")
        return result
    except Exception as e:
        logger.error(f"Error fetching agent config for ID '{agent_id}': {e}", exc_info=True)
        return None


async def db_get_all_agents(db: AsyncSession, skip: int = 0, limit: int = 100) -> List[AgentConfigDB]:
    """Retrieves a list of agent configurations from the database."""
    logger.debug(f"Fetching all agent configs from DB (skip={skip}, limit={limit})")
    stmt = select(AgentConfigDB).order_by(AgentConfigDB.created_at.desc()).offset(skip).limit(limit) # Добавим сортировку
    result = await db.execute(stmt)
    agents = result.scalars().all()
    logger.debug(f"Fetched {len(agents)} agent configs from DB")
    return agents

async def db_delete_agent_config(db: AsyncSession, agent_id: str) -> bool:
    """Deletes an agent configuration from the database by ID."""
    logger.info(f"Deleting agent config from DB for agent_id: {agent_id}")
    db_agent = await db.get(AgentConfigDB, agent_id)
    if db_agent:
        await db.delete(db_agent)
        await db.commit()
        logger.info(f"Agent config deleted successfully for agent_id: {agent_id}")
        return True
    else:
        logger.warning(f"Agent config not found for deletion, agent_id: {agent_id}")
        return False


async def db_update_agent_config(db: AsyncSession, agent_id: str, agent_config: AgentConfigInput) -> Optional[AgentConfigDB]:
    """Updates an existing agent configuration in the database."""
    logger.info(f"Updating agent config in DB for agent_id: {agent_id}")
    # Используем select().where() и scalars().first() для получения объекта ORM
    stmt = select(AgentConfigDB).where(AgentConfigDB.id == agent_id)
    result = await db.execute(stmt)
    db_agent = result.scalars().first() # Получаем экземпляр AgentConfigDB или None

    if not db_agent:
        logger.warning(f"Agent config not found for update, agent_id: {agent_id}")
        return None

    # Обновляем поля
    db_agent.name = agent_config.name
    db_agent.description = agent_config.description
    # --- ИЗМЕНЕНИЕ: Маппинг userId на owner_id ---
    db_agent.owner_id = agent_config.userId
    # --- КОНЕЦ ИЗМЕНЕНИЯ ---
    # Преобразуем Pydantic config в JSON (словарь) для хранения
    db_agent.config_json = agent_config.config_json # Обновляем JSON как есть

    try:
        await db.commit()
        await db.refresh(db_agent)
        logger.info(f"Agent config updated successfully for agent_id: {agent_id}")
        return db_agent
    except Exception as e:
        await db.rollback()
        logger.error(f"Error updating agent config for agent_id {agent_id}: {e}", exc_info=True)
        raise # Передаем исключение дальше


# --- НОВОЕ: CRUD для истории чатов ---

async def db_add_chat_message(
    db: AsyncSession,
    agent_id: str,
    thread_id: str,
    sender_type: SenderType, # Используем Enum
    content: str,
    channel: Optional[str],
    timestamp: datetime # Принимаем timestamp
) -> ChatMessageDB:
    """Adds a new chat message to the database."""
    logger.debug(f"Adding chat message to DB: Agent={agent_id}, Thread={thread_id}, Sender={sender_type}")
    db_message = ChatMessageDB(
        agent_id=agent_id,
        thread_id=thread_id,
        sender_type=sender_type,
        content=content,
        channel=channel,
        timestamp=timestamp # Сохраняем переданный timestamp
    )
    db.add(db_message)
    try:
        await db.commit()
        await db.refresh(db_message)
        logger.debug(f"Chat message added successfully (ID: {db_message.id})")
        return db_message
    except Exception as e:
        await db.rollback()
        logger.error(f"Error adding chat message for Agent={agent_id}, Thread={thread_id}: {e}", exc_info=True)
        raise # Передаем исключение дальше


async def db_get_chat_history(db: AsyncSession, agent_id: str, thread_id: str, skip: int = 0, limit: int = 100) -> List[ChatMessageDB]:
    """Retrieves chat history for a specific agent and thread, ordered by timestamp."""
    logger.debug(f"Fetching chat history for Agent={agent_id}, Thread={thread_id} (skip={skip}, limit={limit})")
    stmt = (
        select(ChatMessageDB)
        .where(ChatMessageDB.agent_id == agent_id, ChatMessageDB.thread_id == thread_id)
        .order_by(ChatMessageDB.timestamp.asc()) # Сортируем по возрастанию времени
        .offset(skip)
        .limit(limit)
    )
    result = await db.execute(stmt)
    messages = result.scalars().all()
    logger.debug(f"Fetched {len(messages)} messages for Agent={agent_id}, Thread={thread_id}")
    return messages

# --- НОВОЕ: Функция для получения недавней истории для контекста ---
async def db_get_recent_chat_history(db: AsyncSession, agent_id: str, thread_id: str, limit: int) -> List[ChatMessageDB]:
    """
    Retrieves the most recent 'limit' chat messages for a specific agent and thread,
    ordered by timestamp ascending (oldest of the recent first).
    """
    logger.debug(f"Fetching recent chat history for context: Agent={agent_id}, Thread={thread_id}, Limit={limit}")
    # Сначала получаем ID последних 'limit' сообщений, отсортированных по убыванию
    subquery = (
        select(ChatMessageDB.id)
        .where(ChatMessageDB.agent_id == agent_id, ChatMessageDB.thread_id == thread_id)
        .order_by(ChatMessageDB.timestamp.desc())
        .limit(limit)
        .subquery()
    )
    # Затем выбираем полные сообщения с этими ID и сортируем их по возрастанию
    stmt = (
        select(ChatMessageDB)
        .join(subquery, ChatMessageDB.id == subquery.c.id)
        .order_by(ChatMessageDB.timestamp.asc()) # Сортируем по возрастанию для правильного порядка в контексте
    )
    try:
        result = await db.execute(stmt)
        messages = result.scalars().all()
        logger.debug(f"Fetched {len(messages)} recent messages for context: Agent={agent_id}, Thread={thread_id}")
        return messages
    except Exception as e:
        logger.error(f"Error fetching recent chat history for context: Agent={agent_id}, Thread={thread_id}: {e}", exc_info=True)
        return [] # Возвращаем пустой список в случае ошибки
# --- КОНЕЦ НОВОГО ---


# --- ИЗМЕНЕНИЕ: Добавляем параметр channel ---
async def db_get_agent_chats(db: AsyncSession, agent_id: str, skip: int = 0, limit: int = 100, channel: Optional[str] = None) -> List[ChatListItemOutput]:
# --- КОНЕЦ ИЗМЕНЕНИЯ ---
    """
    Retrieves a list of unique threads for an agent with details of the first and last messages.
    Allows filtering by the channel of the last message.
    """
    # --- ИЗМЕНЕНИЕ: Обновляем лог ---
    logger.debug(f"Fetching chat list for Agent={agent_id} (skip={skip}, limit={limit}, channel={channel})")
    # --- КОНЕЦ ИЗМЕНЕНИЯ ---

    # --- ИЗМЕНЕНИЕ: Подзапрос для последнего сообщения ---
    last_message_subquery_base = (
        select(
            ChatMessageDB.thread_id,
            ChatMessageDB.content.label("last_message_content"),
            ChatMessageDB.timestamp.label("last_message_timestamp"),
            ChatMessageDB.sender_type.label("last_message_sender_type"),
            ChatMessageDB.channel.label("last_message_channel"),
            sql_func.count(ChatMessageDB.id).over(partition_by=ChatMessageDB.thread_id).label("message_count"), # Используем sql_func
            sql_func.row_number().over(partition_by=ChatMessageDB.thread_id, order_by=ChatMessageDB.timestamp.desc()).label("rn_desc") # Используем sql_func
        )
        .where(ChatMessageDB.agent_id == agent_id)
    )
    # --- ИЗМЕНЕНИЕ: Добавляем фильтр по channel, если он задан ---
    if channel:
        last_message_subquery_base = last_message_subquery_base.where(ChatMessageDB.channel == channel)
    # --- КОНЕЦ ИЗМЕНЕНИЯ ---
    last_message_subquery = last_message_subquery_base.subquery('last_ranked_messages')
    # --- КОНЕЦ ИЗМЕНЕНИЯ ---

    # --- НОВОЕ: Подзапрос для первого сообщения ---
    first_message_subquery = (
        select(
            ChatMessageDB.thread_id,
            ChatMessageDB.content.label("first_message_content"),
            ChatMessageDB.timestamp.label("first_message_timestamp"),
            sql_func.row_number().over(partition_by=ChatMessageDB.thread_id, order_by=ChatMessageDB.timestamp.asc()).label("rn_asc") # Используем sql_func
        )
        .where(ChatMessageDB.agent_id == agent_id)
        # --- ИЗМЕНЕНИЕ: Добавляем фильтр по channel и сюда, чтобы не терять треды, где первое сообщение не совпадает по каналу ---
        # Если фильтр по каналу активен, убедимся, что первое сообщение тоже из этого канала,
        # ИЛИ что это действительно первое сообщение треда (rn_asc=1), чтобы не отфильтровать тред целиком,
        # если только последнее сообщение совпадает с каналом.
        # Однако, логика требует фильтровать ТРЕДЫ по ПОСЛЕДНЕМУ сообщению.
        # Поэтому фильтр по каналу в first_message_subquery не нужен.
        # Оставляем как было.
        # --- КОНЕЦ ИЗМЕНЕНИЯ ---
        .subquery('first_ranked_messages')
    )
    # --- КОНЕЦ НОВОГО ---

    # --- ИЗМЕНЕНИЕ: Основной запрос с JOIN ---
    stmt = (
        select(
            last_message_subquery.c.thread_id,
            first_message_subquery.c.first_message_content, # Из подзапроса первого сообщения
            first_message_subquery.c.first_message_timestamp, # Из подзапроса первого сообщения
            last_message_subquery.c.last_message_content,
            last_message_subquery.c.last_message_timestamp,
            last_message_subquery.c.last_message_sender_type,
            last_message_subquery.c.last_message_channel,
            last_message_subquery.c.message_count
        )
        # Соединяем подзапросы по thread_id
        .join(
            first_message_subquery,
            last_message_subquery.c.thread_id == first_message_subquery.c.thread_id
        )
        # Выбираем только последние (rn_desc=1) и первые (rn_asc=1) строки для каждого треда
        .where(last_message_subquery.c.rn_desc == 1)
        .where(first_message_subquery.c.rn_asc == 1)
        # --- ИЗМЕНЕНИЕ: Добавляем фильтр по каналу из подзапроса последнего сообщения ---
        # Фильтр по каналу уже применен внутри last_message_subquery_base,
        # поэтому здесь дополнительное условие не требуется, если last_message_subquery используется.
        # Если бы фильтр не был применен в подзапросе, его нужно было бы добавить здесь:
        # if channel:
        #     stmt = stmt.where(last_message_subquery.c.last_message_channel == channel)
        # --- КОНЕЦ ИЗМЕНЕНИЯ ---
        .order_by(last_message_subquery.c.last_message_timestamp.desc()) # Сортируем треды по времени последнего сообщения
        .offset(skip)
        .limit(limit)
    )
    # --- КОНЕЦ ИЗМЕНЕНИЯ ---

    result = await db.execute(stmt)
    # Преобразуем результат в список Pydantic моделей
    chat_list = [
        ChatListItemOutput(
            thread_id=row.thread_id,
            # --- ИЗМЕНЕНИЕ: Добавляем поля первого сообщения ---
            first_message_content=row.first_message_content,
            first_message_timestamp=row.first_message_timestamp,
            # --- КОНЕЦ ИЗМЕНЕНИЯ ---
            last_message_content=row.last_message_content,
            last_message_timestamp=row.last_message_timestamp,
            last_message_sender_type=row.last_message_sender_type,
            last_message_channel=row.last_message_channel,
            message_count=row.message_count
        ) for row in result.mappings().all()
    ]
    # --- ИЗМЕНЕНИЕ: Обновляем лог ---
    logger.debug(f"Fetched {len(chat_list)} chat threads for Agent={agent_id} matching criteria (channel={channel})")
    # --- КОНЕЦ ИЗМЕНЕНИЯ ---
    return chat_list


# --- КОНЕЦ НОВОГО ---

# --- НОВОЕ: CRUD для удаления треда ---

async def db_delete_chat_thread(db: AsyncSession, agent_id: str, thread_id: str) -> int:
    """Deletes all chat messages for a specific agent and thread ID."""
    logger.info(f"Deleting chat thread for Agent={agent_id}, Thread={thread_id}")
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
    except Exception as e:
        await db.rollback()
        logger.error(f"Error deleting chat thread for Agent={agent_id}, Thread={thread_id}: {e}", exc_info=True)
        raise # Передаем исключение дальше

# --- КОНЕЦ НОВОГО ---

# --- НОВОЕ: CRUD операции для пользователей ---

async def get_user_by_platform_id(db: AsyncSession, platform: str, platform_user_id: str) -> Optional[UserDB]:
    """Retrieves a user by their platform and platform-specific ID."""
    try:
        result = await db.execute(
            select(UserDB).where(
                UserDB.platform == platform,
                UserDB.platform_user_id == platform_user_id
            )
        )
        return result.scalar_one_or_none()
    except Exception as e:
        logger.error(f"Error getting user by platform ID ({platform}/{platform_user_id}): {e}", exc_info=True)
        return None

async def create_or_update_user(
    db: AsyncSession,
    platform: str,
    platform_user_id: str,
    user_details: Dict[str, Any]
) -> Optional[UserDB]:
    """
    Creates a new user or updates an existing one based on platform and platform_user_id.
    Authorization status is NOT handled here anymore.

    Args:
        db: The database session.
        platform: The platform identifier (e.g., 'telegram').
        platform_user_id: The user's ID on that platform.
        user_details: A dictionary containing user fields to create/update
                      (e.g., 'username', 'first_name', 'last_name', 'phone_number').

    Returns:
        The created or updated UserDB object, or None if an error occurred.
    """
    try:
        # Check if user exists
        existing_user = await get_user_by_platform_id(db, platform, platform_user_id)

        if existing_user:
            # Update existing user
            # Filter out None values and 'is_authorized' from user_details
            update_data = {k: v for k, v in user_details.items() if v is not None and k != 'is_authorized'}
            # Ensure updated_at is updated
            update_data['updated_at'] = datetime.now(timezone.utc)

            if update_data: # Only update if there's something to update
                await db.execute(
                    update(UserDB)
                    .where(UserDB.id == existing_user.id)
                    .values(**update_data)
                )
                # Refresh the object to get updated values (including updated_at)
                await db.refresh(existing_user, attribute_names=list(update_data.keys()))
                logger.info(f"Updated user: Platform={platform}, PlatformID={platform_user_id}, DB_ID={existing_user.id}")
                await db.commit() # Commit after successful update
            else:
                logger.debug(f"No fields to update for user: Platform={platform}, PlatformID={platform_user_id}")
            return existing_user
        else:
            # Create new user
            new_user = UserDB(
                platform=platform,
                platform_user_id=platform_user_id,
                username=user_details.get('username'),
                first_name=user_details.get('first_name'),
                last_name=user_details.get('last_name'),
                phone_number=user_details.get('phone_number'),
                # --- ИЗМЕНЕНИЕ: is_authorized больше не устанавливается здесь ---
                # is_authorized=user_details.get('is_authorized', False)
                # --- КОНЕЦ ИЗМЕНЕНИЯ ---
            )
            db.add(new_user)
            await db.flush() # Assigns ID to new_user
            await db.refresh(new_user) # Load all attributes including defaults
            logger.info(f"Created new user: Platform={platform}, PlatformID={platform_user_id}, DB_ID={new_user.id}")
            await db.commit() # Commit after successful creation
            return new_user

    except Exception as e:
        logger.error(f"Error creating/updating user ({platform}/{platform_user_id}): {e}", exc_info=True)
        await db.rollback() # Rollback on error
        return None

# --- КОНЕЦ НОВОГО ---

# --- НОВОЕ: Функция для получения пользователей с фильтрами ---
async def db_get_users(
    db: AsyncSession,
    platform: Optional[str] = None,
    # --- ИЗМЕНЕНИЕ: Удаляем is_authorized ---
    # is_authorized: Optional[bool] = None,
    # --- КОНЕЦ ИЗМЕНЕНИЯ ---
    search_query: Optional[str] = None, # Для поиска по имени, username, телефону
    skip: int = 0,
    limit: int = 100
) -> List[UserDB]:
    """
    Retrieves a list of users from the database with optional filtering and pagination.
    Does not filter by authorization status anymore.
    """
    # --- ИЗМЕНЕНИЕ: Обновляем лог ---
    logger.debug(f"Fetching users with filters: platform={platform}, search='{search_query}', skip={skip}, limit={limit}")
    # --- КОНЕЦ ИЗМЕНЕНИЯ ---
    try:
        stmt = select(UserDB)

        if platform:
            stmt = stmt.where(UserDB.platform == platform)
        # --- ИЗМЕНЕНИЕ: Удаляем фильтр is_authorized ---
        # if is_authorized is not None:
        #     stmt = stmt.where(UserDB.is_authorized == is_authorized)
        # --- КОНЕЦ ИЗМЕНЕНИЯ ---
        if search_query:
            # Ищем по части строки в нескольких полях
            search_ilike = f"%{search_query}%"
            # --- ИЗМЕНЕНИЕ: Используем or_ ---
            stmt = stmt.where(
                or_(
                    UserDB.platform_user_id.ilike(search_ilike),
                    UserDB.phone_number.ilike(search_ilike),
                    UserDB.first_name.ilike(search_ilike), # <--- Строка 424
                    UserDB.last_name.ilike(search_ilike),
                    UserDB.username.ilike(search_ilike)
                )
            )
            # --- КОНЕЦ ИЗМЕНЕНИЯ ---

        stmt = stmt.order_by(desc(UserDB.updated_at)).offset(skip).limit(limit)

        result = await db.execute(stmt)
        users = result.scalars().all()
        logger.debug(f"Found {len(users)} users matching criteria.")
        return users
    except SQLAlchemyError as e:
        logger.error(f"Database error fetching users: {e}", exc_info=True)
        raise # Передаем исключение выше для обработки в API
    except Exception as e:
        logger.error(f"Unexpected error fetching users: {e}", exc_info=True)
        raise
# --- КОНЕЦ НОВОГО ---

# --- НОВОЕ: CRUD для AgentUserAuthorization ---

async def db_get_agent_authorization(db: AsyncSession, agent_id: str, user_id: int) -> Optional[AgentUserAuthorizationDB]:
    """Retrieves the authorization status for a specific user and agent."""
    logger.debug(f"Fetching authorization for Agent={agent_id}, UserID={user_id}")
    try:
        stmt = select(AgentUserAuthorizationDB).where(
            AgentUserAuthorizationDB.agent_id == agent_id,
            AgentUserAuthorizationDB.user_id == user_id
        )
        result = await db.execute(stmt)
        auth_record = result.scalar_one_or_none()
        if auth_record:
            logger.debug(f"Authorization found for Agent={agent_id}, UserID={user_id}: is_authorized={auth_record.is_authorized}")
        else:
            logger.debug(f"Authorization NOT found for Agent={agent_id}, UserID={user_id}")
        return auth_record
    except Exception as e:
        logger.error(f"Error fetching authorization for Agent={agent_id}, UserID={user_id}: {e}", exc_info=True)
        return None

async def db_create_or_update_agent_authorization(
    db: AsyncSession,
    agent_id: str,
    user_id: int,
    is_authorized: bool
) -> Optional[AgentUserAuthorizationDB]:
    """Creates or updates the authorization status for a user and agent."""
    logger.debug(f"Setting authorization for Agent={agent_id}, UserID={user_id} to {is_authorized}")
    try:
        existing_auth = await db_get_agent_authorization(db, agent_id, user_id)

        if existing_auth:
            # Update existing authorization
            if existing_auth.is_authorized != is_authorized:
                existing_auth.is_authorized = is_authorized
                existing_auth.updated_at = datetime.now(timezone.utc)
                await db.commit()
                await db.refresh(existing_auth)
                logger.info(f"Updated authorization for Agent={agent_id}, UserID={user_id} to {is_authorized}")
            else:
                logger.debug(f"Authorization already set to {is_authorized} for Agent={agent_id}, UserID={user_id}. No update needed.")
            return existing_auth
        else:
            # Create new authorization record
            new_auth = AgentUserAuthorizationDB(
                agent_id=agent_id,
                user_id=user_id,
                is_authorized=is_authorized
            )
            db.add(new_auth)
            await db.flush() # Assigns ID
            await db.refresh(new_auth)
            await db.commit()
            logger.info(f"Created authorization for Agent={agent_id}, UserID={user_id} with status {is_authorized}")
            return new_auth
    except Exception as e:
        logger.error(f"Error creating/updating authorization for Agent={agent_id}, UserID={user_id}: {e}", exc_info=True)
        await db.rollback()
        return None

async def db_get_users_for_agent(
    db: AsyncSession,
    agent_id: str,
    skip: int = 0,
    limit: int = 100
) -> List[UserDB]:
    """Retrieves users who have interacted with (or are authorized for) a specific agent."""
    logger.debug(f"Fetching users for Agent={agent_id} (skip={skip}, limit={limit})")
    try:
        # Выбираем уникальные user_id из AgentUserAuthorizationDB для данного agent_id
        subquery = select(AgentUserAuthorizationDB.user_id).where(
            AgentUserAuthorizationDB.agent_id == agent_id
        ).distinct().subquery()

        # Выбираем пользователей, чьи ID есть в результатах подзапроса
        stmt = select(UserDB).join(
            subquery, UserDB.id == subquery.c.user_id
        ).order_by(desc(UserDB.updated_at)).offset(skip).limit(limit)

        result = await db.execute(stmt)
        users = result.scalars().all()
        logger.debug(f"Found {len(users)} users associated with Agent={agent_id}")
        return users
    except SQLAlchemyError as e:
        logger.error(f"Database error fetching users for agent {agent_id}: {e}", exc_info=True)
        raise
    except Exception as e:
        logger.error(f"Unexpected error fetching users for agent {agent_id}: {e}", exc_info=True)
        raise

# --- КОНЕЦ НОВОГО ---
