"""Load the integration's pure modules without Home Assistant.

Importing custom_components.vigicam normally runs its __init__.py, which needs a full
Home Assistant install. api.py, const.py, openapi.py, onvif_port.py and event_image.py don't,
so they are loaded here directly under a stand-in package name. See docs/TESTING.md, Phase 1.
onvif_events.py uses Home Assistant only to send its events; without Home Assistant
installed, those two imports are stood in for.
"""
import importlib.util
import pathlib
import sys
import types

_PKG = "vigicam_pure"
_SRC = pathlib.Path(__file__).resolve().parent.parent / "custom_components" / "vigicam"

try:
    import homeassistant  # noqa: F401
except ImportError:
    for _name, _attrs in (
        ("homeassistant", {}),
        ("homeassistant.core", {"HomeAssistant": object}),
        ("homeassistant.helpers", {}),
        ("homeassistant.helpers.dispatcher", {"async_dispatcher_send": None}),
    ):
        sys.modules[_name] = types.ModuleType(_name)
        sys.modules[_name].__dict__.update(_attrs)

sys.modules.setdefault(_PKG, types.ModuleType(_PKG)).__path__ = [str(_SRC)]
for _name in ("const", "api", "openapi", "onvif_port", "onvif_events", "event_image"):
    _spec = importlib.util.spec_from_file_location(f"{_PKG}.{_name}", _SRC / f"{_name}.py")
    _mod = importlib.util.module_from_spec(_spec)
    sys.modules[f"{_PKG}.{_name}"] = _mod
    _spec.loader.exec_module(_mod)
