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
    mesh_enabled: bool = False
    public_url: str = ""
    encryption_key: str = ""
    ai_key: str = ""
    ai_model: str = ""
    web_host: str = "127.0.0.1"
    web_port: int = 8080
    poll_interval: int = 1800

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
        enabled = os.getenv("MESH_EXPERIMENTAL_ENABLED", "false").lower() == "true"
        public_url = os.getenv("PUBLIC_URL", "").strip().rstrip("/")
        key = os.getenv("SESSION_ENCRYPTION_KEY", "").strip()
        if enabled:
            parsed = urlsplit(public_url)
            if parsed.scheme != "https" or not parsed.netloc or parsed.path or parsed.query or parsed.fragment or parsed.username:
                raise ValueError("PUBLIC_URL должен содержать HTTPS-домен без пути")
            if not key:
                raise ValueError("Задайте SESSION_ENCRYPTION_KEY")
            from cryptography.fernet import Fernet
            Fernet(key.encode())
        interval = int(os.getenv("POLL_INTERVAL_SECONDS", "1800"))
        if interval < 1800:
            raise ValueError("Интервал проверки должен быть не меньше 1800 секунд")
        host = os.getenv("WEB_HOST", "127.0.0.1")
        port = int(os.getenv("WEB_PORT", "8080"))
        if host not in ("127.0.0.1", "0.0.0.0") or not 1024 <= port <= 65535:
            raise ValueError("Некорректный адрес сервера подключения")
        return cls(
            token, admin_id, os.getenv("DATABASE_PATH", "data/autocdz.db"), *urls,
            mesh_enabled=enabled, public_url=public_url, encryption_key=key,
            ai_key=os.getenv("OPENAI_API_KEY", ""), ai_model=os.getenv("OPENAI_MODEL", ""),
            web_host=host, web_port=port, poll_interval=interval,
        )
