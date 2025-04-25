import os
import logging
import asyncio
import json
import argparse
from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart, Command
from aiogram.types import Message, ReplyKeyboardRemove, ReplyKeyboardMarkup, KeyboardButton
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode, ChatAction
from dotenv import load_dotenv
import redis.asyncio as redis
from redis import exceptions as redis_exceptions
from contextlib import asynccontextmanager

# --- Configuration & Setup ---
load_dotenv(dotenv_path=os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '.env')))

# --- УДАЛЕНО: Глобальный токен из .env ---
# TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
# --- Конец удаления ---
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379") # Оставляем для Redis

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
AUTH_TRIGGER = "[AUTH_REQUIRED]"

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

async def publish_to_agent(agent_id: str, chat_id: int, user_message: str, user_data: dict):
    """Publishes a message to the agent's Redis input channel."""
    if not redis_client:
        logger.error("Redis client not available for publishing.")
        await bot.send_message(chat_id, "Ошибка: Не удалось подключиться к сервису агента.")
        return

    input_channel = f"agent:{agent_id}:input"
    payload = {
        "message": user_message,
        "thread_id": str(chat_id), # Ensure thread_id is string
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
                try:
                    data = json.loads(message['data'])
                    chat_id = data.get("thread_id") # Assuming thread_id is the chat_id
                    response = data.get("response")
                    error = data.get("error")

                    if chat_id:
                        if error:
                            logger.error(f"Received error from agent for chat {chat_id}: {error}")
                            await bot.send_message(chat_id, f"Произошла ошибка: {error}")
                        elif response:
                            logger.info(f"Received response from agent for chat {chat_id}: {response[:100]}...")
                            await bot.send_message(chat_id, response)
                        else:
                            logger.warning(f"Received message from agent without response or error: {data}")
                    else:
                        logger.warning(f"Received message from agent without thread_id (chat_id): {data}")

                except json.JSONDecodeError:
                    logger.error(f"Failed to decode JSON from Redis: {message['data']}")
                except Exception as e:
                    logger.error(f"Error processing message from Redis: {e}", exc_info=True)
                    # Optionally send error to a default chat_id if available

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

@dp.message(F.contact)
async def handle_contact(message: Message):
    """Handles receiving user contact information for authorization."""
    user_id = message.from_user.id
    contact = message.contact
    chat_id = message.chat.id

    if contact.user_id == user_id:
        authorized_users[chat_id] = contact
        logger.info(f"User {user_id} authorized in chat {chat_id} with phone {contact.phone_number}")
        await message.answer(
            "✅ Авторизация прошла успешно!",
            reply_markup=ReplyKeyboardRemove() # Remove the contact button
        )
        # Optionally send a confirmation message to the agent
        user_data = {
            "is_authenticated": True,
            "user_id": contact.user_id,
            "phone_number": contact.phone_number,
            "first_name": contact.first_name,
            "last_name": contact.last_name
        }
        await publish_to_agent(agent_id_global, chat_id, "Пользователь успешно авторизовался.", user_data)
    else:
        await message.answer("❌ Пожалуйста, поделитесь *своим* контактом для авторизации.")

@dp.message()
async def handle_message(message: Message):
    """Handles regular text messages from the user."""
    chat_id = message.chat.id
    user_message = message.text # Use .text for plain text

    if not user_message: # Ignore empty messages or non-text messages not handled elsewhere
        return

    typing_task = asyncio.create_task(send_typing_periodically(chat_id))

    try:
        user_data = {}
        if chat_id in authorized_users:
            contact = authorized_users[chat_id]
            user_data = {
                "is_authenticated": True,
                "user_id": contact.user_id,
                "phone_number": contact.phone_number,
                "first_name": contact.first_name,
                "last_name": contact.last_name
            }
        else:
            user_data = {"is_authenticated": False}

        await publish_to_agent(agent_id_global, chat_id, user_message, user_data)
        # Don't wait for response here, the redis_output_listener handles it

    except Exception as e:
        logger.error(f"Error handling message from chat {chat_id}: {e}", exc_info=True)
        await message.answer("⚠ Произошла ошибка при обработке вашего запроса.")
    finally:
        # Stop the typing simulation *after* publishing the message
        # The listener will handle sending the actual response when it arrives
        await asyncio.sleep(1) # Keep typing briefly after sending
        typing_task.cancel()


# --- Application Lifecycle ---
@asynccontextmanager
async def lifespan(dp: Dispatcher, agent_id: str, bot_token: str): # Добавляем bot_token
    """Manages application startup and shutdown."""
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

