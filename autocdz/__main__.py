"""Запуск бота через длительный опрос Telegram."""

import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.types import BotCommand

from autocdz.bot import create_router
from autocdz.config import Config
from autocdz.storage import Storage


async def main():
    config = Config.load()
    storage = Storage(config.database_path)
    await storage.open()
    try:
        async with Bot(config.bot_token) as bot:
            identity = await bot.get_me()
            logging.info("Бот @%s подключён", identity.username)
            webhook = await bot.get_webhook_info()
            if webhook.url:
                raise RuntimeError("У бота установлен webhook. Отключите его перед запуском опроса.")
            await bot.set_my_commands([
                BotCommand(command="start", description="Начать работу"),
                BotCommand(command="menu", description="Главное меню"),
                BotCommand(command="delete_me", description="Удалить мои данные"),
            ])
            dispatcher = Dispatcher()
            dispatcher.include_router(create_router(config, storage))
            await dispatcher.start_polling(bot, handle_as_tasks=False, allowed_updates=dispatcher.resolve_used_update_types())
    finally:
        await storage.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING, format="%(asctime)s %(levelname)s %(message)s")
    logging.getLogger().setLevel(logging.INFO)
    logging.getLogger("aiogram").setLevel(logging.WARNING)
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        pass
    except Exception as error:
        logging.error("Запуск остановлен: %s. Проверьте настройки и доступ к Telegram.", type(error).__name__)
        raise SystemExit(1)
