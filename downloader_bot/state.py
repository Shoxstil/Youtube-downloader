"""Короткоживущие меню и ограничение параллельных заданий."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
import secrets
from threading import Lock
import time

from .media import MediaInfo


@dataclass(frozen=True)
class Session:
    user_id: int
    chat_id: int
    info: MediaInfo
    created: float


class Sessions:
    def __init__(self, ttl: int = 900, capacity: int = 500):
        self.ttl = ttl
        self.capacity = capacity
        self._items: dict[str, Session] = {}
        self._lock = Lock()

    def add(self, user_id: int, chat_id: int, info: MediaInfo) -> str:
        with self._lock:
            now = time.monotonic()
            self._items = {k: v for k, v in self._items.items() if now - v.created < self.ttl}
            self._items = {k: v for k, v in self._items.items()
                           if (v.user_id, v.chat_id) != (user_id, chat_id)}
            if len(self._items) >= self.capacity:
                del self._items[min(self._items, key=lambda k: self._items[k].created)]
            token = secrets.token_urlsafe(9)
            self._items[token] = Session(user_id, chat_id, info, now)
            return token

    def take(self, token: str, user_id: int, chat_id: int) -> Session | None:
        with self._lock:
            item = self._items.get(token)
            if not item or (item.user_id, item.chat_id) != (user_id, chat_id):
                return None
            del self._items[token]
            return item if time.monotonic() - item.created < self.ttl else None


class Jobs:
    """Без очереди: максимум workers задач и одна задача на пользователя."""
    def __init__(self, workers: int):
        self._workers = workers
        self._busy: set[int] = set()
        self._lock = Lock()
        self._pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="download")

    def submit(self, user_id: int, function, *args) -> bool:
        with self._lock:
            if user_id in self._busy or len(self._busy) >= self._workers:
                return False
            self._busy.add(user_id)

        def run():
            try:
                function(*args)
            finally:
                with self._lock:
                    self._busy.discard(user_id)

        try:
            self._pool.submit(run)
        except RuntimeError:
            with self._lock:
                self._busy.discard(user_id)
            return False
        return True

    def close(self):
        self._pool.shutdown(wait=True)
