"""Which events update Last Detection, and what it labels them.

image.py needs Home Assistant, so the few names it imports are stood in for here, as in
test_spotlight_off.py. The grab itself is replaced; only the routing is under test.
"""
import ast
import asyncio
import importlib.util
import pathlib
import sys
import types

import vigicam_pure  # noqa: F401  (conftest loads onvif_events.py and event_image.py)
from vigicam_pure import onvif_events

_SRC = pathlib.Path(__file__).resolve().parent.parent / "custom_components" / "vigicam"


def _load_image():
    class ImageEntity:
        def __init__(self, hass):
            self.hass = hass

    stand_ins = {
        "homeassistant.components": {},
        "homeassistant.components.ffmpeg": {"get_ffmpeg_manager": None},
        "homeassistant.components.image": {"ImageEntity": ImageEntity},
        "homeassistant.config_entries": {"ConfigEntry": object},
        "homeassistant.core": {"HomeAssistant": object, "callback": lambda f: f},
        "homeassistant.helpers.dispatcher": {"async_dispatcher_connect": None},
        "homeassistant.helpers.entity_platform": {"AddEntitiesCallback": object},
    }
    for name, attrs in stand_ins.items():
        module = sys.modules.setdefault(name, types.ModuleType(name))
        for attr, value in attrs.items():
            if not hasattr(module, attr):
                setattr(module, attr, value)

    if "vigicam_pure.entity" not in sys.modules:
        class VIGIEntity:
            def __init__(self, coordinator, entry_data):
                self.coordinator = coordinator
                self._entry_data = entry_data

            def async_write_ha_state(self):
                pass

        entity = types.ModuleType("vigicam_pure.entity")
        entity.VIGIEntity = VIGIEntity
        sys.modules["vigicam_pure.entity"] = entity
    if not hasattr(vigicam_pure, "communicate_or_kill"):
        vigicam_pure.communicate_or_kill = None
    spec = importlib.util.spec_from_file_location("vigicam_pure.image", _SRC / "image.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


image = _load_image()


class _Hass:
    def __init__(self):
        self.tasks = []

    def async_create_task(self, coro):
        self.tasks.append(coro)
        if asyncio.iscoroutine(coro):
            coro.close()  # the fallback is run by hand where a test needs it


def _image(include_audio=False):
    hass = _Hass()
    img = image.VIGILastDetectionImage(types.SimpleNamespace(hass=hass), {}, include_audio)
    img._unsub_dispatcher = object()  # as if added to hass
    img.grabbed = []

    def grab(event):  # record the event a grab would label, and finish at once
        img.grabbed.append(event)
        img._grabbing = False

    img._grab_frame = grab
    return img, hass


def _smart(kind=None):
    event = {"type": "smart_event", "active": True, "area": None}
    if kind:
        event["kind"] = kind
    return event


def test_a_named_sound_is_left_out_without_openapi():
    img, hass = _image()
    img._handle_event(_smart("audio_anomaly"))
    assert hass.tasks == [] and img.grabbed == []


def test_a_named_sound_updates_the_image_when_sounds_are_on():
    img, _ = _image(include_audio=True)
    img._handle_event(_smart("audio_anomaly"))
    assert [e["type"] for e in img.grabbed] == ["audio_anomaly"]


def test_a_named_kind_labels_the_image_and_is_the_event_kept():
    img, _ = _image()
    img._start_grab(_smart("vehicle"))
    (event,) = img.grabbed
    assert event["type"] == "vehicle" and img._latest_event is event


def test_camera_kinds_use_openapi_type_names():
    # A named kind gives way to OpenAPI's event by type name, so the two must match or
    # every named detection is grabbed twice. openapi_events.py needs more of Home
    # Assistant than is stood in for, so its map is read from the source.
    tree = ast.parse((_SRC / "openapi_events.py").read_text())
    (node,) = [n for n in ast.walk(tree) if isinstance(n, ast.AnnAssign) and getattr(n.target, "id", None) == "_EVENT_TYPE_MAP"]
    openapi_types = set(ast.literal_eval(node.value).values())
    assert set(onvif_events.SMART_EVENT_KINDS.values()) <= openapi_types


def test_a_smart_event_waits_for_openapi_first():
    img, hass = _image()
    img._handle_event(_smart("vehicle"))
    assert len(hass.tasks) == 1 and img.grabbed == []


def _fallback(img, smart, openapi_type, monkeypatch):
    """Run the wait, with OpenAPI reporting openapi_type 0.5 s into it (None: nothing)."""
    clock = [100.0]

    async def sleep(_seconds):
        if openapi_type:
            clock[0] = 100.5
            img._handle_event({"type": openapi_type, "active": True, "area": None, "source": "openapi"})
        clock[0] = 102.0

    monkeypatch.setattr(image, "time", types.SimpleNamespace(monotonic=lambda: clock[0]))
    monkeypatch.setattr(image, "asyncio", types.SimpleNamespace(sleep=sleep))
    asyncio.run(img._smart_event_fallback(smart))
    return [(e["type"], e.get("source")) for e in img.grabbed]


def test_without_openapi_the_named_kind_is_used(monkeypatch):
    img, _ = _image()
    assert _fallback(img, _smart("vehicle"), None, monkeypatch) == [("vehicle", None)]


def test_openapi_naming_the_same_kind_grabs_once(monkeypatch):
    img, _ = _image()
    assert _fallback(img, _smart("vehicle"), "vehicle", monkeypatch) == [("vehicle", "openapi")]


def test_a_removed_image_does_not_grab(monkeypatch):
    img, _ = _image()
    img._unsub_dispatcher = None
    assert _fallback(img, _smart("vehicle"), None, monkeypatch) == []


def test_an_openapi_sound_does_not_cancel_a_named_vehicle(monkeypatch):
    img, _ = _image()
    assert _fallback(img, _smart("vehicle"), "audio_anomaly", monkeypatch) == [("vehicle", None)]


def test_an_unnamed_smart_event_gives_way_to_any_openapi_event(monkeypatch):
    img, _ = _image()
    assert _fallback(img, _smart(), "audio_anomaly", monkeypatch) == []


def test_an_unnamed_smart_event_is_used_when_openapi_says_nothing(monkeypatch):
    img, _ = _image()
    assert _fallback(img, _smart(), None, monkeypatch) == [("smart_event", None)]
