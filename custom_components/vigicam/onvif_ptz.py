"""ONVIF PTZ client for VIGI cameras.

Provides ContinuousMove, Stop and GotoPreset via the ONVIF PTZ service
(same endpoint as events, http://{ip}:80/onvif/service).

Auth: same WS-Security PasswordDigest as onvif_events.py —
SHA1(nonce_bytes + created_utf8 + raw_password_utf8).

Profile token: VIGI cameras consistently use "profile_1" (mainStream)
for PTZ. If a future firmware breaks this, discover it via GetProfiles.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import logging
import os
from datetime import datetime, timezone

import aiohttp

_LOGGER = logging.getLogger(__name__)

_NS = "http://www.onvif.org/ver20/ptz/wsdl"
_ACTIONS = {
    "ContinuousMove": f"{_NS}/PTZ/ContinuousMove",
    "Stop":           f"{_NS}/PTZ/Stop",
    "GotoPreset":     f"{_NS}/PTZ/GotoPreset",
}
_PROFILE = "profile_1"  # mainStream — consistent across all tested VIGI firmware

# Move speed applied when direction buttons are pressed (0.0–1.0)
DEFAULT_SPEED = 0.3
# How long a direction button press moves the camera before auto-stopping
BUTTON_MOVE_S = 1.0


class VIGIOnvifPtz:
    """On-demand ONVIF PTZ client — no background task, session reused per instance."""

    def __init__(self, ip: str, username: str, password: str) -> None:
        self._ip = ip
        self._username = username
        self._password = password
        self._url = f"http://{ip}:80/onvif/service"
        self._session: aiohttp.ClientSession | None = None

    def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()

    def _envelope(self, action: str, body: str) -> str:
        created = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        nonce_bytes = os.urandom(16)
        nonce_b64 = base64.b64encode(nonce_bytes).decode()
        digest = base64.b64encode(
            hashlib.sha1(nonce_bytes + created.encode() + self._password.encode()).digest()
        ).decode()
        return (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<SOAP-ENV:Envelope'
            ' xmlns:SOAP-ENV="http://www.w3.org/2003/05/soap-envelope"'
            ' xmlns:wsa5="http://www.w3.org/2005/08/addressing"'
            ' xmlns:tptz="http://www.onvif.org/ver20/ptz/wsdl"'
            ' xmlns:tt="http://www.onvif.org/ver10/schema"'
            ' xmlns:wsse="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-secext-1.0.xsd"'
            ' xmlns:wsu="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-utility-1.0.xsd">'
            '<SOAP-ENV:Header>'
            '<wsse:Security SOAP-ENV:mustUnderstand="1">'
            '<wsse:UsernameToken>'
            f'<wsse:Username>{self._username}</wsse:Username>'
            '<wsse:Password Type="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-username-token-profile-1.0#PasswordDigest">'
            f'{digest}</wsse:Password>'
            '<wsse:Nonce EncodingType="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-soap-message-security-1.0#Base64Binary">'
            f'{nonce_b64}</wsse:Nonce>'
            f'<wsu:Created>{created}</wsu:Created>'
            '</wsse:UsernameToken>'
            '</wsse:Security>'
            f'<wsa5:Action SOAP-ENV:mustUnderstand="1">{action}</wsa5:Action>'
            f'<wsa5:To SOAP-ENV:mustUnderstand="1">{self._url}</wsa5:To>'
            '</SOAP-ENV:Header>'
            f'<SOAP-ENV:Body>{body}</SOAP-ENV:Body>'
            '</SOAP-ENV:Envelope>'
        )

    async def _soap(self, action_key: str, body: str) -> None:
        action = _ACTIONS[action_key]
        env = self._envelope(action, body)
        sess = self._get_session()
        try:
            async with sess.post(
                self._url,
                data=env.encode(),
                headers={"Content-Type": "application/soap+xml; charset=utf-8"},
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                text = await resp.text()
                if "Fault" in text:
                    _LOGGER.warning(
                        "ONVIF PTZ %s fault for %s: %s",
                        action_key, self._ip, text[:300],
                    )
        except aiohttp.ClientError as exc:
            _LOGGER.warning("ONVIF PTZ %s error for %s: %s", action_key, self._ip, exc)

    async def continuous_move(self, pan: float, tilt: float, zoom: float) -> None:
        await self._soap("ContinuousMove",
            f'<tptz:ContinuousMove>'
            f'<tptz:ProfileToken>{_PROFILE}</tptz:ProfileToken>'
            f'<tptz:Velocity>'
            f'<tt:PanTilt x="{pan:.4f}" y="{tilt:.4f}"/>'
            f'<tt:Zoom x="{zoom:.4f}"/>'
            f'</tptz:Velocity>'
            f'</tptz:ContinuousMove>')

    async def stop(self) -> None:
        await self._soap("Stop",
            f'<tptz:Stop>'
            f'<tptz:ProfileToken>{_PROFILE}</tptz:ProfileToken>'
            f'<tptz:PanTilt>true</tptz:PanTilt>'
            f'<tptz:Zoom>true</tptz:Zoom>'
            f'</tptz:Stop>')

    async def goto_preset(self, token: str) -> None:
        await self._soap("GotoPreset",
            f'<tptz:GotoPreset>'
            f'<tptz:ProfileToken>{_PROFILE}</tptz:ProfileToken>'
            f'<tptz:PresetToken>{token}</tptz:PresetToken>'
            f'</tptz:GotoPreset>')
