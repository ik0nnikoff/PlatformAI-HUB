import logging
from typing import List, Optional
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, desc, func, label, and_, text, literal_column, delete # Добавляем импорт delete
from sqlalchemy.orm import aliased
from datetime import datetime # Импортируем datetime

from .models import AgentConfigInput, AgentConfigDB, ChatMessageDB, ChatListItemOutput, SenderType # Импортируем SenderType

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
        user_id=agent_config.userId,
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
    db_agent.user_id = agent_config.userId # Убедитесь, что userId обновляется
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


async def db_get_agent_chats(db: AsyncSession, agent_id: str, skip: int = 0, limit: int = 100) -> List[ChatListItemOutput]:
    """
    Retrieves a list of unique threads for an agent with details of the first and last messages.
    """
    logger.debug(f"Fetching chat list for Agent={agent_id} (skip={skip}, limit={limit})")

    # --- ИЗМЕНЕНИЕ: Подзапрос для последнего сообщения ---
    last_message_subquery = (
        select(
            ChatMessageDB.thread_id,
            ChatMessageDB.content.label("last_message_content"),
            ChatMessageDB.timestamp.label("last_message_timestamp"),
            ChatMessageDB.sender_type.label("last_message_sender_type"),
            ChatMessageDB.channel.label("last_message_channel"),
            func.count(ChatMessageDB.id).over(partition_by=ChatMessageDB.thread_id).label("message_count"),
            func.row_number().over(partition_by=ChatMessageDB.thread_id, order_by=ChatMessageDB.timestamp.desc()).label("rn_desc")
        )
        .where(ChatMessageDB.agent_id == agent_id)
        .subquery('last_ranked_messages')
    )
    # --- КОНЕЦ ИЗМЕНЕНИЯ ---

    # --- НОВОЕ: Подзапрос для первого сообщения ---
    first_message_subquery = (
        select(
            ChatMessageDB.thread_id,
            ChatMessageDB.content.label("first_message_content"),
            ChatMessageDB.timestamp.label("first_message_timestamp"),
            func.row_number().over(partition_by=ChatMessageDB.thread_id, order_by=ChatMessageDB.timestamp.asc()).label("rn_asc")
        )
        .where(ChatMessageDB.agent_id == agent_id)
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
    logger.debug(f"Fetched {len(chat_list)} chat threads for Agent={agent_id}")
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
