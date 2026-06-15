"""Base entity for VIGI cameras."""
from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import BRAND, DOMAIN
from .coordinator import VIGICoordinator


def _clean_firmware(version: str | None) -> str | None:
    """'2.2.0 Build 250904 Rel.60109n' → '2.2.0'"""
    if not version:
        return None
    return version.split(" Build")[0].split(" build")[0].strip()


def _clean_model(model: str | None) -> str | None:
    """'VIGI C540V 1.0' → 'VIGI C540V'  (trailing ' X.Y' hardware rev)"""
    if not model:
        return None
    import re
    return re.sub(r"\s+\d+\.\d+$", "", model).strip()


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
        return DeviceInfo(
            identifiers={(DOMAIN, self._device_id)},
            name=info.get("dev_name") or info.get("alias") or self._entry_data["ip"],
            manufacturer=BRAND,
            model=_clean_model(info.get("device_model")),
            sw_version=_clean_firmware(info.get("sw_version")),
            hw_version=info.get("hw_version"),
            configuration_url=f"http://{self._entry_data['ip']}",
        )

    @property
    def unique_id(self) -> str:
        return f"{self._device_id}_{self._unique_id_suffix}"

    @property
    def _unique_id_suffix(self) -> str:
        raise NotImplementedError
