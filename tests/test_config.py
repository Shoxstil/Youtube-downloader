from unittest.mock import call, patch

import pytest

from downloader_bot.config import Config
from downloader_bot import app


def test_missing_token_explains_setup(monkeypatch):
    monkeypatch.delenv('TELEGRAM_BOT_TOKEN', raising=False)
    with patch('downloader_bot.config.load_dotenv'):
        with pytest.raises(ValueError, match='TELEGRAM_BOT_TOKEN'):
            Config.from_env()


def test_cookie_path_must_exist(monkeypatch, tmp_path):
    monkeypatch.setenv('TELEGRAM_BOT_TOKEN', 'opaque-secret-placeholder')
    monkeypatch.setenv('YTDLP_COOKIE_FILE', str(tmp_path / 'missing.txt'))
    with patch('downloader_bot.config.load_dotenv'):
        with pytest.raises(ValueError, match='YTDLP_COOKIE_FILE'):
            Config.from_env()


def test_existing_cookie_path(monkeypatch, tmp_path):
    cookie = tmp_path / 'cookies.txt'
    cookie.write_text('# Netscape HTTP Cookie File\n')
    monkeypatch.setenv('TELEGRAM_BOT_TOKEN', 'opaque-secret-placeholder')
    monkeypatch.setenv('YTDLP_COOKIE_FILE', str(cookie))
    with patch('downloader_bot.config.load_dotenv'):
        assert Config.from_env().cookie_file == cookie.resolve()


def test_startup_authentication_polling_and_shutdown():
    with patch.object(app.Config, 'from_env', return_value=Config('opaque-secret-placeholder')), \
         patch.object(app.shutil, 'which', return_value='/usr/bin/tool'), \
         patch.object(app.telebot, 'TeleBot') as cls, patch.object(app, 'Jobs') as jobs:
        app.main()
        cls.assert_called_once_with('opaque-secret-placeholder', threaded=True, num_threads=4, validate_token=False)
        cls.return_value.get_me.assert_called_once()
        cls.return_value.delete_webhook.assert_called_once_with(drop_pending_updates=True)
        calls = cls.return_value.mock_calls
        assert calls.index(call.get_me()) < calls.index(call.delete_webhook(drop_pending_updates=True))
        assert calls.index(call.delete_webhook(drop_pending_updates=True)) < next(
            index for index, item in enumerate(calls) if item[0] == 'infinity_polling')
        cls.return_value.infinity_polling.assert_called_once()
        cls.return_value.stop_polling.assert_called_once()
        jobs.return_value.close.assert_called_once()


def test_failed_authentication_does_not_poll_or_expose_token():
    with patch.object(app.Config, 'from_env', return_value=Config('PRIVATE')), \
         patch.object(app.shutil, 'which', return_value='/usr/bin/tool'), \
         patch.object(app.telebot, 'TeleBot') as cls, patch.object(app, 'Jobs') as jobs:
        cls.return_value.get_me.side_effect = RuntimeError('PRIVATE')
        with pytest.raises(SystemExit) as error:
            app.main()
        assert 'PRIVATE' not in str(error.value)
        cls.return_value.infinity_polling.assert_not_called()
        cls.return_value.delete_webhook.assert_not_called()
        jobs.return_value.close.assert_called_once()


@pytest.mark.parametrize('failure', [False, RuntimeError('PRIVATE')])
def test_failed_webhook_removal_does_not_start_polling(failure):
    with patch.object(app.Config, 'from_env', return_value=Config('PRIVATE')), \
         patch.object(app.shutil, 'which', return_value='/usr/bin/tool'), \
         patch.object(app.telebot, 'TeleBot') as cls, patch.object(app, 'Jobs') as jobs:
        if isinstance(failure, Exception):
            cls.return_value.delete_webhook.side_effect = failure
        else:
            cls.return_value.delete_webhook.return_value = failure
        with pytest.raises(SystemExit) as error:
            app.main()
        assert 'PRIVATE' not in str(error.value)
        cls.return_value.infinity_polling.assert_not_called()
        jobs.return_value.close.assert_called_once()


def test_missing_ffmpeg_explains_installation():
    with patch.object(app.Config, 'from_env', return_value=Config('unused')), \
         patch.object(app.shutil, 'which', return_value=None):
        with pytest.raises(SystemExit, match='ffmpeg'):
            app.main()
