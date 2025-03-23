import os
import logging
import asyncio
from aiogram import Bot, Dispatcher, types
from aiogram.filters import CommandStart
from aiogram.types import Message
from dotenv import load_dotenv
from workflow import app

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
    user_question = message.text

    # await message.answer("🔍 Ищу ответ...")
    chat_id = message.chat.id

    # for _ in range(5):
    #     await bot.send_chat_action(chat_id, action="typing")
    #     await asyncio.sleep(2)

    async def typing_simulation():
        try:
            while True:
                await bot.send_chat_action(chat_id, action="typing")
                await asyncio.sleep(3)
        except asyncio.CancelledError:
            pass

    typing_task = asyncio.create_task(typing_simulation())

    try:
        inputs = {"question": user_question}
        # config = {"configurable": {"thread_id": chat_id}}
        agent = app.stream(inputs)
        # agent = app.stream(
        #     {"messages": [{"role": "user", "content": inputs}]},
        #     config,
        #     stream_mode="values",
        # )
        response = None

        # Получаем ответ от агента
        loop = asyncio.get_running_loop()
        outputs = await loop.run_in_executor(None, lambda: list(agent))
        # for output in app.stream(inputs):
        for output in outputs:
            for key, value in output.items():
                response = value.get("generation", None)

        if response:
            await message.answer(response)
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
