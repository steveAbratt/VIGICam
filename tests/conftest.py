"""Load the integration's pure modules without Home Assistant.

Importing custom_components.vigicam normally runs its __init__.py, which needs a full
Home Assistant install. api.py, const.py and onvif_port.py don't, so they are loaded here directly
under a stand-in package name. See docs/TESTING.md, Phase 1.
"""
import importlib.util
import pathlib
import sys
import types

_PKG = "vigicam_pure"
_SRC = pathlib.Path(__file__).resolve().parent.parent / "custom_components" / "vigicam"

sys.modules.setdefault(_PKG, types.ModuleType(_PKG)).__path__ = [str(_SRC)]
for _name in ("const", "api", "onvif_port"):
    _spec = importlib.util.spec_from_file_location(f"{_PKG}.{_name}", _SRC / f"{_name}.py")
    _mod = importlib.util.module_from_spec(_spec)
    sys.modules[f"{_PKG}.{_name}"] = _mod
    _spec.loader.exec_module(_mod)
