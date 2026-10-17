"""Настройки без секретов в исходном коде."""
from dataclasses import dataclass
import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class Config:
    token: str
    max_bytes: int = 49 * 1024 * 1024
    workers: int = 2
    cookie_file: Path | None = None

    @classmethod
    def from_env(cls) -> "Config":
        load_dotenv(ROOT / ".env", override=False)
        token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
        if not token:
            raise ValueError("Укажите TELEGRAM_BOT_TOKEN в .env или переменных окружения.")
        cookie = os.getenv("YTDLP_COOKIE_FILE", "").strip()
        cookie_file = Path(cookie).expanduser().resolve() if cookie else None
        if cookie_file and not cookie_file.is_file():
            raise ValueError("Файл YTDLP_COOKIE_FILE не найден.")
        return cls(token=token, cookie_file=cookie_file)
