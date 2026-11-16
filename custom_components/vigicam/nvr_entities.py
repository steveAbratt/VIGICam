"""Entities for a VIGI NVR entry: one NVR device and one child device per channel.

Each platform module calls the matching async_setup_* here when the entry is an NVR.

Per channel: live stream (RTSP through the NVR, snapshots from the NVR's snapshot API),
online sensor, pushed detection sensors, and an Active Defense arm switch when the
camera supports it. On the NVR: disk sensors, alarm mode switch, PoE power, reboot.
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

import aiohttp

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.components.button import ButtonDeviceClass, ButtonEntity
from homeassistant.components.camera import Camera, CameraEntityFeature
from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory, UnitOfInformation, UnitOfPower
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_call_later
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    BRAND,
    CONF_NVR_EVENT_HOLD,
    CONF_STREAM_USE_MAIN,
    DEFAULT_NVR_EVENT_HOLD,
    DEFAULT_STREAM_USE_MAIN,
    DOMAIN,
    SIGNAL_NVR_EVENT,
)
from .nvr_api import VIGINVRError
from .nvr_coordinator import VIGINVRCoordinator, channel_online

_LOGGER = logging.getLogger(__name__)


def _data(hass: HomeAssistant, entry: ConfigEntry) -> dict:
    return hass.data[DOMAIN][entry.entry_id]


def _channels(coordinator: VIGINVRCoordinator) -> dict[int, dict]:
    return (coordinator.data or {}).get("devices") or {}


def _parse_gb(value: Any) -> float | None:
    """'698 GB' → 698.0. The doc formats disk sizes as "%d GB"."""
    if value is None:
        return None
    text = str(value).strip().upper()
    factor = 1.0
    for suffix, mult in (("TB", 1024.0), ("GB", 1.0), ("MB", 1 / 1024)):
        if text.endswith(suffix):
            text, factor = text[: -len(suffix)].strip(), mult
            break
    try:
        return round(float(text) * factor, 1)
    except ValueError:
        return None


async def _call(coro, what: str) -> Any:
    """Run a write; turn its failure into an error the UI shows."""
    try:
        return await coro
    except (VIGINVRError, aiohttp.ClientError, TimeoutError) as exc:
        raise HomeAssistantError(f"{what} failed: {exc}") from exc


# ── Base entities ─────────────────────────────────────────────────────────────

class VIGINVREntity(CoordinatorEntity[VIGINVRCoordinator]):
    """An entity on the NVR device itself."""

    _attr_has_entity_name = True

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, key: str) -> None:
        data = _data(hass, entry)
        super().__init__(data["coordinator"])
        self._entry = entry
        self._nvr_name = data["name"]
        self._api = data["api"]
        self._attr_unique_id = f"nvr_{entry.entry_id}_{key}"

    @property
    def _nvr_identifier(self) -> tuple[str, str]:
        return (DOMAIN, f"nvr_{self._entry.entry_id}")

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={self._nvr_identifier},
            name=self._nvr_name,
            manufacturer=BRAND,
            model="VIGI NVR",
            configuration_url=f"https://{self._api.host}",
        )


class VIGINVRChannelEntity(VIGINVREntity):
    """An entity on one channel's camera, shown as a device behind the NVR."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, channel: int, key: str) -> None:
        super().__init__(hass, entry, f"ch{channel}_{key}")
        self._channel = channel

    @property
    def _device(self) -> dict:
        return _channels(self.coordinator).get(self._channel) or {}

    @property
    def device_info(self) -> DeviceInfo:
        dev = self._device
        name = dev.get("name") or dev.get("alias") or f"Channel {self._channel}"
        info = DeviceInfo(
            identifiers={(DOMAIN, f"nvr_{self._entry.entry_id}_ch{self._channel}")},
            name=f"{name}",
            manufacturer=BRAND,
            model=f"NVR channel {self._channel}",
            via_device=self._nvr_identifier,
        )
        if dev.get("ip"):
            info["configuration_url"] = f"http://{dev['ip']}"
        return info

    @property
    def available(self) -> bool:
        return super().available and channel_online(self._device)


# ── Camera ────────────────────────────────────────────────────────────────────

async def async_setup_camera(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator = _data(hass, entry)["coordinator"]
    use_main = entry.options.get(CONF_STREAM_USE_MAIN, DEFAULT_STREAM_USE_MAIN)
    async_add_entities(
        VIGINVRCamera(hass, entry, ch, use_main) for ch in sorted(_channels(coordinator))
    )


class VIGINVRCamera(VIGINVRChannelEntity, Camera):
    _attr_name = None  # the channel device's own name
    _attr_supported_features = CameraEntityFeature.STREAM

    def __init__(self, hass, entry, channel: int, use_main: bool) -> None:
        VIGINVRChannelEntity.__init__(self, hass, entry, channel, "stream")
        Camera.__init__(self)
        self._stream = 1 if use_main else 2

    async def stream_source(self) -> str | None:
        return self._api.rtsp_url(self._channel, self._stream)

    async def async_camera_image(
        self, width: int | None = None, height: int | None = None
    ) -> bytes | None:
        try:
            return await self._api.snapshot(self._channel)
        except (VIGINVRError, aiohttp.ClientError, TimeoutError) as exc:
            _LOGGER.debug("NVR %s ch%s: snapshot failed: %s", self._api.host, self._channel, exc)
            return None


# ── Binary sensors ────────────────────────────────────────────────────────────

# key, name, device class, enabled by default
EVENT_SENSORS: tuple[tuple[str, str, BinarySensorDeviceClass, bool], ...] = (
    ("motion",      "Motion",           BinarySensorDeviceClass.MOTION,    True),
    ("person",      "Person Detected",  BinarySensorDeviceClass.OCCUPANCY, True),
    ("vehicle",     "Vehicle Detected", BinarySensorDeviceClass.MOTION,    True),
    ("line_cross",  "Line Crossing",    BinarySensorDeviceClass.MOTION,    True),
    ("intrusion",   "Intrusion",        BinarySensorDeviceClass.MOTION,    True),
    ("tamper",      "Tamper",           BinarySensorDeviceClass.TAMPER,    False),
    ("area_entry",  "Area Entry",       BinarySensorDeviceClass.MOTION,    False),
    ("area_exit",   "Area Exit",        BinarySensorDeviceClass.MOTION,    False),
    ("loitering",   "Loitering",        BinarySensorDeviceClass.MOTION,    False),
    ("crowd",       "Crowd Gathering",  BinarySensorDeviceClass.OCCUPANCY, False),
    ("alarm_input", "Alarm Input",      BinarySensorDeviceClass.SAFETY,    False),
)


async def async_setup_binary_sensor(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    data = _data(hass, entry)
    hold = int(entry.options.get(CONF_NVR_EVENT_HOLD, DEFAULT_NVR_EVENT_HOLD))
    entities: list[BinarySensorEntity] = []
    for ch in sorted(_channels(data["coordinator"])):
        entities.append(VIGINVROnlineSensor(hass, entry, ch))
        if data["events"]:
            entities.extend(
                VIGINVREventSensor(hass, entry, ch, key, name, dc, enabled, hold)
                for key, name, dc, enabled in EVENT_SENSORS
            )
    async_add_entities(entities)


class VIGINVROnlineSensor(VIGINVRChannelEntity, BinarySensorEntity):
    _attr_name = "Online"
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, hass, entry, channel: int) -> None:
        super().__init__(hass, entry, channel, "online")

    @property
    def available(self) -> bool:
        # Stays available while the channel is offline: that is what it reports.
        return self.coordinator.last_update_success

    @property
    def is_on(self) -> bool:
        return channel_online(self._device)


class VIGINVREventSensor(VIGINVRChannelEntity, BinarySensorEntity):
    """On when the NVR pushes a matching event; off after the hold time.

    The NVR sends one message per detection and no "ended" message, so the sensor
    turns itself off `hold` seconds after the last one.
    """

    def __init__(
        self, hass, entry, channel: int, key: str, name: str,
        device_class: BinarySensorDeviceClass, enabled: bool, hold: int,
    ) -> None:
        super().__init__(hass, entry, channel, f"event_{key}")
        self._key = key
        self._hold = max(1, hold)
        self._attr_name = name
        self._attr_device_class = device_class
        self._attr_entity_registry_enabled_default = enabled
        self._attr_is_on = False
        self._clear: CALLBACK_TYPE | None = None

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass, SIGNAL_NVR_EVENT.format(self._entry.entry_id), self._on_event
            )
        )
        self.async_on_remove(self._cancel_clear)

    @callback
    def _cancel_clear(self) -> None:
        if self._clear:
            self._clear()
            self._clear = None

    @callback
    def _on_event(self, channel: int, keys: list[str]) -> None:
        if channel != self._channel or self._key not in keys:
            return
        self._cancel_clear()
        self._attr_is_on = True
        self.async_write_ha_state()
        self._clear = async_call_later(self.hass, self._hold, self._off)

    @callback
    def _off(self, _now) -> None:
        self._clear = None
        self._attr_is_on = False
        self.async_write_ha_state()


# ── Sensors ───────────────────────────────────────────────────────────────────

async def async_setup_sensor(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: VIGINVRCoordinator = _data(hass, entry)["coordinator"]
    data = coordinator.data or {}
    entities: list[SensorEntity] = [VIGINVRChannelCount(hass, entry)]
    for disk in data.get("disks") or []:
        ident = str(disk.get("id", disk.get("slot", "")))
        entities += [
            VIGINVRDiskSensor(hass, entry, ident, "free_space", "Free space"),
            VIGINVRDiskSensor(hass, entry, ident, "total_space", "Capacity"),
            VIGINVRDiskSensor(hass, entry, ident, "status", "Status"),
        ]
    poe = data.get("poe")
    if isinstance(poe, dict) and "cost_power" in poe:
        entities.append(VIGINVRPoEPower(hass, entry))
    async_add_entities(entities)


class VIGINVRChannelCount(VIGINVREntity, SensorEntity):
    _attr_name = "Cameras online"
    _attr_icon = "mdi:cctv"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, hass, entry) -> None:
        super().__init__(hass, entry, "channels_online")

    @property
    def native_value(self) -> int:
        return sum(1 for d in _channels(self.coordinator).values() if channel_online(d))

    @property
    def extra_state_attributes(self) -> dict:
        return {"channels": len(_channels(self.coordinator))}


class VIGINVRDiskSensor(VIGINVREntity, SensorEntity):
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, hass, entry, disk_id: str, field: str, label: str) -> None:
        super().__init__(hass, entry, f"disk{disk_id}_{field}")
        self._disk_id = disk_id
        self._field = field
        self._attr_name = f"Disk {disk_id} {label.lower()}" if disk_id else f"Disk {label.lower()}"
        if field != "status":
            self._attr_device_class = SensorDeviceClass.DATA_SIZE
            self._attr_native_unit_of_measurement = UnitOfInformation.GIGABYTES
            self._attr_suggested_display_precision = 0
            if field == "free_space":
                self._attr_state_class = SensorStateClass.MEASUREMENT
        else:
            self._attr_icon = "mdi:harddisk"

    @property
    def _disk(self) -> dict | None:
        for disk in (self.coordinator.data or {}).get("disks") or []:
            if str(disk.get("id", disk.get("slot", ""))) == self._disk_id:
                return disk
        return None

    @property
    def available(self) -> bool:
        return super().available and self._disk is not None

    @property
    def native_value(self):
        disk = self._disk or {}
        if self._field == "status":
            return disk.get("status")
        return _parse_gb(disk.get(self._field))

    @property
    def extra_state_attributes(self) -> dict | None:
        if self._field != "status" or not self._disk:
            return None
        return {k: self._disk.get(k) for k in ("slot", "rw_attr") if k in self._disk}


class VIGINVRPoEPower(VIGINVREntity, SensorEntity):
    _attr_name = "PoE power used"
    _attr_device_class = SensorDeviceClass.POWER
    _attr_native_unit_of_measurement = UnitOfPower.WATT
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, hass, entry) -> None:
        super().__init__(hass, entry, "poe_power")

    @property
    def native_value(self) -> float | None:
        poe = (self.coordinator.data or {}).get("poe") or {}
        try:
            # The doc gives PoE power budgets in 0.1 W units; cost_power is assumed to match.
            return round(float(poe["cost_power"]) / 10, 1)
        except (KeyError, TypeError, ValueError):
            return None


# ── Switches ──────────────────────────────────────────────────────────────────

async def async_setup_switch(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: VIGINVRCoordinator = _data(hass, entry)["coordinator"]
    data = coordinator.data or {}
    entities: list[SwitchEntity] = []
    if data.get("alarm_mode") in ("alarm", "disarming"):
        entities.append(VIGINVRAlarmModeSwitch(hass, entry))
    for ch, status in (data.get("arm") or {}).items():
        if status in ("armed", "disarmed"):
            entities.append(VIGINVRArmSwitch(hass, entry, ch))
    async_add_entities(entities)


class VIGINVRAlarmModeSwitch(VIGINVREntity, SwitchEntity):
    """NVR alarm mode: on = alarm (armed), off = disarming."""

    _attr_name = "Alarm mode"
    _attr_icon = "mdi:shield-home"

    def __init__(self, hass, entry) -> None:
        super().__init__(hass, entry, "alarm_mode")

    @property
    def is_on(self) -> bool | None:
        mode = (self.coordinator.data or {}).get("alarm_mode")
        return None if mode is None else mode == "alarm"

    async def async_turn_on(self, **kwargs) -> None:
        await _call(self._api.set_alarm_mode("alarm"), "Arming the NVR")
        await self.coordinator.async_request_refresh()

    async def async_turn_off(self, **kwargs) -> None:
        await _call(self._api.set_alarm_mode("disarming"), "Disarming the NVR")
        await self.coordinator.async_request_refresh()


class VIGINVRArmSwitch(VIGINVRChannelEntity, SwitchEntity):
    """Active Defense (siren and light deterrence) arm state for one channel."""

    _attr_name = "Active defense"
    _attr_icon = "mdi:alarm-light"

    def __init__(self, hass, entry, channel: int) -> None:
        super().__init__(hass, entry, channel, "arm")

    @property
    def is_on(self) -> bool | None:
        status = ((self.coordinator.data or {}).get("arm") or {}).get(self._channel)
        return None if status is None else status == "armed"

    async def async_turn_on(self, **kwargs) -> None:
        await _call(self._api.set_arm_status(self._channel, True), "Arming active defense")
        await self.coordinator.async_request_refresh()

    async def async_turn_off(self, **kwargs) -> None:
        await _call(self._api.set_arm_status(self._channel, False), "Disarming active defense")
        await self.coordinator.async_request_refresh()


# ── Buttons ───────────────────────────────────────────────────────────────────

async def async_setup_button(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: VIGINVRCoordinator = _data(hass, entry)["coordinator"]
    entities: list[ButtonEntity] = []
    if coordinator.has_module("system"):
        entities.append(VIGINVRRebootButton(hass, entry))
    if coordinator.has_module("alarm_output"):
        entities.append(VIGINVRAlarmButton(hass, entry))
        entities.extend(
            VIGINVRChannelAlarmButton(hass, entry, ch) for ch in sorted(_channels(coordinator))
        )
    async_add_entities(entities)


class VIGINVRRebootButton(VIGINVREntity, ButtonEntity):
    _attr_name = "Reboot"
    _attr_device_class = ButtonDeviceClass.RESTART
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, hass, entry) -> None:
        super().__init__(hass, entry, "reboot")

    async def async_press(self) -> None:
        await _call(self._api.reboot(), "Rebooting the NVR")


class VIGINVRAlarmButton(VIGINVREntity, ButtonEntity):
    """Fire the NVR's own manual alarm output.

    Disabled by default: only an NVR wired to a siren or relay has one.
    """

    _attr_name = "Trigger alarm output"
    _attr_icon = "mdi:bullhorn"
    _attr_entity_registry_enabled_default = False

    def __init__(self, hass, entry) -> None:
        super().__init__(hass, entry, "manual_alarm")

    async def async_press(self) -> None:
        await _call(self._api.nvr_manual_alarm(True), "NVR alarm output")


class VIGINVRChannelAlarmButton(VIGINVRChannelEntity, ButtonEntity):
    """Fire one camera's manual alarm output (siren, or a relay wired to it)."""

    _attr_name = "Trigger alarm output"
    _attr_icon = "mdi:bullhorn"
    _attr_entity_registry_enabled_default = False

    def __init__(self, hass, entry, channel: int) -> None:
        super().__init__(hass, entry, channel, "manual_alarm")

    async def async_press(self) -> None:
        await _call(self._api.ipc_manual_alarm(self._channel, True), "Camera alarm output")


PLATFORM_SETUP: dict[str, Callable] = {
    "camera": async_setup_camera,
    "binary_sensor": async_setup_binary_sensor,
    "sensor": async_setup_sensor,
    "switch": async_setup_switch,
    "button": async_setup_button,
}
