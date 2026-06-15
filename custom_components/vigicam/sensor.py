"""Sensor entities for VIGI cameras — SD card storage and firmware."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE, UnitOfInformation
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .entity import VIGIEntity


@dataclass(frozen=True, kw_only=True)
class VIGISensorDescription(SensorEntityDescription):
    value_fn: Callable[[dict], float | str | None]


def _parse_gb(value: str | None) -> float | None:
    """Convert camera storage strings like '116.8G' or '59.2G' to float GB."""
    if not value:
        return None
    try:
        return float(str(value).rstrip("GgMmKk"))
    except ValueError:
        return None


SENSORS: tuple[VIGISensorDescription, ...] = (
    VIGISensorDescription(
        key="sd_used_percent",
        name="SD Card Used",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda d: d.get("storage", {}).get("percent"),
    ),
    VIGISensorDescription(
        key="sd_total",
        name="SD Card Total",
        device_class=SensorDeviceClass.DATA_SIZE,
        native_unit_of_measurement=UnitOfInformation.GIGABYTES,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda d: _parse_gb(d.get("storage", {}).get("total_space")),
    ),
    VIGISensorDescription(
        key="sd_free",
        name="SD Card Free",
        device_class=SensorDeviceClass.DATA_SIZE,
        native_unit_of_measurement=UnitOfInformation.GIGABYTES,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda d: _parse_gb(d.get("storage", {}).get("free_space")),
    ),
    VIGISensorDescription(
        key="sd_status",
        name="SD Card Status",
        value_fn=lambda d: d.get("storage", {}).get("status"),
    ),
    VIGISensorDescription(
        key="firmware",
        name="Firmware Version",
        value_fn=lambda d: d.get("device_info", {}).get("sw_version"),
        entity_registry_enabled_default=False,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    data = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        VIGISensor(data["coordinator"], data, desc) for desc in SENSORS
    )


class VIGISensor(VIGIEntity, SensorEntity):
    entity_description: VIGISensorDescription

    def __init__(self, coordinator, entry_data, description):
        super().__init__(coordinator, entry_data)
        self.entity_description = description

    @property
    def _unique_id_suffix(self) -> str:
        return self.entity_description.key

    @property
    def native_value(self) -> float | str | None:
        return self.entity_description.value_fn(self.coordinator.data or {})
