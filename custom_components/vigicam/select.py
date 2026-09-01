"""Select entities for VIGI cameras — night vision mode, PTZ preset, image controls."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Callable

from homeassistant.components.select import SelectEntity, SelectEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .api import VIGICamera
from .const import (
    CONF_FEATURE_IMAGE_CONTROLS,
    DEFAULT_FEATURE_IMAGE_CONTROLS,
    DOMAIN,
    NIGHT_VISION_MODES,
    PRE_NIGHT_VISION_MODES,
    PRE_NIGHT_VISION_WHITE_LED_MODES,
)
from .entity import VIGIEntity

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, kw_only=True)
class VIGISelectDescription(SelectEntityDescription):
    options: list[str]
    value_fn: Callable[[dict], str | None]
    set_fn: Callable[[VIGICamera, str], Any]
    supported_fn: Callable[[dict], bool] = lambda _: True


IMAGE_CONTROL_SELECTS: tuple[VIGISelectDescription, ...] = (
    VIGISelectDescription(
        key="flip",
        name="Flip",
        icon="mdi:flip-vertical",
        options=["off", "center", "flip", "mirror"],
        entity_category=EntityCategory.CONFIG,
        entity_registry_enabled_default=False,
        value_fn=lambda d: d.get("image_switch", {}).get("flip_type"),
        set_fn=lambda api, v: api.set_image_switch_value("flip_type", v),
        supported_fn=lambda d: "flip_type" in d.get("image_switch", {}),
    ),
    VIGISelectDescription(
        key="rotate",
        name="Rotate",
        icon="mdi:rotate-right",
        options=["off", "90", "180", "270"],
        entity_category=EntityCategory.CONFIG,
        entity_registry_enabled_default=False,
        value_fn=lambda d: d.get("image_switch", {}).get("rotate_type"),
        set_fn=lambda api, v: api.set_image_switch_value("rotate_type", v),
        supported_fn=lambda d: "rotate_type" in d.get("image_switch", {}),
    ),
    VIGISelectDescription(
        key="flicker",
        name="Flicker",
        icon="mdi:sine-wave",
        options=["50hz", "60hz"],
        entity_category=EntityCategory.CONFIG,
        entity_registry_enabled_default=False,
        value_fn=lambda d: d.get("image_switch", {}).get("flicker"),
        set_fn=lambda api, v: api.set_image_switch_value("flicker", v),
        supported_fn=lambda d: "flicker" in d.get("image_switch", {}),
    ),
    VIGISelectDescription(
        key="white_balance",
        name="White Balance",
        icon="mdi:white-balance-auto",
        options=["auto", "nature", "manual", "lock"],
        entity_category=EntityCategory.CONFIG,
        entity_registry_enabled_default=False,
        value_fn=lambda d: d.get("image_common", {}).get("wb_type"),
        set_fn=lambda api, v: api.set_image_common_value("wb_type", v),
        supported_fn=lambda d: "wb_type" in d.get("image_common", {}),
    ),
    VIGISelectDescription(
        key="exposure_type",
        name="Exposure Type",
        icon="mdi:camera-iris",
        options=["auto", "manual"],
        entity_category=EntityCategory.CONFIG,
        entity_registry_enabled_default=False,
        value_fn=lambda d: d.get("image_common", {}).get("exp_type"),
        set_fn=lambda api, v: api.set_image_common_value("exp_type", v),
        supported_fn=lambda d: "exp_type" in d.get("image_common", {}),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    data = hass.data[DOMAIN][entry.entry_id]
    coordinator = data["coordinator"]
    coord_data = coordinator.data or {}
    entities: list = []

    if coord_data.get("image_switch"):
        entities.append(VIGINightVisionSelect(coordinator, data))

    if data["has_ptz"]:
        entities.append(VIGIPTZPresetSelect(coordinator, data))

    if entry.options.get(CONF_FEATURE_IMAGE_CONTROLS, DEFAULT_FEATURE_IMAGE_CONTROLS):
        entities.extend(
            VIGISelect(coordinator, data, desc)
            for desc in IMAGE_CONTROL_SELECTS
            if desc.supported_fn(coord_data)
        )

    async_add_entities(entities)


class VIGINightVisionSelect(VIGIEntity, SelectEntity):
    """Select between IR, spotlight, and auto night-vision modes.

    Newer firmware (C340/C350-class) exposes `pre_night_vision_mode` as the
    writable preference and demotes `night_vision_mode` to a read-only status
    of which emitter is lit. Where that field is present we read and write it;
    otherwise we fall back to the older single-field behaviour.
    """

    _attr_name = "Night Vision Mode"

    @property
    def _unique_id_suffix(self) -> str:
        return "night_vision"

    @property
    def _image_switch(self) -> dict:
        return (self.coordinator.data or {}).get("image_switch", {})

    @property
    def _modes(self) -> dict[str, str]:
        """The value→label map this camera's firmware uses."""
        switch = self._image_switch
        if "pre_night_vision_mode" not in switch:
            return NIGHT_VISION_MODES

        modes = dict(PRE_NIGHT_VISION_MODES)
        # Models without a white LED (e.g. C340i) offer only the IR-side modes.
        # They report no white-light intensity control, which is the tell.
        if "wtl_intensity_level" not in switch:
            for key in PRE_NIGHT_VISION_WHITE_LED_MODES:
                modes.pop(key, None)
        # Never hide the mode the camera is actually in — if the capability
        # probe is wrong, showing an extra option beats a blank dropdown.
        current = switch.get("pre_night_vision_mode")
        if current in PRE_NIGHT_VISION_MODES:
            modes.setdefault(current, PRE_NIGHT_VISION_MODES[current])
        return modes

    @property
    def _mode_key(self) -> str:
        return (
            "pre_night_vision_mode"
            if "pre_night_vision_mode" in self._image_switch
            else "night_vision_mode"
        )

    @property
    def options(self) -> list[str]:
        return list(self._modes.values())

    @property
    def current_option(self) -> str | None:
        return self._modes.get(self._image_switch.get(self._mode_key))

    async def async_select_option(self, option: str) -> None:
        modes = self._modes
        mode = next((k for k, v in modes.items() if v == option), None)
        if not mode:
            return
        api = self._entry_data["api"]
        if self._mode_key == "pre_night_vision_mode":
            await api.set_pre_night_vision_mode(mode)
        else:
            await api.set_night_vision_mode(mode)
        await self.coordinator.async_request_refresh()


class VIGIPTZPresetSelect(VIGIEntity, SelectEntity):
    """Select a named PTZ preset to move the camera to."""

    _attr_name = "PTZ Preset"

    @property
    def _unique_id_suffix(self) -> str:
        return "ptz_preset"

    @property
    def options(self) -> list[str]:
        return [p["name"] for p in self.coordinator.presets]

    @property
    def current_option(self) -> str | None:
        last = self.coordinator.last_preset
        if last and last in self.options:
            return last
        return None

    async def async_select_option(self, option: str) -> None:
        preset = next((p for p in self.coordinator.presets if p["name"] == option), None)
        if preset is None:
            _LOGGER.warning("PTZ preset '%s' is not on the camera; ignoring", option)
            return
        await self._entry_data["api"].goto_preset(preset["id"])
        self.coordinator.last_preset = option
        self.async_write_ha_state()


class VIGISelect(VIGIEntity, SelectEntity):
    """Descriptor-driven select entity for image controls."""

    entity_description: VIGISelectDescription

    def __init__(self, coordinator, entry_data, description: VIGISelectDescription) -> None:
        super().__init__(coordinator, entry_data)
        self.entity_description = description
        self._attr_options = description.options

    @property
    def _unique_id_suffix(self) -> str:
        return self.entity_description.key

    @property
    def current_option(self) -> str | None:
        return self.entity_description.value_fn(self.coordinator.data or {})

    async def async_select_option(self, option: str) -> None:
        await self.entity_description.set_fn(self._entry_data["api"], option)
        await self.coordinator.async_request_refresh()
