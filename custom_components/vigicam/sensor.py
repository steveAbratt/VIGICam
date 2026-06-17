"""Sensor entities for VIGI cameras — SD card storage and firmware."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory, PERCENTAGE, UnitOfInformation, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .entity import VIGIEntity, _clean_firmware


@dataclass(frozen=True, kw_only=True)
class VIGISensorDescription(SensorEntityDescription):
    value_fn: Callable[[dict], float | str | datetime | None]


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


def _seconds_to_hours(value) -> float | None:
    """Convert seconds (int or numeric string) to hours, 1 decimal place."""
    try:
        return round(int(value) / 3600, 1)
    except (TypeError, ValueError):
        return None


def _unix_to_datetime(value) -> datetime | None:
    """Convert a Unix timestamp to a UTC-aware datetime."""
    try:
        return datetime.fromtimestamp(int(value), tz=timezone.utc)
    except (TypeError, ValueError, OSError):
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


# OpenAPI SD card sensors — require has_openapi + has_sd_card.
OPENAPI_SD_SENSORS: tuple[VIGISensorDescription, ...] = (
    VIGISensorDescription(
        key="sd_record_duration",
        name="SD Card Recording Duration",
        native_unit_of_measurement=UnitOfTime.HOURS,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:clock-time-eight",
        value_fn=lambda d: _seconds_to_hours(d.get("openapi_sd", {}).get("record_duration")),
    ),
    VIGISensorDescription(
        key="sd_oldest_recording",
        name="SD Card Oldest Recording",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=lambda d: _unix_to_datetime(d.get("openapi_sd", {}).get("record_start_time")),
    ),
    VIGISensorDescription(
        key="sd_record_capacity",
        name="SD Card Record Capacity Remaining",
        native_unit_of_measurement=UnitOfTime.HOURS,
        state_class=SensorStateClass.MEASUREMENT,
        icon="mdi:clock-time-eight-outline",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: _seconds_to_hours(d.get("openapi_sd", {}).get("record_free_duration")),
    ),
    VIGISensorDescription(
        key="sd_video_free",
        name="SD Card Video Space Free",
        device_class=SensorDeviceClass.DATA_SIZE,
        native_unit_of_measurement=UnitOfInformation.GIGABYTES,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: _parse_gb(d.get("openapi_sd", {}).get("video_free_space")),
    ),
)

# OpenAPI device sensors — require has_openapi only.
OPENAPI_DEVICE_SENSORS: tuple[VIGISensorDescription, ...] = (
    VIGISensorDescription(
        key="uptime",
        name="Uptime",
        native_unit_of_measurement=UnitOfTime.HOURS,
        state_class=SensorStateClass.TOTAL_INCREASING,
        icon="mdi:timer-outline",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda d: _seconds_to_hours(d.get("openapi_device", {}).get("uptime")),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    data = hass.data[DOMAIN][entry.entry_id]
    has_sd_card = data.get("has_sd_card", True)  # default True preserves behaviour on upgrade
    has_openapi = data.get("has_openapi", False)

    entities: list[VIGISensor] = [
        VIGISensor(data["coordinator"], data, desc)
        for desc in SENSORS
        if has_sd_card or desc.key not in _SD_CARD_KEYS
    ]

    if has_openapi and has_sd_card:
        entities.extend(
            VIGISensor(data["coordinator"], data, desc) for desc in OPENAPI_SD_SENSORS
        )

    if has_openapi:
        entities.extend(
            VIGISensor(data["coordinator"], data, desc) for desc in OPENAPI_DEVICE_SENSORS
        )

    async_add_entities(entities)


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
