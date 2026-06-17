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
from homeassistant.const import EntityCategory, PERCENTAGE, UnitOfInformation
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .entity import VIGIEntity, _clean_firmware


@dataclass(frozen=True, kw_only=True)
class VIGISensorDescription(SensorEntityDescription):
    value_fn: Callable[[dict], float | str | None]


def _parse_bytes(value: str | None) -> int | None:
    """Convert accurate byte strings like '125403037696B' to int bytes."""
    if not value:
        return None
    try:
        v = str(value).strip().upper()
        return int(v[:-1]) if v.endswith("B") else int(v)
    except ValueError:
        return None


def _used_percent(storage: dict) -> float | None:
    """Calculate used % from accurate byte values — the camera's percent field is unreliable."""
    total = _parse_bytes(storage.get("total_space_accurate"))
    free = _parse_bytes(storage.get("free_space_accurate"))
    if total is None or free is None or total == 0:
        return None
    return round((total - free) / total * 100, 1)


def _parse_gb(value: str | None) -> float | None:
    """Convert camera storage strings (116.8GB, 0B, 59.2G, etc.) to float GB."""
    if not value:
        return None
    try:
        v = str(value).strip().upper()
        if v.endswith("GB"):
            return float(v[:-2])
        if v.endswith("MB"):
            return round(float(v[:-2]) / 1024, 3)
        if v.endswith("KB"):
            return round(float(v[:-2]) / (1024 ** 2), 6)
        if v.endswith("B"):
            return round(float(v[:-1]) / (1024 ** 3), 3)
        if v.endswith("G"):
            return float(v[:-1])
        return float(v)
    except ValueError:
        return None


_SD_CARD_KEYS = {"sd_used_percent", "sd_total", "sd_free", "sd_status"}

SENSORS: tuple[VIGISensorDescription, ...] = (
    VIGISensorDescription(
        key="sd_used_percent",
        name="SD Card Used",
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda d: _used_percent(d.get("storage", {})),
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
        value_fn=lambda d: _clean_firmware(d.get("device_info", {}).get("sw_version")),
        entity_registry_enabled_default=False,
    ),
    VIGISensorDescription(
        key="ip_address",
        name="IP Address",
        icon="mdi:ip-network",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: d.get("network", {}).get("ipaddr"),
    ),
    VIGISensorDescription(
        key="connection_type",
        name="Connection Type",
        icon="mdi:ethernet",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: {"dhcp": "DHCP", "static": "Static"}.get(
            d.get("network", {}).get("wan_type", ""),
            d.get("network", {}).get("wan_type"),
        ),
    ),
    VIGISensorDescription(
        key="mac_address",
        name="MAC Address",
        icon="mdi:identifier",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda d: d.get("device_info", {}).get("mac"),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    data = hass.data[DOMAIN][entry.entry_id]
    has_sd_card = data.get("has_sd_card", True)  # default True preserves behaviour on upgrade
    async_add_entities(
        VIGISensor(data["coordinator"], data, desc)
        for desc in SENSORS
        if has_sd_card or desc.key not in _SD_CARD_KEYS
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
