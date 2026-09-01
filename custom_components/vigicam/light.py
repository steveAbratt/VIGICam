"""Light platform for VIGI cameras — spotlight / white-light entity.

Replaces the separate spotlight_intensity number entity. The camera's 1–4
intensity scale is mapped linearly to HA's 1–255 brightness range.

Turn on  → white light on; turn off → back to IR.

Which field carries that depends on firmware. Older cameras take it on the
writable `night_vision_mode` ("wtl_night_vision" / "inf_night_vision"). Newer
C340/C350-class firmware demotes `night_vision_mode` to a read-only status and
takes the setting on `pre_night_vision_mode` instead ("white_led_always_on" /
"auto_ir") — writing the status field there is rejected by the camera.

Either way `night_vision_mode` remains the honest readback for whether the
white light is currently lit, so is_on reads it on both firmwares.

The night_vision select entity remains available for switching between all IR
and colour modes; both entities reflect the same underlying camera state.
"""
from __future__ import annotations

import math
import logging

from homeassistant.components.light import ATTR_BRIGHTNESS, ColorMode, LightEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import (
    DOMAIN,
    PRE_SPOTLIGHT_OFF_MODE,
    PRE_SPOTLIGHT_ON_MODE,
)
from .entity import VIGIEntity

_LOGGER = logging.getLogger(__name__)

_SPOTLIGHT_MODE = "wtl_night_vision"
_FALLBACK_MODE = "inf_night_vision"

# Camera intensity scale: 1 (dim) – 4 (full)
_CAM_MIN = 1
_CAM_MAX = 4


def _cam_to_ha(level: int) -> int:
    """Map camera 1–4 intensity to HA 1–255 brightness."""
    return round((level - _CAM_MIN) / (_CAM_MAX - _CAM_MIN) * 254) + 1


def _ha_to_cam(brightness: int) -> int:
    """Map HA 1–255 brightness to camera 1–4 intensity."""
    return max(_CAM_MIN, min(_CAM_MAX, math.ceil(brightness / 255 * _CAM_MAX)))


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    data = hass.data[DOMAIN][entry.entry_id]
    coordinator = data["coordinator"]
    image_switch = (coordinator.data or {}).get("image_switch", {})
    if "wtl_intensity_level" in image_switch or "night_vision_mode" in image_switch:
        async_add_entities([VIGISpotlight(coordinator, data)])


class VIGISpotlight(VIGIEntity, LightEntity):
    """Spotlight / white-light entity for VIGI cameras."""

    _attr_color_mode = ColorMode.BRIGHTNESS
    _attr_supported_color_modes = {ColorMode.BRIGHTNESS}
    _attr_name = "Spotlight"
    _attr_icon = "mdi:spotlight"

    def __init__(self, coordinator, entry_data) -> None:
        super().__init__(coordinator, entry_data)
        self._optimistic_on: bool | None = None
        self._optimistic_brightness: int | None = None

    @property
    def _unique_id_suffix(self) -> str:
        return "spotlight"

    @property
    def is_on(self) -> bool:
        if self._optimistic_on is not None:
            return self._optimistic_on
        mode = (self.coordinator.data or {}).get("image_switch", {}).get("night_vision_mode")
        return mode == _SPOTLIGHT_MODE

    @property
    def brightness(self) -> int:
        if self._optimistic_brightness is not None:
            return self._optimistic_brightness
        level = (self.coordinator.data or {}).get("image_switch", {}).get(
            "wtl_intensity_level", _CAM_MAX
        )
        try:
            return _cam_to_ha(int(level))
        except (TypeError, ValueError):
            return 255

    async def _set_mode(self, api, legacy_mode: str, pre_mode: str) -> None:
        """Write whichever night-vision field this camera's firmware accepts."""
        image_switch = (self.coordinator.data or {}).get("image_switch", {})
        if "pre_night_vision_mode" in image_switch:
            await api.set_pre_night_vision_mode(pre_mode)
        else:
            await api.set_night_vision_mode(legacy_mode)

    def _clear_optimistic(self) -> None:
        self._optimistic_on = None
        self._optimistic_brightness = None

    async def async_turn_on(self, **kwargs) -> None:
        ha_brightness = kwargs.get(ATTR_BRIGHTNESS)
        cam_level = _ha_to_cam(ha_brightness) if ha_brightness is not None else None

        # Optimistic update for instant UI feedback.
        self._optimistic_on = True
        self._optimistic_brightness = ha_brightness if ha_brightness is not None else self.brightness
        self.async_write_ha_state()

        try:
            api = self._entry_data["api"]
            await self._set_mode(api, _SPOTLIGHT_MODE, PRE_SPOTLIGHT_ON_MODE)
            if cam_level is not None:
                await api.set_spotlight_intensity(cam_level)
        except Exception as exc:
            _LOGGER.error("Spotlight turn on failed: %s", exc)
        finally:
            self._clear_optimistic()
            await self.coordinator.async_request_refresh()

    async def async_turn_off(self, **kwargs) -> None:
        self._optimistic_on = False
        self.async_write_ha_state()

        try:
            await self._set_mode(
                self._entry_data["api"], _FALLBACK_MODE, PRE_SPOTLIGHT_OFF_MODE
            )
        except Exception as exc:
            _LOGGER.error("Spotlight turn off failed: %s", exc)
        finally:
            self._clear_optimistic()
            await self.coordinator.async_request_refresh()

    def _handle_coordinator_update(self) -> None:
        """Clear optimistic state on coordinator update."""
        self._clear_optimistic()
        super()._handle_coordinator_update()
