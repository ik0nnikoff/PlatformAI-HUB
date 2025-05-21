import asyncio
import json
import logging
import os
from typing import Optional, Dict, Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart, Command
from aiogram.types import Message, ReplyKeyboardRemove, ReplyKeyboardMarkup, KeyboardButton, User
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode, ChatAction
from aiogram.exceptions import TelegramBadRequest
from redis import exceptions as redis_exceptions

from app.core.config import settings
from app.core.base.service_component import ServiceComponentBase
from app.db.crud import user_crud
from app.api.schemas.common_schemas import IntegrationType


# Constants
REDIS_USER_CACHE_TTL = getattr(settings, "REDIS_USER_CACHE_TTL", int(os.getenv("REDIS_USER_CACHE_TTL", 3600)))
USER_CACHE_PREFIX = "user_cache:"
AUTH_TRIGGER = "AUTH_REQUIRED"


class TelegramIntegrationBot(ServiceComponentBase):
    """
    Manages the lifecycle and execution of a Telegram Bot integration for a specific agent.
    Inherits from ServiceComponentBase for unified state and lifecycle management.
    """

    def __init__(self,
                 agent_id: str,
                 bot_token: str,
                 db_session_factory: Optional[async_sessionmaker[AsyncSession]],
                 logger_adapter: logging.LoggerAdapter,
                 ):

        # Initialize ServiceComponentBase (which calls RunnableComponent and StatusUpdater inits)
        super().__init__(component_id=f"{agent_id}:{IntegrationType.TELEGRAM.value}",
                         status_key_prefix="integration_status:",
                         logger_adapter=logger_adapter)

        self.agent_id = agent_id # Still useful to have directly for some logic
        self.bot_token = bot_token
        self.db_session_factory = db_session_factory
        self._pubsub_channel = f"agent:{self.agent_id}:output"

        # --- Aiogram and Bot specific attributes ---
        self.bot: Optional[Bot] = None
        self.dp: Optional[Dispatcher] = None

        self.typing_tasks: Dict[int, asyncio.Task] = {} # To manage typing indicator tasks
        self.logger.info(f"TelegramIntegrationBot initialized. PID: {os.getpid()}")

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
        try:
            redis_cli = await self.redis_client # Use inherited client
        except RuntimeError as e:
            self.logger.error(f"Redis client not available for publishing to agent: {e}")
            await self.bot.send_message(chat_id, "Ошибка: Не удалось связаться с агентом (сервис недоступен).")
            return

        input_channel = f"agent:{self.agent_id}:input"

        payload = {
            "text": message_text,  # Changed from "message" to "text"
            "chat_id": str(chat_id),  # Changed from "thread_id" to "chat_id"
            "platform_user_id": platform_user_id,
            "user_data": user_data,
            "channel": "telegram"
        }
        try:
            await redis_cli.publish(input_channel, json.dumps(payload).encode('utf-8'))
            self.logger.info(f"Published message to {input_channel} for chat {chat_id}")
        except redis_exceptions.RedisError as e:
            self.logger.error(f"Redis error publishing to {input_channel}: {e}", exc_info=True)
            await self.bot.send_message(chat_id, "Ошибка: Не удалось отправить сообщение агенту.")
        except Exception as e:
            self.logger.error(f"Unexpected error publishing to {input_channel}: {e}", exc_info=True)
            await self.bot.send_message(chat_id, "Произошла внутренняя ошибка при отправке сообщения.")


    async def _check_user_authorization(self, platform_user_id: str) -> bool:
        try:
            redis_cli = await self.redis_client # Use inherited client
        except RuntimeError as e:
            self.logger.error(f"Redis client not available for auth check: {e}")
            return False

        cache_key = f"{USER_CACHE_PREFIX}telegram:{platform_user_id}:agent:{self.agent_id}"
        try:
            self.logger.debug(f"Auth check: Attempting to get cache for key '{cache_key}'")
            cached_auth_status_bytes = await redis_cli.get(cache_key)
            if cached_auth_status_bytes is not None:
                cached_auth_status = cached_auth_status_bytes.decode('utf-8') # Decode bytes to string
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
                        # Encode string to bytes for Redis set
                        await redis_cli.set(cache_key, "false".encode('utf-8'), ex=REDIS_USER_CACHE_TTL // 4)
                        return False
                    
                    authorization_entry = await user_crud.get_agent_user_authorization(
                        session,
                        agent_id=self.agent_id,
                        user_id=user.id
                    )
                    
                    if authorization_entry and authorization_entry.is_authorized:
                        self.logger.info(f"User {platform_user_id} (DBID: {user.id}) IS authorized for agent {self.agent_id} via DB.")
                        await redis_cli.set(cache_key, "true".encode('utf-8'), ex=REDIS_USER_CACHE_TTL)
                        return True
                    else:
                        status_detail = f"entry found: {authorization_entry is not None}, is_authorized flag: {authorization_entry.is_authorized if authorization_entry else 'N/A'}"
                        self.logger.info(f"User {platform_user_id} (DBID: {user.id}) IS NOT authorized for agent {self.agent_id} via DB ({status_detail}).")
                        await redis_cli.set(cache_key, "false".encode('utf-8'), ex=REDIS_USER_CACHE_TTL // 4) 
                        return False
        except redis_exceptions.RedisError as e:
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
            self.logger.error(f"Bot not initialized, cannot handle contact.")
            await message.answer("Бот не инициализирован. Пожалуйста, попробуйте позже.")
            return

        if not self.db_session_factory:
            self.logger.error("Database session factory not configured. Cannot process contact.")
            await message.answer("Ошибка: База данных не настроена. Невозможно обработать контакт.")
            return

        user_id = message.from_user.id
        chat_id = message.chat.id
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
                    await message.answer("Не удалось сохранить информацию о пользователе.", reply_markup=ReplyKeyboardRemove())
                    return

                auth_record = await user_crud.update_agent_user_authorization(
                    session, agent_id=self.agent_id, user_id=created_or_updated_user.id, is_authorized=True
                )

                if auth_record and auth_record.is_authorized:
                    self.logger.info(f"User {created_or_updated_user.id} (TG: {contact_platform_user_id}) authorized for agent {self.agent_id}")
                    
                    try:
                        redis_cli = await self.redis_client # Use inherited client
                        cache_key = f"{USER_CACHE_PREFIX}telegram:{contact_platform_user_id}:agent:{self.agent_id}"
                        await redis_cli.delete(cache_key)
                        self.logger.info(f"Auth cache cleared for {cache_key}")
                    except RuntimeError as e_redis_runtime:
                        self.logger.error(f"Redis client not available for cache clearing: {e_redis_runtime}")
                    except redis_exceptions.RedisError as e_redis_del:
                        self.logger.error(f"Redis error clearing auth cache for {cache_key}: {e_redis_del}")
                    
                    await message.answer(
                        "Спасибо! Вы успешно авторизованы.",
                        reply_markup=ReplyKeyboardRemove()
                    )
                    # Отправляем сообщение агенту (оставляем, как просил пользователь)
                    agent_user_data = {
                        "is_authenticated": True,
                        "user_id": user_id,
                        "phone_number": phone_number,
                        "first_name": first_name,
                        "last_name": last_name
                    }
                    await self._publish_to_agent(
                        chat_id,
                        user_id,
                        "Пользователь успешно авторизовался.",
                        agent_user_data
                    )
                else:
                    self.logger.error(f"Failed to update auth status for user {created_or_updated_user.id}, agent {self.agent_id}")
                    await message.answer("Ошибка при обновлении статуса авторизации.", reply_markup=ReplyKeyboardRemove())

            except Exception as e_contact:
                self.logger.error(f"Error processing contact for user {contact_platform_user_id}, agent {self.agent_id}: {e_contact}", exc_info=True)
                await message.answer("Внутренняя ошибка при обработке контакта.", reply_markup=ReplyKeyboardRemove())

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


    async def _handle_pubsub_message(self, message_data: bytes) -> None: # <--- Изменена сигнатура
        """Handles a message received from Redis Pub/Sub."""
        if not self.bot:
            self.logger.error(f"Bot not initialized, cannot handle pubsub message.")
            return

        try:
            data_str: Optional[str] = None
            if isinstance(message_data, bytes):
                data_str = message_data.decode('utf-8')
            else:
                self.logger.warning(f"Received non-bytes data in pubsub message: {type(message_data)}")
                return

            if data_str is None: # Should not happen if decode is successful
                self.logger.warning(f"Decoded message data is None from: {message_data!r}")
                return

            payload = json.loads(data_str)
            response_channel = payload.get("channel")

            if response_channel == "telegram":
                chat_id_str = payload.get("chat_id")
                response = payload.get("response")
                error = payload.get("error")
                auth_required = False

                if not response or not chat_id_str:
                    self.logger.warning(f"Missing response or chat_id in pubsub message: {payload}")
                    return

                try:
                    chat_id = int(chat_id_str)
                except ValueError:
                    self.logger.error(f"Invalid chat_id format: {chat_id_str}")
                    return

                if error:
                    self.logger.error(f"Received error from agent for chat {chat_id}: {error}")
                    await self.bot.send_message(chat_id, f"Произошла ошибка: {error}")
                elif response:
                    self.logger.info(f"Received response from agent for chat {chat_id}: {response[:100]}...")

                    if AUTH_TRIGGER in response:
                        auth_required = True
                        response = response.replace(AUTH_TRIGGER, "").strip()

                    is_user_authorized = False # По умолчанию
                    if auth_required:
                        is_user_authorized = await self._check_user_authorization(chat_id)
                        self.logger.debug(f"Checked authorization for agent {self.agent_id}, user {chat_id} due to AUTH_TRIGGER: {is_user_authorized}")

                    if auth_required and not is_user_authorized:
                        await self.bot.send_message(
                            chat_id,
                            f"{response}\n\nДля продолжения требуется авторизация. Используйте /login или кнопку ниже:",
                            reply_markup=self._request_contact_markup()
                        )
                    else:
                        if chat_id in self.typing_tasks:
                            task = self.typing_tasks.pop(chat_id)
                            if not task.done():
                                task.cancel()
                                try:
                                    await task
                                except asyncio.CancelledError:
                                    self.logger.debug(f"Typing task for chat {chat_id} cancelled.")
                                except Exception as e_task_cancel:
                                    self.logger.error(f"Error awaiting cancelled typing task for chat {chat_id}: {e_task_cancel}", exc_info=True)

                        self.logger.info(f"Sending message from agent to chat {chat_id}: '{response[:50]}...'")
                        await self.bot.send_message(chat_id, response)

                else:
                    self.logger.warning(f"Received message from agent for chat {chat_id} without response or error: {payload}")

        except UnicodeDecodeError:
            self.logger.error(f"Failed to decode UTF-8 from pubsub message data: {message_data!r}")
        except json.JSONDecodeError:
            self.logger.error(f"Failed to decode JSON from pubsub message: {data_str if 'data_str' in locals() else message_data!r}")
        except TelegramBadRequest as e:
            self.logger.error(f"Telegram API error sending message to {chat_id_str if 'chat_id_str' in locals() else 'unknown chat'}: {e}", exc_info=True)
            if "chat not found" in str(e).lower() or \
               "bot was blocked by the user" in str(e).lower() or \
               "user is deactivated" in str(e).lower():
                self.logger.warning(f"Chat {chat_id_str if 'chat_id_str' in locals() else 'unknown chat'} may be inactive or bot blocked. Consider cleanup.")
                # Potentially mark user as inactive or remove subscription if applicable
            # TODO: Add more specific error handling if needed (e.g., rate limits)
        except Exception as e:
            self.logger.error(f"Error processing pubsub message: {e}", exc_info=True)

    # --- Core Lifecycle Methods (setup, run_loop, cleanup) ---
    async def setup(self) -> None:
        """
        Prepare the Telegram bot for running.
        Inherits from ServiceComponentBase, which handles StatusUpdater and Redis client setup.
        """
        self.logger.info(f"TelegramIntegrationBot setup started.")
        await super().setup() # Calls ServiceComponentBase.setup(), which also clears needs_restart

        if not self.bot_token:
            self.logger.critical(f"Bot token is not configured. Telegram bot cannot start.")
            await self.mark_as_error(reason="Bot token missing")
            # self.initiate_shutdown() # Request shutdown
            raise RuntimeError("Bot token is not configured.")

        self.bot = Bot(token=self.bot_token, default=DefaultBotProperties(parse_mode=ParseMode.MARKDOWN))
        self.dp = Dispatcher()
        self.logger.info(f"Aiogram Bot and Dispatcher initialized.")

        await self._register_handlers()

        self.logger.info(f"TelegramIntegrationBot setup complete.")


    async def run_loop(self) -> None:
        """
        Core execution loop for the Telegram Bot.
        """

        self.logger.info(f"TelegramIntegrationBot run_loop started for agent {self.agent_id}. Polling for updates...")
        
        self._register_main_task(self._pubsub_listener_loop(), name="RedisOutputListener")
        
        async def polling_wrapper():
            if not self.dp or not self.bot:
                self.logger.error("DP or Bot not available for polling_wrapper")
                return
            try:
                self.logger.info(f"Starting aiogram polling...")
                await self.dp.start_polling(self.bot, allowed_updates=self.dp.resolve_used_update_types())
            except asyncio.CancelledError:
                self.logger.info(f"Aiogram polling task cancelled.")
            except Exception as e_poll:
                self.logger.error(f"Aiogram polling failed: {e_poll}", exc_info=True)
                await self.mark_as_error(f"Polling error: {e_poll}")
                self.request_restart()
            finally:
                self.logger.info(f"Aiogram polling has stopped.")
        
        self._register_main_task(polling_wrapper(), name="AiogramPolling")

        try:
            await super().run_loop()
        except Exception as e:
            self.logger.critical(f"Unexpected error in TelegramIntegrationBot run_loop for {self.agent_id}: {e}", exc_info=True)
            self._running = False
            self.clear_restart_request()
            await self.mark_as_error(f"Run_loop critical error: {e}")
        finally:
            self.logger.info(f"TelegramIntegrationBot run_loop for agent {self.agent_id} finishing.")
            self._running = False 


    async def cleanup(self) -> None:
        """
        Clean up resources used by the Telegram bot.
        Inherits from ServiceComponentBase, which handles StatusUpdater and Redis client cleanup.
        """
        self.logger.info(f"TelegramIntegrationBot cleanup started.")
        # self._running is already False if shutdown was initiated properly

        if self.typing_tasks:
            self.logger.info(f"Cancelling {len(self.typing_tasks)} active typing tasks...")
            for chat_id, task in list(self.typing_tasks.items()): # list() for safe iteration
                if task and not task.done():
                    task.cancel()
            await asyncio.gather(*(task for task in self.typing_tasks.values() if task and not task.done()), return_exceptions=True)
            self.typing_tasks.clear()
            self.logger.info("Active typing tasks processed for cancellation.")

        if self.bot and self.bot.session:
            self.logger.info(f"Closing Aiogram bot session.")
            await self.bot.session.close()
            self.logger.info(f"Aiogram bot session closed.")
        
        self.bot = None
        self.dp = None

        await super().cleanup() # Calls ServiceComponentBase.cleanup()
        self.logger.info(f"TelegramIntegrationBot cleanup finished.")
