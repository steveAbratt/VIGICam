"""Number entities for VIGI cameras — volume, sensitivity, spotlight intensity."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from homeassistant.components.number import NumberEntity, NumberEntityDescription, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .api import VIGICamera
from .const import DOMAIN
from .entity import VIGIEntity


@dataclass(frozen=True, kw_only=True)
class VIGINumberDescription(NumberEntityDescription):
    value_fn: Callable[[dict], float | None]
    set_fn: Callable[[VIGICamera, float], Any]
    supported_fn: Callable[[dict], bool] = lambda _: True


NUMBERS: tuple[VIGINumberDescription, ...] = (
    VIGINumberDescription(
        key="speaker_volume",
        name="Speaker Volume",
        native_min_value=0,
        native_max_value=100,
        native_step=1,
        native_unit_of_measurement=PERCENTAGE,
        mode=NumberMode.SLIDER,
        value_fn=lambda d: d.get("speaker", {}).get("volume"),
        set_fn=lambda api, v: api.set_speaker_volume(int(v)),
        supported_fn=lambda d: bool(d.get("speaker")),
    ),
    VIGINumberDescription(
        key="motion_sensitivity",
        name="Motion Sensitivity",
        native_min_value=0,
        native_max_value=100,
        native_step=1,
        native_unit_of_measurement=PERCENTAGE,
        mode=NumberMode.SLIDER,
        value_fn=lambda d: d.get("motion", {}).get("digital_sensitivity"),
        set_fn=lambda api, v: api.set_motion_sensitivity(int(v)),
        supported_fn=lambda d: "digital_sensitivity" in d.get("motion", {}),
    ),

    VIGINumberDescription(
        key="alarm_sound_times",
        name="Alarm Sound Repetitions",
        native_min_value=1,
        native_max_value=50,
        native_step=1,
        mode=NumberMode.BOX,
        icon="mdi:repeat",
        value_fn=lambda d: (
            int(d.get("alarm", {}).get("sound_alarm_times", 1))
            if d.get("alarm", {}).get("sound_alarm_times") is not None else None
        ),
        set_fn=lambda api, v: api.set_sound_alarm_times(int(v)),
        supported_fn=lambda d: "sound_alarm_times" in d.get("alarm", {}),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    data = hass.data[DOMAIN][entry.entry_id]
    coordinator = data["coordinator"]
    async_add_entities(
        VIGINumber(coordinator, data, desc)
        for desc in NUMBERS
        if desc.supported_fn(coordinator.data or {})
    )


class VIGINumber(VIGIEntity, NumberEntity):
    entity_description: VIGINumberDescription

    def __init__(self, coordinator, entry_data, description):
        super().__init__(coordinator, entry_data)
        self.entity_description = description

    @property
    def _unique_id_suffix(self) -> str:
        return self.entity_description.key

    @property
    def native_value(self) -> float | None:
        return self.entity_description.value_fn(self.coordinator.data or {})

    async def async_set_native_value(self, value: float) -> None:
        await self.entity_description.set_fn(self._entry_data["api"], value)
        await self.coordinator.async_request_refresh()
