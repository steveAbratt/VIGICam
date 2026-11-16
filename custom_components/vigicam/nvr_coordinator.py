"""Polling coordinator for a VIGI NVR entry."""
from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

import aiohttp

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import DEFAULT_SCAN_INTERVAL, DOMAIN
from .nvr_api import VIGINVR, VIGINVRAuthError, VIGINVRError

_LOGGER = logging.getLogger(__name__)


def channel_online(device: dict | None) -> bool:
    """added_devices reports online as "1"/"0"; accept bools and ints too."""
    if not device:
        return False
    return str(device.get("online", "0")).strip().lower() in ("1", "true", "on", "online")


class VIGINVRCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Reads channels, disks and arm state every 30 s.

    Only added_devices is required: everything else is optional, depends on the modules
    the firmware reports, and is left as None when a read fails so one unsupported call
    does not take every entity down.
    """

    def __init__(
        self, hass: HomeAssistant, api: VIGINVR, modules: dict[str, int], name: str
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN} NVR {name}",
            update_interval=timedelta(seconds=DEFAULT_SCAN_INTERVAL),
        )
        self.api = api
        self.modules = modules
        # Optional reads that failed once with an error_code are not retried every poll.
        self._unsupported: set[str] = set()

    def has_module(self, name: str) -> bool:
        # An empty module list means the firmware didn't answer module_list: try everything.
        return not self.modules or name in self.modules

    async def _optional(self, key: str, coro) -> Any:
        if key in self._unsupported:
            coro.close()
            return None
        try:
            return await coro
        except VIGINVRAuthError:
            raise
        except VIGINVRError as exc:
            if getattr(exc, "error_code", None) is not None:
                _LOGGER.debug("NVR %s: %s unsupported (%s); not asking again", self.api.host, key, exc)
                self._unsupported.add(key)
            else:
                _LOGGER.debug("NVR %s: %s failed: %s", self.api.host, key, exc)
            return None
        except (aiohttp.ClientError, TimeoutError) as exc:
            _LOGGER.debug("NVR %s: %s failed: %r", self.api.host, key, exc)
            return None

    async def _async_update_data(self) -> dict[str, Any]:
        try:
            devices = await self.api.added_devices()
        except VIGINVRAuthError as exc:
            raise ConfigEntryAuthFailed(str(exc)) from exc
        except (VIGINVRError, aiohttp.ClientError, TimeoutError) as exc:
            raise UpdateFailed(f"{type(exc).__name__}: {exc}") from exc

        by_channel: dict[int, dict] = {}
        for dev in devices:
            try:
                by_channel[int(dev["id"])] = dev
            except (TypeError, ValueError):
                continue

        try:
            data: dict[str, Any] = {"devices": by_channel}
            if self.has_module("disk"):
                data["disks"] = await self._optional("disks", self.api.disks())
            if self.has_module("alarm_device"):
                data["alarm_mode"] = await self._optional(
                    "alarm_mode", self.api.get_alarm_mode()
                )
            if self.has_module("active_defense"):
                arm: dict[int, str | None] = {}
                for ch, dev in by_channel.items():
                    if channel_online(dev):
                        arm[ch] = await self._optional(f"arm_{ch}", self.api.get_arm_status(ch))
                data["arm"] = arm
            if self.has_module("poe"):
                data["poe"] = await self._optional("poe", self.api.poe_status())
        except VIGINVRAuthError as exc:
            raise ConfigEntryAuthFailed(str(exc)) from exc
        return data
