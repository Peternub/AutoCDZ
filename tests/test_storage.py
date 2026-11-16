"""Проверки сохранности и изоляции пользовательских данных."""

import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from autocdz.storage import Storage, now, subscription_active


class StorageTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.storage = Storage(str(Path(self.temp.name) / "test.db"))
        await self.storage.open()

    async def asyncTearDown(self):
        await self.storage.close()
        self.temp.cleanup()

    async def test_registration_is_idempotent(self):
        await self.storage.register(1)
        await self.storage.register(1)
        self.assertEqual((await self.storage.stats())["users"], 1)

    async def test_grant_extends_existing_access(self):
        await self.storage.register(1)
        first = datetime.fromisoformat(await self.storage.grant(1, 7, 99))
        second = datetime.fromisoformat(await self.storage.grant(1, 7, 99))
        self.assertEqual(second - first, timedelta(days=7))
        self.assertTrue(subscription_active(await self.storage.user(1)))
        self.assertFalse(subscription_active({"subscription_until": (now() - timedelta(seconds=1)).isoformat()}))

    async def test_delete_is_isolated_and_persistent(self):
        await self.storage.register(1)
        await self.storage.register(2)
        await self.storage.grant(1, 7, 99)
        await self.storage.delete(1)
        await self.storage.close()
        await self.storage.open()
        self.assertIsNone(await self.storage.user(1))
        self.assertIsNotNone(await self.storage.user(2))
        async with self.storage.db.execute("SELECT COUNT(*) FROM grants") as cursor:
            self.assertEqual((await cursor.fetchone())[0], 0)

    async def test_invalid_grant(self):
        with self.assertRaises(ValueError):
            await self.storage.grant(1, 7, 99)
        with self.assertRaises(ValueError):
            await self.storage.grant(1, -1, 99)
