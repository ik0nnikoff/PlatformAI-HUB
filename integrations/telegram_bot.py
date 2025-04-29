import os
import logging
import asyncio
import json
import argparse
# --- НОВОЕ: Импорты для БД ---
from typing import AsyncGenerator, Optional, Dict, Any # Добавляем типы
# --- УДАЛЕНО: Убираем импорт Depends ---
# from fastapi import Depends
# --- КОНЕЦ УДАЛЕНИЯ ---
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
# --- КОНЕЦ НОВОГО ---
from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart, Command
from aiogram.types import Message, ReplyKeyboardRemove, ReplyKeyboardMarkup, KeyboardButton
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode, ChatAction
from dotenv import load_dotenv
import redis.asyncio as redis
from redis import exceptions as redis_exceptions
from contextlib import asynccontextmanager
from aiogram.exceptions import TelegramBadRequest # Импортируем исключение
# --- НОВОЕ: Импорты из agent_manager ---
try:
    from agent_manager import crud
    from agent_manager.models import UserDB # Импортируем модель UserDB
except ImportError:
    crud = None
    UserDB = None
    logging.critical("Could not import 'crud' or 'UserDB' from 'agent_manager'. Database features will be disabled.")
# --- КОНЕЦ НОВОГО ---


# --- Configuration & Setup ---
load_dotenv(dotenv_path=os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '.env')))

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379") # Оставляем для Redis
# --- НОВОЕ: Конфигурация БД и кэша ---
DATABASE_URL = os.getenv("DATABASE_URL")
REDIS_USER_CACHE_TTL = int(os.getenv("REDIS_USER_CACHE_TTL", 3600)) # 1 час по умолчанию
USER_CACHE_PREFIX = "user_cache:"
# --- КОНЕЦ НОВОГО ---

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

bot: Bot = None
# Initialize Dispatcher at the module level
dp: Dispatcher = Dispatcher()
redis_client: redis.Redis = None
redis_listener_task: asyncio.Task = None
agent_id_global: str = None # Store agent_id globally for handlers

# In-memory storage for authorized users (replace with DB/Redis later if needed)
authorized_users = {}
AUTH_TRIGGER = "AUTH_REQUIRED"

# --- НОВОЕ: Настройка SQLAlchemy ---
engine = None
SessionLocal: Optional[async_sessionmaker[AsyncSession]] = None

if DATABASE_URL and crud and UserDB:
    try:
        engine = create_async_engine(DATABASE_URL, echo=False, future=True)
        SessionLocal = async_sessionmaker(
            bind=engine,
            class_=AsyncSession,
            expire_on_commit=False,
            autoflush=False,
            autocommit=False
        )
        logger.info("SQLAlchemy async engine and session maker configured for bot.")
    except Exception as e:
        logger.error(f"Failed to configure SQLAlchemy for bot: {e}", exc_info=True)
        engine = None
        SessionLocal = None
else:
    logger.warning("Database URL not set or CRUD/UserDB not imported. Database features disabled.")

# --- УДАЛЕНО: Функция get_db_session больше не нужна ---
# --- КОНЕЦ НОВОГО ---


# --- Helper Functions ---
def request_contact_markup() -> ReplyKeyboardMarkup:
    """Creates the keyboard markup to request user contact."""
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text="Поделиться контактом", request_contact=True)]],
        resize_keyboard=True,
        one_time_keyboard=True # Hide after use
    )

async def send_typing_periodically(chat_id: int):
    """Sends typing action periodically until cancelled."""
    try:
        while True:
            await bot.send_chat_action(chat_id, action=ChatAction.TYPING)
            await asyncio.sleep(4) # Send typing action every 4 seconds
    except asyncio.CancelledError:
        logger.debug(f"Typing simulation cancelled for chat {chat_id}")
    except Exception as e:
        logger.error(f"Error in typing simulation for chat {chat_id}: {e}")

# --- ИЗМЕНЕНИЕ: Добавляем platform_user_id в аргументы и payload ---
async def publish_to_agent(agent_id: str, chat_id: int, platform_user_id: str, user_message: str, user_data: dict):
# --- КОНЕЦ ИЗМЕНЕНИЯ ---
    """Publishes a message to the agent's Redis input channel."""
    if not redis_client:
        logger.error("Redis client not available for publishing.")
        await bot.send_message(chat_id, "Ошибка: Не удалось подключиться к сервису агента.")
        return

    input_channel = f"agent:{agent_id}:input"
    payload = {
        "message": user_message,
        "thread_id": str(chat_id), # Ensure thread_id is string
        "platform_user_id": platform_user_id, # <--- Добавлено
        "user_data": user_data,
        "channel": "telegram"
    }
    try:
        await redis_client.publish(input_channel, json.dumps(payload))
        logger.info(f"Published message to {input_channel} for chat {chat_id}")
    except redis_exceptions.ConnectionError as e:
        logger.error(f"Redis connection error publishing to {input_channel}: {e}")
        await bot.send_message(chat_id, "Ошибка: Не удалось отправить сообщение агенту (проблема с соединением).")
    except Exception as e:
        logger.error(f"Error publishing to {input_channel}: {e}", exc_info=True)
        await bot.send_message(chat_id, "Ошибка: Не удалось отправить сообщение агенту.")

# --- НОВОЕ: Helper для проверки авторизации ---
async def check_user_authorization(platform_user_id: str) -> bool:
    """Checks user authorization status, using cache first, then DB."""
    if not redis_client:
        logger.error("Cannot check authorization: Redis client not available.")
        return False # Assume not authorized if Redis is down

    cache_key = f"{USER_CACHE_PREFIX}telegram:{platform_user_id}"
    try:
        logger.debug(f"Authorization check: Attempting to get cache for key '{cache_key}'")
        cached_data_raw = await redis_client.get(cache_key)
        if cached_data_raw:
            user_data = json.loads(cached_data_raw)
            is_auth = user_data.get('is_authorized', False)
            logger.debug(f"Authorization check cache hit for {platform_user_id}: {is_auth}")
            return is_auth
        else:
            logger.debug(f"Authorization check cache miss for {platform_user_id}. Checking DB.")
            # Cache miss, check DB
            if not SessionLocal:
                logger.error("Cannot check authorization in DB: SessionLocal not configured.")
                return False # Assume not authorized if DB is not configured

            async with SessionLocal() as session:
                logger.debug(f"Authorization check: Attempting to get user from DB for platform='telegram', platform_user_id='{platform_user_id}'")
                user = await crud.get_user_by_platform_id(session, 'telegram', platform_user_id)
                if user:
                    is_auth = user.is_authorized
                    # Cache the result from DB
                    cache_data = {
                        'db_id': user.id,
                        'is_authorized': user.is_authorized,
                        'first_name': user.first_name,
                        'last_name': user.last_name,
                        'phone_number': user.phone_number
                    }
                    logger.debug(f"Authorization check: Attempting to set cache for key '{cache_key}' with TTL {REDIS_USER_CACHE_TTL}")
                    await redis_client.set(cache_key, json.dumps(cache_data), ex=REDIS_USER_CACHE_TTL)
                    logger.debug(f"Authorization check DB hit for {platform_user_id}: {is_auth}. Cached.")
                    return is_auth
                else:
                    # User not found in DB, cache this status
                    logger.debug(f"Authorization check: Attempting to set 'not found' cache for key '{cache_key}' with TTL {REDIS_USER_CACHE_TTL}")
                    cache_data = {'is_authenticated': False, 'is_authorized': False} # Use both for consistency
                    await redis_client.set(cache_key, json.dumps(cache_data), ex=REDIS_USER_CACHE_TTL)
                    logger.debug(f"Authorization check DB miss for {platform_user_id}. Cached 'not found'.")
                    return False

    except redis.RedisError as e:
        logger.error(f"Redis error during authorization check for {platform_user_id}: {e}")
        return False # Assume not authorized on Redis error
    except Exception as e:
        logger.error(f"Unexpected error during authorization check for {platform_user_id}: {e}", exc_info=True)
        return False # Assume not authorized on other errors
# --- КОНЕЦ НОВОГО ---


# --- Redis Listener ---
async def redis_output_listener(agent_id: str):
    """Listens to the agent's output channel and sends messages to Telegram."""
    global redis_client, bot
    output_channel = f"agent:{agent_id}:output"
    pubsub = None
    while True:
        try:
            if not redis_client:
                logger.warning("Redis client not available, listener cannot run.")
                await asyncio.sleep(10)
                continue

            if pubsub is None:
                pubsub = redis_client.pubsub()
                await pubsub.subscribe(output_channel)
                logger.info(f"Subscribed to Redis output channel: {output_channel}")

            message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
            if message and message.get("type") == "message":
                raw_data = message['data']
                try:
                    data = json.loads(raw_data)
                    response_channel = data.get("channel")

                    # --- НАЧАЛО ИЗМЕНЕНИЯ: Фильтрация по каналу и проверка AUTH_TRIGGER ---
                    if response_channel == "telegram":
                        chat_id = data.get("thread_id") # Предполагаем, что thread_id это chat_id
                        platform_user_id = data.get("platform_user_id") # <--- Извлекаем platform_user_id
                        response = data.get("response")
                        error = data.get("error")
                        auth_required = False # Флаг для запроса авторизации

                        if chat_id:
                            # Конвертируем chat_id в integer для aiogram
                            try:
                                chat_id_int = int(chat_id)
                            except (ValueError, TypeError):
                                logger.error(f"Invalid chat_id (thread_id) received from agent: {chat_id}")
                                continue # Пропускаем это сообщение

                            if error:
                                logger.error(f"Received error from agent for chat {chat_id_int}: {error}")
                                await bot.send_message(chat_id_int, f"Произошла ошибка: {error}")
                            elif response:
                                logger.info(f"Received response from agent for chat {chat_id_int}: {response[:100]}...")

                                # Проверяем наличие триггера авторизации
                                if AUTH_TRIGGER in response:
                                    auth_required = True
                                    response = response.replace(AUTH_TRIGGER, "").strip() # Убираем триггер из ответа

                                # Отправляем сообщение с учетом необходимости авторизации
                                # --- ИЗМЕНЕНИЕ: Проверяем авторизацию через helper ---
                                is_user_authorized = False # По умолчанию
                                if auth_required and platform_user_id:
                                    is_user_authorized = await check_user_authorization(platform_user_id)
                                    logger.debug(f"Checked authorization for {platform_user_id} due to AUTH_TRIGGER: {is_user_authorized}")

                                if auth_required and not is_user_authorized:
                                # --- КОНЕЦ ИЗМЕНЕНИЯ ---
                                    # Пользователь не авторизован, но агент требует авторизацию
                                    await bot.send_message(
                                        chat_id_int,
                                        f"{response}\n\nДля продолжения требуется авторизация. Используйте /login или кнопку ниже:",
                                        reply_markup=request_contact_markup()
                                    )
                                else:
                                    # Либо авторизация не требуется, либо пользователь уже авторизован
                                    # Отправляем обычный ответ (возможно, без триггера)
                                    await bot.send_message(chat_id_int, response)

                            else:
                                logger.warning(f"Received message from agent for chat {chat_id_int} without response or error: {data}")
                        else:
                            logger.warning(f"Received 'telegram' channel message from agent without thread_id (chat_id): {data}")
                    else:
                        # Игнорируем сообщения не для Telegram
                        logger.debug(f"Ignoring message from Redis for Telegram - channel mismatch (channel: {response_channel})")
                    # --- КОНЕЦ ИЗМЕНЕНИЯ ---

                except json.JSONDecodeError:
                    logger.error(f"Failed to decode JSON from Redis: {raw_data}")
                except TelegramBadRequest as e:
                    # Ловим специфичные ошибки Telegram, например 'chat not found'
                    logger.error(f"Telegram API error sending message to chat {chat_id}: {e}")
                except Exception as e:
                    logger.error(f"Error processing message from Redis: {e}", exc_info=True)
                    # Опционально отправить ошибку в default chat_id, если доступно

        except redis_exceptions.ConnectionError as e: # Используем импортированное имя
            logger.error(f"Redis connection error in listener: {e}. Retrying...")
            if pubsub:
                try:
                    await pubsub.unsubscribe(output_channel)
                    await pubsub.aclose()
                except Exception: pass
                pubsub = None
            await asyncio.sleep(5)
        except asyncio.CancelledError:
            logger.info("Redis listener task cancelled.")
            break
        except Exception as e:
            logger.error(f"Unexpected error in Redis listener: {e}", exc_info=True)
            await asyncio.sleep(5) # Avoid tight loop on unexpected errors
    # Cleanup pubsub on exit
    if pubsub:
        try:
            await pubsub.unsubscribe(output_channel)
            await pubsub.aclose()
            logger.info("Unsubscribed from Redis output channel.")
        except Exception as clean_e:
            logger.error(f"Error closing Redis pubsub in listener: {clean_e}")

# --- Aiogram Handlers ---
@dp.message(CommandStart())
async def start(message: Message):
    """Handles the /start command."""
    await message.answer("Привет! Задайте мне вопрос.")

@dp.message(Command("login"))
async def login_command(message: Message):
    """Handles the /login command, prompting for contact sharing."""
    await message.answer(
        "Для авторизации, пожалуйста, поделитесь своим контактом:",
        reply_markup=request_contact_markup()
    )

# --- ИЗМЕНЕНИЕ: Убираем зависимость Depends, создаем сессию вручную ---
@dp.message(F.contact)
async def handle_contact(message: Message):
# --- КОНЕЦ ИЗМЕНЕНИЯ ---
    """Handles receiving user contact information for authorization."""
    user_id = message.from_user.id
    contact = message.contact
    chat_id = message.chat.id
    platform_user_id = str(contact.user_id) # Используем ID из контакта как platform_user_id

    if contact.user_id == user_id:
        # --- ИЗМЕНЕНИЕ: Логика сохранения в БД и кэширования ---
        # --- ИЗМЕНЕНИЕ: Проверяем SessionLocal и создаем сессию ---
        if not SessionLocal:
            logger.error(f"Cannot process contact for user {user_id}: Database SessionLocal not configured.")
            await message.answer("❌ Ошибка: Сервис базы данных недоступен. Попробуйте позже.")
            return
        # --- КОНЕЦ ИЗМЕНЕНИЯ ---

        user_details = {
            'phone_number': contact.phone_number,
            'first_name': contact.first_name,
            'last_name': contact.last_name,
            'username': message.from_user.username, # Добавляем username из from_user
            'is_authorized': True
        }

        db_user = None
        try:
            async with SessionLocal() as session: # Создаем сессию
                logger.debug(f"handle_contact: Attempting to create/update user in DB for platform='telegram', platform_user_id='{platform_user_id}'")
                db_user = await crud.create_or_update_user(
                    session, # Передаем созданную сессию
                    platform='telegram',
                    platform_user_id=platform_user_id,
                    user_details=user_details
                )
                # --- ИЗМЕНЕНИЕ: Переносим логику обработки результата внутрь try/async with ---
                if db_user:
                    logger.info(f"User {platform_user_id} authorized via contact. DB ID: {db_user.id}")

                    # Кэшируем данные пользователя
                    cache_key = f"{USER_CACHE_PREFIX}telegram:{platform_user_id}"
                    cache_data = {
                        'db_id': db_user.id,
                        'is_authorized': True,
                        'first_name': db_user.first_name,
                        'last_name': db_user.last_name,
                        'phone_number': db_user.phone_number
                    }
                    try:
                        if redis_client:
                            logger.debug(f"handle_contact: Attempting to set cache for key '{cache_key}' with TTL {REDIS_USER_CACHE_TTL}")
                            await redis_client.set(cache_key, json.dumps(cache_data), ex=REDIS_USER_CACHE_TTL)
                            logger.debug(f"Cached user data for {platform_user_id}")
                        else:
                            logger.warning(f"Redis client not available, cannot cache user data for {platform_user_id}")
                    except redis.RedisError as e:
                        logger.error(f"Failed to cache user data for {platform_user_id}: {e}")

                    await message.answer(
                        "✅ Авторизация прошла успешно!",
                        reply_markup=ReplyKeyboardRemove() # Remove the contact button
                    )

                    # Отправляем сообщение агенту (оставляем, как просил пользователь)
                    agent_user_data = {
                        "is_authenticated": True,
                        "user_id": platform_user_id,
                        "phone_number": db_user.phone_number,
                        "first_name": db_user.first_name,
                        "last_name": db_user.last_name
                    }
                    await publish_to_agent(
                        agent_id_global,
                        chat_id,
                        platform_user_id,
                        "Пользователь успешно авторизовался.",
                        agent_user_data
                    )
                else:
                    # Эта ветка выполнится, если create_or_update_user вернул None (ошибка внутри CRUD)
                    logger.error(f"Failed to save user authorization to DB for {platform_user_id} (CRUD returned None)")
                    await message.answer("❌ Ошибка: Не удалось сохранить данные авторизации. Попробуйте позже.")
                # --- КОНЕЦ ИЗМЕНЕНИЯ ---
        except Exception as e:
            logger.error(f"Error during contact handling DB operation for {platform_user_id}: {e}", exc_info=True)
            await message.answer("❌ Произошла внутренняя ошибка при обработке контакта.")
        # --- КОНЕЦ ИЗМЕНЕНИЯ ---
    else:
        await message.answer("❌ Пожалуйста, поделитесь *своим* контактом для авторизации.")

# --- ИЗМЕНЕНИЕ: Уточняем фильтр для текстовых сообщений ---
@dp.message(F.text) # Явно указываем, что ловим только текст
async def handle_text_message(message: Message):
# --- КОНЕЦ ИЗМЕНЕНИЯ ---
    """Handles regular text messages from the user."""
    chat_id = message.chat.id
    user_message = message.md_text
    platform_user_id = str(message.from_user.id) # Используем ID пользователя из сообщения

    if not user_message: # Ignore empty messages or non-text messages not handled elsewhere
        return

    typing_task = asyncio.create_task(send_typing_periodically(chat_id))

    # --- ИЗМЕНЕНИЕ: Получение user_data из кэша/БД ---
    try:
        user_data_for_agent = {"is_authenticated": False} # По умолчанию для агента
        cache_key = f"{USER_CACHE_PREFIX}telegram:{platform_user_id}"
        cached_data_raw = None

        if redis_client:
            try:
                logger.debug(f"handle_text_message: Attempting to get cache for key '{cache_key}'")
                cached_data_raw = await redis_client.get(cache_key)
            except redis.RedisError as e:
                logger.error(f"Redis error getting user cache for {platform_user_id}: {e}")

        if cached_data_raw:
            logger.debug(f"Cache hit for user {platform_user_id}")
            cached_data = json.loads(cached_data_raw)
            # Формируем данные для агента из кэша
            user_data_for_agent = {
                "is_authenticated": cached_data.get('is_authorized', False),
                "user_id": platform_user_id,
                "phone_number": cached_data.get('phone_number'),
                "first_name": cached_data.get('first_name'),
                "last_name": cached_data.get('last_name')
            }
        else:
            logger.debug(f"Cache miss for user {platform_user_id}. Checking DB.")
            # Cache miss, check DB
            # --- ИЗМЕНЕНИЕ: Проверяем SessionLocal и создаем сессию ---
            if not SessionLocal:
                logger.warning(f"DB SessionLocal not configured for user {platform_user_id}. Assuming not authenticated.")
            else:
                async with SessionLocal() as session: # Создаем сессию
                    logger.debug(f"handle_text_message: Attempting to get user from DB for platform='telegram', platform_user_id='{platform_user_id}'")
                    user = await crud.get_user_by_platform_id(session, 'telegram', platform_user_id) # Передаем сессию
                    if user:
                        logger.debug(f"DB hit for user {platform_user_id}. Caching.")
                        # Формируем данные для агента из БД
                        user_data_for_agent = {
                            "is_authenticated": user.is_authorized,
                            "user_id": platform_user_id,
                            "phone_number": user.phone_number,
                            "first_name": user.first_name,
                            "last_name": user.last_name
                        }
                        # Кэшируем данные из БД
                        cache_data_to_set = {
                            'db_id': user.id,
                            'is_authorized': user.is_authorized,
                            'first_name': user.first_name,
                            'last_name': user.last_name,
                            'phone_number': user.phone_number
                        }
                        if redis_client:
                            try:
                                logger.debug(f"handle_text_message: Attempting to set cache for key '{cache_key}' with TTL {REDIS_USER_CACHE_TTL}")
                                await redis_client.set(cache_key, json.dumps(cache_data_to_set), ex=REDIS_USER_CACHE_TTL)
                            except redis.RedisError as e:
                                logger.error(f"Redis error setting user cache for {platform_user_id}: {e}")
                    else:
                        logger.debug(f"DB miss for user {platform_user_id}. Caching 'not found'.")
                        # Пользователь не найден в БД, кэшируем этот статус
                        cache_data_to_set = {'is_authenticated': False, 'is_authorized': False}
                        if redis_client:
                            try:
                                logger.debug(f"handle_text_message: Attempting to set 'not found' cache for key '{cache_key}' with TTL {REDIS_USER_CACHE_TTL}")
                                await redis_client.set(cache_key, json.dumps(cache_data_to_set), ex=REDIS_USER_CACHE_TTL)
                            except redis.RedisError as e:
                                logger.error(f"Redis error setting 'not found' cache for {platform_user_id}: {e}")
                        # user_data_for_agent остается {"is_authenticated": False}
            # --- КОНЕЦ ИЗМЕНЕНИЯ ---

        # Убедимся, что is_authenticated всегда присутствует
        if "is_authenticated" not in user_data_for_agent:
            user_data_for_agent["is_authenticated"] = False

        await publish_to_agent(
            agent_id_global,
            chat_id,
            platform_user_id, # Передаем platform_user_id
            user_message,
            user_data_for_agent
        )
    # --- КОНЕЦ ИЗМЕНЕНИЯ ---
        # Don't wait for response here, the redis_output_listener handles it

    except Exception as e:
        logger.error(f"Error handling message from chat {chat_id}: {e}", exc_info=True)
        await message.answer("⚠ Произошла ошибка при обработке вашего запроса.")
    finally:
        # Stop the typing simulation *after* publishing the message
        # The listener will handle sending the actual response when it arrives
        await asyncio.sleep(1) # Keep typing briefly after sending
        typing_task.cancel()

# --- НОВОЕ: Обработчик для всех остальных типов сообщений ---
@dp.message()
async def handle_other_messages(message: Message):
    """Logs unhandled message types."""
    logger.warning(
        f"Received unhandled message type in chat {message.chat.id}. Message details: {message.model_dump_json(exclude_defaults=True)}"
    )
# --- КОНЕЦ НОВОГО ---

# --- Application Lifecycle ---
@asynccontextmanager
async def lifespan(dp: Dispatcher, agent_id: str, bot_token: str): # Добавляем bot_token
    """Manages application startup and shutdown, including DB engine.""" # Обновляем docstring
    global bot, redis_client, redis_listener_task, agent_id_global

    # --- ИСПРАВЛЕНИЕ: Используем переданный bot_token ---
    if not bot_token:
        logger.critical("Bot token was not provided to lifespan.")
        return # Не можем запустить бота без токена

    agent_id_global = agent_id
    # Инициализируем бота с переданным токеном
    bot = Bot(token=bot_token, default=DefaultBotProperties(parse_mode=ParseMode.MARKDOWN))
    logger.info("Bot initialized.")
    # --- Конец исправления ---

    try:
        redis_client = await redis.from_url(REDIS_URL, decode_responses=True)
        await redis_client.ping()
        logger.info("Connected to Redis successfully.")
    except Exception as e:
        logger.critical(f"Failed to connect to Redis at {REDIS_URL}: {e}", exc_info=True)
        redis_client = None

    if redis_client:
        redis_listener_task = asyncio.create_task(redis_output_listener(agent_id))
        logger.info(f"Redis listener task started for agent {agent_id}.")

    logger.info("Starting bot polling...")
    yield # Run the application
    logger.info("Shutting down bot...")

    if redis_listener_task and not redis_listener_task.done():
        redis_listener_task.cancel()
        try:
            await redis_listener_task
        except asyncio.CancelledError:
            logger.info("Redis listener task cancelled successfully.")
        except Exception as e:
            logger.error(f"Error during Redis listener task shutdown: {e}") # Ошибка здесь была из-за redis.asyncio.exceptions

    # --- НОВОЕ: Закрытие DB engine ---
    if engine:
        await engine.dispose()
        logger.info("Database engine disposed.")
    # --- КОНЕЦ НОВОГО ---
    if redis_client:
        await redis_client.aclose() # Используем aclose()
        logger.info("Redis connection closed.")

    await bot.session.close()
    logger.info("Bot session closed.")


# --- Main Execution ---
async def main(agent_id: str, bot_token: str): # Добавляем bot_token
    """Initializes and runs the bot."""
    # global dp # No longer needed to modify global dp here
    # dp = Dispatcher() # Remove initialization from here

    # Передаем bot_token в lifespan
    async with lifespan(dp, agent_id, bot_token):
        await dp.start_polling(bot)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Telegram Bot Integration for Configurable Agent")
    parser.add_argument("--agent-id", required=True, help="Unique ID of the agent this bot interacts with")
    parser.add_argument("--redis-url", required=True, help="URL for the Redis server")
    # --- ИСПРАВЛЕНИЕ: Добавляем аргумент для токена ---
    parser.add_argument("--bot-token", required=True, help="Telegram Bot Token")
    # --- Конец исправления ---
    args = parser.parse_args()

    REDIS_URL = args.redis_url

    try:
        # Передаем agent_id и bot_token в main
        asyncio.run(main(args.agent_id, args.bot_token))
    except (KeyboardInterrupt, SystemExit):
        logger.info("Bot stopped manually.")
    except Exception as e:
        logger.critical(f"Unhandled exception in main: {e}", exc_info=True)

