"""Button entities for VIGI cameras — one per PTZ preset."""
from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .entity import VIGIEntity


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    data = hass.data[DOMAIN][entry.entry_id]
    if not data["has_ptz"]:
        return
    async_add_entities(
        VIGIPresetButton(data["coordinator"], data, preset)
        for preset in data["presets"]
    )


class VIGIPresetButton(VIGIEntity, ButtonEntity):
    """Press to move PTZ camera to a named preset position."""

    def __init__(self, coordinator, entry_data, preset: dict) -> None:
        super().__init__(coordinator, entry_data)
        self._preset = preset
        self._attr_name = f"Go to {preset['name']}"

    @property
    def _unique_id_suffix(self) -> str:
        return f"preset_{self._preset['id']}"

    async def async_press(self) -> None:
        await self._entry_data["api"].goto_preset(self._preset["id"])
