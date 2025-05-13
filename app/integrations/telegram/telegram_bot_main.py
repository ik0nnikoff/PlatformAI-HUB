import asyncio
import json
import logging
import os
import argparse
from typing import Optional, Dict, Any
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart, Command
from aiogram.types import Message, ReplyKeyboardRemove, ReplyKeyboardMarkup, KeyboardButton
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode, ChatAction
import redis.asyncio as redis
from redis import exceptions as redis_exceptions
from contextlib import asynccontextmanager
from aiogram.exceptions import TelegramBadRequest

from app.core.config import settings
from app.db.session import get_async_session_factory
from app.db.crud import user_crud
from app.db.alchemy_models import UserDB, AgentUserAuthorizationDB

REDIS_USER_CACHE_TTL = getattr(settings, "REDIS_USER_CACHE_TTL", int(os.getenv("REDIS_USER_CACHE_TTL", 3600)))
USER_CACHE_PREFIX = "user_cache:"

logger = logging.getLogger(__name__)

bot: Optional[Bot] = None
dp: Dispatcher = Dispatcher()
redis_client: Optional[redis.Redis] = None
redis_listener_task: Optional[asyncio.Task] = None
agent_id_global: Optional[str] = None
redis_url_global: Optional[str] = None

AUTH_TRIGGER = "AUTH_REQUIRED"

SessionLocal: Optional[async_sessionmaker[AsyncSession]] = None

if settings.DATABASE_URL and user_crud and UserDB and AgentUserAuthorizationDB:
    try:
        SessionLocal = get_async_session_factory()
        if isinstance(SessionLocal, async_sessionmaker):
             logger.info("SQLAlchemy async session maker configured for bot using global factory.")
        else:
            logger.error(f"get_async_session_factory did not return an async_sessionmaker. Type: {type(SessionLocal)}. Re-creating.")
            from app.db.session import engine as db_engine
            if db_engine:
                SessionLocal = async_sessionmaker(
                    bind=db_engine,
                    class_=AsyncSession,
                    expire_on_commit=False,
                    autoflush=False,
                    autocommit=False
                )
                logger.info("SQLAlchemy async session maker re-created directly.")
            else:
                logger.error("db_engine not available from app.db.session to re-create SessionLocal.")
                SessionLocal = None

    except Exception as e:
        logger.error(f"Failed to configure SQLAlchemy for bot: {e}", exc_info=True)
        SessionLocal = None
else:
    logger.warning("Database URL not set or CRUD/Models not available. Database features disabled for bot.")

def request_contact_markup():
    button = KeyboardButton(text="Поделиться контактом", request_contact=True)
    keyboard = ReplyKeyboardMarkup(keyboard=[[button]], resize_keyboard=True, one_time_keyboard=True)
    return keyboard

async def send_typing_periodically(chat_id: int):
    global bot
    if not bot:
        logger.error("Bot not initialized, cannot send typing action.")
        return
    while True:
        try:
            await bot.send_chat_action(chat_id, ChatAction.TYPING)
            logger.debug(f"Sent typing action to chat {chat_id}")
            await asyncio.sleep(3)
        except asyncio.CancelledError:
            logger.debug(f"Typing task for chat {chat_id} cancelled.")
            break
        except TelegramBadRequest as e:
            logger.warning(f"Telegram API error sending typing action for chat {chat_id}: {e.message}")
            if "chat not found" in e.message.lower():
                logger.error(f"Chat {chat_id} not found. Stopping typing task for this chat.")
                break
            await asyncio.sleep(10)
        except Exception as e:
            logger.error(f"Unexpected error sending typing action for chat {chat_id}: {e}", exc_info=True)
            await asyncio.sleep(10)

async def publish_to_agent(agent_id: str, chat_id: int, platform_user_id: str, message_text: str, user_data: dict):
    global redis_client
    if not redis_client:
        logger.error("Cannot publish to agent: Redis client not available.")
        await bot.send_message(chat_id, "❌ Ошибка: Не удалось связаться с агентом (сервис недоступен).")
        return

    input_channel = f"agent:{agent_id}:input"
    payload = {
        "message": message_text,
        "thread_id": str(chat_id),
        "platform_user_id": platform_user_id,
        "user_data": user_data,
        "channel": "telegram"
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
    global redis_client, SessionLocal
    if not redis_client:
        logger.error("Cannot check authorization: Redis client not available.")
        return False

    cache_key = f"{USER_CACHE_PREFIX}telegram:{platform_user_id}:agent:{agent_id}"
    try:
        logger.debug(f"Auth check: Attempting to get cache for key '{cache_key}'")
        cached_auth_status = await redis_client.get(cache_key)
        if cached_auth_status is not None:
            is_authorized = cached_auth_status == "true"
            logger.info(f"Auth cache hit for user {platform_user_id}, agent {agent_id}. Status: {is_authorized}")
            return is_authorized
        else:
            logger.info(f"Auth cache miss for user {platform_user_id}, agent {agent_id}. Checking DB.")
            if not SessionLocal:
                logger.error("SessionLocal not configured. Cannot check DB for authorization.")
                return False

            async with SessionLocal() as session:
                user = await user_crud.get_user_by_platform_id(session, platform="telegram", platform_user_id=platform_user_id)
                if not user:
                    logger.info(f"User with platform_id {platform_user_id} (telegram) not found. Cannot check authorization.")
                    await redis_client.set(cache_key, "false", ex=REDIS_USER_CACHE_TTL // 4)
                    return False
                
                authorization_entry = await user_crud.get_agent_user_authorization(
                    session,
                    agent_id=agent_id,
                    user_id=user.id
                )
                
                if authorization_entry and authorization_entry.is_authorized:
                    logger.info(f"User {platform_user_id} (DBID: {user.id}) IS authorized for agent {agent_id} via DB.")
                    await redis_client.set(cache_key, "true", ex=REDIS_USER_CACHE_TTL)
                    return True
                else:
                    status_detail = f"entry found: {authorization_entry is not None}, is_authorized flag: {authorization_entry.is_authorized if authorization_entry else 'N/A'}"
                    logger.info(f"User {platform_user_id} (DBID: {user.id}) IS NOT authorized for agent {agent_id} via DB ({status_detail}).")
                    await redis_client.set(cache_key, "false", ex=REDIS_USER_CACHE_TTL // 4) 
                    return False
    except redis.RedisError as e:
        logger.error(f"Redis error during authorization check for agent {agent_id}, user {platform_user_id}: {e}", exc_info=True)
        return False
    except Exception as e:
        logger.error(f"Unexpected error during authorization check for agent {agent_id}, user {platform_user_id}: {e}", exc_info=True)
        return False

async def redis_output_listener(agent_id: str):
    global redis_client, bot
    if not bot:
        logger.critical("Bot is not initialized. Redis listener cannot function.")
        return
    if not redis_client:
        logger.critical("Redis client is not initialized. Redis listener cannot function.")
        return

    output_channel = f"agent:{agent_id}:output"
    pubsub = None
    
    while True:
        try:
            if not await redis_client.ping():
                logger.warning(f"Redis ping failed in listener for {output_channel}. Attempting to re-establish pubsub.")
                if pubsub:
                    try: await pubsub.close()
                    except: pass
                pubsub = None
                await asyncio.sleep(5)
                continue

            if pubsub is None:
                pubsub = redis_client.pubsub()
                await pubsub.subscribe(output_channel)
                logger.info(f"Subscribed to Redis channel: {output_channel}")

            message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
            if message and message.get("type") == "message":
                data_str = message.get("data")
                logger.info(f"Received message from {output_channel}: {data_str[:250]}...")
                
                payload: Optional[Dict[str, Any]] = None
                chat_id_from_payload: Optional[int] = None
                try:
                    payload = json.loads(data_str)
                    if not isinstance(payload, dict):
                        logger.error(f"Decoded payload is not a dictionary: {type(payload)}")
                        continue

                    thread_id_str = payload.get("thread_id")
                    if not thread_id_str:
                        logger.error(f"Missing 'thread_id' in payload from {output_channel}: {payload}")
                        continue
                    
                    try:
                        chat_id_from_payload = int(thread_id_str)
                    except ValueError:
                        logger.error(f"Invalid 'thread_id' format (not an int): {thread_id_str} in payload: {payload}")
                        continue

                    if payload.get("chat_action"):
                        try:
                            await bot.send_chat_action(chat_id_from_payload, payload["chat_action"])
                            logger.info(f"Sent chat action '{payload['chat_action']}' to chat {chat_id_from_payload}")
                        except Exception as e_action:
                            logger.error(f"Failed to send chat action to {chat_id_from_payload}: {e_action}")
                    
                    elif payload.get("response"): # Изменено с "text" на "response"
                        await bot.send_message(chat_id_from_payload, payload["response"], parse_mode=ParseMode.MARKDOWN)
                        logger.info(f"Sent response message to chat {chat_id_from_payload}")
                    
                    elif payload.get("message") == AUTH_TRIGGER:
                        logger.info(f"Received AUTH_TRIGGER for chat {chat_id_from_payload}. Requesting contact.")
                        await bot.send_message(
                            chat_id_from_payload,
                            "Для продолжения работы с этим агентом, пожалуйста, авторизуйтесь. "
                            "Нажмите кнопку ниже или используйте команду /login, чтобы поделиться своим контактом.",
                            reply_markup=request_contact_markup()
                        )

                    else:
                        logger.debug(f"Received unhandled payload structure from {output_channel}: {payload}")

                except json.JSONDecodeError:
                    logger.error(f"Failed to decode JSON from {output_channel}: {data_str}")
                except TelegramBadRequest as e:
                    logger.error(f"Telegram API error sending message to chat {chat_id_from_payload or 'unknown'}: {e.message}", exc_info=True)
                except Exception as e_inner:
                    logger.error(f"Error processing message from {output_channel} for chat_id {chat_id_from_payload or 'unknown'}: {e_inner}", exc_info=True)
            
            await asyncio.sleep(0.01)

        except redis_exceptions.ConnectionError as e:
            logger.error(f"Redis connection error in listener for {output_channel}: {e}. Attempting to reconnect pubsub.")
            if pubsub:
                try: await pubsub.close()
                except Exception: pass
                pubsub = None
            await asyncio.sleep(settings.REDIS_RECONNECT_INTERVAL if hasattr(settings, 'REDIS_RECONNECT_INTERVAL') else 5)
        except asyncio.CancelledError:
            logger.info(f"Redis listener for {output_channel} cancelled.")
            break
        except Exception as e:
            logger.error(f"Unexpected error in Redis listener for {output_channel}: {e}", exc_info=True)
            if pubsub:
                try: await pubsub.close()
                except Exception: pass
                pubsub = None
            await asyncio.sleep(5)
    
    if pubsub:
        try:
            await pubsub.unsubscribe(output_channel)
            await pubsub.close()
            logger.info(f"Unsubscribed and closed pubsub for {output_channel}")
        except Exception as clean_e:
            logger.error(f"Error cleaning up pubsub for {output_channel}: {clean_e}", exc_info=True)

@dp.message(CommandStart())
async def start(message: Message):
    global agent_id_global
    platform_user_id = str(message.from_user.id)
    logger.info(f"User {platform_user_id} (ChatID: {message.chat.id}) triggered /start for agent {agent_id_global or 'Unknown'}")
    await message.answer("Привет! Задайте мне вопрос.")

@dp.message(Command("login"))
async def login_command(message: Message):
    global agent_id_global
    platform_user_id = str(message.from_user.id)
    logger.info(f"User {platform_user_id} (ChatID: {message.chat.id}) triggered /login for agent {agent_id_global or 'Unknown'}")
    
    if not agent_id_global:
        logger.error("agent_id_global not set. Cannot process /login.")
        await message.answer("❌ Ошибка конфигурации агента. Невозможно выполнить вход.", reply_markup=ReplyKeyboardRemove())
        return

    is_authorized = await check_user_authorization(agent_id_global, platform_user_id)
    if (is_authorized):
        logger.info(f"User {platform_user_id} is already authorized for agent {agent_id_global}.")
        await message.answer("Вы уже авторизованы! Можете продолжать работу.", reply_markup=ReplyKeyboardRemove())
    else:
        logger.info(f"User {platform_user_id} is not authorized for agent {agent_id_global}. Requesting contact for login.")
        await message.answer(
            "Для авторизации, пожалуйста, поделитесь своим контактом, нажав кнопку ниже:",
            reply_markup=request_contact_markup()
        )

@dp.message(F.contact)
async def handle_contact(message: Message):
    global agent_id_global
    contact = message.contact
    telegram_user_id = str(message.from_user.id)
    contact_platform_user_id = str(contact.user_id)
    phone_number = contact.phone_number

    logger.info(f"Received contact from Telegram UserID {telegram_user_id} (ChatID: {message.chat.id}). Contact details: Phone {phone_number}, ContactPlatformUserID {contact_platform_user_id}. For agent {agent_id_global or 'Unknown'}")

    if not agent_id_global:
        logger.error("agent_id_global not set. Cannot process contact.")
        await message.answer("❌ Ошибка конфигурации агента. Невозможно обработать контакт.", reply_markup=ReplyKeyboardRemove())
        return

    if telegram_user_id != contact_platform_user_id:
        logger.warning(
            f"Telegram UserID {telegram_user_id} shared contact for a different ContactPlatformUserID {contact_platform_user_id}. "
            f"Authorization will be processed for ContactPlatformUserID {contact_platform_user_id}."
        )

    if not SessionLocal:
        logger.error("SessionLocal not configured. Cannot process contact for authorization.")
        await message.answer("❌ Ошибка: Сервис базы данных временно недоступен. Попробуйте позже.", reply_markup=ReplyKeyboardRemove())
        return

    async with SessionLocal() as session:
        try:
            db_user = await user_crud.get_user_by_platform_id(session, platform_id=contact_platform_user_id, platform_type="telegram")
            if not db_user:
                logger.info(f"User with platform_id {contact_platform_user_id} (telegram) not found. Creating new user.")
                user_in_data = {
                    "platform_id": contact_platform_user_id,
                    "platform_type": "telegram",
                    "phone_number": phone_number,
                    "username": message.from_user.username or f"tg_user_{contact_platform_user_id}",
                    "first_name": contact.first_name,
                    "last_name": contact.last_name
                }
                db_user = await user_crud.create_user(session, user_data=user_in_data)
                logger.info(f"Created new user: DBID {db_user.id}, PlatformID {db_user.platform_id}")
            else:
                logger.info(f"Found existing user: DBID {db_user.id}, PlatformID {db_user.platform_id}. Updating phone if necessary.")
                if db_user.phone_number != phone_number or \
                   db_user.first_name != contact.first_name or \
                   db_user.last_name != contact.last_name:
                    update_data = {"phone_number": phone_number, "first_name": contact.first_name, "last_name": contact.last_name}
                    if message.from_user.username:
                        update_data["username"] = message.from_user.username
                    db_user = await user_crud.update_user(session, user_id=db_user.id, user_update_data=update_data)
                    logger.info(f"User {db_user.id} data updated.")

            auth_entry = await user_crud.get_agent_user_authorization(
                session,
                agent_id=agent_id_global,
                platform_user_id=contact_platform_user_id,
                integration_type="telegram"
            )

            if auth_entry:
                if not auth_entry.is_authorized:
                    logger.info(f"Found existing authorization for user {contact_platform_user_id}, agent {agent_id_global}, but it's not active. Activating now.")
                    auth_entry = await user_crud.update_agent_user_authorization(
                        session,
                        auth_id=auth_entry.id,
                        auth_update_data={"is_authorized": True, "authorization_time": datetime.utcnow()}
                    )
                else:
                    logger.info(f"User {contact_platform_user_id} is already authorized for agent {agent_id_global}.")
            else:
                logger.info(f"No authorization entry found for user {contact_platform_user_id}, agent {agent_id_global}. Creating new authorized entry.")
                auth_data = {
                    "agent_id": agent_id_global,
                    "user_id": db_user.id,
                    "platform_user_id": contact_platform_user_id,
                    "integration_type": "telegram",
                    "is_authorized": True,
                    "authorization_time": datetime.utcnow()
                }
                auth_entry = await user_crud.create_agent_user_authorization(session, authorization_data=auth_data)
            
            await session.commit()

            if auth_entry and auth_entry.is_authorized:
                await message.answer(f"✅ Вы успешно авторизованы для агента! Теперь вы можете отправлять сообщения.", reply_markup=ReplyKeyboardRemove())
                logger.info(f"User {contact_platform_user_id} (Phone: {phone_number}) successfully authorized for agent {agent_id_global}.")
                cache_key = f"{USER_CACHE_PREFIX}telegram:{contact_platform_user_id}:agent:{agent_id_global}"
                await redis_client.set(cache_key, "true", ex=REDIS_USER_CACHE_TTL)
            else:
                await message.answer("⚠️ Не удалось завершить авторизацию. Пожалуйста, попробуйте еще раз или свяжитесь с поддержкой.", reply_markup=ReplyKeyboardRemove())
                logger.error(f"Failed to authorize user {contact_platform_user_id} for agent {agent_id_global} after contact share. Auth entry: {auth_entry}")

        except Exception as e:
            await session.rollback()
            logger.error(f"Error processing contact for user {contact_platform_user_id}, agent {agent_id_global}: {e}", exc_info=True)
            await message.answer("❌ Произошла ошибка при обработке вашего запроса на авторизацию.", reply_markup=ReplyKeyboardRemove())

@dp.message(F.text) 
async def handle_text_message(message: Message):
    global agent_id_global, redis_client, bot
    platform_user_id = str(message.from_user.id)
    chat_id = message.chat.id
    message_text = message.text

    logger.info(f"Received text message from user {platform_user_id} (ChatID: {chat_id}) for agent {agent_id_global or 'Unknown'}: '{message_text}'")

    if not agent_id_global:
        logger.error("agent_id_global not set. Cannot process text message.")
        await message.answer("❌ Ошибка конфигурации агента. Невозможно обработать сообщение.")
        return

    if not redis_client or not bot:
        logger.error("Redis client or Bot not initialized. Cannot process message.")
        await message.answer("❌ Внутренняя ошибка сервера. Пожалуйста, попробуйте позже.")
        return

    is_authorized = await check_user_authorization(agent_id_global, platform_user_id)
    logger.info(f"User {platform_user_id} authorization status for agent {agent_id_global}: {is_authorized}")

    user_data_for_agent = {
        "telegram_user_id": message.from_user.id,
        "telegram_chat_id": chat_id,
        "first_name": message.from_user.first_name,
        "last_name": message.from_user.last_name,
        "username": message.from_user.username,
        "is_authorized": is_authorized
    }

    input_channel = f"agent:{agent_id_global}:input"
    payload = {
        "message": message_text,
        "thread_id": str(chat_id),
        "platform_user_id": platform_user_id,
        "user_data": user_data_for_agent,
        "channel": "telegram"
    }
    try:
        await redis_client.publish(input_channel, json.dumps(payload))
        logger.info(f"Published message to {input_channel} for chat {chat_id} with payload: {payload}")
    except redis.RedisError as e:
        logger.error(f"Redis error publishing message to {input_channel}: {e}")
        await bot.send_message(chat_id, "❌ Ошибка: Не удалось отправить сообщение агенту.")
    except Exception as e:
        logger.error(f"Unexpected error publishing message to {input_channel}: {e}", exc_info=True)
        await bot.send_message(chat_id, "❌ Произошла внутренняя ошибка при отправке сообщения.")

@asynccontextmanager
async def lifespan(dp_obj: Dispatcher, agent_id: str, bot_token: str, redis_url: str):
    global bot, redis_client, redis_listener_task, agent_id_global, redis_url_global, SessionLocal

    if not bot_token:
        logger.critical("Telegram Bot Token is not provided. Bot cannot start.")
        raise ValueError("Telegram Bot Token is not provided.")

    agent_id_global = agent_id
    redis_url_global = redis_url

    logger.info(f"Attempting to initialize bot for agent {agent_id}...")
    try:
        bot = Bot(token=bot_token, default=DefaultBotProperties(parse_mode=ParseMode.MARKDOWN))
        bot_user = await bot.get_me()
        logger.info(f"Bot initialized successfully: {bot_user.username} (ID: {bot_user.id}) for agent {agent_id}.")
    except Exception as e:
        logger.critical(f"Failed to initialize bot with token (length {len(bot_token) if bot_token else 0}): {e}", exc_info=True)
        raise

    logger.info(f"Attempting to connect to Redis at {redis_url}...")
    try:
        redis_client = await redis.from_url(redis_url, decode_responses=True)
        await redis_client.ping()
        logger.info(f"Successfully connected to Redis at {redis_url}")
    except Exception as e:
        logger.critical(f"Could not connect to Redis at {redis_url}: {e}", exc_info=True)
        if bot and bot.session: await bot.session.close()
        raise

    if not SessionLocal:
        logger.warning("Re-checking SessionLocal: Still not configured. Database operations will fail.")
    else:
        logger.info("SessionLocal appears to be configured.")

    redis_listener_task = asyncio.create_task(redis_output_listener(agent_id))
    logger.info(f"Redis output listener task created for agent {agent_id}")

    try:
        yield {
            "bot": bot,
            "dp": dp_obj,
            "redis_client": redis_client,
            "redis_listener_task": redis_listener_task,
            "agent_id": agent_id
        }
    finally:
        logger.info(f"Shutting down Telegram bot for agent {agent_id}...")
        if redis_listener_task and not redis_listener_task.done():
            logger.info("Cancelling Redis listener task...")
            redis_listener_task.cancel()
            try:
                await redis_listener_task
            except asyncio.CancelledError:
                logger.info("Redis listener task successfully cancelled.")
            except Exception as e_cancel:
                logger.error(f"Error during Redis listener task cancellation: {e_cancel}", exc_info=True)
        
        if redis_client:
            logger.info("Closing Redis client...")
            await redis_client.close()
            logger.info("Redis client closed.")
        
        if bot and bot.session:
            logger.info("Closing bot session...")
            await bot.session.close()
            logger.info("Bot session closed.")
        logger.info(f"Telegram bot for agent {agent_id} shutdown complete.")

async def main_bot_runner(agent_id_param: str, bot_token_param: str, redis_url_param: str):
    global dp, bot, SessionLocal
    logger.info(f"Starting Telegram bot runner for agent_id: {agent_id_param} using Redis at {redis_url_param}")

    if not SessionLocal and settings.DATABASE_URL:
        logger.warning("SessionLocal not configured before lifespan. Attempting re-init.")
        try:
            SessionLocal = get_async_session_factory()
            if isinstance(SessionLocal, async_sessionmaker):
                 logger.info("SQLAlchemy async session maker re-configured successfully.")
            else:
                logger.error(f"Re-init: get_async_session_factory did not return an async_sessionmaker. Type: {type(SessionLocal)}")
                SessionLocal = None
        except Exception as e_sql:
            logger.error(f"Failed to re-configure SQLAlchemy for bot during main_bot_runner: {e_sql}", exc_info=True)
            SessionLocal = None

    async with lifespan(dp, agent_id_param, bot_token_param, redis_url_param) as lf_context:
        logger.info(f"Lifespan context entered for agent {agent_id_param}. Starting polling...")
        try:
            await dp.start_polling(bot, skip_updates=True)
        except Exception as e_poll:
            logger.critical(f"Error during bot polling for agent {agent_id_param}: {e_poll}", exc_info=True)
        finally:
            logger.info(f"Bot polling stopped for agent {agent_id_param}.")

if __name__ == "__main__":
    log_level_name = os.getenv("LOG_LEVEL", "INFO").upper()
    log_level = getattr(logging, log_level_name, logging.INFO)

    logging.basicConfig(
        level=log_level,
        format='%(asctime)s - %(levelname)s - %(name)s - %(module)s:%(lineno)d - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )

    parser = argparse.ArgumentParser(description="Telegram Bot Integration for Configurable Agent")
    parser.add_argument("--agent-id", required=True, help="Unique ID of the agent this bot interacts with")
    parser.add_argument("--redis-url", required=True, help="URL for Redis connection")
    parser.add_argument("--integration-settings", type=str, help="JSON string with integration-specific settings (e.g., bot_token)")
    
    args = parser.parse_args()

    logger.info(f"Raw arguments received: agent_id='{args.agent_id}', redis_url='{args.redis_url}', integration_settings='{args.integration_settings}'")

    integration_settings_data: Optional[Dict[str, Any]] = None
    bot_token_to_use: Optional[str] = None

    if args.integration_settings:
        try:
            integration_settings_data = json.loads(args.integration_settings)
            if isinstance(integration_settings_data, dict):
                bot_token_to_use = integration_settings_data.get("botToken") or integration_settings_data.get("bot_token")
                logger.info(f"Parsed integration_settings. Bot token found: {'Yes' if bot_token_to_use else 'No (or empty)'}")
            else:
                logger.error(f"Parsed integration_settings is not a dictionary: {type(integration_settings_data)}. Settings: {args.integration_settings}")
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse --integration-settings JSON: {e}. Settings: {args.integration_settings}", exc_info=True)
        except Exception as e_parse:
            logger.error(f"Unexpected error parsing --integration-settings: {e_parse}. Settings: {args.integration_settings}", exc_info=True)
    else:
        logger.warning("--integration-settings not provided. Bot token will be missing unless set via other means (not currently supported).")

    if not bot_token_to_use:
        logger.critical("CRITICAL: Telegram Bot Token is not available after parsing arguments. Bot cannot start.")
    else:
        logger.info(f"Bot token to be used (length: {len(bot_token_to_use)})")

    from datetime import datetime

    try:
        asyncio.run(main_bot_runner(
            agent_id_param=args.agent_id,
            bot_token_param=bot_token_to_use,
            redis_url_param=args.redis_url
        ))
    except (KeyboardInterrupt, SystemExit):
        logger.info("Telegram bot process interrupted or exited.")
    except ValueError as ve:
        logger.critical(f"ValueError during bot startup: {ve}", exc_info=True)
    except Exception as e:
        logger.critical(f"Unhandled exception in Telegram bot main execution: {e}", exc_info=True)
    finally:
        logger.info("Telegram bot application finished.")
