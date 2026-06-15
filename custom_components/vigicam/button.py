"""Button entities for VIGI PTZ cameras — direction jog controls.

Six buttons per PTZ camera: Pan Left/Right, Tilt Up/Down, Zoom In/Out.
Each press runs ContinuousMove for BUTTON_MOVE_S seconds then stops.
For fine-grained duration control use the vigicam.ptz service instead.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .entity import VIGIEntity
from .onvif_ptz import BUTTON_MOVE_S, DEFAULT_SPEED


@dataclass(frozen=True)
class PTZButtonDesc:
    key: str
    name: str
    icon: str
    pan: float
    tilt: float
    zoom: float


_PTZ_BUTTONS: tuple[PTZButtonDesc, ...] = (
    PTZButtonDesc("ptz_left",      "PTZ Pan Left",    "mdi:pan-left",      -DEFAULT_SPEED, 0.0,           0.0),
    PTZButtonDesc("ptz_right",     "PTZ Pan Right",   "mdi:pan-right",      DEFAULT_SPEED, 0.0,           0.0),
    PTZButtonDesc("ptz_up",        "PTZ Tilt Up",     "mdi:pan-up",         0.0,           DEFAULT_SPEED, 0.0),
    PTZButtonDesc("ptz_down",      "PTZ Tilt Down",   "mdi:pan-down",       0.0,          -DEFAULT_SPEED, 0.0),
    PTZButtonDesc("ptz_zoom_in",   "PTZ Zoom In",     "mdi:magnify-plus",   0.0,           0.0,           DEFAULT_SPEED),
    PTZButtonDesc("ptz_zoom_out",  "PTZ Zoom Out",    "mdi:magnify-minus",  0.0,           0.0,          -DEFAULT_SPEED),
)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    data = hass.data[DOMAIN][entry.entry_id]
    if not data.get("has_ptz") or not data.get("onvif_ptz"):
        return
    async_add_entities(
        VIGIPTZButton(data["coordinator"], data, desc) for desc in _PTZ_BUTTONS
    )


class VIGIPTZButton(VIGIEntity, ButtonEntity):
    """Press to jog the camera in a direction for BUTTON_MOVE_S seconds."""

    def __init__(self, coordinator, entry_data, desc: PTZButtonDesc) -> None:
        super().__init__(coordinator, entry_data)
        self._desc = desc
        self._attr_name = desc.name
        self._attr_icon = desc.icon

    @property
    def _unique_id_suffix(self) -> str:
        return self._desc.key

    async def async_press(self) -> None:
        ptz = self._entry_data["onvif_ptz"]
        await ptz.continuous_move(self._desc.pan, self._desc.tilt, self._desc.zoom)
        await asyncio.sleep(BUTTON_MOVE_S)
        await ptz.stop()
