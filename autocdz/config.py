"""Загрузка настроек без публикации секретов."""

import os
from dataclasses import dataclass
from urllib.parse import urlsplit

from dotenv import load_dotenv


@dataclass(frozen=True)
class Config:
    bot_token: str
    admin_id: int
    database_path: str = "data/autocdz.db"
    support_url: str = ""
    tribute_url: str = ""

    @classmethod
    def load(cls):
        load_dotenv()
        token = os.getenv("BOT_TOKEN", "").strip()
        if not token:
            raise ValueError("Укажите BOT_TOKEN в файле .env")
        admin_id = int(os.getenv("ADMIN_ID", "0"))
        if admin_id <= 0:
            raise ValueError("Укажите положительный ADMIN_ID")
        urls = [os.getenv(name, "").strip() for name in ("SUPPORT_URL", "TRIBUTE_URL")]
        for url in urls:
            if url and (urlsplit(url).scheme != "https" or not urlsplit(url).netloc):
                raise ValueError("Ссылки поддержки и оплаты должны начинаться с https://")
        return cls(token, admin_id, os.getenv("DATABASE_PATH", "data/autocdz.db"), *urls)
