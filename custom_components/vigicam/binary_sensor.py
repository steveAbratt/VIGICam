"""Binary sensor entities for VIGI cameras.

Real-time detection events (motion, person, vehicle, tamper) require ONVIF event
subscriptions. Polling the local API only returns configuration state (enabled/disabled),
not live detections — so those are exposed as switches instead.

Read-only status fields (e.g. loop recording) that can't be set via the API live here.

ONVIF event support is planned for a future release.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from homeassistant.components.binary_sensor import (
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .entity import VIGIEntity


@dataclass(frozen=True, kw_only=True)
class VIGIBinarySensorDescription(BinarySensorEntityDescription):
    is_on_fn: Callable[[dict], bool | None]
    supported_fn: Callable[[dict], bool] = lambda _: True


BINARY_SENSORS: tuple[VIGIBinarySensorDescription, ...] = (
    VIGIBinarySensorDescription(
        key="loop_recording",
        name="Loop Recording",
        icon="mdi:rotate-3d-variant",
        is_on_fn=lambda d: d.get("storage", {}).get("loop_record_status") == "1",
        supported_fn=lambda d: "loop_record_status" in d.get("storage", {}),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    data = hass.data[DOMAIN][entry.entry_id]
    coordinator = data["coordinator"]
    async_add_entities(
        VIGIBinarySensor(coordinator, data, desc)
        for desc in BINARY_SENSORS
        if desc.supported_fn(coordinator.data or {})
    )


class VIGIBinarySensor(VIGIEntity, BinarySensorEntity):
    entity_description: VIGIBinarySensorDescription

    def __init__(self, coordinator, entry_data, description):
        super().__init__(coordinator, entry_data)
        self.entity_description = description

    @property
    def _unique_id_suffix(self) -> str:
        return self.entity_description.key

    @property
    def is_on(self) -> bool | None:
        return self.entity_description.is_on_fn(self.coordinator.data or {})
