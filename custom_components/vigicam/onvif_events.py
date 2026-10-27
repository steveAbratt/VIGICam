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


class VIGIOnvifError(Exception):
    """An ONVIF exchange completed but the camera refused it."""


def _parse_termination_time(resp: str) -> datetime | None:
    """Pull <wsnt:TerminationTime> out of a subscribe or renew response."""
    term = re.search(r"<wsnt:TerminationTime>([^<]+)</wsnt:TerminationTime>", resp)
    if not term:
        return None
    try:
        return datetime.fromisoformat(term.group(1).replace("Z", "+00:00"))
    except ValueError:
        return None

ONVIF_SERVICE_URL = "http://{}:80/onvif/service"
PULL_TIMEOUT_S = 8          # camera holds connection this long if no events
SUBSCRIPTION_DURATION = "PT1H"
RENEW_MARGIN_S = 300        # renew 5 min before expiry
RESUBSCRIBE_DELAY_S = 15   # backoff between subscribe attempts
_TIMEOUT_TOLERANCE = 3     # consecutive pull timeouts before rebuilding the subscription
AUTO_CLEAR_S = 15           # seconds before a detected state auto-resets

SIGNAL_VIGICAM_EVENT = "vigicam_event_{}"  # format with entry_id

_CREATE_ACTION = "http://www.onvif.org/ver10/events/wsdl/EventPortType/CreatePullPointSubscription"
_PULL_ACTION = "http://www.onvif.org/ver10/events/wsdl/PullPointSubscription/PullMessages"
_RENEW_ACTION = "http://www.onvif.org/ver10/events/wsdl/SubscriptionManager/Renew"
_UNSUBSCRIBE_ACTION = "http://docs.oasis-open.org/wsn/bw-2/SubscriptionManager/UnsubscribeRequest"

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
        self._consecutive_timeouts: int = 0
        self._timeout_warned: bool = False
        self._task: asyncio.Task | None = None
        self._session: aiohttp.ClientSession | None = None
        self._stop_task: asyncio.Future | None = None

    @property
    def is_connected(self) -> bool:
        """True when an active pull-point subscription exists."""
        return (
            self._sub_address is not None
            and self._task is not None
            and not self._task.done()
        )

    async def async_start(self) -> None:
        """Start the polling loop, which subscribes and keeps re-subscribing.

        The task is always created, even if the first subscribe fails. Previously a camera
        that was rebooting when HA started left events dead until the integration was
        reloaded by hand; the loop now simply retries.
        """
        self._session = aiohttp.ClientSession()
        if not await self._subscribe():
            _LOGGER.warning(
                "ONVIF subscribe failed for %s — retrying in the background; "
                "binary sensors will not update in real time until it succeeds",
                self._ip,
            )
        self._task = self._hass.async_create_background_task(
            self._poll_loop(),
            f"vigicam_onvif_{self._entry_id}",
        )

    async def async_stop(self) -> None:
        """Stop polling and release resources. Safe to call concurrently.

        It runs on an auth failure and again at unload; a second caller arriving while the
        first is mid-Unsubscribe would otherwise close the session under it and orphan the
        subscription on the camera.
        """
        if self._stop_task is None:
            self._stop_task = self._hass.async_create_background_task(
                self._do_stop(), "vigicam_onvif_stop"
            )
        await asyncio.shield(self._stop_task)

    async def _do_stop(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        # Release the subscription before closing the session, or the camera keeps it
        # alive for up to an hour after every reload — which on a camera with a small
        # subscription limit means a few reloads can lock out real-time events (#95).
        # finally: _unsubscribe does network I/O and does not swallow CancelledError, and
        # HA cancels unload if it runs long. Without this the session leaked whenever a
        # camera was unreachable at shutdown.
        try:
            await self._unsubscribe()
        finally:
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
            self._sub_expiry = _parse_termination_time(resp)
            _LOGGER.debug("ONVIF subscription: %s expires %s", self._sub_address, self._sub_expiry)
            return True
        except Exception as exc:
            _LOGGER.warning(
                "ONVIF subscribe failed for %s (%s: %s)",
                self._ip, type(exc).__name__, exc,
            )
            return False

    async def _renew(self) -> None:
        if not self._sub_address:
            return
        try:
            resp = await self._soap(
                self._sub_address, _RENEW_ACTION,
                f"<wsnt:Renew>"
                f"<wsnt:TerminationTime>{SUBSCRIPTION_DURATION}</wsnt:TerminationTime>"
                f"</wsnt:Renew>",
                timeout=10,
            )
            if "ResourceUnknown" in resp or "SOAP-ENV:Fault" in resp or "s:Fault" in resp:
                # A Fault here means the subscription is already gone. Treating it as
                # success left the loop renewing a dead subscription until expiry.
                raise VIGIOnvifError("renew refused (subscription no longer exists)")
            # Re-read the new expiry. Without this _sub_expiry kept its original value, so
            # once inside RENEW_MARGIN_S the renew condition was true on EVERY pass — a
            # reporter's log showed "subscription renewed" every ~9 seconds on a one-hour
            # subscription, one extra SOAP round trip before every single pull, against the
            # scarce resource #95 is about.
            self._sub_expiry = _parse_termination_time(resp) or self._sub_expiry
            _LOGGER.debug(
                "ONVIF subscription renewed for %s (expires %s)", self._ip, self._sub_expiry
            )
        except Exception as exc:
            _LOGGER.warning(
                "ONVIF renew failed for %s (%s: %s) — will re-subscribe",
                self._ip, type(exc).__name__, exc,
            )
            # Release rather than forget: a renew that failed may still have left a live
            # subscription on the camera, and those are a limited resource (#95).
            await self._unsubscribe()

    async def _unsubscribe(self) -> None:
        """Release the pull-point subscription on the camera, best effort.

        Abandoning a subscription by forgetting its address does NOT free it: the camera
        holds it until InitialTerminationTime (PT1H) expires. A poll error every ~15s
        therefore accumulated hundreds of live subscriptions per camera, and these
        devices allow only a handful — so the camera started refusing or stalling, which
        caused more poll errors, which orphaned more subscriptions. Reported in #95 on a
        4-camera install also running Synology Surveillance Station as a second ONVIF
        client, which consumed the same budget.
        """
        address, self._sub_address = self._sub_address, None
        self._sub_expiry = None
        # The timeout counter and its warning latch describe THIS subscription, so they
        # reset here — the one place a subscription ends. Resetting them at the call sites
        # instead left a renew-triggered rebuild carrying a stale count, so the first
        # timeout on a brand-new subscription could claim "timed out 3 times in a row" and
        # immediately tear it down again.
        self._consecutive_timeouts = 0
        self._timeout_warned = False
        if not address or self._session is None or self._session.closed:
            return
        try:
            await self._soap(
                address, _UNSUBSCRIBE_ACTION, "<wsnt:Unsubscribe/>", timeout=5
            )
            _LOGGER.debug("ONVIF subscription released for %s", self._ip)
        except Exception as exc:  # noqa: BLE001
            # Never let cleanup failure propagate — the caller is already handling an
            # error, and a camera that will not answer Unsubscribe will expire it anyway.
            _LOGGER.debug(
                "ONVIF unsubscribe failed for %s (%s: %s) — letting it expire",
                self._ip, type(exc).__name__, exc,
            )

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
                # Without a subscription there is nothing to pull from: _pull() would post
                # to a None address and raise on every pass. Re-subscribe first instead.
                if not self._sub_address:
                    if self._session is None or self._session.closed:
                        self._session = aiohttp.ClientSession()
                    if not await self._subscribe():
                        await asyncio.sleep(RESUBSCRIBE_DELAY_S)
                        continue

                if (
                    self._sub_expiry
                    and (self._sub_expiry - datetime.now(timezone.utc)).total_seconds()
                    < RENEW_MARGIN_S
                ):
                    await self._renew()
                    if not self._sub_address and not await self._subscribe():
                        await asyncio.sleep(RESUBSCRIBE_DELAY_S)
                        continue

                try:
                    events = await self._pull()
                except (TimeoutError, asyncio.TimeoutError):
                    # A PullMessages timeout is not a subscription failure. The camera
                    # holds the request open for PULL_TIMEOUT_S and should answer with an
                    # empty result; if it is merely slow — busy serving another ONVIF
                    # client, say — the subscription is still perfectly good. Tearing it
                    # down here was what orphaned subscriptions and lost every event in
                    # the 15s re-subscribe window (#95). Just pull again.
                    self._consecutive_timeouts += 1
                    if self._consecutive_timeouts >= _TIMEOUT_TOLERANCE:
                        if not self._timeout_warned:
                            # Once per episode, not once per rebuild: a permanently slow
                            # camera would otherwise log this every ~40s indefinitely,
                            # which is the kind of log noise this release set out to end.
                            _LOGGER.warning(
                                "ONVIF pull keeps timing out for %s — the camera is slow "
                                "to answer and events may be delayed. If another ONVIF "
                                "client (an NVR, Synology Surveillance Station, a second "
                                "Home Assistant) also subscribes to this camera, it may "
                                "be at its subscription limit.",
                                self._ip,
                            )
                        # Persistently unanswered: rebuild, releasing the old subscription
                        # first so it does not linger on the camera. _unsubscribe() clears
                        # the latch along with the rest of the subscription state, so set it
                        # again afterwards — the episode is not over until a pull succeeds.
                        await self._unsubscribe()
                        self._timeout_warned = True
                        await asyncio.sleep(RESUBSCRIBE_DELAY_S)
                    continue
                if self._timeout_warned:
                    _LOGGER.info("ONVIF polling recovered for %s", self._ip)
                self._consecutive_timeouts = 0
                self._timeout_warned = False

                for event in events:
                    _LOGGER.debug(
                        "ONVIF event %s: topic=%s data=%s",
                        self._ip, event["topic"], event["data"],
                    )
                    self._dispatch(event)

            except asyncio.CancelledError:
                return
            except Exception as exc:
                # type(exc).__name__ as well as the message: several of the exceptions
                # that land here (TimeoutError among them) have an empty str(), so the
                # old log line read "poll error for <ip>: — re-subscribing" and named
                # nothing. #95 was reported with 1306 of those.
                _LOGGER.warning(
                    "ONVIF poll error for %s (%s: %s) — re-subscribing in %s s",
                    self._ip, type(exc).__name__, exc, RESUBSCRIBE_DELAY_S,
                )
                # Release it on the camera, not just locally, then let the top of the
                # loop rebuild — so a failed re-subscribe backs off instead of falling
                # straight into _pull().
                await self._unsubscribe()
                await asyncio.sleep(RESUBSCRIBE_DELAY_S)

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

        # The ONVIF Source block identifies which detection zone fired.
        # TP-Link cameras put this in a "Rule" SimpleItem (e.g. "Area1", "Line1").
        area: str | None = data.get("Rule") or data.get("RuleName") or data.get("RuleRegionID")

        async_dispatcher_send(
            self._hass,
            SIGNAL_VIGICAM_EVENT.format(self._entry_id),
            {"type": event_type, "active": active, "area": area},
        )
