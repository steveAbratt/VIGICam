"""An event that saved no capture must not re-show the previous detection's image as new.

Seen on an InSight S245 (2026-11-10): two smart events with no person or vehicle came 36 and
39 minutes after a person detection, and each re-published that detection's file as new.
"""
import asyncio

from vigicam_pure import event_image


class _Camera:
    legacy_tls = False

    async def get_event_images(self, days_back, max_items):
        return [{"file_id": "00010000201735", "start_time": "1", "event_type": 27}]


def _fetch(known_file_id, monkeypatch):
    downloads = []

    async def download(*args):
        downloads.append(args)
        return None  # stop before ffmpeg; only whether a download was tried matters

    monkeypatch.setattr(event_image, "_ws_download_h264", download)
    result = asyncio.run(event_image.fetch_latest_event_image(
        "cam", "user", "pass", _Camera(), known_file_id=known_file_id,
    ))
    return result, len(downloads)


def test_same_file_as_last_time_is_not_fetched(monkeypatch):
    assert _fetch("00010000201735", monkeypatch) == (event_image.NO_NEW_IMAGE, 0)


def test_new_file_is_fetched(monkeypatch):
    assert _fetch("00010000201734", monkeypatch)[1] == 1


def test_first_fetch_is_fetched(monkeypatch):
    assert _fetch(None, monkeypatch)[1] == 1
