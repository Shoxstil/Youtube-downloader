from threading import Event
from types import SimpleNamespace as Obj
from unittest.mock import MagicMock

import pytest
import telebot

from downloader_bot.app import BotApp, WELCOME
from downloader_bot.media import Choice, MediaError, MediaInfo
from downloader_bot.state import Jobs, Sessions

INFO = MediaInfo('https://instagram.com/reel/demo', 'Заголовок <без разметки>', (
    Choice('v720', '🎬 720p', 720), Choice('audio', 'MP3', audio=True),
))


class ImmediateJobs:
    def submit(self, user, function, *args):
        function(*args)
        return True


@pytest.fixture
def app():
    bot = telebot.TeleBot('123456:test-placeholder-not-a-real-token', threaded=False)
    for name in ('send_message', 'edit_message_text', 'answer_callback_query', 'send_video', 'send_audio', 'send_document'):
        setattr(bot, name, MagicMock(return_value=Obj(message_id=100)))
    downloader = MagicMock()
    downloader.inspect.return_value = INFO
    app = BotApp(bot, downloader, ImmediateJobs())
    app.register()
    return app


def message(text=INFO.url, user=1, chat=2):
    return Obj(text=text, from_user=Obj(id=user), chat=Obj(id=chat), message_id=100)


def callback(data, user=1, chat=2):
    return Obj(id='callback', data=data, from_user=Obj(id=user), message=message(user=user, chat=chat))


def test_start_and_real_handler_registration(app):
    update = telebot.types.Update.de_json({
        'update_id': 1,
        'message': {'message_id': 1, 'date': 1, 'chat': {'id': 2, 'type': 'private'},
                    'from': {'id': 1, 'is_bot': False, 'first_name': 'Test'}, 'text': '/start',
                    'entities': [{'offset': 0, 'length': 6, 'type': 'bot_command'}]},
    })
    app.bot.process_new_updates([update])
    app.bot.send_message.assert_called_once_with(2, WELCOME)
    assert len(app.bot.callback_query_handlers) == 1


@pytest.mark.parametrize('audio', [False, True])
def test_link_menu_callback_upload_and_cleanup(app, audio):
    paths = []

    def download(info, choice, directory):
        path = directory / ('media.mp3' if choice.audio else 'media.mp4')
        path.write_bytes(b'test file')
        paths.append(path)
        return path

    app.downloader.download.side_effect = download
    send = app.bot.send_audio if audio else app.bot.send_video

    def verify_upload(chat, file, **kwargs):
        assert chat == 2 and file.read() == b'test file'
        assert not file.closed

    send.side_effect = verify_upload
    app.link(message())
    keyboard = app.bot.edit_message_text.call_args.kwargs['reply_markup'].to_dict()['inline_keyboard']
    buttons = [button for row in keyboard for button in row]
    assert len(buttons) == 2
    assert all(len(b['callback_data'].encode()) <= 64 for b in buttons)
    data = buttons[1 if audio else 0]['callback_data']
    app.choose(callback(data))
    send.assert_called_once()
    assert paths and not paths[0].parent.exists()
    app.choose(callback(data))
    send.assert_called_once()  # Повторный callback не запускает скачивание.
    assert app.bot.answer_callback_query.call_args.kwargs['show_alert'] is True


def test_other_user_cannot_use_menu(app):
    app.link(message())
    keyboard = app.bot.edit_message_text.call_args.kwargs['reply_markup'].to_dict()
    data = keyboard['inline_keyboard'][0][0]['callback_data']
    app.choose(callback(data, user=99))
    app.downloader.download.assert_not_called()
    assert app.bot.answer_callback_query.call_args.kwargs['show_alert'] is True


def test_invalid_link_does_not_call_downloader(app):
    app.link(message('https://evil.example/video'))
    app.downloader.inspect.assert_not_called()


def test_download_error_cleans_up(app):
    dirs = []

    def fail(info, choice, directory):
        dirs.append(directory)
        (directory / 'media.part').write_bytes(b'partial')
        raise MediaError('Проблема загрузки')

    app.downloader.download.side_effect = fail
    app.download_job(2, 100, INFO, INFO.choices[0])
    assert not dirs[0].exists()
    assert 'Проблема загрузки' in app.bot.edit_message_text.call_args.args[0]
    app.bot.send_video.assert_not_called()


def test_upload_error_cleans_up_without_leaking_details(app):
    dirs = []

    def download(info, choice, directory):
        dirs.append(directory)
        output = directory / 'media.mp4'
        output.write_bytes(b'video')
        return output

    app.downloader.download.side_effect = download
    app.bot.send_video.side_effect = RuntimeError('SECRET')
    app.download_job(2, 100, INFO, INFO.choices[0])
    assert not dirs[0].exists()
    assert 'SECRET' not in app.bot.edit_message_text.call_args.args[0]


def test_sessions_owner_expiry_replacement_and_one_time(monkeypatch):
    clock = [100]
    monkeypatch.setattr('downloader_bot.state.time.monotonic', lambda: clock[0])
    store = Sessions(ttl=10)
    first = store.add(1, 2, INFO)
    second = store.add(1, 2, INFO)
    assert store.take(first, 1, 2) is None
    assert store.take(second, 9, 2) is None
    assert store.take(second, 1, 9) is None
    assert store.take(second, 1, 2).info == INFO
    assert store.take(second, 1, 2) is None
    expired = store.add(1, 2, INFO)
    clock[0] += 11
    assert store.take(expired, 1, 2) is None


def test_parallel_jobs_limit_and_shutdown():
    started, release = Event(), Event()
    jobs = Jobs(1)

    def hold():
        started.set()
        assert release.wait(5)

    try:
        assert jobs.submit(1, hold)
        assert started.wait(5)
        assert not jobs.submit(1, lambda: None)
        assert not jobs.submit(2, lambda: None)
    finally:
        release.set()
        jobs.close()
    assert not jobs.submit(3, lambda: None)
