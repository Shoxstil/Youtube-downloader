from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from functools import partial
import json
import shutil
import subprocess
from threading import Thread
from unittest.mock import patch

import pytest
import yt_dlp
from yt_dlp.utils import DownloadError

from downloader_bot.config import Config
from downloader_bot.media import Choice, Downloader, MediaError, MediaInfo, parse_url


@pytest.mark.parametrize('url', [
    'https://www.tiktok.com/@author/video/123', 'https://vm.tiktok.com/abc/',
    'https://vt.tiktok.com/abc/', 'https://www.instagram.com/reel/abc/',
    'https://instagram.com/p/abc/?igsh=test',
])
def test_supported_urls(url):
    assert parse_url(url) == url


@pytest.mark.parametrize('url', [
    '', 'http://instagram.com/p/1', 'https://instagram.com.evil.org/p/1',
    'https://instagram.com@evil.org/p/1', 'https://user@instagram.com/p/1',
    'https://instagram.com:9000/p/1', 'https://instagram.com:bad/p/1',
    'https://instagram.com/', 'https://127.0.0.1/video', 'file:///etc/passwd',
    'https://www.tiktok.com/a https://instagram.com/b', 'hello',
])
def test_invalid_urls(url):
    with pytest.raises(MediaError):
        parse_url(url)


def extract_stub(info):
    stub = patch('downloader_bot.media.yt_dlp.YoutubeDL')
    mocked = stub.start()
    mocked.return_value.__enter__.return_value.extract_info.return_value = info
    return stub


def test_choices_only_include_real_unique_heights_and_audio():
    stub = extract_stub({'title': 'Demo', 'formats': [
        {'height': 720, 'vcodec': 'h264', 'acodec': 'aac'},
        {'height': 360, 'vcodec': 'h264', 'acodec': 'aac'},
        {'height': 720, 'vcodec': 'vp9', 'acodec': 'none'},
        {'vcodec': 'none', 'acodec': 'aac'},
    ]})
    try:
        info = Downloader(Config('unused')).inspect('https://instagram.com/reel/test')
        assert [c.key for c in info.choices] == ['v720', 'v360', 'audio']
    finally:
        stub.stop()


@pytest.mark.parametrize('info', [
    {'_type': 'playlist', 'entries': []}, {'is_live': True},
    {'formats': [{'vcodec': 'none', 'acodec': 'aac'}]}, None,
])
def test_reject_nonvideo(info):
    stub = extract_stub(info)
    try:
        with pytest.raises(MediaError):
            Downloader(Config('unused')).inspect('test')
    finally:
        stub.stop()


def test_unknown_height_and_silent_video():
    stub = extract_stub({'formats': [{'vcodec': 'h264', 'acodec': 'none'}]})
    try:
        assert [c.key for c in Downloader(Config('unused')).inspect('test').choices] == ['source']
    finally:
        stub.stop()


def test_instagram_formats_with_unspecified_codecs():
    stub = extract_stub({'formats': [{'ext': 'mp4', 'height': 1280, 'width': 720}]})
    try:
        info = Downloader(Config('unused')).inspect('test')
        assert [choice.key for choice in info.choices] == ['v1280', 'audio']
    finally:
        stub.stop()


def test_extractor_error_is_readable_and_does_not_leak_details():
    with patch('downloader_bot.media.yt_dlp.YoutubeDL') as cls:
        cls.return_value.__enter__.return_value.extract_info.side_effect = DownloadError('private-details')
        with pytest.raises(MediaError) as error:
            Downloader(Config('unused')).inspect('test')
        assert 'private-details' not in str(error.value)


def test_format_selection_respects_height():
    downloader = Downloader(Config('unused'))
    formats = [
        {'format_id': '360', 'height': 360, 'vcodec': 'h264', 'acodec': 'aac', 'url': 'http://test/360', 'ext': 'mp4'},
        {'format_id': '720', 'height': 720, 'vcodec': 'h264', 'acodec': 'aac', 'url': 'http://test/720', 'ext': 'mp4'},
    ]
    with yt_dlp.YoutubeDL({'quiet': True}) as ydl:
        select = ydl.build_format_selector(downloader.format_selector(Choice('v360', '360p', 360)))
        result = list(select({'formats': formats, 'has_merged_format': True, 'incomplete_formats': False}))
    assert result[0]['format_id'] == '360'


@pytest.fixture(scope='module')
def media_server(tmp_path_factory):
    assert shutil.which('ffmpeg') and shutil.which('ffprobe'), 'Integration tests require FFmpeg'
    root = tmp_path_factory.mktemp('media-server')
    subprocess.run([
        'ffmpeg', '-hide_banner', '-loglevel', 'error', '-y',
        '-f', 'lavfi', '-i', 'color=c=blue:s=320x180:r=10',
        '-f', 'lavfi', '-i', 'sine=frequency=440:sample_rate=44100', '-t', '1',
        '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-c:a', 'aac', '-movflags', '+faststart',
        str(root / 'sample.mp4'),
    ], check=True, timeout=30)

    class QuietHandler(SimpleHTTPRequestHandler):
        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(('127.0.0.1', 0), partial(QuietHandler, directory=str(root)))
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f'http://127.0.0.1:{server.server_port}/sample.mp4'
    server.shutdown()
    server.server_close()
    thread.join()


def probe(path):
    result = subprocess.run(['ffprobe', '-v', 'error', '-show_streams', '-of', 'json', str(path)],
                            check=True, capture_output=True, text=True, timeout=20)
    return json.loads(result.stdout)['streams']


def test_real_download_and_mp3_conversion(media_server, tmp_path):
    # Локальный URL используется напрямую в загрузчике, а не через пользовательский parse_url.
    downloader = Downloader(Config('unused'))
    info = MediaInfo(media_server, 'Integration', ())
    video_dir, audio_dir = tmp_path / 'video', tmp_path / 'audio'
    video_dir.mkdir()
    audio_dir.mkdir()
    video = downloader.download(info, Choice('source', 'Source'), video_dir)
    audio = downloader.download(info, Choice('audio', 'MP3', audio=True), audio_dir)
    assert video.suffix == '.mp4'
    assert {s['codec_type'] for s in probe(video)} == {'video', 'audio'}
    assert audio.suffix == '.mp3'
    assert {s['codec_name'] for s in probe(audio)} == {'mp3'}


def test_real_oversized_download_is_rejected(media_server, tmp_path):
    downloader = Downloader(Config('unused', max_bytes=100))
    with pytest.raises(MediaError):
        downloader.download(MediaInfo(media_server, 'Large', ()), Choice('source', 'Source'), tmp_path)
