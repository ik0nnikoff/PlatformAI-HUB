import asyncio
import json
import logging
import os
import argparse
import time # Добавлено для last_active
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

from app.core.logging_config import setup_logging
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

async def update_integration_status_in_redis(
    redis_client_instance: Optional[redis.Redis], # Сделаем Optional на случай если он None
    agent_id: str,
    integration_type_value: str, # e.g., "telegram"
    status: str,
    pid: Optional[int],
    error_detail: Optional[Any], # Может быть Exception или None
    logger_instance: logging.Logger
):
    """Updates the integration status in Redis."""
    if not redis_client_instance:
        logger_instance.error(f"Redis client not available, cannot update status for agent {agent_id}, integration {integration_type_value}")
        return

    status_key = f"integration_status:{agent_id}:{integration_type_value}"
    
    status_payload: Dict[str, Any] = {
        "status": status,
        "last_active": time.time()
    }
    if pid is not None:
        status_payload["pid"] = pid
    
    if error_detail is not None:
        status_payload["error_detail"] = str(error_detail)

    try:
        await redis_client_instance.hset(status_key, mapping=status_payload)
        logger_instance.info(f"Integration status for agent {agent_id}, type {integration_type_value} updated to: {status} (PID: {pid}, Error: {status_payload.get('error_detail')})")
    except redis.RedisError as e:
        logger_instance.error(f"Redis error updating integration status for {agent_id}, {integration_type_value}: {e}", exc_info=True)
    except Exception as e:
        logger_instance.error(f"Unexpected error updating integration status for {agent_id}, {integration_type_value}: {e}", exc_info=True)

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
            await asyncio.sleep(3) # Send typing status every 3 seconds
        except asyncio.CancelledError:
            logger.debug(f"Typing task cancelled for chat {chat_id}.")
            break
        except TelegramBadRequest as e:
            logger.warning(f"Could not send typing action to chat {chat_id}: {e}. User might have blocked the bot or chat not found.")
            break # Stop trying if there's a fundamental issue with the chat
        except Exception as e:
            logger.error(f"Error in typing task for chat {chat_id}: {e}", exc_info=True)
            # Decide if to break or continue. For now, let's break to avoid spamming logs on persistent errors.
            # If transient network issues are expected, a retry mechanism or longer sleep might be better.
            break

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
                    try: await pubsub.aclose()
                    except: pass
                pubsub = None
                await asyncio.sleep(5)
                continue

            if pubsub is None:
                pubsub = redis_client.pubsub()
                await pubsub.subscribe(output_channel)
                logger.info(f"Subscribed to Redis channel: {output_channel}")

            # Используем redis_message вместо message, чтобы избежать конфликта с aiogram.types.Message
            redis_message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
            if redis_message and redis_message.get("type") == "message":
                data_str = redis_message.get("data")
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

                    response_payload_content = payload.get("response")
                    
                    auth_triggered_in_response = False
                    if isinstance(response_payload_content, str) and AUTH_TRIGGER in response_payload_content:
                        auth_triggered_in_response = True

                    if auth_triggered_in_response:
                        logger.info(f"AUTH_TRIGGER detected in 'response' for chat {chat_id_from_payload}.")
                        
                        # Удаляем AUTH_TRIGGER из ответа
                        modified_response = response_payload_content.replace(AUTH_TRIGGER, "").strip()
                        
                        # Отправляем модифицированный ответ, если он не пустой
                        if (modified_response):
                            try:
                                await bot.send_message(chat_id_from_payload, modified_response, parse_mode=ParseMode.MARKDOWN)
                                logger.info(f"Sent modified response (without AUTH_TRIGGER) to chat {chat_id_from_payload}: {modified_response[:100]}...")
                            except Exception as e_send_modified:
                                logger.error(f"Failed to send modified response to {chat_id_from_payload}: {e_send_modified}")
                        else:
                            logger.info(f"Response became empty after removing AUTH_TRIGGER for chat {chat_id_from_payload}. Nothing to send before auth prompt.")

                        # Инициируем процесс авторизации
                        try:
                            await bot.send_message(
                                chat_id_from_payload,
                                "Для продолжения работы с этим агентом, пожалуйста, авторизуйтесь. "
                                "Нажмите кнопку ниже или используйте команду /login, чтобы поделиться своим контактом.",
                                reply_markup=request_contact_markup()
                            )
                            logger.info(f"Sent authorization prompt to chat {chat_id_from_payload}.")
                        except Exception as e_auth_prompt:
                            logger.error(f"Failed to send auth prompt to {chat_id_from_payload}: {e_auth_prompt}")
                    
                    # Обработка других сообщений (не AUTH_TRIGGER в response)
                    else:
                        if payload.get("chat_action"):
                            try:
                                await bot.send_chat_action(chat_id_from_payload, payload["chat_action"])
                                logger.info(f"Sent chat action '{payload['chat_action']}' to chat {chat_id_from_payload}")
                            except Exception as e_action:
                                logger.error(f"Failed to send chat action to {chat_id_from_payload}: {e_action}")
                        
                        elif isinstance(response_payload_content, str): # Если есть 'response' (и это не AUTH_TRIGGER)
                            try:
                                await bot.send_message(chat_id_from_payload, response_payload_content, parse_mode=ParseMode.MARKDOWN)
                                logger.info(f"Sent normal response message to chat {chat_id_from_payload}")
                            except Exception as e_send_resp:
                                logger.error(f"Failed to send normal response message to {chat_id_from_payload}: {e_send_resp}")
                        
                        # Если есть "message" и это не AUTH_TRIGGER (на случай, если агент шлет и туда, и туда, но без триггера)
                        elif payload.get("message") and isinstance(payload.get("message"), str):
                            message_field_content = payload.get("message")
                            # Дополнительная проверка, чтобы не отправить AUTH_TRIGGER, если он вдруг оказался только в 'message'
                            if AUTH_TRIGGER not in message_field_content:
                                try:
                                    await bot.send_message(chat_id_from_payload, message_field_content, parse_mode=ParseMode.MARKDOWN)
                                    logger.info(f"Sent message from 'message' field (no AUTH_TRIGGER) to chat {chat_id_from_payload}")
                                except Exception as e_send_msg_field:
                                    logger.error(f"Failed to send message from 'message' field to {chat_id_from_payload}: {e_send_msg_field}")
                            else:
                                logger.warning(f"AUTH_TRIGGER found in 'message' field but not in 'response'. This case is not standard for auth flow. Message: {message_field_content[:100]}")
                        else:
                            logger.debug(f"Received unhandled payload structure (no auth in response, no action, no response string, no message string) from {output_channel}: {payload}")

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
                try: await pubsub.aclose()
                except Exception: pass
                pubsub = None
            await asyncio.sleep(settings.REDIS_RECONNECT_INTERVAL if hasattr(settings, 'REDIS_RECONNECT_INTERVAL') else 5)
        except asyncio.CancelledError:
            logger.info(f"Redis listener for {output_channel} cancelled.")
            break
        except Exception as e:
            logger.error(f"Unexpected error in Redis listener for {output_channel}: {e}", exc_info=True)
            if pubsub:
                try: await pubsub.aclose()
                except Exception: pass
                pubsub = None
            await asyncio.sleep(5)
    
    if pubsub:
        try:
            await pubsub.unsubscribe(output_channel)
            await pubsub.aclose()
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
    global agent_id_global, SessionLocal, bot
    if not bot:
        logger.error("Bot not initialized, cannot handle contact.")
        await message.answer("Бот не инициализирован. Пожалуйста, попробуйте позже.")
        return

    if not SessionLocal:
        logger.error("Database session not configured. Cannot process contact.")
        await message.answer("❌ Ошибка: База данных не настроена. Невозможно обработать контакт.")
        return

    if not agent_id_global:
        logger.error("Agent ID not set globally. Cannot process contact for authorization.")
        await message.answer("❌ Ошибка: Не удалось определить агента для авторизации.")
        return
    
    contact_platform_user_id = str(message.contact.user_id) if message.contact.user_id else None
    phone_number = message.contact.phone_number
    first_name = message.contact.first_name
    last_name = message.contact.last_name
    
    # Убедимся, что platform_user_id из контакта совпадает с telegram_id пользователя, отправившего контакт
    telegram_user_id_from_message = str(message.from_user.id)

    logger.info(
        f"Received contact from Telegram UserID {telegram_user_id_from_message} (ChatID: {message.chat.id}). "
        f"Contact details: Phone {phone_number}, ContactPlatformUserID {contact_platform_user_id}. For agent {agent_id_global}"
    )

    if not contact_platform_user_id or contact_platform_user_id != telegram_user_id_from_message:
        logger.warning(
            f"Contact's platform_user_id ({contact_platform_user_id}) does not match "
            f"sender's Telegram ID ({telegram_user_id_from_message}). Ignoring contact for security reasons."
        )
        await message.answer(
            "Похоже, вы пытаетесь поделиться чужим контактом. Пожалуйста, поделитесь своим собственным контактом.",
            reply_markup=ReplyKeyboardRemove()
        )
        return

    async with SessionLocal() as session:
        try:
            # Исправляем имена аргументов
            db_user = await user_crud.get_user_by_platform_id(session, platform="telegram", platform_user_id=contact_platform_user_id)
            
            user_details_for_update: Dict[str, Any] = {
                "phone_number": phone_number,
                "first_name": first_name,
                "last_name": last_name,
                "username": message.from_user.username # Используем username из from_user
            }
            
            # Удаляем None значения из user_details, чтобы они не перезаписывали существующие данные в БД на None
            user_details_for_update = {k: v for k, v in user_details_for_update.items() if v is not None}

            created_or_updated_user = await user_crud.create_or_update_user(
                session,
                platform="telegram",
                platform_user_id=contact_platform_user_id,
                user_details=user_details_for_update
            )

            if not created_or_updated_user:
                await message.answer("❌ Не удалось сохранить информацию о пользователе. Попробуйте еще раз.", reply_markup=ReplyKeyboardRemove())
                return

            # Теперь обновляем или создаем запись об авторизации
            auth_record = await user_crud.update_agent_user_authorization(
                session,
                agent_id=agent_id_global,
                user_id=created_or_updated_user.id,
                is_authorized=True
            )

            if auth_record and auth_record.is_authorized:
                logger.info(f"User {created_or_updated_user.id} (TG: {contact_platform_user_id}) successfully authorized for agent {agent_id_global}")
                
                # Очищаем кеш авторизации для этого пользователя и агента
                if redis_client:
                    cache_key = f"{USER_CACHE_PREFIX}telegram:{contact_platform_user_id}:agent:{agent_id_global}"
                    try:
                        await redis_client.delete(cache_key)
                        logger.info(f"Authorization cache cleared for {cache_key}")
                    except redis.RedisError as e:
                        logger.error(f"Redis error clearing auth cache for {cache_key}: {e}")
                
                await message.answer(
                    "✅ Спасибо! Ваш контакт получен, и вы успешно авторизованы. Теперь вы можете продолжить работу с агентом.",
                    reply_markup=ReplyKeyboardRemove()
                )
                # Можно отправить "приветственное" сообщение от агента или инструкцию
                # await publish_to_agent(agent_id_global, message.chat.id, contact_platform_user_id, "/start_after_auth", {"is_authenticated": True})

            else:
                logger.error(f"Failed to update authorization status for user {created_or_updated_user.id}, agent {agent_id_global}")
                await message.answer("❌ Произошла ошибка при обновлении статуса авторизации. Пожалуйста, попробуйте еще раз.", reply_markup=ReplyKeyboardRemove())

        except Exception as e:
            logger.error(f"Error processing contact for user {contact_platform_user_id}, agent {agent_id_global}: {e}", exc_info=True)
            await message.answer("❌ Произошла внутренняя ошибка при обработке вашего контакта. Пожалуйста, попробуйте позже.", reply_markup=ReplyKeyboardRemove())

@dp.message(F.text) 
async def handle_text_message(message: Message):
    global agent_id_global, SessionLocal, bot
    if not bot:
        logger.error("Bot not initialized, cannot handle text message.")
        # Consider sending a message to the user, though it might also fail if bot is broken.
        return

    if not agent_id_global:
        logger.error("Agent ID not set globally. Cannot process message.")
        await message.answer("❌ Ошибка: Не удалось определить агента для обработки вашего сообщения.")
        return

    chat_id = message.chat.id
    user_message_text = message.text # Используем .text для простого текста, .md_text для Markdown
    platform_user_id = str(message.from_user.id)

    if not user_message_text:
        logger.debug(f"Received empty text message from {platform_user_id} in chat {chat_id}. Ignoring.")
        return

    logger.info(f"Received text message from {platform_user_id} in chat {chat_id} for agent {agent_id_global}: '{user_message_text[:50]}...'")

    typing_task = asyncio.create_task(send_typing_periodically(chat_id))

    try:
        is_authorized_for_agent = await check_user_authorization(agent_id_global, platform_user_id)
        logger.debug(f"Authorization status for agent {agent_id_global}, user {platform_user_id}: {is_authorized_for_agent}")

        user_data_for_agent: Dict[str, Any] = {
            "is_authenticated": is_authorized_for_agent,
            "user_id": platform_user_id # Всегда отправляем platform_user_id
        }

        if is_authorized_for_agent:
            if SessionLocal:
                async with SessionLocal() as session:
                    logger.debug(f"User {platform_user_id} is authorized. Attempting to get user details from DB.")
                    db_user = await user_crud.get_user_by_platform_id(session, platform="telegram", platform_user_id=platform_user_id)
                    if db_user:
                        logger.debug(f"User details found for {platform_user_id} (DB ID: {db_user.id}). Adding to payload.")
                        user_data_for_agent.update({
                            "phone_number": db_user.phone_number,
                            "first_name": db_user.first_name,
                            "last_name": db_user.last_name,
                            "username": db_user.username # Берем username из БД, т.к. он может быть обновлен
                        })
                    else:
                        # Эта ситуация может возникнуть, если запись об авторизации есть, а самого пользователя удалили
                        # или произошла ошибка при его создании/обновлении, но авторизация как-то записалась.
                        logger.warning(f"User {platform_user_id} is marked as authorized for agent {agent_id_global}, but user details not found in DB. Sending minimal data.")
            else:
                logger.warning(f"DB SessionLocal not configured. Cannot fetch full user details for authorized user {platform_user_id}. Sending minimal data.")
        else:
            logger.debug(f"User {platform_user_id} is not authorized for agent {agent_id_global}. Sending minimal data.")

        await publish_to_agent(
            agent_id=agent_id_global,
            chat_id=chat_id,
            platform_user_id=platform_user_id,
            message_text=user_message_text, # Передаем обычный текст
            user_data=user_data_for_agent
        )
        logger.info(f"Message from {platform_user_id} published to agent {agent_id_global}. User data sent: {user_data_for_agent}")

    except Exception as e:
        logger.error(f"Error handling text message from chat {chat_id} for agent {agent_id_global}: {e}", exc_info=True)
        try:
            await message.answer("⚠️ Произошла ошибка при обработке вашего запроса. Пожалуйста, попробуйте еще раз позже.")
        except Exception as e_reply:
            logger.error(f"Failed to send error reply to chat {chat_id}: {e_reply}")
    finally:
        # Даем агенту немного времени на ответ перед тем, как убирать "печатает"
        # Это значение можно настроить
        await asyncio.sleep(1) 
        typing_task.cancel()
        try:
            await typing_task # Дожидаемся завершения задачи тайпинга
        except asyncio.CancelledError:
            logger.debug(f"Typing task successfully cancelled for chat {chat_id} after processing message.")

@asynccontextmanager
async def lifespan(dp_obj: Dispatcher, agent_id: str, bot_token: str, redis_url: str):
    global bot, redis_client, redis_listener_task, agent_id_global, redis_url_global, SessionLocal, logger

    if not bot_token:
        logger.critical("Telegram Bot Token is not provided. Bot cannot start.")
        raise ValueError("Telegram Bot Token is not provided.")

    agent_id_global = agent_id
    redis_url_global = redis_url
    current_pid = os.getpid()
    integration_type_val = "telegram"

    logger.info(f"Attempting to connect to Redis at {redis_url} for agent {agent_id} (PID: {current_pid})...")
    try:
        redis_client = await redis.from_url(redis_url, decode_responses=True)
        await redis_client.ping()
        logger.info(f"Successfully connected to Redis at {redis_url} for agent {agent_id}")
    except Exception as e:
        logger.critical(f"Could not connect to Redis at {redis_url} for agent {agent_id}: {e}", exc_info=True)
        raise

    await update_integration_status_in_redis(
        redis_client_instance=redis_client,
        agent_id=agent_id,
        integration_type_value=integration_type_val,
        status="starting",
        pid=current_pid,
        error_detail=None,
        logger_instance=logger
    )

    logger.info(f"Attempting to initialize bot for agent {agent_id}...")
    try:
        bot = Bot(token=bot_token, default=DefaultBotProperties(parse_mode=ParseMode.MARKDOWN))
        bot_user = await bot.get_me()
        logger.info(f"Bot initialized successfully: {bot_user.username} (ID: {bot_user.id}) for agent {agent_id}.")
    except Exception as e:
        logger.critical(f"Failed to initialize bot for agent {agent_id}: {e}", exc_info=True)
        await update_integration_status_in_redis(
            redis_client_instance=redis_client, agent_id=agent_id, integration_type_value=integration_type_val,
            status="error", pid=current_pid, error_detail=f"Bot initialization failed: {e}", logger_instance=logger
        )
        if redis_client: await redis_client.close()
        raise

    if not SessionLocal:
        logger.warning(f"Re-checking SessionLocal for agent {agent_id}: Still not configured. Database operations will fail.")
    else:
        logger.info(f"SessionLocal appears to be configured for agent {agent_id}.")

    redis_listener_task = asyncio.create_task(redis_output_listener(agent_id))
    logger.info(f"Redis output listener task created for agent {agent_id}")

    try:
        await update_integration_status_in_redis(
            redis_client_instance=redis_client,
            agent_id=agent_id,
            integration_type_value=integration_type_val,
            status="running",
            pid=current_pid,
            error_detail=None,
            logger_instance=logger
        )
        logger.info(f"Telegram integration for agent {agent_id} is now running (PID: {current_pid}).")
        yield {
            "bot": bot,
            "dp": dp_obj,
            "redis_client": redis_client,
            "redis_listener_task": redis_listener_task,
            "agent_id": agent_id
        }
    except Exception as e_lifespan:
        logger.error(f"Error during lifespan execution for agent {agent_id}: {e_lifespan}", exc_info=True)
        await update_integration_status_in_redis(
            redis_client_instance=redis_client, agent_id=agent_id, integration_type_value=integration_type_val,
            status="error", pid=current_pid, error_detail=f"Lifespan error: {e_lifespan}", logger_instance=logger
        )
        raise
    finally:
        logger.info(f"Shutting down Telegram bot for agent {agent_id} (PID: {current_pid})...")
        await update_integration_status_in_redis(
            redis_client_instance=redis_client, agent_id=agent_id, integration_type_value=integration_type_val,
            status="stopping", pid=current_pid, error_detail=None, logger_instance=logger
        )

        if redis_listener_task and not redis_listener_task.done():
            logger.info(f"Cancelling Redis listener task for agent {agent_id}...")
            redis_listener_task.cancel()
            try:
                await redis_listener_task
            except asyncio.CancelledError:
                logger.info(f"Redis listener task for agent {agent_id} cancelled successfully.")
            except Exception as e_cancel:
                logger.error(f"Error awaiting cancelled Redis listener task for agent {agent_id}: {e_cancel}", exc_info=True)
        
        if redis_url_global:
            temp_redis_client_for_final_status = None
            try:
                temp_redis_client_for_final_status = await redis.from_url(redis_url_global, decode_responses=True)
                await temp_redis_client_for_final_status.ping()
                await update_integration_status_in_redis(
                    redis_client_instance=temp_redis_client_for_final_status, agent_id=agent_id, 
                    integration_type_value=integration_type_val, status="stopped", 
                    pid=current_pid, error_detail=None, logger_instance=logger
                )
                logger.info(f"Final status for agent {agent_id} updated to 'stopped'.")
            except Exception as e_temp_redis:
                logger.error(f"Failed to update final status to 'stopped' using temporary Redis client for agent {agent_id}: {e_temp_redis}")
            finally:
                if temp_redis_client_for_final_status:
                    await temp_redis_client_for_final_status.aclose()
        else:
            logger.warning(f"redis_url_global not set, cannot reliably update final status to 'stopped' for agent {agent_id}")

        if redis_client:
            logger.info(f"Closing main Redis client connection for agent {agent_id}...")
            await redis_client.aclose()
            logger.info(f"Main Redis client for agent {agent_id} closed.")
        
        if bot and bot.session:
            logger.info(f"Closing bot session for agent {agent_id}...")
            await bot.session.close()
            logger.info(f"Bot session for agent {agent_id} closed.")
        
        logger.info(f"Telegram bot for agent {agent_id} shutdown complete.")

async def main_bot_runner(agent_id_param: str, bot_token_param: str, redis_url_param: str):
    global dp, bot, SessionLocal, logger
    current_pid = os.getpid()
    integration_type_val = "telegram"
    
    runner_logger = logger 
    runner_logger.info(f"Starting Telegram bot runner for agent_id: {agent_id_param} (PID: {current_pid}) using Redis at {redis_url_param}")

    if not SessionLocal and settings.DATABASE_URL:
        runner_logger.warning("SessionLocal not configured before lifespan in main_bot_runner. Attempting re-init.")
        try:
            SessionLocal = get_async_session_factory()
            if SessionLocal:
                runner_logger.info("SessionLocal re-initialized successfully in main_bot_runner.")
            else:
                runner_logger.error("Failed to re-initialize SessionLocal in main_bot_runner (returned None).")
        except Exception as e_sql:
            runner_logger.error(f"Error re-initializing SessionLocal in main_bot_runner: {e_sql}", exc_info=True)

    temp_redis_for_startup_error = None
    try:
        async with lifespan(dp, agent_id_param, bot_token_param, redis_url_param) as lf_context:
            runner_logger.info(f"Lifespan context entered for agent {agent_id_param}. Starting polling...")
            try:
                current_bot_instance = lf_context.get("bot")
                if not current_bot_instance:
                    runner_logger.error(f"Bot instance not found in lifespan context for agent {agent_id_param}. Polling cannot start.")
                    if lf_context and lf_context.get("redis_client"):
                         await update_integration_status_in_redis(
                            redis_client_instance=lf_context["redis_client"], agent_id=agent_id_param, integration_type_value=integration_type_val,
                            status="error", pid=current_pid, error_detail="Bot instance missing post-lifespan", logger_instance=runner_logger
                        )
                    return

                await dp.start_polling(current_bot_instance, allowed_updates=dp.resolve_used_update_types())
            except Exception as e_poll:
                runner_logger.error(f"Polling failed for agent {agent_id_param}: {e_poll}", exc_info=True)
                if lf_context and lf_context.get("redis_client"):
                    await update_integration_status_in_redis(
                        redis_client_instance=lf_context["redis_client"], agent_id=agent_id_param, integration_type_value=integration_type_val,
                        status="error", pid=current_pid, error_detail=f"Polling failed: {e_poll}", logger_instance=runner_logger
                    )
            finally:
                runner_logger.info(f"Polling stopped for agent {agent_id_param}.")
                
    except ValueError as ve: 
        runner_logger.critical(f"ValueError during startup for agent {agent_id_param}: {ve}", exc_info=True)
        if redis_url_param:
            try:
                temp_redis_for_startup_error = await redis.from_url(redis_url_param, decode_responses=True)
                await temp_redis_for_startup_error.ping()
                await update_integration_status_in_redis(
                    redis_client_instance=temp_redis_for_startup_error, agent_id=agent_id_param, integration_type_value=integration_type_val,
                    status="error", pid=current_pid, error_detail=str(ve), logger_instance=runner_logger
                )
            except Exception as e_redis_init_fail:
                runner_logger.error(f"Could not connect to Redis to report startup ValueError for agent {agent_id_param}: {e_redis_init_fail}")
            finally:
                if temp_redis_for_startup_error: await temp_redis_for_startup_error.close()
    except Exception as e_outer:
        runner_logger.critical(f"Unhandled exception in main_bot_runner for agent {agent_id_param}: {e_outer}", exc_info=True)
        if redis_url_param:
            try:
                temp_redis_for_startup_error = await redis.from_url(redis_url_param, decode_responses=True)
                await temp_redis_for_startup_error.ping()
                await update_integration_status_in_redis(
                    redis_client_instance=temp_redis_for_startup_error, agent_id=agent_id_param, integration_type_value=integration_type_val,
                    status="error", pid=current_pid, error_detail=f"Critical runner error: {e_outer}", logger_instance=runner_logger
                )
            except Exception as e_redis_init_fail:
                runner_logger.error(f"Could not connect to Redis to report critical runner error for agent {agent_id_param}: {e_redis_init_fail}")
            finally:
                if temp_redis_for_startup_error: await temp_redis_for_startup_error.close()
    finally:
        runner_logger.info(f"Main bot runner for agent {agent_id_param} is exiting.")

if __name__ == "__main__":
    # log_level_name = os.getenv("LOG_LEVEL", "INFO").upper()
    # log_level = getattr(logging, log_level_name, logging.INFO)

    # logging.basicConfig(
    #     level=log_level,
    #     format='%(asctime)s - %(levelname)s - %(name)s - %(module)s:%(lineno)d - %(message)s',
    #     datefmt='%Y-%m-%d %H:%M:%S'
    # )
    setup_logging()
    

    parser = argparse.ArgumentParser(description="Telegram Bot Integration for Configurable Agent")
    parser.add_argument("--agent-id", required=True, help="Unique ID of the agent this bot interacts with")
    parser.add_argument("--redis-url", required=True, help="URL for Redis connection")
    parser.add_argument("--integration-settings", type=str, help="JSON string with integration-specific settings (e.g., bot_token)")
    
    args = parser.parse_args()

    logger = logging.getLogger(f"telegram:{args.agent_id}")
    # logger.info(f"Raw arguments received: agent_id='{args.agent_id}', redis_url='{args.redis_url}', integration_settings='{args.integration_settings}'")

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
