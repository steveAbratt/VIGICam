"""Switch entities for VIGI cameras — detection toggles, LED, alarm, audio mute, image controls."""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any, Callable

from homeassistant.components.switch import SwitchEntity, SwitchEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .api import VIGICamera, VIGIError
from .const import (
    CONF_FEATURE_IMAGE_CONTROLS,
    DEFAULT_FEATURE_IMAGE_CONTROLS,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
)
from .entity import VIGIEntity
from .openapi import VIGIOpenAPIError

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, kw_only=True)
class VIGISwitchDescription(SwitchEntityDescription):
    is_on_fn: Callable[[dict], bool | None]
    turn_on_fn: Callable[[VIGICamera], Any]
    turn_off_fn: Callable[[VIGICamera], Any]
    # Return False if the camera doesn't support this feature (field absent from data)
    supported_fn: Callable[[dict], bool] = lambda _: True
    # Optional OpenAPI handlers — preferred over the JSON API, but only when the state is
    # also READ from OpenAPI. openapi_data_key names the coordinator key that holds it.
    # Called as fn(openapi_client, last_read_state). Some firmware rejects a setter
    # that carries only the field being changed, so the writer is given the whole
    # object the matching getter returned and sends it back with one value altered.
    openapi_turn_on_fn: Callable[[Any, dict], Any] | None = None
    openapi_turn_off_fn: Callable[[Any, dict], Any] | None = None
    openapi_data_key: str | None = None
    # Getter used to refresh the object immediately before echoing it back. Without it the
    # write would echo the last 30s poll, so changing one of the sibling settings in the
    # VIGI app and then toggling here would silently revert that change.
    openapi_get_method: str | None = None
    # Checked before turning on, as (allowed_fn, fresh_read_fn, translation_key):
    # allowed_fn(data, keys switched on in the last poll interval) -> False refuses with
    # that translated exception rather than sending a write that would do nothing. It is
    # re-checked against fresh_read_fn's data before refusing, since the last poll may
    # predate a sibling switch turned on moments ago.
    turn_on_guard: tuple[
        Callable[[dict, set[str]], bool], Callable[[VIGICamera], Any], str
    ] | None = None


def _alarm_has_output(data: dict, recently_on: set[str]) -> bool:
    """The Alarm master only arms the camera's alarm: with Alarm Light and Alarm Sound
    both off it stores "on" and does nothing (#79, C540S and C350; the camera's own web
    page behaves the same). Firmware without the separate outputs is not blocked, and
    any value other than "off" counts as on.
    """
    if recently_on & {"light_alarm", "sound_alarm"}:
        return True
    alarm = data.get("alarm", {})
    outputs = [alarm[k] for k in ("light_alarm_enabled", "sound_alarm_enabled") if k in alarm]
    return not outputs or any(v != "off" for v in outputs)


async def _read_alarm(api: VIGICamera) -> dict:
    return {"alarm": await api.get_alarm()}


# Fields getPeople/VehicleDetectionSwitch returns, and that their setters appear to want
# back in full. A C350 on firmware 2.3.2 rejected {"enabled": ...} alone with errCode
# -10009 (#92), which is why the whole object is echoed with one value changed.
_DETECTION_WRITE_FIELDS = (
    "sound_alarm_enabled",
    "light_alarm_enabled",
    "msg_push_enabled",
    "record_enabled",
    "sensitivity",
)


def _detection_params(current: dict, enabled: str) -> dict:
    """Build a detection-switch payload: the last-read object with `enabled` replaced.

    Only known fields are echoed, so an unexpected key in the getter's reply cannot be
    sent back and rejected. Fields the camera did not report are simply omitted.
    """
    params = {"enabled": enabled}
    for field in _DETECTION_WRITE_FIELDS:
        if field in current:
            params[field] = current[field]
    if len(params) == 1 and current:
        # The camera returned something, but none of the fields we know. Probably a nested
        # or renamed reply shape — and the payload we are about to send is exactly the
        # single-field one that gets rejected with -10009, so name the keys we did see.
        _LOGGER.debug(
            "Detection reply had no known fields, sending enabled only. Keys seen: %s",
            sorted(current),
        )
    return params


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
        # OpenAPI results are stored raw, so an error reply can be a non-empty dict with
        # no "enabled" key — indexing it directly raised KeyError inside a coordinator
        # listener and broke the whole update.
        is_on_fn=lambda d: (
            d["openapi_people"].get("enabled") == "on"
            if d.get("openapi_people")
            else d.get("motion", {}).get("people_enabled") == "on"
        ),
        turn_on_fn=lambda api: api.set_person_detection(True),
        turn_off_fn=lambda api: api.set_person_detection(False),
        supported_fn=lambda d: (
            "people_enabled" in d.get("motion", {}) or bool(d.get("openapi_people"))
        ),
        openapi_turn_on_fn=lambda oapi, cur: oapi.call(
            "setPeopleDetectionSwitch", _detection_params(cur, "on")
        ),
        openapi_turn_off_fn=lambda oapi, cur: oapi.call(
            "setPeopleDetectionSwitch", _detection_params(cur, "off")
        ),
        openapi_data_key="openapi_people",
        openapi_get_method="getPeopleDetectionSwitch",
    ),
    VIGISwitchDescription(
        key="vehicle_detection",
        name="Detection Vehicle",
        is_on_fn=lambda d: (
            d["openapi_vehicle"].get("enabled") == "on"
            if d.get("openapi_vehicle")
            else d.get("motion", {}).get("vehicle_enabled") == "on"
        ),
        turn_on_fn=lambda api: api.set_vehicle_detection(True),
        turn_off_fn=lambda api: api.set_vehicle_detection(False),
        supported_fn=lambda d: (
            "vehicle_enabled" in d.get("motion", {}) or bool(d.get("openapi_vehicle"))
        ),
        openapi_turn_on_fn=lambda oapi, cur: oapi.call(
            "setVehicleDetectionSwitch", _detection_params(cur, "on")
        ),
        openapi_turn_off_fn=lambda oapi, cur: oapi.call(
            "setVehicleDetectionSwitch", _detection_params(cur, "off")
        ),
        openapi_data_key="openapi_vehicle",
        openapi_get_method="getVehicleDetectionSwitch",
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
        turn_on_guard=(_alarm_has_output, _read_alarm, "alarm_needs_output"),
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

    def _openapi_writable(self, fn: Callable[[Any, dict], Any] | None) -> Any | None:
        """Return the OpenAPI client only if state is also read from OpenAPI.

        The camera exposes these toggles through two independent subsystems. Writing via
        OpenAPI while is_on_fn falls back to the JSON API sends the change somewhere the
        read never looks, so the toggle flips and then springs back on the next poll —
        which is exactly what a camera with OpenAPI enabled but the detection methods
        unsupported (an empty openapi_people / openapi_vehicle) would do.
        """
        openapi = self._entry_data.get("openapi")
        key = self.entity_description.openapi_data_key
        if openapi is None or fn is None or not key:
            return None
        return openapi if (self.coordinator.data or {}).get(key) else None

    async def _refresh_openapi_state(self, openapi: Any) -> dict:
        """Re-read the settings object just before echoing it back.

        Falls back to the coordinator's copy if the read fails, so a transient error
        degrades to the previous behaviour rather than sending an empty object.
        """
        desc = self.entity_description
        cached = (self.coordinator.data or {}).get(desc.openapi_data_key) or {}
        if not desc.openapi_get_method:
            return cached
        try:
            raw = await openapi.call(desc.openapi_get_method)
        except VIGIOpenAPIError as exc:
            _LOGGER.debug(
                "%s: could not refresh %s before writing (%s) — using the last poll",
                self.entity_id, desc.openapi_get_method, exc,
            )
            return cached
        result = raw.get("result", raw)
        return result if isinstance(result, dict) and result else cached

    async def _write(
        self,
        openapi_fn: Callable[[Any, dict], Any] | None,
        api_fn: Callable[[VIGICamera], Any],
    ) -> None:
        """Write via OpenAPI when it is the authority, falling back to the JSON API.

        Some firmware answers getPeopleDetectionSwitch but rejects the matching setter, so
        preferring OpenAPI on the strength of the read alone is not enough — the rejection
        has to be caught and the change retried through the JSON API, or the toggle flips
        and springs back with nothing in the log.

        The writer receives the last-read object so it can echo the whole thing back with
        one value changed; a C350 rejected a single-field payload with errCode -10009.
        """
        openapi = self._openapi_writable(openapi_fn)
        if openapi is not None:
            current = await self._refresh_openapi_state(openapi)
            try:
                await openapi_fn(openapi, current)
                return
            except VIGIOpenAPIError as exc:
                _LOGGER.warning(
                    "%s: OpenAPI write failed (%s) — retrying via the JSON API",
                    self.entity_id, exc,
                )
        await api_fn(self._entry_data["api"])

    def _recently_on(self) -> set[str]:
        cutoff = time.monotonic() - DEFAULT_SCAN_INTERVAL
        turned_on_at = self._entry_data.get("switch_turned_on_at", {})
        return {key for key, at in turned_on_at.items() if at > cutoff}

    async def _check_turn_on_guard(self) -> None:
        allowed_fn, fresh_read_fn, translation_key = self.entity_description.turn_on_guard
        if allowed_fn(self.coordinator.data or {}, self._recently_on()):
            return
        try:
            fresh = await fresh_read_fn(self._entry_data["api"])
        except VIGIError:
            return  # can't tell: send the write rather than refuse wrongly
        if not allowed_fn(fresh, self._recently_on()):
            raise ServiceValidationError(
                translation_domain=DOMAIN, translation_key=translation_key
            )

    async def async_turn_on(self, **kwargs: Any) -> None:
        desc = self.entity_description
        # Recorded first, so a sibling's guard running in the same service call sees it.
        self._entry_data.setdefault("switch_turned_on_at", {})[desc.key] = time.monotonic()
        if desc.turn_on_guard:
            await self._check_turn_on_guard()
        await self._write(desc.openapi_turn_on_fn, desc.turn_on_fn)
        await self.coordinator.async_request_refresh()

    async def async_turn_off(self, **kwargs: Any) -> None:
        desc = self.entity_description
        self._entry_data.setdefault("switch_turned_on_at", {}).pop(desc.key, None)
        await self._write(desc.openapi_turn_off_fn, desc.turn_off_fn)
        await self.coordinator.async_request_refresh()
