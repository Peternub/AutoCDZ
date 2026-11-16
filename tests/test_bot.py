"""Проверки команд через настоящий маршрутизатор без сети."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock

from aiogram import Bot, Dispatcher
from aiogram.types import Update

from autocdz.bot import create_router
from autocdz.config import Config
from autocdz.storage import Storage


class BotTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.storage = Storage(str(Path(self.temp.name) / "test.db"))
        await self.storage.open()
        self.bot = Bot("123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghi")
        self.bot.session.make_request = AsyncMock(return_value=True)
        self.dispatcher = Dispatcher()
        self.dispatcher.include_router(create_router(Config(self.bot.token, 99), self.storage))

    async def asyncTearDown(self):
        await self.bot.session.close()
        await self.storage.close()
        self.temp.cleanup()

    async def send(self, user_id, text, chat_type="private"):
        update = Update.model_validate({"update_id": 1, "message": {
            "message_id": 1, "date": 1,
            "chat": {"id": user_id, "type": chat_type},
            "from": {"id": user_id, "is_bot": False, "first_name": "Тест"},
            "text": text,
            "entities": [{"type": "bot_command", "offset": 0, "length": len(text.split()[0])}] if text.startswith("/") else [],
        }})
        await self.dispatcher.feed_update(self.bot, update)

    def last_text(self):
        return self.bot.session.make_request.call_args.args[1].text

    async def test_start_registers_and_explains_limits(self):
        await self.send(1, "/start")
        self.assertIsNotNone(await self.storage.user(1))
        self.assertIn("ещё не работают", self.last_text())

    async def test_non_admin_cannot_grant(self):
        await self.send(1, "/grant 1 7")
        self.assertEqual(self.last_text(), "Доступ запрещён.")
        self.assertIsNone((await self.storage.user(1))["subscription_until"])

    async def test_admin_grants_registered_user(self):
        await self.send(1, "/start")
        await self.send(99, "/grant 1 7")
        self.assertIsNotNone((await self.storage.user(1))["subscription_until"])

    async def test_groups_do_not_register_users(self):
        await self.send(1, "/start", "group")
        self.assertIsNone(await self.storage.user(1))
        self.bot.session.make_request.assert_not_called()

    async def test_delete_callback_only_deletes_owner(self):
        await self.storage.register(1)
        await self.storage.register(2)
        update = Update.model_validate({"update_id": 2, "callback_query": {
            "id": "test", "chat_instance": "test", "data": "delete:confirm",
            "from": {"id": 1, "is_bot": False, "first_name": "Тест"},
            "message": {"message_id": 1, "date": 1, "chat": {"id": 1, "type": "private"}},
        }})
        await self.dispatcher.feed_update(self.bot, update)
        self.assertIsNone(await self.storage.user(1))
        self.assertIsNotNone(await self.storage.user(2))
