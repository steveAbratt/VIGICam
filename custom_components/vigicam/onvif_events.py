"""ONVIF pull-point event subscription for VIGI cameras.

Establishes a pull-point subscription at startup and polls for events in a
background task. Motion/person/vehicle/tamper events are dispatched as HA
signals so binary_sensor entities can update their state without waiting for
the 30-second coordinator cycle.

Auth note: VIGI cameras require WS-Security PasswordDigest where the digest
is SHA1(nonce + created + raw_password). Using SHA1(password) first — as some
libraries do — produces NotAuthorized on these cameras.

Subscription address is returned by the camera on port 1024, not port 80.
Pull calls must go to that subscription address, not the main service URL.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import logging
import os
import re
from datetime import datetime, timezone
from typing import Any

import aiohttp
from homeassistant.core import HomeAssistant
from homeassistant.helpers.dispatcher import async_dispatcher_send

_LOGGER = logging.getLogger(__name__)

ONVIF_SERVICE_URL = "http://{}:80/onvif/service"
PULL_TIMEOUT_S = 8          # camera holds connection this long if no events
SUBSCRIPTION_DURATION = "PT1H"
RENEW_MARGIN_S = 300        # renew 5 min before expiry
AUTO_CLEAR_S = 15           # seconds before a detected state auto-resets

SIGNAL_VIGICAM_EVENT = "vigicam_event_{}"  # format with entry_id

_CREATE_ACTION = "http://www.onvif.org/ver10/events/wsdl/EventPortType/CreatePullPointSubscription"
_PULL_ACTION = "http://www.onvif.org/ver10/events/wsdl/PullPointSubscription/PullMessages"
_RENEW_ACTION = "http://www.onvif.org/ver10/events/wsdl/SubscriptionManager/Renew"

# Maps substrings found in ONVIF topic strings → event type name used by binary sensors.
# Verified topic names from GetEventProperties on VIGI C540V + InSight S245:
#   tns1:RuleEngine/tns1:CellMotionDetector/tns1:Motion      → IsMotion
#   tns1:RuleEngine/tns1:TamperDetector/tns1:Tamper          → IsTamper
#   tns1:RuleEngine/tns1:IntrusionDetector/tns1:Intrusion    → IsIntrusion
#   tns1:RuleEngine/tns1:LineCrossDetector/tns1:LineCross    → IsLineCross
#   tns1:RuleEngine/tns1:PeopleDetector/tns1:People          → IsPeople
#   tns1:RuleEngine/tns1:TPSmartEventDetector/tns1:TPSmartEvent → IsTPSmartEvent
# TPSmartEvent is a catch-all for vehicle, sound, loitering, abandoned object, scene change.
# More specific entries must come before broader ones (first match wins).
TOPIC_KEYWORD_MAP: dict[str, str] = {
    "TPSmartEvent": "smart_event",
    "CellMotion":   "motion",
    "LineCross":    "line_cross",
    "Intrusion":    "intrusion",
    "People":       "person",
    "Tamper":       "tamper",
    "Motion":       "motion",  # fallback for non-CellMotion motion topics
}


class VIGIOnvifEvents:
    """Manages an ONVIF pull-point subscription for a single VIGI camera."""

    def __init__(
        self,
        hass: HomeAssistant,
        ip: str,
        username: str,
        password: str,
        entry_id: str,
    ) -> None:
        self._hass = hass
        self._ip = ip
        self._username = username
        self._password = password
        self._entry_id = entry_id
        self._sub_address: str | None = None
        self._sub_expiry: datetime | None = None
        self._task: asyncio.Task | None = None
        self._session: aiohttp.ClientSession | None = None

    async def async_start(self) -> None:
        """Create subscription and start background polling loop."""
        self._session = aiohttp.ClientSession()
        if await self._subscribe():
            self._task = self._hass.async_create_background_task(
                self._poll_loop(),
                f"vigicam_onvif_{self._entry_id}",
            )
        else:
            _LOGGER.warning(
                "ONVIF events unavailable for %s — binary sensors will not update in real time",
                self._ip,
            )

    async def async_stop(self) -> None:
        """Stop polling and release resources."""
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        if self._session and not self._session.closed:
            await self._session.close()

    # ── SOAP ─────────────────────────────────────────────────────────────────

    def _envelope(self, url: str, action: str, body: str) -> str:
        created = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        nonce_bytes = os.urandom(16)
        nonce_b64 = base64.b64encode(nonce_bytes).decode()
        # SHA1(nonce_bytes + created_utf8 + raw_password_utf8)
        digest = base64.b64encode(
            hashlib.sha1(
                nonce_bytes + created.encode() + self._password.encode()
            ).digest()
        ).decode()
        return (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<SOAP-ENV:Envelope'
            ' xmlns:SOAP-ENV="http://www.w3.org/2003/05/soap-envelope"'
            ' xmlns:wsa5="http://www.w3.org/2005/08/addressing"'
            ' xmlns:tev="http://www.onvif.org/ver10/events/wsdl"'
            ' xmlns:wsnt="http://docs.oasis-open.org/wsn/b-2"'
            ' xmlns:wsse="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-secext-1.0.xsd"'
            ' xmlns:wsu="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-wssecurity-utility-1.0.xsd">'
            "<SOAP-ENV:Header>"
            '<wsse:Security SOAP-ENV:mustUnderstand="1">'
            "<wsse:UsernameToken>"
            f"<wsse:Username>{self._username}</wsse:Username>"
            '<wsse:Password Type="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-username-token-profile-1.0#PasswordDigest">'
            f"{digest}</wsse:Password>"
            '<wsse:Nonce EncodingType="http://docs.oasis-open.org/wss/2004/01/oasis-200401-wss-soap-message-security-1.0#Base64Binary">'
            f"{nonce_b64}</wsse:Nonce>"
            f"<wsu:Created>{created}</wsu:Created>"
            "</wsse:UsernameToken>"
            "</wsse:Security>"
            f'<wsa5:Action SOAP-ENV:mustUnderstand="1">{action}</wsa5:Action>'
            f'<wsa5:To SOAP-ENV:mustUnderstand="1">{url}</wsa5:To>'
            "</SOAP-ENV:Header>"
            f"<SOAP-ENV:Body>{body}</SOAP-ENV:Body>"
            "</SOAP-ENV:Envelope>"
        )

    async def _soap(self, url: str, action: str, body: str, timeout: int = 15) -> str:
        envelope = self._envelope(url, action, body)
        t = aiohttp.ClientTimeout(total=timeout)
        async with self._session.post(
            url,
            data=envelope.encode(),
            headers={"Content-Type": "application/soap+xml; charset=utf-8"},
            timeout=t,
        ) as resp:
            return await resp.text()

    # ── Subscription ─────────────────────────────────────────────────────────

    async def _subscribe(self) -> bool:
        try:
            url = ONVIF_SERVICE_URL.format(self._ip)
            resp = await self._soap(
                url, _CREATE_ACTION,
                "<tev:CreatePullPointSubscription>"
                f"<tev:InitialTerminationTime>{SUBSCRIPTION_DURATION}</tev:InitialTerminationTime>"
                "</tev:CreatePullPointSubscription>",
                timeout=10,
            )
            addr = re.search(r"<wsa5?:Address>([^<]+)</wsa5?:Address>", resp)
            if not addr:
                _LOGGER.debug("ONVIF subscribe response: %s", resp[:500])
                return False
            self._sub_address = addr.group(1).strip()
            term = re.search(r"<wsnt:TerminationTime>([^<]+)</wsnt:TerminationTime>", resp)
            if term:
                try:
                    self._sub_expiry = datetime.fromisoformat(
                        term.group(1).replace("Z", "+00:00")
                    )
                except ValueError:
                    self._sub_expiry = None
            _LOGGER.debug("ONVIF subscription: %s expires %s", self._sub_address, self._sub_expiry)
            return True
        except Exception as exc:
            _LOGGER.warning("ONVIF subscribe failed for %s: %s", self._ip, exc)
            return False

    async def _renew(self) -> None:
        if not self._sub_address:
            return
        try:
            await self._soap(
                self._sub_address, _RENEW_ACTION,
                f"<wsnt:Renew>"
                f"<wsnt:TerminationTime>{SUBSCRIPTION_DURATION}</wsnt:TerminationTime>"
                f"</wsnt:Renew>",
                timeout=10,
            )
            _LOGGER.debug("ONVIF subscription renewed for %s", self._ip)
        except Exception as exc:
            _LOGGER.warning("ONVIF renew failed for %s: %s — will re-subscribe", self._ip, exc)
            self._sub_address = None

    # ── Polling ───────────────────────────────────────────────────────────────

    async def _pull(self) -> list[dict[str, Any]]:
        resp = await self._soap(
            self._sub_address, _PULL_ACTION,
            "<tev:PullMessages>"
            f"<tev:Timeout>PT{PULL_TIMEOUT_S}S</tev:Timeout>"
            "<tev:MessageLimit>100</tev:MessageLimit>"
            "</tev:PullMessages>",
            timeout=PULL_TIMEOUT_S + 5,
        )
        events = []
        for msg in re.findall(
            r"<wsnt:NotificationMessage>(.*?)</wsnt:NotificationMessage>",
            resp, re.DOTALL,
        ):
            topic_m = re.search(r"<wsnt:Topic[^>]*>(.*?)</wsnt:Topic>", msg, re.DOTALL)
            if not topic_m:
                continue
            topic = re.sub(r"\s+", "", topic_m.group(1))
            items = dict(re.findall(r'<tt:SimpleItem Name="([^"]+)" Value="([^"]+)"', msg))
            events.append({"topic": topic, "data": items})
        return events

    async def _poll_loop(self) -> None:
        while True:
            try:
                if (
                    self._sub_expiry
                    and (self._sub_expiry - datetime.now(timezone.utc)).total_seconds()
                    < RENEW_MARGIN_S
                ):
                    await self._renew()
                    if not self._sub_address:
                        await self._subscribe()

                events = await self._pull()
                for event in events:
                    _LOGGER.debug(
                        "ONVIF event %s: topic=%s data=%s",
                        self._ip, event["topic"], event["data"],
                    )
                    self._dispatch(event)

            except asyncio.CancelledError:
                return
            except Exception as exc:
                _LOGGER.warning(
                    "ONVIF poll error for %s: %s — re-subscribing in 15 s", self._ip, exc
                )
                self._sub_address = None
                await asyncio.sleep(15)
                await self._subscribe()

    def _dispatch(self, event: dict[str, Any]) -> None:
        topic = event["topic"]
        data = event["data"]

        event_type = None
        for keyword, etype in TOPIC_KEYWORD_MAP.items():
            if keyword.lower() in topic.lower():
                event_type = etype
                break

        if event_type is None:
            _LOGGER.debug("Unmapped ONVIF topic (add to TOPIC_KEYWORD_MAP if needed): %s", topic)
            return

        # VIGI cameras use Is* boolean fields (IsMotion, IsTamper, IsPeople, etc.)
        # Scan for the first Is* or Value field to determine active state.
        active = True  # default to active if no recognisable field present
        for key, val in data.items():
            if key.startswith("Is") or key == "Value":
                active = str(val).lower() in ("true", "1", "yes")
                break

        async_dispatcher_send(
            self._hass,
            SIGNAL_VIGICAM_EVENT.format(self._entry_id),
            {"type": event_type, "active": active},
        )
