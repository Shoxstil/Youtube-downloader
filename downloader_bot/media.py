"""Разбор ссылок, доступных форматов и загрузка через yt-dlp."""
from dataclasses import dataclass
from pathlib import Path
import re
from urllib.parse import urlsplit, urlunsplit

import yt_dlp
from yt_dlp.utils import DownloadError

from .config import Config

HOSTS = {"tiktok.com", "www.tiktok.com", "m.tiktok.com", "vm.tiktok.com", "vt.tiktok.com",
         "instagram.com", "www.instagram.com", "m.instagram.com"}


class MediaError(Exception):
    """Ошибка, которую можно показать пользователю."""


def parse_url(text: str) -> str:
    text = text.strip()
    if len(text) > 2048 or re.search(r"\s", text):
        raise MediaError("Пришлите одну ссылку без дополнительного текста.")
    try:
        parts = urlsplit(text)
        if (parts.scheme != "https" or parts.hostname not in HOSTS
                or parts.username or parts.password or parts.port not in (None, 443)):
            raise ValueError
    except ValueError:
        raise MediaError("Нужна HTTPS-ссылка на видео из TikTok или Instagram.") from None
    if not parts.path.strip("/"):
        raise MediaError("Пришлите ссылку на публикацию, а не на главную страницу.")
    return urlunsplit((parts.scheme, parts.netloc, parts.path, parts.query, ""))


@dataclass(frozen=True)
class Choice:
    key: str
    label: str
    height: int | None = None
    audio: bool = False


@dataclass(frozen=True)
class MediaInfo:
    url: str
    title: str
    choices: tuple[Choice, ...]


class QuietLogger:
    # yt-dlp может писать подписанные URL и содержимое ответов. Не логируем их.
    def debug(self, message):
        pass

    def warning(self, message):
        pass

    def error(self, message):
        pass


class Downloader:
    def __init__(self, config: Config):
        self.config = config

    def options(self) -> dict:
        options = {
            "quiet": True, "no_warnings": True, "logger": QuietLogger(),
            "noplaylist": True, "socket_timeout": 20, "retries": 2,
            "extractor_retries": 1, "fragment_retries": 2,
            "cachedir": False, "ignoreerrors": False,
        }
        if self.config.cookie_file:
            options["cookiefile"] = str(self.config.cookie_file)
        return options

    def inspect(self, url: str) -> MediaInfo:
        try:
            with yt_dlp.YoutubeDL(self.options()) as ydl:
                info = ydl.extract_info(url, download=False)
        except DownloadError:
            raise MediaError("Не удалось получить видео. Проверьте ссылку: публикация должна быть "
                             "доступна. Сервис также может требовать вход или ограничивать запросы.") from None
        if not info or info.get("_type") in {"playlist", "multi_video"} or "entries" in info:
            raise MediaError("Пришлите ссылку на одно видео. Альбомы и подборки не поддерживаются.")
        if info.get("is_live") or info.get("live_status") in {"is_live", "is_upcoming"}:
            raise MediaError("Прямые эфиры не поддерживаются. Пришлите готовое видео.")
        formats = info.get("formats") or [info]
        # Instagram иногда не сообщает кодеки. yt-dlp трактует неизвестный
        # кодек как потенциально доступный; явное 'none' означает отсутствие.
        videos = [f for f in formats if f.get("vcodec") != "none"]
        if not videos:
            raise MediaError("В публикации не найдено видео. Фото не поддерживаются.")
        heights = sorted({int(f["height"]) for f in videos if f.get("height")}, reverse=True)
        choices = [Choice(f"v{height}", f"🎬 {height}p", height) for height in heights[:8]]
        if not choices:
            choices.append(Choice("source", "🎬 Исходное качество"))
        if any(f.get("acodec") != "none" for f in formats):
            choices.append(Choice("audio", "🎵 Только звук · MP3", audio=True))
        return MediaInfo(url, str(info.get("title") or "Видео")[:180], tuple(choices))

    @staticmethod
    def format_selector(choice: Choice) -> str:
        if choice.audio:
            return "bestaudio/best"
        limit = f"[height<={choice.height}]" if choice.height else ""
        return f"bestvideo{limit}+bestaudio/best{limit}/bestvideo{limit}"

    def download(self, info: MediaInfo, choice: Choice, directory: Path) -> Path:
        def check_size(progress):
            if progress.get("downloaded_bytes", 0) > self.config.max_bytes:
                raise MediaError("Файл больше лимита отправки. Выберите меньшее качество или только звук.")

        options = self.options() | {
            "format": self.format_selector(choice),
            "outtmpl": str(directory / "media.%(ext)s"),
            "max_filesize": self.config.max_bytes,
            "progress_hooks": [check_size], "overwrites": True,
        }
        if choice.audio:
            options["postprocessors"] = [{"key": "FFmpegExtractAudio", "preferredcodec": "mp3",
                                           "preferredquality": "128"}]
        else:
            options["merge_output_format"] = "mp4"
        try:
            with yt_dlp.YoutubeDL(options) as ydl:
                ydl.extract_info(info.url, download=True)
        except DownloadError:
            raise MediaError("Не удалось скачать выбранный формат. Попробуйте другое качество. "
                             "Доступ к публикации мог измениться.") from None
        files = [p for p in directory.glob("media.*") if p.suffix not in {".part", ".ytdl", ".temp"}]
        if len(files) != 1:
            raise MediaError("Файл не получен: он может превышать лимит. Попробуйте меньшее качество.")
        output = files[0]
        if not output.stat().st_size or output.stat().st_size > self.config.max_bytes:
            raise MediaError("Файл пустой или больше 49 МБ. Выберите меньшее качество или только звук.")
        return output
