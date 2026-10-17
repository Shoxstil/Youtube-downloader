"""Telegram-интерфейс с отдельным пулом для загрузок."""
import logging
from pathlib import Path
import shutil
from tempfile import TemporaryDirectory

import telebot
from telebot import types
from telebot.apihelper import ApiException

from .config import Config
from .media import Choice, Downloader, MediaError, MediaInfo, parse_url
from .state import Jobs, Sessions

WELCOME = (
    "👋 Привет! Я помогу сохранить видео из TikTok и Instagram.\n\n"
    "Пришли ссылку на одно видео — я покажу доступное качество и кнопку «Только звук». "
    "Звук отправлю в MP3, если у видео есть аудиодорожка.\n\n"
    "📦 Лимит файла — 49 МБ. Меню действует 15 минут и используется один раз.\n"
    "Поддерживаются доступные публикации; закрытые аккаунты могут требовать вход.\n"
    "Скачивай только материалы, которые имеешь право сохранять.\n\n"
    "/help — помощь"
)
HELP = ("Отправь HTTPS-ссылку из TikTok или Instagram отдельным сообщением. "
        "После анализа выбери разрешение или «Только звук». "
        "Если файл слишком большой, пришли ссылку снова и выбери меньшее качество. "
        "Альбомы, фото и прямые эфиры не поддерживаются. Новая ссылка заменяет предыдущее меню.")
BUSY = "Сейчас уже идёт загрузка или все места заняты. Подожди завершения и пришли ссылку снова."
log = logging.getLogger(__name__)


def quality_menu(token: str, info: MediaInfo) -> types.InlineKeyboardMarkup:
    keyboard = types.InlineKeyboardMarkup(row_width=2)
    keyboard.add(*(types.InlineKeyboardButton(c.label, callback_data=f"get:{token}:{c.key}")
                   for c in info.choices))
    return keyboard


class BotApp:
    def __init__(self, bot: telebot.TeleBot, downloader: Downloader, jobs: Jobs):
        self.bot = bot
        self.downloader = downloader
        self.jobs = jobs
        self.sessions = Sessions()

    def register(self):
        self.bot.register_message_handler(self.start, commands=["start"])
        self.bot.register_message_handler(self.help, commands=["help"])
        self.bot.register_message_handler(self.link, content_types=["text"])
        self.bot.register_message_handler(self.unsupported,
                                          content_types=["photo", "video", "audio", "document", "voice", "sticker"])
        self.bot.register_callback_query_handler(self.choose, func=lambda call: bool(call.data and call.data.startswith("get:")))

    def start(self, message):
        self.bot.send_message(message.chat.id, WELCOME)

    def help(self, message):
        self.bot.send_message(message.chat.id, HELP)

    def unsupported(self, message):
        self.bot.send_message(message.chat.id, "Пришли текстовую ссылку на видео из TikTok или Instagram.")

    def link(self, message):
        try:
            url = parse_url(message.text)
        except MediaError as error:
            self.bot.send_message(message.chat.id, str(error))
            return
        status = self.bot.send_message(message.chat.id, "🔎 Проверяю ссылку и доступное качество…")
        if not self.jobs.submit(message.from_user.id, self.inspect_job,
                                message.from_user.id, message.chat.id, status.message_id, url):
            self.bot.edit_message_text(BUSY, message.chat.id, status.message_id)

    def inspect_job(self, user_id: int, chat_id: int, message_id: int, url: str):
        try:
            info = self.downloader.inspect(url)
            token = self.sessions.add(user_id, chat_id, info)
            self.bot.edit_message_text(f"{info.title}\n\nВыбери доступное качество или только звук:",
                                       chat_id, message_id, reply_markup=quality_menu(token, info))
        except Exception as error:
            self.report_error(chat_id, message_id, error)

    def choose(self, call):
        parts = (call.data or "").split(":")
        if len(parts) != 3 or not call.message:
            self.bot.answer_callback_query(call.id, "Некорректное меню.")
            return
        session = self.sessions.take(parts[1], call.from_user.id, call.message.chat.id)
        if not session:
            self.bot.answer_callback_query(call.id, "Меню устарело или принадлежит другому пользователю. Пришли ссылку снова.", show_alert=True)
            return
        choice = next((c for c in session.info.choices if c.key == parts[2]), None)
        if not choice:
            self.bot.answer_callback_query(call.id, "Неизвестный формат. Пришли ссылку снова.")
            return
        self.bot.answer_callback_query(call.id, "Выбор принят")
        self.bot.edit_message_text("⏳ Скачиваю и подготавливаю файл…", session.chat_id, call.message.message_id)
        if not self.jobs.submit(session.user_id, self.download_job, session.chat_id,
                                call.message.message_id, session.info, choice):
            self.bot.edit_message_text(BUSY, session.chat_id, call.message.message_id)

    def download_job(self, chat_id: int, message_id: int, info: MediaInfo, choice: Choice):
        try:
            with TemporaryDirectory(prefix="social-video-") as directory:
                output = self.downloader.download(info, choice, Path(directory))
                caption = info.title[:900]
                with output.open("rb") as file:
                    if choice.audio:
                        self.bot.send_audio(chat_id, file, caption=caption, title=info.title[:64], timeout=120)
                    elif output.suffix.lower() == ".mp4":
                        self.bot.send_video(chat_id, file, caption=caption, supports_streaming=True, timeout=120)
                    else:
                        self.bot.send_document(chat_id, file, caption=caption, timeout=120)
            self.bot.edit_message_text("✅ Готово! Можешь прислать следующую ссылку.", chat_id, message_id)
        except Exception as error:
            self.report_error(chat_id, message_id, error)

    def report_error(self, chat_id: int, message_id: int, error: Exception):
        if isinstance(error, MediaError):
            text = str(error)
        elif isinstance(error, ApiException):
            text = "Telegram не принял файл или запрос. Попробуй меньшее качество и пришли ссылку снова."
        else:
            text = "Не удалось обработать видео. Попробуй другую ссылку или повтори позже."
        # Исключение целиком может содержать URL с токеном или cookies.
        log.warning("Задание не выполнено: %s", type(error).__name__)
        try:
            self.bot.edit_message_text(f"⚠️ {text}", chat_id, message_id)
        except Exception:
            log.warning("Не удалось обновить сообщение об ошибке")


def main():
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    try:
        config = Config.from_env()
        if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
            raise ValueError("Нужны ffmpeg и ffprobe. Инструкции установки находятся в README.md.")
        # Облачное хранилище секретов может передать непрозрачную подстановку.
        # Проверяем подлинность через get_me(), а не по виду строки токена.
        bot = telebot.TeleBot(config.token, threaded=True, num_threads=4, validate_token=False)
    except ValueError as error:
        raise SystemExit(str(error)) from None
    jobs = Jobs(config.workers)
    app = BotApp(bot, Downloader(config), jobs)
    app.register()
    try:
        bot.get_me()
    except Exception as error:
        jobs.close()
        raise SystemExit(f"Не удалось подключиться к Telegram ({type(error).__name__}). Проверь токен и сеть.") from None
    log.info("Бот запущен. Для остановки нажми Ctrl+C.")
    try:
        bot.infinity_polling(skip_pending=True, timeout=20, long_polling_timeout=20,
                             allowed_updates=["message", "callback_query"])
    finally:
        bot.stop_polling()
        jobs.close()


if __name__ == "__main__":
    main()
