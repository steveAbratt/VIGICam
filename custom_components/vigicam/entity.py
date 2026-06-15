"""Base entity for VIGI cameras."""
from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import BRAND, DOMAIN
from .coordinator import VIGICoordinator


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
            model=info.get("device_model"),
            sw_version=info.get("sw_version"),
            hw_version=info.get("hw_version"),
        )

    @property
    def unique_id(self) -> str:
        return f"{self._device_id}_{self._unique_id_suffix}"

    @property
    def _unique_id_suffix(self) -> str:
        raise NotImplementedError
