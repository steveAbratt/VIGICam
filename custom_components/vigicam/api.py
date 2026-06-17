"""VIGI/InSight local HTTPS API client.

Authentication protocol:
  1. POST / with get_encrypt_info → receive nonce + RSA public key
  2. Encrypt: base64(RSA_PKCS1v15(MD5("TPCQ75NF2Y:<pass>").upper() + ":" + nonce))
  3. POST / with login credentials → receive stok token
     NOTE: VIGI cameras return stok at the TOP LEVEL of the response,
     unlike Tapo cameras which nest it under result.stok. Both are handled.
  4. All subsequent calls: POST /stok=<token>/ds

Protocol discovered via local network probing; authentication flow based on
the pytapo library (github.com/JurajNyiri/pytapo, MIT licence).
"""
from __future__ import annotations

import base64
import hashlib
import ssl
import urllib.parse
from typing import Any

import aiohttp
from Crypto.Cipher import PKCS1_v1_5
from Crypto.PublicKey import RSA

from .const import TAPO_PASSWORD_PREFIX, TIMEOUT


class VIGIAuthError(Exception):
    pass


class VIGIError(Exception):
    pass


class VIGICamera:
    def __init__(
        self,
        ip: str,
        username: str,
        password: str,
        session: aiohttp.ClientSession | None = None,
    ) -> None:
        self._ip = ip
        self._username = username
        self._password = password
        self._stok: str | None = None
        self._base_url = f"https://{ip}"
        # When HA provides a session (via async_get/create_clientsession) it
        # manages that session's lifecycle — we must not close it ourselves.
        self._ha_session = session
        self._own_session: aiohttp.ClientSession | None = None

    def _get_session(self) -> aiohttp.ClientSession:
        if self._ha_session is not None and not self._ha_session.closed:
            return self._ha_session
        # Standalone use (probe scripts, config-flow test) — own no-verify session.
        # SSLContext() is used directly to avoid the blocking load_default_certs()
        # call that ssl.create_default_context() triggers (HA loop guard, Python 3.14+).
        if self._own_session is None or self._own_session.closed:
            ssl_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
            ssl_ctx.check_hostname = False
            ssl_ctx.verify_mode = ssl.CERT_NONE
            self._own_session = aiohttp.ClientSession(
                connector=aiohttp.TCPConnector(ssl=ssl_ctx),
                timeout=aiohttp.ClientTimeout(total=TIMEOUT),
            )
        return self._own_session

    async def close(self) -> None:
        # Only close the session we created ourselves; HA manages its own sessions.
        if self._own_session and not self._own_session.closed:
            await self._own_session.close()

    async def _post(self, url: str, body: dict) -> dict:
        session = self._get_session()
        try:
            async with session.post(url, json=body) as resp:
                return await resp.json(content_type=None)
        except aiohttp.ClientError as exc:
            raise VIGIError(f"HTTP error communicating with {self._ip}: {exc}") from exc

    def _encrypt_password(self, nonce: str, key_b64: str) -> str:
        """RSA-encrypt the password+nonce. Synchronous but fast (<1ms)."""
        if "BEGIN" not in key_b64:
            key_b64 = (
                f"-----BEGIN PUBLIC KEY-----\n{key_b64}\n-----END PUBLIC KEY-----"
            )
        rsa_key = RSA.import_key(key_b64)
        pwd_hash = hashlib.md5(
            (TAPO_PASSWORD_PREFIX + self._password).encode()
        ).hexdigest().upper()
        payload = f"{pwd_hash}:{nonce}".encode()
        return base64.b64encode(PKCS1_v1_5.new(rsa_key).encrypt(payload)).decode()

    async def authenticate(self) -> None:
        """Fetch a fresh stok session token from the camera."""
        enc = await self._post(
            self._base_url + "/",
            {"user_management": {"get_encrypt_info": None}, "method": "do"},
        )
        try:
            nonce = enc["data"]["nonce"]
            key_b64 = urllib.parse.unquote(enc["data"]["key"])
        except KeyError as exc:
            raise VIGIAuthError(f"Unexpected encrypt_info response: {enc}") from exc

        encrypted = self._encrypt_password(nonce, key_b64)
        login = await self._post(
            self._base_url + "/",
            {
                "method": "do",
                "login": {
                    "username": self._username,
                    "password": encrypted,
                    "passwdType": "md5",
                    "encrypt_type": "2",
                },
            },
        )
        # VIGI: stok at top level; Tapo: stok under result
        stok = login.get("stok") or login.get("result", {}).get("stok")
        if not stok:
            raise VIGIAuthError(
                f"Login failed (error_code={login.get('error_code')})"
            )
        self._stok = stok

    @property
    def _ds_url(self) -> str:
        return f"{self._base_url}/stok={self._stok}/ds"

    async def _request(self, body: dict) -> dict:
        """POST to /stok=.../ds, re-authenticating once on session expiry."""
        if not self._stok:
            await self.authenticate()

        resp = await self._post(self._ds_url, body)
        error_code = resp.get("error_code", 0)

        if error_code in (-40401, -40415):  # session expired / invalid token
            await self.authenticate()
            resp = await self._post(self._ds_url, body)
            error_code = resp.get("error_code", 0)

        if error_code not in (0, None):
            raise VIGIError(f"API error {error_code} for request: {body}")
        return resp

    # ── Low-level helpers ──────────────────────────────────────────────────────

    async def get(self, module: str, name: str | list[str]) -> dict:
        resp = await self._request({"method": "get", module: {"name": name}})
        return resp.get(module, resp)

    async def set(self, module: str, fields: dict) -> None:
        await self._request({"method": "set", module: fields})

    async def do(self, module: str, action: dict) -> dict:
        return await self._request({"method": "do", module: action})

    # ── Device info ───────────────────────────────────────────────────────────

    async def get_device_info(self) -> dict:
        resp = await self.get("device_info", "basic_info")
        return resp.get("basic_info", resp)

    # ── Motion detection ──────────────────────────────────────────────────────

    async def get_motion_detection(self) -> dict:
        resp = await self.get("motion_detection", "motion_det")
        return resp.get("motion_det", resp)

    async def set_motion_detection(self, enabled: bool) -> None:
        await self.set(
            "motion_detection",
            {"motion_det": {"enabled": "on" if enabled else "off"}},
        )

    async def set_motion_sensitivity(self, value: int) -> None:
        await self.set(
            "motion_detection",
            {"motion_det": {"digital_sensitivity": value}},
        )

    async def set_person_detection(self, enabled: bool) -> None:
        await self.set(
            "motion_detection",
            {"motion_det": {"people_enabled": "on" if enabled else "off"}},
        )

    async def set_vehicle_detection(self, enabled: bool) -> None:
        await self.set(
            "motion_detection",
            {"motion_det": {"vehicle_enabled": "on" if enabled else "off"}},
        )

    # ── Night vision / spotlight ──────────────────────────────────────────────

    async def get_image_switch(self) -> dict:
        resp = await self.get("image", ["switch"])
        return resp.get("switch", resp)

    async def set_night_vision_mode(self, mode: str) -> None:
        await self.set("image", {"switch": {"night_vision_mode": mode}})

    async def set_spotlight_intensity(self, level: int) -> None:
        await self.set("image", {"switch": {"wtl_intensity_level": level}})

    # ── Alarm ─────────────────────────────────────────────────────────────────

    async def get_alarm(self) -> dict:
        resp = await self.get("msg_alarm", "chn1_msg_alarm_info")
        return resp.get("chn1_msg_alarm_info", resp)

    async def set_alarm(self, enabled: bool) -> None:
        await self.set(
            "msg_alarm",
            {"chn1_msg_alarm_info": {"enabled": "on" if enabled else "off"}},
        )

    async def set_light_alarm(self, enabled: bool) -> None:
        await self.set(
            "msg_alarm",
            {"chn1_msg_alarm_info": {"light_alarm_enabled": "on" if enabled else "off"}},
        )

    async def set_sound_alarm(self, enabled: bool) -> None:
        await self.set(
            "msg_alarm",
            {"chn1_msg_alarm_info": {"sound_alarm_enabled": "on" if enabled else "off"}},
        )

    async def trigger_alarm(self) -> None:
        """Manually fire the alarm for 10 seconds (camera auto-stops; stop_alarm() cancels early)."""
        await self.do("msg_alarm", {"manual_msg_alarm": {"action": "start"}})

    async def stop_alarm(self) -> None:
        """Cancel a running manual alarm trigger."""
        await self.do("msg_alarm", {"manual_msg_alarm": {"action": "stop"}})

    async def upload_audio(self, slot_id: int, name: str, data: bytes) -> None:
        """Upload audio to a custom slot (101, 102, or 103).

        Two-step: tell the camera to prepare a slot (get upload URL), then POST the file.
        Supported formats: WAV mono 8 kHz ≤15 s ≤256 KB; MP3 mono ≤15 s ≤128 KB ≤64 kbps.
        """
        resp = await self.do("system", {
            "upload_usr_def_audio": {
                "id": slot_id,
                "audio_name": name,
                "parse_enabled": "on",
            }
        })
        upload_path = urllib.parse.unquote(resp.get("url", ""))
        if upload_path.startswith(".."):
            upload_path = upload_path[2:]

        if not self._stok:
            await self.authenticate()
        upload_url = f"{self._base_url}/stok={self._stok}{upload_path}"

        # Detect MP3 by magic bytes; everything else treated as WAV.
        is_mp3 = data[:3] == b"ID3" or (len(data) >= 2 and data[0] == 0xFF and (data[1] & 0xE0) == 0xE0)
        content_type = "audio/mpeg" if is_mp3 else "audio/wav"
        filename = f"{name}.mp3" if is_mp3 else f"{name}.wav"

        form = aiohttp.FormData()
        form.add_field("file", data, filename=filename, content_type=content_type)

        session = self._get_session()
        try:
            async with session.post(upload_url, data=form) as response:
                result = await response.json(content_type=None)
        except aiohttp.ClientError as exc:
            raise VIGIError(f"Audio upload HTTP error: {exc}") from exc

        if result.get("error_code", 0) != 0:
            raise VIGIError(
                f"Audio upload failed (error_code={result.get('error_code')}). "
                "Camera requires: WAV mono 8 kHz ≤15 s ≤256 KB or MP3 mono ≤15 s ≤128 KB ≤64 kbps."
            )

    async def delete_audio(self, slot_id: int) -> None:
        """Delete a custom audio slot (101, 102, or 103). No-op if slot is already empty."""
        await self.do("usr_def_audio_alarm", {"delete_audio": {"id": [slot_id]}})

    async def play_audio(
        self,
        slot_id: int = 101,
        times: int = 1,
        pause: float = 1.0,
        audio_duration: float | None = None,
    ) -> None:
        """Play a custom audio slot through the camera speaker.

        times: repeat count (default 1).
        pause: extra gap between plays in seconds (default 1.0).
        audio_duration: if known, sleep for audio_duration + pause between plays so
            the clip finishes before the next trigger. Without it only pause is used,
            which may be too short if pause < clip length.
        """
        import asyncio
        sleep_between = (audio_duration or 0.0) + pause
        for i in range(max(1, times)):
            await self.do("usr_def_audio_alarm", {"test_audio": {"id": slot_id}})
            if i < times - 1:
                await asyncio.sleep(sleep_between)

    async def set_sound_alarm_times(self, times: int) -> None:
        """Set how many times the alarm sound repeats when the alarm is triggered."""
        await self.set(
            "msg_alarm",
            {"chn1_msg_alarm_info": {"sound_alarm_times": str(times)}},
        )

    # ── LED ───────────────────────────────────────────────────────────────────

    async def get_led(self) -> dict:
        resp = await self.get("led", "config")
        return resp.get("config", resp)

    async def set_led(self, enabled: bool) -> None:
        await self.set("led", {"config": {"enabled": "on" if enabled else "off"}})

    # ── Audio ─────────────────────────────────────────────────────────────────

    async def get_audio_speaker(self) -> dict:
        resp = await self.get("audio_config", "speaker")
        return resp.get("speaker", resp)

    async def set_speaker_volume(self, volume: int) -> None:
        await self.set("audio_config", {"speaker": {"volume": volume}})

    async def set_speaker_mute(self, muted: bool) -> None:
        await self.set(
            "audio_config",
            {"speaker": {"mute": "on" if muted else "off"}},
        )

    async def get_audio_microphone(self) -> dict:
        resp = await self.get("audio_config", "microphone")
        return resp.get("microphone", resp)

    async def set_mic_mute(self, muted: bool) -> None:
        await self.set(
            "audio_config",
            {"microphone": {"mute": "on" if muted else "off"}},
        )

    # ── Storage ───────────────────────────────────────────────────────────────

    async def get_storage(self) -> dict:
        """Returns the first disk's info dict from harddisk_manage."""
        resp = await self._request(
            {"method": "get", "harddisk_manage": {"table": "hd_info"}}
        )
        hd = resp.get("harddisk_manage", {}).get("hd_info", [])
        # Some firmware returns a list, others a single dict
        if isinstance(hd, list):
            entry = hd[0] if hd else {}
        elif isinstance(hd, dict):
            entry = hd
        else:
            return {}
        # Camera wraps disk data one level deeper: {"hd_info_1": {actual data}}
        # Unwrap if the expected keys aren't at the top level
        if isinstance(entry, dict) and "status" not in entry and "disk_name" not in entry:
            entry = next(iter(entry.values()), {})
        return entry if isinstance(entry, dict) else {}

    # ── Tamper detection ──────────────────────────────────────────────────────

    async def get_tamper(self) -> dict:
        resp = await self.get("tamper_detection", "tamper_det")
        return resp.get("tamper_det", resp)

    async def set_tamper(self, enabled: bool) -> None:
        await self.set(
            "tamper_detection",
            {"tamper_det": {"enabled": "on" if enabled else "off"}},
        )

    # ── PTZ presets ───────────────────────────────────────────────────────────

    async def get_presets(self) -> list[dict]:
        """Returns [{id, name}, ...] for PTZ cameras, [] otherwise.

        API returns position_pan/position_tilt/position_zoom (not pan/tilt/zoom)
        and URL-encodes spaces in preset names.
        """
        try:
            resp = await self.get("preset", "preset")
            data = resp.get("preset", resp)
            ids = data.get("id", [])
            names = data.get("name", [])
            return [
                {
                    "id": ids[i],
                    "name": urllib.parse.unquote(names[i]),
                }
                for i in range(len(ids))
            ]
        except (VIGIError, KeyError, IndexError):
            return []

    async def goto_preset(self, preset_id: str) -> None:
        await self.do("preset", {"goto_preset": {"channel": 0, "id": preset_id}})

    # ── Smart Frames ──────────────────────────────────────────────────────────

    async def supports_smart_frames(self) -> bool:
        """Return True if this camera supports Smart Frame image capture.

        Probes get_media_list with media_type=2. Returns True if the camera
        accepts the call (error_code 0, even with empty results). Returns False
        if the camera returns an API error — models like the VIGI C540V do not
        support Smart Frame / split SD card storage and will error here.
        """
        import time as _time
        now = int(_time.time())
        try:
            r = await self._request({"method": "do", "system": {"get_user_id": None}})
            user_id = r.get("system", {}).get("user_id", 1)
            await self._request({"method": "do", "media": {"get_media_list": {
                "channel": [0], "media_type": [2], "all_event": 1,
                "user_id": user_id,
                "start_time": str(now - 3600), "end_time": str(now),
                "event_type": [2],
            }}})
            return True
        except VIGIError:
            return False

    async def get_smart_frames(self, days_back: int = 1, max_items: int = 5) -> list[dict]:
        """Return the most recent Smart Frame entries from the SD card, newest first.

        Each dict: file_id, start_time (unix str), event_type, size.
        Returns [] if Smart Frame capture is disabled or no SD card is present.

        Uses get_media_list (not get_picture_list) because get_picture_list returns
        items oldest-first by page and there is no way to request the last page without
        knowing the total count first. get_media_list returns all items for the time
        window in a single call.
        """
        import time as _time
        now = int(_time.time())
        try:
            r = await self._request({"method": "do", "system": {"get_user_id": None}})
            user_id = r.get("system", {}).get("user_id", 1)
            r2 = await self._request({"method": "do", "media": {"get_media_list": {
                "channel": [0], "media_type": [2], "all_event": 1,
                "user_id": user_id,
                "start_time": str(now - days_back * 86400), "end_time": str(now),
                "event_type": [1, 2, 3, 4, 5, 6, 7, 8, 12, 13, 14, 16, 18, 26, 27, 28, 34],
            }}})
        except VIGIError:
            return []
        m = r2.get("media", {})
        fids = m.get("file_id", [])
        starts = m.get("start_time", [])
        if not fids:
            return []
        entries = [
            {
                "file_id": fids[i],
                "start_time": starts[i],
                "event_type": m.get("event_type", [0] * len(fids))[i],
                "size": m.get("size", [0] * len(fids))[i],
            }
            for i in range(len(fids))
        ]
        entries.sort(key=lambda x: int(x["start_time"]), reverse=True)
        return entries[:max_items]

    # ── Network ───────────────────────────────────────────────────────────────

    async def get_network(self) -> dict:
        resp = await self.get("network", "wan")
        return resp.get("wan", resp)
