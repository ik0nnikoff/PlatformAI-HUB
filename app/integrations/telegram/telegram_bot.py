import asyncio
import json
import logging
import os
import time
import uuid # Added import
from typing import Optional, Dict, Any, List

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart, Command
from aiogram.types import Message, ReplyKeyboardRemove, ReplyKeyboardMarkup, KeyboardButton, User
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode, ChatAction
from aiogram.exceptions import TelegramBadRequest # Corrected import
import redis.asyncio as redis # type: ignore
from redis import exceptions as redis_exceptions

from app.core.config import settings
from app.core.base.runnable_component import RunnableComponent
from app.core.base.status_updater import StatusUpdater
from app.db.crud import user_crud # Assuming direct import is okay
from app.db.alchemy_models import UserDB, AgentUserAuthorizationDB # Assuming direct import
from app.api.schemas.common_schemas import IntegrationType # Added import

# Constants
REDIS_USER_CACHE_TTL = getattr(settings, "REDIS_USER_CACHE_TTL", int(os.getenv("REDIS_USER_CACHE_TTL", 3600)))
USER_CACHE_PREFIX = "user_cache:"
AUTH_TRIGGER = "AUTH_REQUIRED"


class TelegramIntegrationBot(RunnableComponent, StatusUpdater):
    """
    Manages the lifecycle and execution of a Telegram Bot integration for a specific agent.
    """

    def __init__(self,
                 agent_id: str,
                 bot_token: str,
                 redis_url: str, # This will be used for StatusUpdater's Redis client
                 db_session_factory: Optional[async_sessionmaker[AsyncSession]],
                 logger_adapter: logging.LoggerAdapter,
                 redis_pubsub_url: Optional[str] = None): # Added for dedicated bot Redis client

        # Initialize RunnableComponent with the logger first
        super().__init__(logger=logger_adapter) 
        # StatusUpdater's __init__ is simple, can be called after.
        # It doesn't initialize its Redis client until setup_status_updater is called.
        StatusUpdater.__init__(self)

        self.agent_id = agent_id
        self.bot_token = bot_token
        
        # For StatusUpdater (will use this URL)
        self.redis_url_status = redis_url 

        # For Bot's own Redis operations (Pub/Sub, Cache)
        self.redis_url_bot_operations = redis_pubsub_url or redis_url # Use specific or fallback to main redis_url
        
        self.db_session_factory = db_session_factory

        # --- Attributes for StatusUpdater ---
        # component_id must be unique for this specific integration instance
        self._component_id = f"{self.agent_id}:{IntegrationType.TELEGRAM.value}"
        # status_key_prefix should be general for integrations, then specific component_id is appended
        self._status_key_prefix = "integration_status:" 

        # --- Aiogram and Bot specific attributes ---
        self.bot: Optional[Bot] = None
        self.dp: Optional[Dispatcher] = None
        self.redis_client_bot: Optional[redis.Redis] = None

        self.redis_listener_task: Optional[asyncio.Task] = None
        self.polling_task: Optional[asyncio.Task] = None
        self.typing_tasks: Dict[int, asyncio.Task] = {} # To manage typing indicator tasks

        self.needs_restart: bool = False

        self.logger.info(f"TelegramIntegrationBot for agent {self.agent_id} initialized. PID: {os.getpid()}")

    async def _get_redis_client_for_bot(self) -> redis.Redis:
        """Ensures a Redis client is available for bot operations."""
        # The check for self.redis_client_bot.closed was removed.
        # Connection is verified by ping() on initialization.
        # If an existing client is disconnected, operations will fail and should be handled by the caller.
        if not self.redis_client_bot:
            self.logger.info(f"Initializing new Redis client for Telegram bot operations from URL: {self.redis_url_bot_operations}.")
            try:
                # Ensure decode_responses=True for string operations with Redis
                self.redis_client_bot = await redis.from_url(self.redis_url_bot_operations, decode_responses=True)
                await self.redis_client_bot.ping() # Ping to confirm connection
                self.logger.info("Redis client for bot operations connected successfully.")
            except redis.RedisError as e:
                self.logger.error(f"Failed to connect to Redis for bot operations: {e}")
                self.redis_client_bot = None # Ensure client is None on failure
                raise
        return self.redis_client_bot

    def _request_contact_markup(self) -> ReplyKeyboardMarkup:
        button = KeyboardButton(text="Поделиться контактом", request_contact=True)
        keyboard = ReplyKeyboardMarkup(keyboard=[[button]], resize_keyboard=True, one_time_keyboard=True)
        return keyboard

    async def _send_typing_periodically(self, chat_id: int):
        if not self.bot:
            self.logger.error("Bot not initialized, cannot send typing action.")
            return
        try:
            while True:
                await self.bot.send_chat_action(chat_id, ChatAction.TYPING)
                await asyncio.sleep(3)  # Send typing status every 3 seconds
        except asyncio.CancelledError:
            self.logger.debug(f"Typing task cancelled for chat {chat_id}.")
        except TelegramBadRequest as e:
            self.logger.warning(f"Could not send typing action to chat {chat_id}: {e}. User might have blocked or chat not found.")
        except Exception as e:
            self.logger.error(f"Error in typing task for chat {chat_id}: {e}", exc_info=True)
        finally:
            if chat_id in self.typing_tasks:
                del self.typing_tasks[chat_id]


    async def _publish_to_agent(self, chat_id: int, platform_user_id: str, message_text: str, user_data: dict):
        if not self.redis_client_bot:
            self.logger.error(f"Redis client for bot operations not available for agent {self.agent_id}. Cannot publish message.")
            return

        input_channel = f"agent:{self.agent_id}:input"
        interaction_id = str(uuid.uuid4()) # Added interaction_id
        payload = {
            "text": message_text,  # Changed from "message" to "text"
            "chat_id": str(chat_id),  # Changed from "thread_id" to "chat_id"
            "interaction_id": interaction_id, # Added interaction_id
            "platform_user_id": platform_user_id,
            "user_data": user_data,
            "channel": "telegram"
        }
        try:
            await self.redis_client_bot.publish(input_channel, json.dumps(payload))
            self.logger.info(f"Published message to {input_channel} for chat {chat_id} (interaction_id: {interaction_id})") # Updated log
        except redis_exceptions.RedisError as e: # CHANGED
            self.logger.error(f"Redis error publishing to {input_channel} for agent {self.agent_id}: {e}", exc_info=True)
        except Exception as e:
            self.logger.error(f"Unexpected error publishing to {input_channel} for agent {self.agent_id}: {e}", exc_info=True)


    async def _check_user_authorization(self, platform_user_id: str) -> bool:
        if not self.redis_client_bot:
            self.logger.error("Cannot check authorization: Redis client (bot) not available.")
            return False

        cache_key = f"{USER_CACHE_PREFIX}telegram:{platform_user_id}:agent:{self.agent_id}"
        try:
            self.logger.debug(f"Auth check: Attempting to get cache for key '{cache_key}'")
            cached_auth_status = await self.redis_client_bot.get(cache_key)
            if cached_auth_status is not None:
                is_authorized = cached_auth_status == "true"
                self.logger.info(f"Auth cache hit for user {platform_user_id}, agent {self.agent_id}. Status: {is_authorized}")
                return is_authorized
            else:
                self.logger.info(f"Auth cache miss for user {platform_user_id}, agent {self.agent_id}. Checking DB.")
                if not self.db_session_factory:
                    self.logger.error("db_session_factory not configured. Cannot check DB for authorization.")
                    return False

                async with self.db_session_factory() as session:
                    user = await user_crud.get_user_by_platform_id(session, platform="telegram", platform_user_id=platform_user_id)
                    if not user:
                        self.logger.info(f"User with platform_id {platform_user_id} (telegram) not found. Cannot check authorization.")
                        await self.redis_client_bot.set(cache_key, "false", ex=REDIS_USER_CACHE_TTL // 4)
                        return False
                    
                    authorization_entry = await user_crud.get_agent_user_authorization(
                        session,
                        agent_id=self.agent_id,
                        user_id=user.id
                    )
                    
                    if authorization_entry and authorization_entry.is_authorized:
                        self.logger.info(f"User {platform_user_id} (DBID: {user.id}) IS authorized for agent {self.agent_id} via DB.")
                        await self.redis_client_bot.set(cache_key, "true", ex=REDIS_USER_CACHE_TTL)
                        return True
                    else:
                        # Corrected f-string
                        status_detail = f"entry found: {authorization_entry is not None}, is_authorized flag: {authorization_entry.is_authorized if authorization_entry else 'N/A'}"
                        self.logger.info(f"User {platform_user_id} (DBID: {user.id}) IS NOT authorized for agent {self.agent_id} via DB ({status_detail}).")
                        await self.redis_client_bot.set(cache_key, "false", ex=REDIS_USER_CACHE_TTL // 4) 
                        return False
        except redis.RedisError as e:
            self.logger.error(f"Redis error during authorization check for agent {self.agent_id}, user {platform_user_id}: {e}", exc_info=True)
            return False # Default to not authorized on Redis error
        except Exception as e:
            self.logger.error(f"Unexpected error during authorization check for agent {self.agent_id}, user {platform_user_id}: {e}", exc_info=True)
            return False # Default to not authorized on other errors

    # --- Aiogram Handlers ---
    async def _handle_start_command(self, message: Message):
        platform_user_id = str(message.from_user.id)
        self.logger.info(f"User {platform_user_id} (ChatID: {message.chat.id}) triggered /start for agent {self.agent_id}")
        await message.answer("Привет! Задайте мне вопрос.")

    async def _handle_login_command(self, message: Message):
        platform_user_id = str(message.from_user.id)
        self.logger.info(f"User {platform_user_id} (ChatID: {message.chat.id}) triggered /login for agent {self.agent_id}")
        
        is_authorized = await self._check_user_authorization(platform_user_id)
        if is_authorized:
            self.logger.info(f"User {platform_user_id} is already authorized for agent {self.agent_id}.")
            await message.answer("Вы уже авторизованы! Можете продолжать работу.", reply_markup=ReplyKeyboardRemove())
        else:
            self.logger.info(f"User {platform_user_id} is not authorized for agent {self.agent_id}. Requesting contact for login.")
            await message.answer(
                "Для авторизации, пожалуйста, поделитесь своим контактом, нажав кнопку ниже:",
                reply_markup=self._request_contact_markup()
            )

    async def _handle_contact(self, message: Message):
        if not self.bot: # Should be initialized if handlers are running
            self.logger.error("Bot not initialized, cannot handle contact.")
            await message.answer("Бот не инициализирован. Пожалуйста, попробуйте позже.")
            return

        if not self.db_session_factory:
            self.logger.error("Database session factory not configured. Cannot process contact.")
            await message.answer("❌ Ошибка: База данных не настроена. Невозможно обработать контакт.")
            return

        contact_platform_user_id = str(message.contact.user_id) if message.contact.user_id else None
        phone_number = message.contact.phone_number
        first_name = message.contact.first_name
        last_name = message.contact.last_name
        
        telegram_user_id_from_message = str(message.from_user.id)

        self.logger.info(
            f"Received contact from Telegram UserID {telegram_user_id_from_message} (ChatID: {message.chat.id}). "
            f"Contact details: Phone {phone_number}, ContactPlatformUserID {contact_platform_user_id}. For agent {self.agent_id}"
        )

        if not contact_platform_user_id or contact_platform_user_id != telegram_user_id_from_message:
            self.logger.warning(
                f"Contact's platform_user_id ({contact_platform_user_id}) does not match "
                f"sender's Telegram ID ({telegram_user_id_from_message}). Ignoring contact."
            )
            await message.answer(
                "Похоже, вы пытаетесь поделиться чужим контактом. Пожалуйста, поделитесь своим собственным контактом.",
                reply_markup=ReplyKeyboardRemove()
            )
            return

        async with self.db_session_factory() as session:
            try:
                user_details_for_update: Dict[str, Any] = {
                    "phone_number": phone_number, "first_name": first_name, "last_name": last_name,
                    "username": message.from_user.username 
                }
                user_details_for_update = {k: v for k, v in user_details_for_update.items() if v is not None}

                created_or_updated_user = await user_crud.create_or_update_user(
                    session, platform="telegram", platform_user_id=contact_platform_user_id,
                    user_details=user_details_for_update
                )

                if not created_or_updated_user:
                    await message.answer("❌ Не удалось сохранить информацию о пользователе.", reply_markup=ReplyKeyboardRemove())
                    return

                auth_record = await user_crud.update_agent_user_authorization(
                    session, agent_id=self.agent_id, user_id=created_or_updated_user.id, is_authorized=True
                )

                if auth_record and auth_record.is_authorized:
                    self.logger.info(f"User {created_or_updated_user.id} (TG: {contact_platform_user_id}) authorized for agent {self.agent_id}")
                    if self.redis_client_bot:
                        cache_key = f"{USER_CACHE_PREFIX}telegram:{contact_platform_user_id}:agent:{self.agent_id}"
                        try:
                            await self.redis_client_bot.delete(cache_key)
                            self.logger.info(f"Auth cache cleared for {cache_key}")
                        except redis.RedisError as e_redis_del:
                            self.logger.error(f"Redis error clearing auth cache for {cache_key}: {e_redis_del}")
                    
                    await message.answer(
                        "✅ Спасибо! Вы успешно авторизованы.",
                        reply_markup=ReplyKeyboardRemove()
                    )
                else:
                    self.logger.error(f"Failed to update auth status for user {created_or_updated_user.id}, agent {self.agent_id}")
                    await message.answer("❌ Ошибка при обновлении статуса авторизации.", reply_markup=ReplyKeyboardRemove())

            except Exception as e_contact:
                self.logger.error(f"Error processing contact for user {contact_platform_user_id}, agent {self.agent_id}: {e_contact}", exc_info=True)
                await message.answer("❌ Внутренняя ошибка при обработке контакта.", reply_markup=ReplyKeyboardRemove())

    async def _handle_text_message(self, message: Message):
        if not self.bot: return # Should be initialized
        if not message.text: return # Ignore empty messages

        chat_id = message.chat.id
        platform_user_id = str(message.from_user.id)
        user_message_text = message.text

        self.logger.info(f"Text from {platform_user_id} in chat {chat_id} for agent {self.agent_id}: '{user_message_text[:50]}...'")

        # Start typing indicator
        if chat_id in self.typing_tasks: # Cancel previous if any
            self.typing_tasks[chat_id].cancel()
        self.typing_tasks[chat_id] = asyncio.create_task(self._send_typing_periodically(chat_id))

        try:
            is_authorized = await self._check_user_authorization(platform_user_id)
            user_data: Dict[str, Any] = {"is_authenticated": is_authorized, "user_id": platform_user_id}

            if is_authorized and self.db_session_factory:
                async with self.db_session_factory() as session:
                    db_user = await user_crud.get_user_by_platform_id(session, platform="telegram", platform_user_id=platform_user_id)
                    if db_user:
                        user_data.update({
                            "phone_number": db_user.phone_number, "first_name": db_user.first_name,
                            "last_name": db_user.last_name, "username": db_user.username
                        })
                    else:
                        self.logger.warning(f"User {platform_user_id} authorized but not in DB. Sending minimal data.")
            elif is_authorized:
                 self.logger.warning(f"DB session factory not configured. Cannot fetch full user details for {platform_user_id}.")


            await self._publish_to_agent(chat_id, platform_user_id, user_message_text, user_data)
            self.logger.info(f"Message from {platform_user_id} published to agent {self.agent_id}. User data: {user_data}")

        except Exception as e:
            self.logger.error(f"Error handling text message from chat {chat_id} for agent {self.agent_id}: {e}", exc_info=True)
            try:
                await message.answer("⚠️ Ошибка при обработке запроса.")
            except Exception as e_reply:
                self.logger.error(f"Failed to send error reply to chat {chat_id}: {e_reply}")
        finally:
            # Stop typing indicator after a short delay, allowing agent to respond
            await asyncio.sleep(1) 
            if chat_id in self.typing_tasks:
                self.typing_tasks[chat_id].cancel()
                # No need to await here, _send_typing_periodically handles its own cleanup on cancel


    async def _register_handlers(self):
        """Registers aiogram message handlers."""
        if not self.dp:
            self.logger.error("Dispatcher not initialized. Cannot register handlers.")
            return

        self.dp.message(CommandStart())(self._handle_start_command)
        self.dp.message(Command("login"))(self._handle_login_command)
        self.dp.message(F.contact)(self._handle_contact)
        self.dp.message(F.text)(self._handle_text_message)
        # Add other handlers as needed

        self.logger.info("Aiogram handlers registered.")


    async def _redis_output_listener_loop(self):
        """Listens to Redis for messages from the agent and sends them to the user."""
        if not self.bot:
            self.logger.critical("Bot is not initialized. Redis listener cannot function.")
            return
        
        redis_cli = await self._get_redis_client_for_bot() # Ensures client is connected
        if not redis_cli: # Should not happen if _get_redis_client_for_bot raises on failure
             self.logger.critical("Redis client (bot) is not initialized. Redis listener cannot function.")
             return

        output_channel = f"agent:{self.agent_id}:output"
        pubsub = None
        
        self.logger.info(f"Redis output listener starting for channel: {output_channel}")

        while self._running: # Check self._running flag from RunnableComponent
            try:
                if not await redis_cli.ping():
                    self.logger.warning(f"Redis ping failed in listener for {output_channel}. Re-establishing pubsub.")
                    if pubsub:
                        try: await pubsub.aclose()
                        except: pass # Ignore errors on close
                    pubsub = None
                    await asyncio.sleep(settings.REDIS_RECONNECT_INTERVAL if hasattr(settings, 'REDIS_RECONNECT_INTERVAL') else 5)
                    continue

                if pubsub is None:
                    pubsub = redis_cli.pubsub()
                    await pubsub.subscribe(output_channel)
                    self.logger.info(f"Subscribed to Redis channel: {output_channel}")

                # Get message with timeout to allow checking self._running
                redis_msg = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
                
                if not self._running: break # Exit loop if component is stopping

                if redis_msg and redis_msg.get("type") == "message":
                    data_str = redis_msg.get("data")
                    self.logger.info(f"Received message from {output_channel}: {data_str[:250]}...")
                    
                    payload: Optional[Dict[str, Any]] = None
                    chat_id_from_payload: Optional[int] = None
                    try:
                        payload = json.loads(data_str)
                        if not isinstance(payload, dict):
                            self.logger.error(f"Decoded payload is not a dictionary: {type(payload)}")
                            continue

                        thread_id_str = payload.get("thread_id")
                        if not thread_id_str:
                            self.logger.error(f"Missing 'thread_id' in payload from {output_channel}: {payload}")
                            continue
                        
                        try:
                            chat_id_from_payload = int(thread_id_str)
                        except ValueError:
                            self.logger.error(f"Invalid 'thread_id' format: {thread_id_str} in payload: {payload}")
                            continue

                        # Stop typing indicator for this chat if one was active
                        if chat_id_from_payload in self.typing_tasks:
                            self.typing_tasks[chat_id_from_payload].cancel()
                            # del self.typing_tasks[chat_id_from_payload] # _send_typing_periodically will del

                        response_content = payload.get("response")
                        
                        auth_triggered = isinstance(response_content, str) and AUTH_TRIGGER in response_content

                        if auth_triggered:
                            self.logger.info(f"AUTH_TRIGGER detected for chat {chat_id_from_payload}.")
                            modified_response = response_content.replace(AUTH_TRIGGER, "").strip()
                            
                            if modified_response:
                                await self.bot.send_message(chat_id_from_payload, modified_response)
                            
                            try:
                                await self.bot.send_message(
                                    chat_id_from_payload,
                                    "Пожалуйста, авторизуйтесь с помощью команды /login или поделитесь своим контактом.",
                                    reply_markup=self._request_contact_markup()
                                )
                            except Exception as e_auth_prompt:
                                self.logger.error(f"Failed to send auth prompt to {chat_id_from_payload}: {e_auth_prompt}")
                        
                        else: # Not an auth trigger in response
                            if payload.get("chat_action"):
                                # Handle chat actions if needed, e.g., typing from agent
                                pass # Example: await self.bot.send_chat_action(chat_id_from_payload, payload.get("chat_action"))
                            
                            elif isinstance(response_content, str):
                                await self.bot.send_message(chat_id_from_payload, response_content)
                            
                            elif payload.get("message") and isinstance(payload.get("message"), str):
                                # Fallback if "response" is not used but "message" is (for compatibility)
                                await self.bot.send_message(chat_id_from_payload, payload.get("message"))
                            else:
                                self.logger.warning(f"No valid message content found in payload for chat {chat_id_from_payload}: {payload}")

                    except json.JSONDecodeError:
                        self.logger.error(f"JSONDecodeError for message from {output_channel}: {data_str}", exc_info=True)
                        if self.bot and chat_id_from_payload: await self.bot.send_message(chat_id_from_payload, "Ошибка: Неверный формат ответа от агента.")
                    except TelegramBadRequest as e_tg_br:
                        self.logger.error(f"Telegram API error sending message to chat {chat_id_from_payload}: {e_tg_br}", exc_info=True)
                        # Consider if specific error codes need handling (e.g., bot blocked by user)
                    except Exception as e_inner:
                        self.logger.error(f"Error processing message from {output_channel} for chat {chat_id_from_payload}: {e_inner}", exc_info=True)
                        if self.bot and chat_id_from_payload:
                            try:
                                await self.bot.send_message(chat_id_from_payload, "Произошла ошибка при обработке ответа от агента.")
                            except Exception:
                                self.logger.error(f"Failed to send error message to chat {chat_id_from_payload} after inner processing error.")
                
                # await asyncio.sleep(0.01) # Short sleep if no message, timeout handles this mostly
            except asyncio.CancelledError:
                self.logger.info(f"Redis listener for {output_channel} cancelled.")
                break # Exit loop
            except Exception as e_outer: # Catch-all for unexpected errors in the loop
                self.logger.error(f"Unexpected error in Redis listener for {output_channel}: {e_outer}", exc_info=True)
                if pubsub:
                    try:
                        await pubsub.unsubscribe(output_channel) # Try to unsubscribe before closing
                        await pubsub.aclose()
                    except Exception as pubsub_close_err:
                        self.logger.error(f"Error closing pubsub in outer exception handler: {pubsub_close_err}")                    
                pubsub = None # Reset pubsub to force re-initialization
                await asyncio.sleep(5) # Wait before retrying loop
        
        # Cleanup pubsub when loop exits
        if pubsub:
            try:
                await pubsub.unsubscribe(output_channel)
                await pubsub.aclose()
                self.logger.info(f"Unsubscribed and closed pubsub for {output_channel}")
            except Exception as clean_e:
                self.logger.error(f"Error cleaning up pubsub for {output_channel}: {clean_e}", exc_info=True)
        self.logger.info(f"Redis output listener for {output_channel} has stopped.")


    # --- Core Lifecycle Methods (setup, run_loop, cleanup) ---
    async def setup(self) -> None:
        """
        Prepare the Telegram bot for running.
        """
        self.logger.info(f"[{self._component_id}] TelegramIntegrationBot setup started.")
        self.needs_restart = False
        # self._running is managed by RunnableComponent.run() before calling setup.

        # Setup StatusUpdater. It will use self._component_id, self._status_key_prefix,
        # and the redis_url passed here (self.redis_url_status).
        await self.setup_status_updater(redis_url=self.redis_url_status) 
        await self.mark_as_initializing(details={"bot_token_present": bool(self.bot_token)})

        if not self.bot_token:
            self.logger.error(f"[{self._component_id}] Bot token is not provided. Cannot start Telegram bot.")
            await self.mark_as_error("Bot token missing")
            raise ValueError("Bot token is required.")

        self.bot = Bot(token=self.bot_token, default=DefaultBotProperties(parse_mode=ParseMode.MARKDOWN))
        self.dp = Dispatcher()
        self.logger.info(f"[{self._component_id}] Aiogram Bot and Dispatcher initialized.")

        await self._register_handlers()

        try:
            # Initialize bot's own redis client using self.redis_url_bot_operations
            self.redis_client_bot = await self._get_redis_client_for_bot() 
            self.logger.info(f"[{self._component_id}] Redis client for bot operations obtained.")
        except Exception as e:
            self.logger.error(f"[{self._component_id}] Failed to initialize Redis client for bot: {e}", exc_info=True)
            await self.mark_as_error(f"Redis client init failed: {e}")
            raise RuntimeError(f"Redis client initialization failed for bot: {e}") from e
        
        await self.mark_as_running(pid=os.getpid() if hasattr(os, 'getpid') else None, details={"message": "Bot setup complete, awaiting run_loop."})
        self.logger.info(f"[{self._component_id}] TelegramIntegrationBot setup complete.")

    async def run_loop(self) -> None:
        """
        Core execution loop for the Telegram Bot.
        """
        if not self.bot or not self.dp or not self.redis_client_bot:
            self.logger.error("Bot, Dispatcher, or Redis client not initialized. Cannot start run_loop.")
            await self.mark_as_error("Core components not initialized for run_loop")
            self._running = False
            return

        self.logger.info(f"TelegramIntegrationBot run_loop started for agent {self.agent_id}. Polling for updates...")
        
        self.redis_listener_task = asyncio.create_task(
            self._redis_output_listener_loop(), 
            name=f"TelegramBotRedisListener-{self.agent_id}"
        )
        self.logger.info(f"Redis output listener task ({self.redis_listener_task.get_name()}) created and will start.")

        try:
            self.polling_task = asyncio.create_task(
                self.dp.start_polling(self.bot, handle_signals=False, allowed_updates=self.dp.resolve_used_update_types()),
                name=f"AiogramPolling-{self.agent_id}"
            )
            self.logger.info(f"Aiogram polling task ({self.polling_task.get_name()}) created and will start.")
            
            # Monitor tasks
            tasks_to_monitor: List[Optional[asyncio.Task]] = [self.polling_task, self.redis_listener_task]
            # Filter out None tasks just in case, though they should be set
            active_tasks = [task for task in tasks_to_monitor if task is not None]

            if not active_tasks:
                self.logger.warning("No active tasks (polling, redis_listener) to monitor in run_loop. Exiting.")
                self._running = False
                await self.mark_as_error("No tasks to run in run_loop")
                return

            done, pending = await asyncio.wait(active_tasks, return_when=asyncio.FIRST_COMPLETED)

            for task in done:
                task_name = task.get_name() if hasattr(task, 'get_name') else "Unknown Task"
                try:
                    result = task.result() # This will raise exception if task failed
                    self.logger.info(f"Task {task_name} completed in run_loop. Result: {result}")
                except asyncio.CancelledError:
                    self.logger.info(f"Task {task_name} was cancelled.")
                except Exception as e:
                    self.logger.error(f"Task {task_name} failed: {e}", exc_info=True)
                    self._running = False 
                    self.needs_restart = False # Do not restart on critical task failure
                    await self.mark_as_error(f"Critical task {task_name} failed: {e}")
            
            # If one task finishes (e.g. polling due to an error, or listener due to critical redis issue),
            # we should stop the other and exit the run_loop.
            # The self._running flag will be set to False if a critical task failed.
            # RunnableComponent's main loop will then call cleanup.

        except asyncio.CancelledError:
            self.logger.info(f"TelegramIntegrationBot run_loop for agent {self.agent_id} was cancelled (likely by shutdown signal).")
            # Tasks will be cleaned up in the cleanup method
            # No need to re-raise if self._running is already False due to signal handler
        except Exception as e:
            self.logger.critical(f"Unexpected error in TelegramIntegrationBot run_loop for {self.agent_id}: {e}", exc_info=True)
            self._running = False
            self.needs_restart = False
            await self.mark_as_error(f"Run_loop critical error: {e}")
        finally:
            self.logger.info(f"TelegramIntegrationBot run_loop for agent {self.agent_id} finishing.")
            # Ensure self._running is false so RunnableComponent exits if it hasn't already been signalled
            self._running = False 
            # Cleanup of tasks is primarily handled in the cleanup() method,
            # which will be called by RunnableComponent's main execution logic.


    async def cleanup(self) -> None:
        """Cleans up resources used by the Telegram Bot."""
        self.logger.info(f"[{self._component_id}] Starting cleanup...")
        # self._running is managed by RunnableComponent

        # 0. Cancel any active typing tasks
        if self.typing_tasks:
            self.logger.info(f"Cancelling {len(self.typing_tasks)} active typing tasks...")
            for chat_id, task in list(self.typing_tasks.items()): # list() for safe iteration
                if task and not task.done():
                    task.cancel()
            # Give them a moment to process cancellation
            await asyncio.gather(*(task for task in self.typing_tasks.values() if task and not task.done()), return_exceptions=True)
            self.typing_tasks.clear()
            self.logger.info("Active typing tasks processed for cancellation.")


        # 1. Stop Aiogram Dispatcher polling task
        if self.polling_task and not self.polling_task.done():
            self.logger.info(f"Cancelling Aiogram polling task ({self.polling_task.get_name()})...")
            self.polling_task.cancel()
        
        # 2. Cancel Redis listener task
        if self.redis_listener_task and not self.redis_listener_task.done():
            self.logger.info(f"Cancelling Redis listener task ({self.redis_listener_task.get_name()})...")
            self.redis_listener_task.cancel()

        # Gather all main tasks
        tasks_to_await_cleanup = []
        if self.polling_task: tasks_to_await_cleanup.append(self.polling_task)
        if self.redis_listener_task: tasks_to_await_cleanup.append(self.redis_listener_task)

        if tasks_to_await_cleanup:
            self.logger.info(f"Waiting for {len(tasks_to_await_cleanup)} main tasks to complete cancellation...")
            results = await asyncio.gather(*tasks_to_await_cleanup, return_exceptions=True)
            for task, result in zip(tasks_to_await_cleanup, results):
                task_name = task.get_name() if hasattr(task, 'get_name') and task.get_name() else "Unnamed Task"
                if isinstance(result, asyncio.CancelledError):
                    self.logger.info(f"Task {task_name} was cancelled successfully during cleanup.")
                elif isinstance(result, Exception):
                     self.logger.error(f"Task {task_name} raised an exception during cleanup/cancellation: {result}", exc_info=isinstance(result, BaseException)) # Log stack if it's an actual exception
                else:
                    self.logger.info(f"Task {task_name} completed with result: {result}")
        
        # 3. Close Aiogram Bot session
        if self.bot and self.bot.session:
            self.logger.info("Closing Aiogram Bot session...")
            try:
                await self.bot.session.close()
                self.logger.info("Aiogram Bot session closed.")
            except Exception as e:
                self.logger.error(f"Error closing Aiogram Bot session: {e}", exc_info=True)
        self.bot = None # Clear bot instance
        self.dp = None  # Clear dispatcher instance

        # 4. Close Redis client for bot operations
        if self.redis_client_bot:
            self.logger.info(f"[{self._component_id}] Closing Redis client for bot operations (URL: {self.redis_url_bot_operations})...")
            try:
                await self.redis_client_bot.aclose()
                self.logger.info(f"[{self._component_id}] Redis client for bot operations closed.")
            except Exception as e:
                self.logger.error(f"[{self._component_id}] Error closing Redis client for bot operations: {e}", exc_info=True)
        self.redis_client_bot = None

        # 5. Call StatusUpdater's cleanup (uses self.redis_url_status)
        # Pass clear_status_on_cleanup based on policy, default is False.
        # For integrations, it's typical to leave the "stopped" status.
        await self.cleanup_status_updater(clear_status_on_cleanup=False) 
        
        self.logger.info(f"[{self._component_id}] Cleanup complete.")

    # --- Overrides from RunnableComponent ---
    # Remove _run_signal_handler, get_restart_flag, set_restart_flag, and run methods.
    # These are now fully handled by the base RunnableComponent.
    # Signal handling is done by RunnableComponent.run() which calls _handle_signal.
    # _handle_signal then sets self._running = False, and run_loop should respect that.
    # The main run() method in RunnableComponent manages setup, run_loop, and cleanup.

# Removed:
# async def _run_signal_handler(self, signum, frame):
# def get_restart_flag(self) -> bool:
# def set_restart_flag(self, value: bool) -> None:
# async def run(self) -> None:

# The main entry point for running this component will be `asyncio.run(instance.run())`
# where `instance.run()` is the method from `RunnableComponent`.
