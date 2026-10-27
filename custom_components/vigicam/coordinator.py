"""VIGI camera data update coordinator — polls all state every 30 s."""
from __future__ import annotations

from datetime import timedelta
import logging
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import VIGIAuthError, VIGICamera, VIGIError
from .const import DEFAULT_SCAN_INTERVAL, DOMAIN

if TYPE_CHECKING:
    from .openapi import VIGIOpenAPI

_LOGGER = logging.getLogger(__name__)

# How many coordinator ticks between OpenAPI re-checks when has_openapi=False.
# 10 ticks × 30 s = 5 minutes.
_OPENAPI_RECHECK_TICKS = 10
_PRESET_REFRESH_TICKS = 10   # ~5 min at the default 30 s scan interval


class VIGICoordinator(DataUpdateCoordinator[dict[str, Any]]):
    def __init__(
        self,
        hass: HomeAssistant,
        camera: VIGICamera,
        *,
        has_ptz: bool = False,
        has_sd_card: bool = False,
        has_openapi: bool = False,
        openapi: "VIGIOpenAPI | None" = None,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=DEFAULT_SCAN_INTERVAL),
        )
        self.camera = camera
        self.has_ptz = has_ptz
        self.has_sd_card = has_sd_card
        self.has_openapi = has_openapi
        self.openapi: VIGIOpenAPI | None = openapi
        self.presets: list[dict] = []
        # Stop functions for the event listeners, registered by async_setup_entry. Called on
        # an auth failure so the listeners stop logging in with the old password every 15s
        # while the user is being asked for the new one — repeated failures can lock the
        # camera account, and then the correct password is refused in the reauth form.
        self.stop_on_auth_failure: list[Callable[[], Awaitable[None]]] = []
        self.last_preset: str | None = None  # last preset selected from HA; None = unknown
        self._openapi_recheck_counter: int = 0
        self._preset_refresh_counter: int = 0

    async def _async_update_data(self) -> dict[str, Any]:
        try:
            return await self._fetch_all()
        except VIGIAuthError as exc:
            # ConfigEntryAuthFailed, not UpdateFailed: this makes HA start the reauth
            # flow and show a "needs attention" card. As UpdateFailed, a password
            # changed while HA was running retried every 30s forever, logging a
            # generic communication error and never telling the user their
            # credentials were the problem.
            for stop in self.stop_on_auth_failure:
                self.hass.async_create_task(stop())
            raise ConfigEntryAuthFailed(f"Authentication error: {exc}") from exc
        except VIGIError as exc:
            raise UpdateFailed(f"Camera communication error: {exc}") from exc

    async def _fetch_all(self) -> dict[str, Any]:
        results: dict[str, Any] = {}
        answered: set[str] = set()

        async def safe_get(key: str, coro):
            try:
                results[key] = await coro
                answered.add(key)
            except VIGIAuthError:
                # Must escape. VIGIAuthError is a SIBLING of VIGIError, not a subclass, so
                # it used to fall through to `except Exception` below, get logged as an
                # "unexpected error" and leave results[key] = {} — meaning _fetch_all
                # returned normally and the ConfigEntryAuthFailed in _async_update_data was
                # never reached. A password changed while HA was running therefore retried
                # forever on every camera except PTZ models, where get_presets() happened to
                # let it through. Credentials are a whole-entry problem, not a per-key one.
                raise
            except VIGIError as exc:
                _LOGGER.debug("Could not fetch %s: %s", key, exc)
                results[key] = {}
            except Exception as exc:  # noqa: BLE001
                _LOGGER.warning("Unexpected error fetching %s: %s", key, exc)
                results[key] = {}

        await safe_get("device_info", self.camera.get_device_info())
        if not answered:
            # The first read failed. Treat it as the camera being unreachable rather than
            # running eleven more requests into a dead host and returning a dict of empties:
            # that made every switch read "off" (motion detection, privacy mask, alarm) while
            # the entities stayed available, so automations acted on invented state.
            # UpdateFailed marks them unavailable and HA logs the transition once.
            raise UpdateFailed("Camera not responding")
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
                    # Including VIGIOpenAPIAuthError, deliberately. Escalating it to reauth
                    # (tried in an unreleased b5 draft) stopped ALL polling on any OpenAPI
                    # reply without a stok — busy, rate-limited, malformed — and the reauth
                    # form only validates the JSON API, so re-entering the same password
                    # "fixed" nothing. A genuinely changed password fails the JSON API too,
                    # and that is what raises ConfigEntryAuthFailed.
                    _LOGGER.debug("OpenAPI %s failed: %s", method, exc)
                    results[key] = {}

            await safe_openapi("openapi_sd", "getSdCardStatus")
            await safe_openapi("openapi_device", "getDeviceStatus")
            await safe_openapi("openapi_people", "getPeopleDetectionSwitch")
            await safe_openapi("openapi_vehicle", "getVehicleDetectionSwitch")

        # Only from a storage read that actually answered. A single failed read used to flip
        # has_sd_card to False, raise a spurious "SD card missing" repair, and then trigger
        # a full entry reload one poll later when the card "reappeared".
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

        ip = self.camera._ip
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
