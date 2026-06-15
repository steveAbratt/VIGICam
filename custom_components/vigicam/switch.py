"""Switch entities for VIGI cameras — detection toggles, LED, alarm, audio mute."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from homeassistant.components.switch import SwitchEntity, SwitchEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .api import VIGICamera
from .const import DOMAIN
from .entity import VIGIEntity


@dataclass(frozen=True, kw_only=True)
class VIGISwitchDescription(SwitchEntityDescription):
    is_on_fn: Callable[[dict], bool | None]
    turn_on_fn: Callable[[VIGICamera], Any]
    turn_off_fn: Callable[[VIGICamera], Any]
    # Return False if the camera doesn't support this feature (field absent from data)
    supported_fn: Callable[[dict], bool] = lambda _: True


SWITCHES: tuple[VIGISwitchDescription, ...] = (
    VIGISwitchDescription(
        key="motion_detection",
        name="Motion Detection",
        is_on_fn=lambda d: d.get("motion", {}).get("enabled") == "on",
        turn_on_fn=lambda api: api.set_motion_detection(True),
        turn_off_fn=lambda api: api.set_motion_detection(False),
        supported_fn=lambda d: "enabled" in d.get("motion", {}),
    ),
    VIGISwitchDescription(
        key="person_detection",
        name="Person Detection",
        is_on_fn=lambda d: d.get("motion", {}).get("people_enabled") == "on",
        turn_on_fn=lambda api: api.set_person_detection(True),
        turn_off_fn=lambda api: api.set_person_detection(False),
        supported_fn=lambda d: "people_enabled" in d.get("motion", {}),
    ),
    VIGISwitchDescription(
        key="vehicle_detection",
        name="Vehicle Detection",
        is_on_fn=lambda d: d.get("motion", {}).get("vehicle_enabled") == "on",
        turn_on_fn=lambda api: api.set_vehicle_detection(True),
        turn_off_fn=lambda api: api.set_vehicle_detection(False),
        supported_fn=lambda d: "vehicle_enabled" in d.get("motion", {}),
    ),
    VIGISwitchDescription(
        key="tamper_detection",
        name="Tamper Detection",
        is_on_fn=lambda d: d.get("tamper", {}).get("enabled") == "on",
        turn_on_fn=lambda api: api.set_tamper(True),
        turn_off_fn=lambda api: api.set_tamper(False),
        supported_fn=lambda d: bool(d.get("tamper")),
    ),
    VIGISwitchDescription(
        key="led",
        name="Status LED",
        is_on_fn=lambda d: d.get("led", {}).get("enabled") == "on",
        turn_on_fn=lambda api: api.set_led(True),
        turn_off_fn=lambda api: api.set_led(False),
        supported_fn=lambda d: bool(d.get("led")),
    ),
    VIGISwitchDescription(
        key="alarm",
        name="Alarm",
        is_on_fn=lambda d: d.get("alarm", {}).get("enabled") == "on",
        turn_on_fn=lambda api: api.set_alarm(True),
        turn_off_fn=lambda api: api.set_alarm(False),
        supported_fn=lambda d: bool(d.get("alarm")),
    ),
    VIGISwitchDescription(
        key="speaker_mute",
        name="Speaker Mute",
        is_on_fn=lambda d: d.get("speaker", {}).get("mute") == "on",
        turn_on_fn=lambda api: api.set_speaker_mute(True),
        turn_off_fn=lambda api: api.set_speaker_mute(False),
        supported_fn=lambda d: bool(d.get("speaker")),
    ),
    VIGISwitchDescription(
        key="mic_mute",
        name="Microphone Mute",
        is_on_fn=lambda d: d.get("microphone", {}).get("mute") == "on",
        turn_on_fn=lambda api: api.set_mic_mute(True),
        turn_off_fn=lambda api: api.set_mic_mute(False),
        supported_fn=lambda d: bool(d.get("microphone")),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    data = hass.data[DOMAIN][entry.entry_id]
    coordinator = data["coordinator"]

    # Only create switches for features the camera actually reports
    entities = [
        VIGISwitch(coordinator, data, desc)
        for desc in SWITCHES
        if desc.supported_fn(coordinator.data or {})
    ]
    async_add_entities(entities)


class VIGISwitch(VIGIEntity, SwitchEntity):
    entity_description: VIGISwitchDescription

    def __init__(self, coordinator, entry_data, description):
        super().__init__(coordinator, entry_data)
        self.entity_description = description

    @property
    def _unique_id_suffix(self) -> str:
        return self.entity_description.key

    @property
    def is_on(self) -> bool | None:
        return self.entity_description.is_on_fn(self.coordinator.data or {})

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.entity_description.turn_on_fn(self._entry_data["api"])
        await self.coordinator.async_request_refresh()

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.entity_description.turn_off_fn(self._entry_data["api"])
        await self.coordinator.async_request_refresh()
