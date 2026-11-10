"""Turning the Spotlight off puts back the last mode that doesn't use the white light.

It used to write Auto IR every time, which reset a gate camera's Night Vision Mode from Off
to Auto IR each morning (2026-11-07). light.py needs Home Assistant, so the few names it
imports are stood in for here.
"""
import asyncio
import importlib.util
import pathlib
import sys
import types

import vigicam_pure  # noqa: F401  (conftest loads const.py, which light.py imports)

_SRC = pathlib.Path(__file__).resolve().parent.parent / "custom_components" / "vigicam"


def _load_light():
    stand_ins = {
        "homeassistant.components": {},
        "homeassistant.components.light": {
            "ATTR_BRIGHTNESS": "brightness",
            "ColorMode": types.SimpleNamespace(BRIGHTNESS="brightness"),
            "LightEntity": object,
        },
        "homeassistant.config_entries": {"ConfigEntry": object},
        "homeassistant.helpers.entity_platform": {"AddEntitiesCallback": object},
    }
    for name, attrs in stand_ins.items():
        if name not in sys.modules:
            sys.modules[name] = types.ModuleType(name)
            sys.modules[name].__dict__.update(attrs)

    class VIGIEntity:
        def __init__(self, coordinator, entry_data):
            self.coordinator = coordinator
            self._entry_data = entry_data

        def _handle_coordinator_update(self):
            pass

        def async_write_ha_state(self):
            pass

    entity = types.ModuleType("vigicam_pure.entity")
    entity.VIGIEntity = VIGIEntity
    sys.modules["vigicam_pure.entity"] = entity
    spec = importlib.util.spec_from_file_location(
        "vigicam_pure.light", _SRC / "light.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


light = _load_light()


class _Api:
    def __init__(self):
        self.writes = []

    async def set_pre_night_vision_mode(self, mode):
        self.writes.append(("pre", mode))

    async def set_night_vision_mode(self, mode):
        self.writes.append(("legacy", mode))


class _Coordinator:
    def __init__(self, image_switch):
        self.data = {"image_switch": image_switch}

    async def async_request_refresh(self):
        pass


def _spotlight(image_switch):
    api = _Api()
    coordinator = _Coordinator(image_switch)
    return light.VIGISpotlight(coordinator, {"api": api}), coordinator, api


def _poll(spot, coordinator, image_switch):
    coordinator.data = {"image_switch": image_switch}
    spot._handle_coordinator_update()


def test_newer_firmware_off_mode_is_kept():
    spot, _, api = _spotlight({"pre_night_vision_mode": "off", "night_vision_mode": "inf_night_vision"})
    asyncio.run(spot.async_turn_off())
    assert api.writes == [("pre", "off")]


def test_newer_firmware_white_mode_not_lit_is_still_turned_off():
    # In daylight a White LED Always On camera reports no white light lit.
    spot, coordinator, api = _spotlight({"pre_night_vision_mode": "off", "night_vision_mode": "inf_night_vision"})
    _poll(spot, coordinator, {"pre_night_vision_mode": "white_led_always_on", "night_vision_mode": "inf_night_vision"})
    asyncio.run(spot.async_turn_off())
    assert api.writes == [("pre", "off")]


def test_newer_firmware_unknown_previous_mode_falls_back_to_auto_ir():
    spot, _, api = _spotlight({"pre_night_vision_mode": "white_led_always_on", "night_vision_mode": "wtl_night_vision"})
    asyncio.run(spot.async_turn_off())
    assert api.writes == [("pre", "auto_ir")]


def test_legacy_spotlight_on_goes_back_to_the_previous_mode():
    spot, coordinator, api = _spotlight({"night_vision_mode": "md_night_vision"})
    _poll(spot, coordinator, {"night_vision_mode": "wtl_night_vision"})
    asyncio.run(spot.async_turn_off())
    assert api.writes == [("legacy", "md_night_vision")]


def test_legacy_unknown_previous_mode_falls_back_to_ir_always_on():
    spot, _, api = _spotlight({"night_vision_mode": "wtl_night_vision"})
    asyncio.run(spot.async_turn_off())
    assert api.writes == [("legacy", "inf_night_vision")]


def test_newer_firmware_triggered_colour_mode_not_lit_is_kept():
    # Human/Vehicle-Triggered Full-Color reports md_night_vision, so the spotlight shows off.
    spot, _, api = _spotlight({"pre_night_vision_mode": "human_triggered_color", "night_vision_mode": "md_night_vision"})
    asyncio.run(spot.async_turn_off())
    assert api.writes == []


def test_legacy_triggered_colour_mode_is_kept():
    spot, _, api = _spotlight({"night_vision_mode": "human_triggered_color"})
    asyncio.run(spot.async_turn_off())
    assert api.writes == []


def test_newer_firmware_auto_colour_lit_is_turned_off():
    spot, coordinator, api = _spotlight({"pre_night_vision_mode": "auto_ir", "night_vision_mode": "inf_night_vision"})
    _poll(spot, coordinator, {"pre_night_vision_mode": "auto_color", "night_vision_mode": "wtl_night_vision"})
    asyncio.run(spot.async_turn_off())
    assert api.writes == [("pre", "auto_ir")]
