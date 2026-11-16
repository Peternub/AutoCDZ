"""Хранение пользователей, настроек и тестовых подписок."""

from datetime import datetime, timedelta, timezone
from pathlib import Path

import aiosqlite


def now():
    return datetime.now(timezone.utc)


class Storage:
    def __init__(self, path):
        self.path = path
        self.db = None

    async def open(self):
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self.db = await aiosqlite.connect(self.path)
        self.db.row_factory = aiosqlite.Row
        await self.db.executescript("""
            PRAGMA journal_mode=WAL;
            PRAGMA foreign_keys=ON;
            CREATE TABLE IF NOT EXISTS users (
                telegram_id INTEGER PRIMARY KEY,
                created_at TEXT NOT NULL,
                notifications INTEGER NOT NULL DEFAULT 1,
                subscription_until TEXT
            );
            CREATE TABLE IF NOT EXISTS grants (
                id INTEGER PRIMARY KEY,
                user_id INTEGER NOT NULL REFERENCES users(telegram_id) ON DELETE CASCADE,
                admin_id INTEGER NOT NULL,
                days INTEGER NOT NULL,
                created_at TEXT NOT NULL
            );
        """)
        await self.db.commit()

    async def close(self):
        if self.db:
            await self.db.close()

    async def register(self, user_id):
        await self.db.execute(
            "INSERT OR IGNORE INTO users(telegram_id, created_at) VALUES (?, ?)",
            (user_id, now().isoformat()),
        )
        await self.db.commit()

    async def user(self, user_id):
        async with self.db.execute("SELECT * FROM users WHERE telegram_id=?", (user_id,)) as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else None

    async def set_notifications(self, user_id, enabled):
        await self.db.execute("UPDATE users SET notifications=? WHERE telegram_id=?", (int(enabled), user_id))
        await self.db.commit()

    async def grant(self, user_id, days, admin_id):
        if not 1 <= days <= 365:
            raise ValueError("Срок должен быть от 1 до 365 дней")
        # Транзакция исключает потерю продления при одновременных запросах.
        connection = await aiosqlite.connect(self.path)
        try:
            await connection.execute("PRAGMA foreign_keys=ON")
            await connection.execute("BEGIN IMMEDIATE")
            async with connection.execute("SELECT subscription_until FROM users WHERE telegram_id=?", (user_id,)) as cursor:
                row = await cursor.fetchone()
            if row is None:
                raise ValueError("Пользователь сначала должен нажать /start")
            base = max(now(), datetime.fromisoformat(row[0])) if row[0] else now()
            expires = (base + timedelta(days=days)).isoformat()
            await connection.execute("UPDATE users SET subscription_until=? WHERE telegram_id=?", (expires, user_id))
            await connection.execute(
                "INSERT INTO grants(user_id, admin_id, days, created_at) VALUES (?, ?, ?, ?)",
                (user_id, admin_id, days, now().isoformat()),
            )
            await connection.commit()
            return expires
        finally:
            await connection.close()

    async def delete(self, user_id):
        await self.db.execute("DELETE FROM users WHERE telegram_id=?", (user_id,))
        await self.db.commit()

    async def stats(self):
        async with self.db.execute(
            "SELECT COUNT(*), COALESCE(SUM(subscription_until > ?), 0) FROM users", (now().isoformat(),)
        ) as cursor:
            row = await cursor.fetchone()
            return {"users": row[0], "active": row[1]}


def subscription_active(user):
    return bool(user and user["subscription_until"] and datetime.fromisoformat(user["subscription_until"]) > now())
