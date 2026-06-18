"""Binary sensor entities for VIGI cameras.

Two kinds of binary sensor:
  - Coordinator-based: read from polling data (e.g. loop recording status)
  - Event-based: driven by ONVIF pull-point events (motion, person, vehicle, tamper)

Event sensors auto-clear after AUTO_CLEAR_S seconds if the camera does not
send an explicit "active=false" event. If ONVIF subscription fails at startup
the sensors are still created but will remain Off until events arrive.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Callable

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_call_later

from .const import CONF_FEATURE_DETECTION_EVENTS, DEFAULT_FEATURE_DETECTION_EVENTS, DOMAIN
from .entity import VIGIEntity
from .onvif_events import AUTO_CLEAR_S, SIGNAL_VIGICAM_EVENT

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, kw_only=True)
class VIGIBinarySensorDescription(BinarySensorEntityDescription):
    # For coordinator-based sensors: reads from coordinator.data
    is_on_fn: Callable[[dict], bool | None] = lambda _: None
    supported_fn: Callable[[dict], bool] = lambda _: True
    # For event-based sensors: set to the event_type string from TOPIC_KEYWORD_MAP
    event_type: str | None = None


BINARY_SENSORS: tuple[VIGIBinarySensorDescription, ...] = (
    # ── Coordinator-based ────────────────────────────────────────────────────
    VIGIBinarySensorDescription(
        key="loop_recording",
        name="Loop Recording",
        icon="mdi:rotate-3d-variant",
        is_on_fn=lambda d: d.get("storage", {}).get("loop_record_status") == "1",
        supported_fn=lambda d: "loop_record_status" in d.get("storage", {}),
    ),
    # ── Event-based (ONVIF) ──────────────────────────────────────────────────
    # Topics confirmed from GetEventProperties on both cameras.
    # TPSmartEvent is a TP-Link catch-all covering vehicle, sound, loitering,
    # abandoned object, scene change — indistinguishable at the ONVIF level.
    VIGIBinarySensorDescription(
        key="motion",
        name="Motion",
        device_class=BinarySensorDeviceClass.MOTION,
        event_type="motion",
    ),
    VIGIBinarySensorDescription(
        key="person",
        name="Person Detected",
        device_class=BinarySensorDeviceClass.OCCUPANCY,
        event_type="person",
    ),
    VIGIBinarySensorDescription(
        key="tamper",
        name="Tamper",
        device_class=BinarySensorDeviceClass.TAMPER,
        event_type="tamper",
    ),
    VIGIBinarySensorDescription(
        key="intrusion",
        name="Intrusion",
        device_class=BinarySensorDeviceClass.MOTION,
        icon="mdi:motion-sensor",
        event_type="intrusion",
    ),
    VIGIBinarySensorDescription(
        key="line_cross",
        name="Line Crossing",
        device_class=BinarySensorDeviceClass.MOTION,
        icon="mdi:vector-line",
        event_type="line_cross",
    ),
    VIGIBinarySensorDescription(
        key="smart_event",
        name="Smart Detection",
        device_class=BinarySensorDeviceClass.MOTION,
        icon="mdi:shield-search",
        event_type="smart_event",
    ),
)

# OpenAPI-only sensors — registered only when has_openapi=True.
# These replace the "Smart Detection" catch-all with specific event types.
OPENAPI_BINARY_SENSORS: tuple[VIGIBinarySensorDescription, ...] = (
    VIGIBinarySensorDescription(
        key="vehicle",
        name="Vehicle Detected",
        device_class=BinarySensorDeviceClass.MOTION,
        icon="mdi:car-side",
        event_type="vehicle",
    ),
    VIGIBinarySensorDescription(
        key="audio_anomaly",
        name="Audio Anomaly",
        device_class=BinarySensorDeviceClass.SOUND,
        icon="mdi:ear-hearing",
        event_type="audio_anomaly",
    ),
    VIGIBinarySensorDescription(
        key="loitering",
        name="Loitering",
        device_class=BinarySensorDeviceClass.MOTION,
        icon="mdi:account-clock",
        event_type="loitering",
    ),
    VIGIBinarySensorDescription(
        key="scene_change",
        name="Scene Change",
        device_class=BinarySensorDeviceClass.PROBLEM,
        icon="mdi:image-broken-variant",
        event_type="scene_change",
    ),
    VIGIBinarySensorDescription(
        key="object_left_taken",
        name="Object Left or Taken",
        device_class=BinarySensorDeviceClass.MOTION,
        icon="mdi:bag-personal",
        event_type="object_left_taken",
    ),
    VIGIBinarySensorDescription(
        key="area_entry",
        name="Area Entry",
        device_class=BinarySensorDeviceClass.MOTION,
        icon="mdi:location-enter",
        event_type="area_entry",
    ),
    VIGIBinarySensorDescription(
        key="area_exit",
        name="Area Exit",
        device_class=BinarySensorDeviceClass.MOTION,
        icon="mdi:location-exit",
        event_type="area_exit",
    ),
)


@dataclass(frozen=True, kw_only=True)
class VIGICapabilityDescription:
    key: str
    name: str
    icon: str


CAPABILITY_SENSORS: tuple[VIGICapabilityDescription, ...] = (
    VIGICapabilityDescription(key="cap_ptz",          name="PTZ",                  icon="mdi:pan"),
    VIGICapabilityDescription(key="cap_openapi",       name="OpenAPI",              icon="mdi:api"),
    VIGICapabilityDescription(key="cap_event_capture",  name="Event Image Capture",  icon="mdi:image-multiple"),
    VIGICapabilityDescription(key="cap_sd_card",       name="SD Card",              icon="mdi:sd"),
    VIGICapabilityDescription(key="cap_onvif",         name="ONVIF Events",         icon="mdi:broadcast"),
)

# Maps VIGICapabilityDescription.key → entry_data key for static capabilities.
# SD card and ONVIF are excluded — they are read live from coordinator/onvif_events.
_STATIC_CAPABILITY_KEYS: dict[str, str] = {
    "cap_ptz":         "has_ptz",
    "cap_openapi":     "has_openapi",
    "cap_event_capture": "has_event_capture",
}


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    data = hass.data[DOMAIN][entry.entry_id]
    coordinator = data["coordinator"]
    entities: list[BinarySensorEntity] = []

    # Capability sensors — always present, not gated on feature flags.
    for desc in CAPABILITY_SENSORS:
        entities.append(VIGICapabilityBinarySensor(coordinator, data, desc))

    # Detection event and coordinator-polled sensors.
    if entry.options.get(CONF_FEATURE_DETECTION_EVENTS, DEFAULT_FEATURE_DETECTION_EVENTS):
        for desc in BINARY_SENSORS:
            if desc.event_type is not None:
                entities.append(VIGIEventBinarySensor(coordinator, data, desc))
            elif desc.supported_fn(coordinator.data or {}):
                entities.append(VIGIBinarySensor(coordinator, data, desc))
        if data.get("has_openapi"):
            for desc in OPENAPI_BINARY_SENSORS:
                entities.append(VIGIEventBinarySensor(coordinator, data, desc))

    async_add_entities(entities)


class VIGICapabilityBinarySensor(VIGIEntity, BinarySensorEntity):
    """Diagnostic binary sensor — is a camera capability detected?

    State is set once at startup from entry_data (static capabilities) or read
    live from coordinator.has_sd_card (SD card, which can be inserted/removed).
    On = detected / available.  Off = not detected / unavailable.
    """

    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator, entry_data: dict, description: VIGICapabilityDescription) -> None:
        super().__init__(coordinator, entry_data)
        self._desc = description
        self._attr_name = description.name
        self._attr_icon = description.icon
        entry_data_key = _STATIC_CAPABILITY_KEYS.get(description.key)
        self._static_value: bool = bool(entry_data.get(entry_data_key, False)) if entry_data_key else False

    @property
    def _unique_id_suffix(self) -> str:
        return self._desc.key

    @property
    def is_on(self) -> bool:
        if self._desc.key == "cap_sd_card":
            return self.coordinator.has_sd_card
        if self._desc.key == "cap_onvif":
            onvif = self._entry_data.get("onvif_events")
            return onvif.is_connected if onvif is not None else False
        return self._static_value


class VIGIBinarySensor(VIGIEntity, BinarySensorEntity):
    """Coordinator-polled binary sensor (e.g. loop recording)."""

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


class VIGIEventBinarySensor(VIGIEntity, BinarySensorEntity):
    """ONVIF-event-driven binary sensor (motion, person, vehicle, tamper).

    State is set True on event arrival and auto-cleared after AUTO_CLEAR_S
    seconds unless the camera sends an explicit active=false event first.
    """

    entity_description: VIGIBinarySensorDescription

    def __init__(self, coordinator, entry_data, description):
        super().__init__(coordinator, entry_data)
        self.entity_description = description
        self._is_on = False
        self._clear_cancel: Callable | None = None
        self._unsub_dispatcher: Callable | None = None
        self._attr_extra_state_attributes: dict = {}

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self._unsub_dispatcher = async_dispatcher_connect(
            self.hass,
            SIGNAL_VIGICAM_EVENT.format(self._entry_data["entry_id"]),
            self._handle_event,
        )

    async def async_will_remove_from_hass(self) -> None:
        if self._unsub_dispatcher:
            self._unsub_dispatcher()
        if self._clear_cancel:
            self._clear_cancel()

    @callback
    def _handle_event(self, event: dict) -> None:
        if event["type"] != self.entity_description.event_type:
            return
        if self._clear_cancel:
            self._clear_cancel()
            self._clear_cancel = None
        self._is_on = event["active"]
        area = event.get("area")
        if area:
            self._attr_extra_state_attributes = {"detection_zone": area}
        if self._is_on:
            self._clear_cancel = async_call_later(
                self.hass, AUTO_CLEAR_S, self._auto_clear
            )
        self.async_write_ha_state()

    @callback
    def _auto_clear(self, _now) -> None:
        self._is_on = False
        self._clear_cancel = None
        self.async_write_ha_state()

    @property
    def _unique_id_suffix(self) -> str:
        return f"event_{self.entity_description.key}"

    @property
    def is_on(self) -> bool:
        return self._is_on
