import os
import logging
import asyncio
from aiogram import Bot, Dispatcher, types
from aiogram.filters import CommandStart, Command
from aiogram.types import Message, ReplyKeyboardRemove
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from dotenv import load_dotenv
# from html import escape
from chatgpt_md_converter import telegram_format
from clear_agent import app

load_dotenv()

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
bot = Bot(token=TELEGRAM_BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher()

logging.basicConfig(level=logging.INFO)

authorized_users = {}
AUTH_TRIGGER = "[AUTH_REQUIRED]"

def request_contact_markup():
    return types.ReplyKeyboardMarkup(
        keyboard=[[types.KeyboardButton(text="Поделиться контактом", request_contact=True)]],
        resize_keyboard=True
    )

async def send_message_to_agent(message: Message, user_message: str = None):
    if user_message is None:
        user_message = message.md_text
    chat_id = message.chat.id

    async def typing_simulation():
        try:
            while True:
                await bot.send_chat_action(chat_id, action="typing")
                await asyncio.sleep(3)
        except asyncio.CancelledError:
            pass

    typing_task = asyncio.create_task(typing_simulation())

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
        
        input = {
            "messages": [
                ("user", user_message),
            ],
            "user_data": user_data,
            "channel": "telegram"
        }
        config = {"configurable": {"thread_id": chat_id}}

        auth_required = False

        # Получаем ответ от агента
        async for output in app.astream(input, config, stream_mode="updates"):
            answer = None
            for key, value in output.items():
                logging.info(f"Output from node '{key}':")
                logging.info("---")
                logging.info(value)
                # answer = value["messages"][-1].content
                if 'messages' not in value:
                    continue
                for msg in value['messages']:
                    meta = getattr(msg, 'response_metadata', {}) or {}
                    if meta.get('finish_reason') == 'stop':
                        answer = msg.content
        
        # Проверка триггера авторизации в ответе
        if answer and AUTH_TRIGGER in answer:
            auth_required = True
            answer = answer.replace(AUTH_TRIGGER, "").strip()

        if auth_required:
            if chat_id in authorized_users:
                await message.answer(answer)
            else:
                await message.answer(
                    f"{answer}\n"
                    "Используйте /login или поделитесь контактом:",
                    reply_markup=request_contact_markup()
                )
        elif answer:
            await message.answer(telegram_format(answer))
        else:
            await message.answer("❌ Не удалось найти ответ. Попробуйте сформулировать вопрос иначе.")

    except Exception as e:
        logging.error(f"Ошибка обработки: {e}")
        await message.answer("⚠ Произошла ошибка при обработке запроса.")

    finally:
        typing_task.cancel()

@dp.message(CommandStart())
async def start(message: Message):
    """Команда /start"""
    await message.answer("Привет! Задайте мне вопрос, и я постараюсь найти на него ответ.")

@dp.message(Command("login"))
async def login_command(message: Message):
    """Обработка команды /login"""
    await message.answer(
        "Для авторизации поделитесь контактом:",
        reply_markup=request_contact_markup()
    )

@dp.message(lambda message: message.contact is not None)
async def handle_contact(message: Message):
    """Обработка полученного контакта"""
    user_id = message.from_user.id
    contact = message.contact
    
    if contact.user_id == user_id:
        authorized_users[message.chat.id] = contact
        await message.answer(
            "✅ Авторизация прошла успешно!",
            reply_markup=ReplyKeyboardRemove()
        )
        await send_message_to_agent(message, "Авторизация прошла успешно.")
    else:
        await message.answer("❌ Это не ваш контакт. Пожалуйста, поделитесь своим контактом.")

@dp.message()
async def handle_message(message: Message):
    await send_message_to_agent(message)

async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
