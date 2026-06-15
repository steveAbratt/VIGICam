"""Binary sensor entities for VIGI cameras."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
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
    available_fn: Callable[[dict], bool] = lambda _: True


BINARY_SENSORS: tuple[VIGIBinarySensorDescription, ...] = (
    VIGIBinarySensorDescription(
        key="motion_enabled",
        name="Motion Detection",
        device_class=BinarySensorDeviceClass.MOTION,
        is_on_fn=lambda d: d.get("motion", {}).get("enabled") == "on",
    ),
    VIGIBinarySensorDescription(
        key="person_detection",
        name="Person Detection Enabled",
        device_class=BinarySensorDeviceClass.OCCUPANCY,
        is_on_fn=lambda d: d.get("motion", {}).get("people_enabled") == "on",
    ),
    VIGIBinarySensorDescription(
        key="vehicle_detection",
        name="Vehicle Detection Enabled",
        is_on_fn=lambda d: d.get("motion", {}).get("vehicle_enabled") == "on",
    ),
    VIGIBinarySensorDescription(
        key="tamper",
        name="Tamper Detection",
        device_class=BinarySensorDeviceClass.TAMPER,
        is_on_fn=lambda d: d.get("tamper", {}).get("enabled") == "on",
        # Only available if the camera returned tamper data
        available_fn=lambda d: bool(d.get("tamper")),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    data = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        VIGIBinarySensor(data["coordinator"], data, desc) for desc in BINARY_SENSORS
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

    @property
    def available(self) -> bool:
        return super().available and self.entity_description.available_fn(
            self.coordinator.data or {}
        )
