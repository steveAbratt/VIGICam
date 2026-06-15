"""Select entities for VIGI cameras — night vision mode and PTZ preset."""
from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, NIGHT_VISION_MODES
from .entity import VIGIEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    data = hass.data[DOMAIN][entry.entry_id]
    coordinator = data["coordinator"]
    entities: list = []

    # Night vision select only if camera reports image switch data
    if (coordinator.data or {}).get("image_switch"):
        entities.append(VIGINightVisionSelect(coordinator, data))

    if data["has_ptz"]:
        entities.append(VIGIPTZPresetSelect(coordinator, data))

    async_add_entities(entities)


class VIGINightVisionSelect(VIGIEntity, SelectEntity):
    """Select between IR, spotlight, and auto night-vision modes."""

    _attr_name = "Night Vision Mode"
    _attr_options = list(NIGHT_VISION_MODES.values())

    @property
    def _unique_id_suffix(self) -> str:
        return "night_vision"

    @property
    def current_option(self) -> str | None:
        mode = (self.coordinator.data or {}).get("image_switch", {}).get(
            "night_vision_mode"
        )
        return NIGHT_VISION_MODES.get(mode)

    async def async_select_option(self, option: str) -> None:
        mode = next((k for k, v in NIGHT_VISION_MODES.items() if v == option), None)
        if mode:
            await self._entry_data["api"].set_night_vision_mode(mode)
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
        # Cameras don't report which preset is active
        return None

    async def async_select_option(self, option: str) -> None:
        preset = next(p for p in self.coordinator.presets if p["name"] == option)
        await self._entry_data["api"].goto_preset(preset["id"])
