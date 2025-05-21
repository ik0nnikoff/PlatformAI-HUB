import os
import logging
import asyncio
import json
import argparse
from typing import AsyncGenerator, Optional, Dict, Any # Добавляем типы
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
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

try:
    from hub.agent_manager import crud
    # --- ИЗМЕНЕНИЕ: Импортируем AgentUserAuthorizationDB ---
    from hub.agent_manager.models import UserDB, AgentUserAuthorizationDB # Импортируем модели
    # --- КОНЕЦ ИЗМЕНЕНИЯ ---
except ImportError:
    crud = None
    UserDB = None
    # --- ИЗМЕНЕНИЕ: Добавляем AgentUserAuthorizationDB ---
    AgentUserAuthorizationDB = None
    # --- КОНЕЦ ИЗМЕНЕНИЯ ---
    logging.critical("Could not import 'crud' or models from 'agent_manager'. Database features will be disabled.")


# --- Configuration & Setup ---
load_dotenv(dotenv_path=os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '.env')))

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379") # Оставляем для Redis
DATABASE_URL = os.getenv("DATABASE_URL")
REDIS_USER_CACHE_TTL = int(os.getenv("REDIS_USER_CACHE_TTL", 3600)) # 1 час по умолчанию
USER_CACHE_PREFIX = "user_cache:"

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(name)s - %(message)s')
logger = logging.getLogger(__name__)

bot: Bot = None
# Initialize Dispatcher at the module level
dp: Dispatcher = Dispatcher()
redis_client: redis.Redis = None
redis_listener_task: asyncio.Task = None
agent_id_global: str = None # Store agent_id globally for handlers

# In-memory storage for authorized users (replace with DB/Redis later if needed)
# authorized_users = {} # Больше не используется
AUTH_TRIGGER = "AUTH_REQUIRED"

# --- НОВОЕ: Настройка SQLAlchemy ---
engine = None
SessionLocal: Optional[async_sessionmaker[AsyncSession]] = None

# --- ИЗМЕНЕНИЕ: Проверяем наличие всех нужных моделей ---
if DATABASE_URL and crud and UserDB and AgentUserAuthorizationDB:
# --- КОНЕЦ ИЗМЕНЕНИЯ ---
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
    logger.warning("Database URL not set or CRUD/Models not imported. Database features disabled.")


# --- Helper Functions ---

def request_contact_markup():
    """Creates a keyboard asking the user to share their contact."""
    button = KeyboardButton(text="Поделиться контактом", request_contact=True)
    keyboard = ReplyKeyboardMarkup(keyboard=[[button]], resize_keyboard=True, one_time_keyboard=True)
    return keyboard

async def send_typing_periodically(chat_id: int):
    """Sends 'typing' action periodically while the agent is processing."""
    while True:
        try:
            await bot.send_chat_action(chat_id, ChatAction.TYPING)
            await asyncio.sleep(4) # Send typing status every 4 seconds
        except asyncio.CancelledError:
            break # Exit loop when task is cancelled
        except Exception as e:
            logger.warning(f"Failed to send typing action to chat {chat_id}: {e}")
            await asyncio.sleep(10) # Wait longer on error

async def publish_to_agent(agent_id: str, chat_id: int, platform_user_id: str, message_text: str, user_data: dict):
    """Publishes a user message to the agent's input Redis channel."""
    global redis_client
    if not redis_client:
        logger.error("Cannot publish to agent: Redis client not available.")
        await bot.send_message(chat_id, "❌ Ошибка: Не удалось связаться с агентом (сервис недоступен).")
        return

    input_channel = f"agent:{agent_id}:input"
    payload = {
        "message": message_text,
        "thread_id": str(chat_id), # Use chat_id as thread_id for Telegram
        "platform_user_id": platform_user_id, # Include platform user ID
        "user_data": user_data, # Include user data (auth status, etc.)
        "channel": "telegram" # Indicate the source channel
    }
    try:
        await redis_client.publish(input_channel, json.dumps(payload))
        logger.info(f"Published message to {input_channel} for chat {chat_id}")
    except redis.RedisError as e:
        logger.error(f"Redis error publishing message to {input_channel}: {e}")
        await bot.send_message(chat_id, "❌ Ошибка: Не удалось отправить сообщение агенту.")
    except Exception as e:
        logger.error(f"Unexpected error publishing message to {input_channel}: {e}", exc_info=True)
        await bot.send_message(chat_id, "❌ Произошла внутренняя ошибка при отправке сообщения.")


async def check_user_authorization(agent_id: str, platform_user_id: str) -> bool:
    """Checks user authorization status for a specific agent, using cache first, then DB."""
    if not redis_client:
        logger.error("Cannot check authorization: Redis client not available.")
        return False # Assume not authorized if Redis is down

    cache_key = f"{USER_CACHE_PREFIX}telegram:{platform_user_id}:agent:{agent_id}"
    try:
        logger.debug(f"Authorization check: Attempting to get cache for key '{cache_key}'")
        cached_auth_status = await redis_client.get(cache_key)
        if cached_auth_status is not None:
            # Кэшируем только 'true' или 'false'
            is_auth = cached_auth_status == 'true'
            logger.debug(f"Authorization check cache hit for agent {agent_id}, user {platform_user_id}: {is_auth}")
            return is_auth
        else:
            logger.debug(f"Authorization check cache miss for agent {agent_id}, user {platform_user_id}. Checking DB.")
            # Cache miss, check DB
            if not SessionLocal:
                logger.error("Cannot check authorization in DB: SessionLocal not configured.")
                return False # Assume not authorized if DB is not configured

            async with SessionLocal() as session:
                logger.debug(f"Authorization check: Attempting to get user from DB for platform='telegram', platform_user_id='{platform_user_id}'")
                user = await crud.get_user_by_platform_id(session, 'telegram', platform_user_id)
                if user:
                    # Пользователь найден, проверяем авторизацию для агента
                    logger.debug(f"Authorization check: User found (ID: {user.id}). Checking authorization for agent {agent_id}.")
                    auth_record = await crud.db_get_agent_authorization(session, agent_id, user.id)
                    is_auth = auth_record.is_authorized if auth_record else False
                    logger.debug(f"Authorization check DB result for agent {agent_id}, user {user.id}: {is_auth}")

                    # Кэшируем результат (только статус авторизации)
                    cache_value = 'true' if is_auth else 'false'
                    logger.debug(f"Authorization check: Attempting to set cache for key '{cache_key}' with value '{cache_value}' and TTL {REDIS_USER_CACHE_TTL}")
                    await redis_client.set(cache_key, cache_value, ex=REDIS_USER_CACHE_TTL)
                    logger.debug(f"Authorization check DB hit for agent {agent_id}, user {platform_user_id}: {is_auth}. Cached.")
                    return is_auth
                else:
                    # Пользователь не найден в DB, кэшируем этот статус (False)
                    logger.debug(f"Authorization check: User not found in DB for platform_user_id='{platform_user_id}'. Caching 'false'.")
                    logger.debug(f"Authorization check: Attempting to set 'false' cache for key '{cache_key}' with TTL {REDIS_USER_CACHE_TTL}")
                    await redis_client.set(cache_key, 'false', ex=REDIS_USER_CACHE_TTL)
                    logger.debug(f"Authorization check DB miss for {platform_user_id}. Cached 'false'.")
                    return False

    except redis.RedisError as e:
        logger.error(f"Redis error during authorization check for agent {agent_id}, user {platform_user_id}: {e}")
        return False # Assume not authorized on Redis error
    except Exception as e:
        logger.error(f"Unexpected error during authorization check for agent {agent_id}, user {platform_user_id}: {e}", exc_info=True)
        return False # Assume not authorized on other errors


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
                                # --- ИЗМЕНЕНИЕ: Проверяем авторизацию через helper с agent_id ---
                                is_user_authorized = False # По умолчанию
                                if auth_required and platform_user_id:
                                    # Используем agent_id_global, так как listener слушает конкретного агента
                                    is_user_authorized = await check_user_authorization(agent_id_global, platform_user_id)
                                    logger.debug(f"Checked authorization for agent {agent_id_global}, user {platform_user_id} due to AUTH_TRIGGER: {is_user_authorized}")

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
                                    if response: # Убедимся, что есть что отправлять после удаления триггера
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

# --- ИЗМЕНЕНИЕ: Обновляем handle_contact ---
@dp.message(F.contact)
async def handle_contact(message: Message):
    """Handles receiving user contact information for authorization for the specific agent."""
    user_id = message.from_user.id
    contact = message.contact
    chat_id = message.chat.id
    platform_user_id = str(contact.user_id) # Используем ID из контакта как platform_user_id

    if contact.user_id == user_id:
        if not SessionLocal:
            logger.error(f"Cannot process contact for user {user_id}: Database SessionLocal not configured.")
            await message.answer("❌ Ошибка: Сервис базы данных недоступен. Попробуйте позже.")
            return

        user_details = {
            'phone_number': contact.phone_number,
            'first_name': contact.first_name,
            'last_name': contact.last_name,
            'username': message.from_user.username,
            # 'is_authorized': True # Больше не используется здесь
        }

        db_user = None
        try:
            async with SessionLocal() as session:
                logger.debug(f"handle_contact: Attempting to create/update user in DB for platform='telegram', platform_user_id='{platform_user_id}'")
                # Создаем или обновляем пользователя (без установки is_authorized)
                db_user = await crud.create_or_update_user(
                    session,
                    platform='telegram',
                    platform_user_id=platform_user_id,
                    user_details=user_details
                )

                if db_user:
                    logger.info(f"User {platform_user_id} found/created (DB ID: {db_user.id}). Setting authorization for agent {agent_id_global}.")

                    # --- НОВОЕ: Устанавливаем авторизацию для агента ---
                    auth_record = await crud.db_create_or_update_agent_authorization(
                        session,
                        agent_id=agent_id_global,
                        user_id=db_user.id,
                        is_authorized=True
                    )
                    # --- КОНЕЦ НОВОГО ---

                    if auth_record:
                        logger.info(f"User {platform_user_id} authorized for agent {agent_id_global}.")

                        # --- НОВОЕ: Обновляем кэш авторизации для агента ---
                        cache_key = f"{USER_CACHE_PREFIX}telegram:{platform_user_id}:agent:{agent_id_global}"
                        try:
                            if redis_client:
                                logger.debug(f"handle_contact: Attempting to set auth cache for key '{cache_key}' with value 'true' and TTL {REDIS_USER_CACHE_TTL}")
                                await redis_client.set(cache_key, 'true', ex=REDIS_USER_CACHE_TTL)
                                logger.debug(f"Cached agent authorization status for {platform_user_id}, agent {agent_id_global}")
                            else:
                                logger.warning(f"Redis client not available, cannot cache agent authorization for {platform_user_id}")
                        except redis.RedisError as e:
                            logger.error(f"Failed to cache agent authorization for {platform_user_id}: {e}")
                        # --- КОНЕЦ НОВОГО ---

                        await message.answer(
                            "✅ Авторизация прошла успешно!",
                            reply_markup=ReplyKeyboardRemove() # Remove the contact button
                        )

                        # Отправляем сообщение агенту (оставляем, как просил пользователь)
                        agent_user_data = {
                            "is_authenticated": True, # Пользователь теперь аутентифицирован для этого агента
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
                        # Ошибка при создании/обновлении записи авторизации
                        logger.error(f"Failed to save agent authorization to DB for user {db_user.id}, agent {agent_id_global}")
                        await message.answer("❌ Ошибка: Не удалось сохранить данные авторизации для агента. Попробуйте позже.")

                else:
                    # Эта ветка выполнится, если create_or_update_user вернул None (ошибка внутри CRUD)
                    logger.error(f"Failed to save user to DB for {platform_user_id} (CRUD returned None)")
                    await message.answer("❌ Ошибка: Не удалось сохранить данные пользователя. Попробуйте позже.")

        except Exception as e:
            logger.error(f"Error during contact handling DB operation for {platform_user_id}: {e}", exc_info=True)
            await message.answer("❌ Произошла внутренняя ошибка при обработке контакта.")

    else:
        await message.answer("❌ Пожалуйста, поделитесь *своим* контактом для авторизации.")
# --- КОНЕЦ ИЗМЕНЕНИЯ ---

# --- ИЗМЕНЕНИЕ: Обновляем handle_text_message ---
@dp.message(F.text) # Явно указываем, что ловим только текст
async def handle_text_message(message: Message):
    """Handles regular text messages from the user, checking agent-specific authorization."""
    chat_id = message.chat.id
    user_message = message.md_text
    platform_user_id = str(message.from_user.id) # Используем ID пользователя из сообщения

    if not user_message: # Ignore empty messages or non-text messages not handled elsewhere
        return

    typing_task = asyncio.create_task(send_typing_periodically(chat_id))

    try:
        # --- ИЗМЕНЕНИЕ: Получаем статус авторизации для агента ---
        is_auth_for_agent = await check_user_authorization(agent_id_global, platform_user_id)
        logger.debug(f"handle_text_message: Authorization status for agent {agent_id_global}, user {platform_user_id}: {is_auth_for_agent}")
        # --- КОНЕЦ ИЗМЕНЕНИЯ ---

        # --- ИЗМЕНЕНИЕ: Получаем данные пользователя ТОЛЬКО если авторизован ---
        user_data_for_agent = {
            "is_authenticated": is_auth_for_agent,
            "user_id": platform_user_id # Всегда отправляем platform_user_id как user_id
        }
        db_user = None

        if is_auth_for_agent: # Только если пользователь авторизован для этого агента
            if SessionLocal:
                async with SessionLocal() as session:
                    logger.debug(f"handle_text_message: User is authorized. Attempting to get user details from DB for platform='telegram', platform_user_id='{platform_user_id}'")
                    db_user = await crud.get_user_by_platform_id(session, 'telegram', platform_user_id)
                    if db_user:
                        logger.debug(f"User details found for {platform_user_id} (DB ID: {db_user.id})")
                        # Добавляем остальные данные
                        user_data_for_agent.update({
                            "phone_number": db_user.phone_number,
                            "first_name": db_user.first_name,
                            "last_name": db_user.last_name
                        })
                    else:
                        # Это странная ситуация: авторизация есть, а пользователя нет. Логируем.
                        logger.warning(f"User {platform_user_id} is authorized for agent {agent_id_global}, but user details not found in DB.")
            else:
                logger.warning(f"DB SessionLocal not configured. Cannot fetch user details for authorized user {platform_user_id}.")
        else:
            # Пользователь не авторизован, не извлекаем доп. данные
            logger.debug(f"handle_text_message: User {platform_user_id} is not authorized for agent {agent_id_global}. Sending minimal data.")
        # --- КОНЕЦ ИЗМЕНЕНИЯ ---

        await publish_to_agent(
            agent_id_global,
            chat_id,
            platform_user_id, # Передаем platform_user_id
            user_message,
            user_data_for_agent # Передаем собранные данные
        )

    except Exception as e:
        logger.error(f"Error handling message from chat {chat_id}: {e}", exc_info=True)
        await message.answer("⚠ Произошла ошибка при обработке вашего запроса.")
    finally:
        await asyncio.sleep(1) # Keep typing briefly after sending
        typing_task.cancel()
# --- КОНЕЦ ИЗМЕНЕНИЯ ---

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

