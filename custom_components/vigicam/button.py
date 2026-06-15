"""Button entities — PTZ direction jog controls and alarm trigger/stop."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Callable

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .entity import VIGIEntity
from .onvif_ptz import BUTTON_MOVE_S, DEFAULT_SPEED


# ── PTZ jog buttons ───────────────────────────────────────────────────────────

@dataclass(frozen=True)
class PTZButtonDesc:
    key: str
    name: str
    icon: str
    pan: float
    tilt: float
    zoom: float


_PTZ_BUTTONS: tuple[PTZButtonDesc, ...] = (
    PTZButtonDesc("ptz_left",      "Pan Left",    "mdi:pan-left",      -DEFAULT_SPEED, 0.0,           0.0),
    PTZButtonDesc("ptz_right",     "Pan Right",   "mdi:pan-right",      DEFAULT_SPEED, 0.0,           0.0),
    PTZButtonDesc("ptz_up",        "Tilt Up",     "mdi:pan-up",         0.0,           DEFAULT_SPEED, 0.0),
    PTZButtonDesc("ptz_down",      "Tilt Down",   "mdi:pan-down",       0.0,          -DEFAULT_SPEED, 0.0),
    PTZButtonDesc("ptz_zoom_in",   "Zoom In",     "mdi:magnify-plus",   0.0,           0.0,           DEFAULT_SPEED),
    PTZButtonDesc("ptz_zoom_out",  "Zoom Out",    "mdi:magnify-minus",  0.0,           0.0,          -DEFAULT_SPEED),
)


# ── Alarm trigger/stop buttons ────────────────────────────────────────────────

@dataclass(frozen=True)
class AlarmButtonDesc:
    key: str
    name: str
    icon: str
    action: str  # "start" or "stop"
    supported_fn: Callable[[dict], bool] = field(default=lambda _: True)


_ALARM_BUTTONS: tuple[AlarmButtonDesc, ...] = (
    AlarmButtonDesc(
        "alarm_trigger", "Alarm Trigger", "mdi:alarm-light", "start",
        supported_fn=lambda d: bool(d.get("alarm")),
    ),
    AlarmButtonDesc(
        "alarm_stop", "Alarm Stop", "mdi:alarm-off", "stop",
        supported_fn=lambda d: bool(d.get("alarm")),
    ),
)


# ── Platform setup ────────────────────────────────────────────────────────────

async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    data = hass.data[DOMAIN][entry.entry_id]
    coordinator = data["coordinator"]
    coord_data = coordinator.data or {}

    entities: list[ButtonEntity] = []

    if data.get("has_ptz") and data.get("onvif_ptz"):
        entities.extend(VIGIPTZButton(coordinator, data, desc) for desc in _PTZ_BUTTONS)

    entities.extend(
        VIGIAlarmButton(coordinator, data, desc)
        for desc in _ALARM_BUTTONS
        if desc.supported_fn(coord_data)
    )

    if entities:
        async_add_entities(entities)


# ── Entity classes ────────────────────────────────────────────────────────────

class VIGIPTZButton(VIGIEntity, ButtonEntity):
    """Jogs the camera in one direction for BUTTON_MOVE_S seconds then stops."""

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


class VIGIAlarmButton(VIGIEntity, ButtonEntity):
    """Triggers or cancels the manual alarm sound (10 s countdown, camera auto-stops)."""

    def __init__(self, coordinator, entry_data, desc: AlarmButtonDesc) -> None:
        super().__init__(coordinator, entry_data)
        self._desc = desc
        self._attr_name = desc.name
        self._attr_icon = desc.icon

    @property
    def _unique_id_suffix(self) -> str:
        return self._desc.key

    async def async_press(self) -> None:
        api = self._entry_data["api"]
        if self._desc.action == "start":
            await api.trigger_alarm()
        else:
            await api.stop_alarm()
