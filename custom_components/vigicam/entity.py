"""Base entity for VIGI cameras."""
from __future__ import annotations

import re
import urllib.parse

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import BRAND, DOMAIN
from .coordinator import VIGICoordinator


def _decode(value: str | None) -> str | None:
    """URL-decode a camera string field (camera returns e.g. 'InSight%20S245')."""
    if not value:
        return None
    return urllib.parse.unquote(value)


def _clean_firmware(version: str | None) -> str | None:
    """'3.1.1 Build 251124 Rel.50306n' → '3.1.1'"""
    v = _decode(version)
    if not v:
        return None
    return v.split(" Build")[0].split(" build")[0].strip()


def _clean_model(model: str | None) -> str | None:
    """'VIGI C540V 1.0' → 'VIGI C540V'  (strips trailing hardware revision)"""
    m = _decode(model)
    if not m:
        return None
    return re.sub(r"\s+\d+\.\d+$", "", m).strip()


class VIGIEntity(CoordinatorEntity[VIGICoordinator]):
    """Base class for all VIGI camera entities."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: VIGICoordinator, entry_data: dict) -> None:
        super().__init__(coordinator)
        self._entry_data = entry_data
        info = entry_data["device_info"]
        mac = info.get("mac", entry_data["ip"])
        self._device_id = mac.replace(":", "").lower()

    @property
    def device_info(self) -> DeviceInfo:
        info = self._entry_data["device_info"]
        name = (
            _decode(info.get("dev_name"))
            or _decode(info.get("alias"))
            or self._entry_data["ip"]
        )
        return DeviceInfo(
            identifiers={(DOMAIN, self._device_id)},
            name=name,
            manufacturer=BRAND,
            model=_clean_model(info.get("device_model")),
            sw_version=_clean_firmware(info.get("sw_version")),
            hw_version=_decode(info.get("hw_version")),
            configuration_url=f"http://{self._entry_data['ip']}",
        )

    @property
    def unique_id(self) -> str:
        return f"{self._device_id}_{self._unique_id_suffix}"

    @property
    def _unique_id_suffix(self) -> str:
        raise NotImplementedError
