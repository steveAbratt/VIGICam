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
    def __init__(self, ip: str, username: str, password: str) -> None:
        self._ip = ip
        self._username = username
        self._password = password
        self._stok: str | None = None
        self._base_url = f"https://{ip}"
        self._session: aiohttp.ClientSession | None = None

        ssl_ctx = ssl.create_default_context()
        ssl_ctx.check_hostname = False
        ssl_ctx.verify_mode = ssl.CERT_NONE
        self._ssl_ctx = ssl_ctx

    def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            connector = aiohttp.TCPConnector(ssl=self._ssl_ctx)
            timeout = aiohttp.ClientTimeout(total=TIMEOUT)
            self._session = aiohttp.ClientSession(connector=connector, timeout=timeout)
        return self._session

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()

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
        disks = resp.get("harddisk_manage", {}).get("hd_info", [])
        return disks[0] if disks else {}

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
        """Returns [{id, name, pan, tilt, zoom}, ...] for PTZ cameras, [] otherwise."""
        try:
            resp = await self.get("preset", "preset")
            data = resp.get("preset", resp)
            ids = data.get("id", [])
            names = data.get("name", [])
            pans = data.get("pan", [])
            tilts = data.get("tilt", [])
            zooms = data.get("zoom", [])
            return [
                {
                    "id": ids[i],
                    "name": names[i],
                    "pan": pans[i],
                    "tilt": tilts[i],
                    "zoom": zooms[i],
                }
                for i in range(len(ids))
            ]
        except (VIGIError, KeyError, IndexError):
            return []

    async def goto_preset(self, preset_id: str) -> None:
        await self.do("preset", {"goto_preset": {"channel": 0, "id": preset_id}})
