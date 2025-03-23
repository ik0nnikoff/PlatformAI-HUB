import os
import logging
import asyncio
from aiogram import Bot, Dispatcher, types
from aiogram.filters import CommandStart
from aiogram.types import Message
from dotenv import load_dotenv
from clear_agent import app

load_dotenv()

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
bot = Bot(token=TELEGRAM_BOT_TOKEN)
dp = Dispatcher()

logging.basicConfig(level=logging.INFO)

@dp.message(CommandStart())
async def start(message: Message):
    """Команда /start"""
    await message.answer("Привет! Задайте мне вопрос, и я постараюсь найти на него ответ.")

@dp.message()
async def handle_message(message: Message):
    """Обрабатываем текстовые сообщения и передаем их в агента."""
    user_message = message.text

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
        input = {
            "messages": [
                ("user", user_message),
            ]
        }
        config = {"configurable": {"thread_id": chat_id}}

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
        if answer:
            await message.answer(answer)
        else:
            await message.answer("❌ Не удалось найти ответ. Попробуйте сформулировать вопрос иначе.")

    except Exception as e:
        logging.error(f"Ошибка обработки: {e}")
        await message.answer("⚠ Произошла ошибка при обработке запроса.")

    finally:
        typing_task.cancel()

async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
