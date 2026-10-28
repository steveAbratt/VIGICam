"""VIGI camera data update coordinator — polls all state every 30 s."""
from __future__ import annotations

from datetime import timedelta
import logging
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any
from urllib.parse import unquote

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import VIGIAuthError, VIGICamera, VIGIError
from .const import DEFAULT_SCAN_INTERVAL, DOMAIN, REPAIRS_WRONG_CAMERA

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from .openapi import VIGIOpenAPI

_LOGGER = logging.getLogger(__name__)

# How many coordinator ticks between OpenAPI re-checks when has_openapi=False.
# 10 ticks × 30 s = 5 minutes.
_OPENAPI_RECHECK_TICKS = 10
_PRESET_REFRESH_TICKS = 10   # ~5 min at the default 30 s scan interval



def _canonical_mac(value: str | None) -> str | None:
    """12 lowercase hex digits, or None if *value* isn't a MAC.

    For comparison only — entries stay keyed by camera_unique_id(), which must never
    change. Accepts dashes, colons, none, upper case and URL-encoded separators, so a
    firmware update that changes the MAC's format can't lock every camera out.
    """
    if not value:
        return None
    digits = "".join(c for c in unquote(value).lower() if c in "0123456789abcdef")
    return digits if len(digits) == 12 else None

class VIGICoordinator(DataUpdateCoordinator[dict[str, Any]]):
    def __init__(
        self,
        hass: HomeAssistant,
        camera: VIGICamera,
        *,
        has_ptz: bool = False,
        has_openapi: bool = False,
        openapi: "VIGIOpenAPI | None" = None,
        config_entry: "ConfigEntry",
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=config_entry,
            name=f"{DOMAIN} {camera.host}",  # per camera, so failures name which one
            update_interval=timedelta(seconds=DEFAULT_SCAN_INTERVAL),
        )
        self.camera = camera
        self.has_ptz = has_ptz
        # None until a storage read answers (live value and diagnostics only). The setup
        # SD listener deliberately uses a bool baseline: see _on_coordinator_update.
        self.has_sd_card: bool | None = None
        self.has_openapi = has_openapi
        self.openapi: VIGIOpenAPI | None = openapi
        self.presets: list[dict] = []
        # Called before raising ConfigEntryAuthFailed so the listeners stop retrying the
        # old password (account lockout); a later successful poll reloads the entry.
        self.stop_on_auth_failure: list[Callable[[], Awaitable[None]]] = []
        self._auth_failed = False
        self.identity_ok = True
        self.last_preset: str | None = None  # last preset selected from HA; None = unknown
        self._openapi_recheck_counter: int = 0
        self._preset_refresh_counter: int = 0

    async def _async_update_data(self) -> dict[str, Any]:
        try:
            data = await self._fetch_all()
        except VIGIAuthError as exc:
            self._auth_failed = True
            for stop in self.stop_on_auth_failure:
                self.hass.async_create_task(stop())
            raise ConfigEntryAuthFailed(f"Authentication error: {exc}") from exc
        except VIGIError as exc:
            raise UpdateFailed(f"Camera communication error: {exc}") from exc
        if self._auth_failed:
            self._auth_failed = False
            # Recovered without a reauth reload: the listeners were stopped, so reload to
            # restart them rather than leave detection sensors "off" with nothing feeding them.
            if self.stop_on_auth_failure:
                self.hass.config_entries.async_schedule_reload(self.config_entry.entry_id)
        return data

    def check_identity(self, device_info: dict) -> None:
        """Refuse to drive a different camera that now answers at this entry's address.

        Two cameras that swap DHCP addresses and share a password otherwise swap
        identities silently. Deliberately called outside safe_get: its broad except would
        swallow the UpdateFailed and the check would do nothing.
        """
        expected = _canonical_mac(self.config_entry.unique_id)
        found = _canonical_mac(device_info.get("mac"))
        issue_id = f"{REPAIRS_WRONG_CAMERA}_{self.config_entry.entry_id}"
        if expected is None or found is None:
            # Entry keyed on an address (camera reported no MAC when added), or this
            # reply carried none: nothing to compare. Leave any existing issue alone —
            # only a positive match may clear it.
            return
        if found == expected:
            self.identity_ok = True
            ir.async_delete_issue(self.hass, DOMAIN, issue_id)
            return
        self.identity_ok = False
        ir.async_create_issue(
            self.hass, DOMAIN, issue_id,
            is_fixable=False,
            severity=ir.IssueSeverity.ERROR,
            translation_key=REPAIRS_WRONG_CAMERA,
            translation_placeholders={
                "title": self.config_entry.title,
                "host": self.camera.host,
                "mac": "-".join(expected[k:k + 2] for k in range(0, 12, 2)).upper(),
            },
        )
        raise UpdateFailed(
            f"A different camera is answering at {self.camera.host} — its address changed"
        )

    async def async_refresh_presets(self) -> None:
        """Re-read presets after a save or delete.

        Keeps the old list if the read comes back empty (a failed read looks the same),
        and forces the next poll to re-read anyway: presets are written through OpenAPI
        and read through the JSON API, which may lag.
        """
        presets = await self.camera.get_presets()
        if presets or not self.presets:
            self.presets = presets
        self._preset_refresh_counter = _PRESET_REFRESH_TICKS
        self.async_update_listeners()

    async def _fetch_all(self) -> dict[str, Any]:
        results: dict[str, Any] = {}
        answered: set[str] = set()

        async def safe_get(key: str, coro):
            try:
                results[key] = await coro
                answered.add(key)
            except VIGIAuthError:
                # Not a VIGIError subclass: must escape so the entry goes to reauth.
                raise
            except VIGIError as exc:
                _LOGGER.debug("Could not fetch %s: %s", key, exc)
                results[key] = {}
            except Exception as exc:  # noqa: BLE001
                _LOGGER.warning("Unexpected error fetching %s: %s", key, exc)
                results[key] = {}

        await safe_get("device_info", self.camera.get_device_info())
        if "device_info" not in answered:
            # Unreachable: fail the poll so entities go unavailable instead of reading "off".
            raise UpdateFailed("Camera not responding")
        self.check_identity(results["device_info"])
        await safe_get("motion", self.camera.get_motion_detection())
        await safe_get("image_switch", self.camera.get_image_switch())
        await safe_get("image_common", self.camera.get_image_common())
        await safe_get("alarm", self.camera.get_alarm())
        await safe_get("led", self.camera.get_led())
        await safe_get("speaker", self.camera.get_audio_speaker())
        await safe_get("microphone", self.camera.get_audio_microphone())
        await safe_get("storage", self.camera.get_storage())
        await safe_get("tamper", self.camera.get_tamper())
        await safe_get("network", self.camera.get_network())
        await safe_get("lens_mask", self.camera.get_lens_mask())

        if self.has_ptz:
            # Refresh periodically, not just once: presets added or renamed in the camera's
            # own web UI or the VIGI app would otherwise never reach HA.
            self._preset_refresh_counter += 1
            if not self.presets or self._preset_refresh_counter >= _PRESET_REFRESH_TICKS:
                self._preset_refresh_counter = 0
                try:
                    self.presets = await self.camera.get_presets()
                except VIGIError:
                    self.presets = self.presets or []
            await safe_get("target_track", self.camera.get_target_track())

        if self.has_openapi and self.openapi:
            async def safe_openapi(key: str, method: str) -> None:
                try:
                    raw = await self.openapi.call(method)
                    results[key] = raw.get("result", {})
                except Exception as exc:  # noqa: BLE001
                    # Includes VIGIOpenAPIAuthError: stok failures are often transient, and
                    # reauth only validates the JSON API. Escalating this was tried in a b5
                    # draft and stopped all polling on any busy or malformed reply.
                    _LOGGER.debug("OpenAPI %s failed: %s", method, exc)
                    results[key] = {}

            await safe_openapi("openapi_sd", "getSdCardStatus")
            await safe_openapi("openapi_device", "getDeviceStatus")
            await safe_openapi("openapi_people", "getPeopleDetectionSwitch")
            await safe_openapi("openapi_vehicle", "getVehicleDetectionSwitch")

        # Only from a read that answered; a failed read must not look like a missing card.
        if "storage" in answered:
            self.has_sd_card = _detect_sd_card(results["storage"])

        # Periodically probe for OpenAPI becoming available.
        if not self.has_openapi:
            self._openapi_recheck_counter += 1
            if self._openapi_recheck_counter >= _OPENAPI_RECHECK_TICKS:
                self._openapi_recheck_counter = 0
                await self._recheck_openapi()

        return results

    async def _recheck_openapi(self) -> None:
        from .openapi import VIGIOpenAPI, try_openapi

        ip = self.camera.host
        if await try_openapi(ip, self.camera._username, self.camera._password):
            self.has_openapi = True
            self.openapi = VIGIOpenAPI(ip, self.camera._username, self.camera._password)
            _LOGGER.info(
                "OpenAPI is now available on %s — reload the VIGICam integration "
                "to enable Vehicle Detection and other OpenAPI sensors",
                ip,
            )


def _detect_sd_card(storage: dict) -> bool:
    """Return True only when a usable SD card is present."""
    return storage.get("status", "").lower() in {"normal", "full"}
