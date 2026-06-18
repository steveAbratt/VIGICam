"""Switch entities for VIGI cameras — detection toggles, LED, alarm, audio mute, image controls."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from homeassistant.components.switch import SwitchEntity, SwitchEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .api import VIGICamera
from .const import CONF_FEATURE_IMAGE_CONTROLS, DEFAULT_FEATURE_IMAGE_CONTROLS, DOMAIN
from .entity import VIGIEntity


@dataclass(frozen=True, kw_only=True)
class VIGISwitchDescription(SwitchEntityDescription):
    is_on_fn: Callable[[dict], bool | None]
    turn_on_fn: Callable[[VIGICamera], Any]
    turn_off_fn: Callable[[VIGICamera], Any]
    # Return False if the camera doesn't support this feature (field absent from data)
    supported_fn: Callable[[dict], bool] = lambda _: True
    # Optional OpenAPI handlers — used in preference to JSON API when openapi is available
    openapi_turn_on_fn: Callable[[Any], Any] | None = None
    openapi_turn_off_fn: Callable[[Any], Any] | None = None


SWITCHES: tuple[VIGISwitchDescription, ...] = (
    VIGISwitchDescription(
        key="motion_detection",
        name="Detection Motion",
        is_on_fn=lambda d: d.get("motion", {}).get("enabled") == "on",
        turn_on_fn=lambda api: api.set_motion_detection(True),
        turn_off_fn=lambda api: api.set_motion_detection(False),
        supported_fn=lambda d: "enabled" in d.get("motion", {}),
    ),
    VIGISwitchDescription(
        key="person_detection",
        name="Detection Person",
        is_on_fn=lambda d: (
            d["openapi_people"]["enabled"] == "on"
            if d.get("openapi_people")
            else d.get("motion", {}).get("people_enabled") == "on"
        ),
        turn_on_fn=lambda api: api.set_person_detection(True),
        turn_off_fn=lambda api: api.set_person_detection(False),
        supported_fn=lambda d: (
            "people_enabled" in d.get("motion", {}) or bool(d.get("openapi_people"))
        ),
        openapi_turn_on_fn=lambda oapi: oapi.call("setPeopleDetectionSwitch", {"enabled": "on"}),
        openapi_turn_off_fn=lambda oapi: oapi.call("setPeopleDetectionSwitch", {"enabled": "off"}),
    ),
    VIGISwitchDescription(
        key="vehicle_detection",
        name="Detection Vehicle",
        is_on_fn=lambda d: (
            d["openapi_vehicle"]["enabled"] == "on"
            if d.get("openapi_vehicle")
            else d.get("motion", {}).get("vehicle_enabled") == "on"
        ),
        turn_on_fn=lambda api: api.set_vehicle_detection(True),
        turn_off_fn=lambda api: api.set_vehicle_detection(False),
        supported_fn=lambda d: (
            "vehicle_enabled" in d.get("motion", {}) or bool(d.get("openapi_vehicle"))
        ),
        openapi_turn_on_fn=lambda oapi: oapi.call("setVehicleDetectionSwitch", {"enabled": "on"}),
        openapi_turn_off_fn=lambda oapi: oapi.call("setVehicleDetectionSwitch", {"enabled": "off"}),
    ),
    VIGISwitchDescription(
        key="tamper_detection",
        name="Detection Tamper",
        is_on_fn=lambda d: d.get("tamper", {}).get("enabled") == "on",
        turn_on_fn=lambda api: api.set_tamper(True),
        turn_off_fn=lambda api: api.set_tamper(False),
        supported_fn=lambda d: bool(d.get("tamper")),
    ),
    VIGISwitchDescription(
        key="led",
        name="Status LED",
        is_on_fn=lambda d: d.get("led", {}).get("enabled") == "on",
        turn_on_fn=lambda api: api.set_led(True),
        turn_off_fn=lambda api: api.set_led(False),
        supported_fn=lambda d: bool(d.get("led")),
    ),
    VIGISwitchDescription(
        key="alarm",
        name="Alarm",
        is_on_fn=lambda d: d.get("alarm", {}).get("enabled") == "on",
        turn_on_fn=lambda api: api.set_alarm(True),
        turn_off_fn=lambda api: api.set_alarm(False),
        supported_fn=lambda d: bool(d.get("alarm")),
    ),
    VIGISwitchDescription(
        key="light_alarm",
        name="Alarm Light",
        icon="mdi:alarm-light",
        is_on_fn=lambda d: d.get("alarm", {}).get("light_alarm_enabled") == "on",
        turn_on_fn=lambda api: api.set_light_alarm(True),
        turn_off_fn=lambda api: api.set_light_alarm(False),
        supported_fn=lambda d: "light_alarm_enabled" in d.get("alarm", {}),
    ),
    VIGISwitchDescription(
        key="sound_alarm",
        name="Alarm Sound",
        icon="mdi:alarm-bell",
        is_on_fn=lambda d: d.get("alarm", {}).get("sound_alarm_enabled") == "on",
        turn_on_fn=lambda api: api.set_sound_alarm(True),
        turn_off_fn=lambda api: api.set_sound_alarm(False),
        supported_fn=lambda d: "sound_alarm_enabled" in d.get("alarm", {}),
    ),
    VIGISwitchDescription(
        key="target_track",
        name="Target Tracking",
        icon="mdi:crosshairs-gps",
        is_on_fn=lambda d: d.get("target_track", {}).get("enabled") == "on",
        turn_on_fn=lambda api: api.set_target_track(True),
        turn_off_fn=lambda api: api.set_target_track(False),
        supported_fn=lambda d: "enabled" in d.get("target_track", {}),
    ),
    VIGISwitchDescription(
        key="speaker_mute",
        name="Speaker Mute",
        is_on_fn=lambda d: d.get("speaker", {}).get("mute") == "on",
        turn_on_fn=lambda api: api.set_speaker_mute(True),
        turn_off_fn=lambda api: api.set_speaker_mute(False),
        supported_fn=lambda d: bool(d.get("speaker")),
    ),
    VIGISwitchDescription(
        key="mic_mute",
        name="Microphone Mute",
        is_on_fn=lambda d: d.get("microphone", {}).get("mute") == "on",
        turn_on_fn=lambda api: api.set_mic_mute(True),
        turn_off_fn=lambda api: api.set_mic_mute(False),
        supported_fn=lambda d: bool(d.get("microphone")),
    ),
)


def _common_switch(key: str, name: str, icon: str | None = None) -> VIGISwitchDescription:
    """Shorthand for an image.common on/off switch in the Config category."""
    return VIGISwitchDescription(
        key=key,
        name=name,
        icon=icon,
        entity_category=EntityCategory.CONFIG,
        entity_registry_enabled_default=False,
        is_on_fn=lambda d, k=key: d.get("image_common", {}).get(k) == "on",
        turn_on_fn=lambda api, k=key: api.set_image_common_value(k, "on"),
        turn_off_fn=lambda api, k=key: api.set_image_common_value(k, "off"),
        supported_fn=lambda d, k=key: k in d.get("image_common", {}),
    )


def _switch_switch(key: str, name: str, icon: str | None = None) -> VIGISwitchDescription:
    """Shorthand for an image.switch on/off switch in the Config category."""
    return VIGISwitchDescription(
        key=key,
        name=name,
        icon=icon,
        entity_category=EntityCategory.CONFIG,
        entity_registry_enabled_default=False,
        is_on_fn=lambda d, k=key: d.get("image_switch", {}).get(k) == "on",
        turn_on_fn=lambda api, k=key: api.set_image_switch_value(k, "on"),
        turn_off_fn=lambda api, k=key: api.set_image_switch_value(k, "off"),
        supported_fn=lambda d, k=key: k in d.get("image_switch", {}),
    )


IMAGE_CONTROL_SWITCHES: tuple[VIGISwitchDescription, ...] = (
    _common_switch("wide_dynamic",            "WDR",                       "mdi:sun-wireless"),
    _common_switch("high_light_compensation", "HLC",                       "mdi:car-light-high"),
    _common_switch("dehaze",                  "Dehaze",                    "mdi:weather-fog"),
    _common_switch("eis",                     "EIS",                       "mdi:image-filter-center-focus-strong"),
    _common_switch("auto_exp_antiflicker",    "Auto Exposure Anti-flicker","mdi:sine-wave"),
    _common_switch("backlight",               "Backlight Compensation",    "mdi:backburger"),
    _switch_switch("ldc",                     "Lens Distortion Correction","mdi:camera-enhance"),
    _switch_switch("full_color_people_enhance",  "Full Colour People Enhance",  "mdi:human"),
    _switch_switch("full_color_vehicle_enhance", "Full Colour Vehicle Enhance", "mdi:car"),
)

PRIVACY_SWITCHES: tuple[VIGISwitchDescription, ...] = (
    VIGISwitchDescription(
        key="privacy_mask",
        name="Privacy Mask",
        icon="mdi:eye-off",
        is_on_fn=lambda d: d.get("lens_mask", {}).get("enabled") == "on",
        turn_on_fn=lambda api: api.set_lens_mask(True),
        turn_off_fn=lambda api: api.set_lens_mask(False),
        supported_fn=lambda d: bool(d.get("lens_mask")),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    data = hass.data[DOMAIN][entry.entry_id]
    coordinator = data["coordinator"]
    coord_data = coordinator.data or {}

    entities = [
        VIGISwitch(coordinator, data, desc)
        for desc in SWITCHES
        if desc.supported_fn(coord_data)
    ]

    entities.extend(
        VIGISwitch(coordinator, data, desc)
        for desc in PRIVACY_SWITCHES
        if desc.supported_fn(coord_data)
    )

    if entry.options.get(CONF_FEATURE_IMAGE_CONTROLS, DEFAULT_FEATURE_IMAGE_CONTROLS):
        entities.extend(
            VIGISwitch(coordinator, data, desc)
            for desc in IMAGE_CONTROL_SWITCHES
            if desc.supported_fn(coord_data)
        )

    async_add_entities(entities)


class VIGISwitch(VIGIEntity, SwitchEntity):
    entity_description: VIGISwitchDescription

    def __init__(self, coordinator, entry_data, description):
        super().__init__(coordinator, entry_data)
        self.entity_description = description

    @property
    def _unique_id_suffix(self) -> str:
        return self.entity_description.key

    @property
    def is_on(self) -> bool | None:
        return self.entity_description.is_on_fn(self.coordinator.data or {})

    async def async_turn_on(self, **kwargs: Any) -> None:
        openapi = self._entry_data.get("openapi")
        if openapi and self.entity_description.openapi_turn_on_fn:
            await self.entity_description.openapi_turn_on_fn(openapi)
        else:
            await self.entity_description.turn_on_fn(self._entry_data["api"])
        await self.coordinator.async_request_refresh()

    async def async_turn_off(self, **kwargs: Any) -> None:
        openapi = self._entry_data.get("openapi")
        if openapi and self.entity_description.openapi_turn_off_fn:
            await self.entity_description.openapi_turn_off_fn(openapi)
        else:
            await self.entity_description.turn_off_fn(self._entry_data["api"])
        await self.coordinator.async_request_refresh()
