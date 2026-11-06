"""ONVIF pull responses are parsed whatever the attribute order, for every detection topic."""
import importlib.util
import pathlib
import sys
import types

import pytest

# onvif_events imports Home Assistant only to dispatch; stand those imports in.
for _name, _attrs in (
    ("homeassistant", {}),
    ("homeassistant.core", {"HomeAssistant": object}),
    ("homeassistant.helpers", {}),
    ("homeassistant.helpers.dispatcher", {"async_dispatcher_send": None}),
):
    sys.modules.setdefault(_name, types.ModuleType(_name)).__dict__.update(
        {k: v for k, v in _attrs.items() if not hasattr(sys.modules[_name], k)}
    )
_SRC = pathlib.Path(__file__).resolve().parent.parent / "custom_components" / "vigicam"
_spec = importlib.util.spec_from_file_location("vigicam_pure.onvif_events", _SRC / "onvif_events.py")
onvif_events = importlib.util.module_from_spec(_spec)
sys.modules["vigicam_pure.onvif_events"] = onvif_events
_spec.loader.exec_module(onvif_events)


def _message(topic: str, rule: str, field: str, value: str, value_first: bool = True) -> str:
    """A NotificationMessage as an InSight S245 sent it (2026-11-06), Value before Name."""

    def item(name: str, val: str) -> str:
        attrs = f'Value="{val}" Name="{name}"' if value_first else f'Name="{name}" Value="{val}"'
        return f"<tt:SimpleItem {attrs}></tt:SimpleItem>"

    return (
        "<wsnt:NotificationMessage>"
        '<wsnt:Topic Dialect="http://www.onvif.org/ver10/tev/topicExpression/ConcreteSet">'
        f"{topic}</wsnt:Topic><wsnt:Message>"
        '<tt:Message PropertyOperation="Initialized" UtcTime="2026-11-06T16:25:50Z">'
        f"<tt:Source>{item('VideoSourceConfigurationToken', 'vsconf')}"
        f"{item('VideoAnalyticsConfigurationToken', 'VideoAnalyticsToken')}"
        f"{item('Rule', rule)}</tt:Source>"
        f"<tt:Data>{item(field, value)}</tt:Data>"
        "</tt:Message></wsnt:Message></wsnt:NotificationMessage>"
    )


def _response(*messages: str) -> str:
    return (
        '<?xml version="1.0"?><SOAP-ENV:Envelope><SOAP-ENV:Body><tev:PullMessagesResponse>'
        + "".join(messages)
        + "</tev:PullMessagesResponse></SOAP-ENV:Body></SOAP-ENV:Envelope>"
    )


# Every rule topic GetEventProperties lists on a C540V and an S245, plus MotionAlarm.
_TOPICS = [
    ("tns1:RuleEngine/CellMotionDetector/Motion", "MyMotionDetectorRule", "IsMotion", "motion"),
    ("tns1:RuleEngine/TamperDetector/Tamper", "MyTamperDetectorRule", "IsTamper", "tamper"),
    ("tns1:RuleEngine/IntrusionDetector/Intrusion", "MyIntrusionDetectorRule", "IsIntrusion", "intrusion"),
    ("tns1:RuleEngine/LineCrossDetector/LineCross", "MyLineCrossDetectorRule", "IsLineCross", "line_cross"),
    ("tns1:RuleEngine/PeopleDetector/People", "MyPeopleDetectorRule", "IsPeople", "person"),
    ("tns1:RuleEngine/TPSmartEventDetector/TPSmartEvent", "MyTPSmartEventDetectorRule", "IsTPSmartEvent", "smart_event"),
    ("tns1:VideoSource/MotionAlarm", "vsconf", "State", "motion"),
]


def _dispatched(resp: str, monkeypatch) -> list[dict]:
    sent = []
    monkeypatch.setattr(onvif_events, "async_dispatcher_send", lambda _h, _s, payload: sent.append(payload))
    ev = onvif_events.VIGIOnvifEvents(None, "camera", "user", "pw", "entry")
    for event in onvif_events.parse_notifications(resp):
        ev._dispatch(event)
    return sent


@pytest.mark.parametrize("value_first", [True, False])
@pytest.mark.parametrize(("topic", "rule", "field", "etype"), _TOPICS)
def test_every_topic_reports_start_and_end(topic, rule, field, etype, value_first, monkeypatch):
    resp = _response(
        _message(topic, rule, field, "true", value_first),
        _message(topic, rule, field, "false", value_first),
    )
    assert [(p["type"], p["active"]) for p in _dispatched(resp, monkeypatch)] == [
        (etype, True), (etype, False),
    ]


def test_items_keep_source_and_data_apart():
    (event,) = onvif_events.parse_notifications(
        _response(_message("tns1:RuleEngine/PeopleDetector/People", "MyPeopleDetectorRule", "IsPeople", "false"))
    )
    assert event["data"] == {"IsPeople": "false"}
    assert event["source"]["Rule"] == "MyPeopleDetectorRule"


def test_empty_value_is_kept():
    (event,) = onvif_events.parse_notifications(
        _response(_message("tns1:RuleEngine/PeopleDetector/People", "", "IsPeople", "true"))
    )
    assert event["source"]["Rule"] == ""


def test_default_rule_name_is_not_a_zone_but_a_named_one_is(monkeypatch):
    resp = _response(
        _message("tns1:RuleEngine/PeopleDetector/People", "MyPeopleDetectorRule", "IsPeople", "true"),
        _message("tns1:RuleEngine/IntrusionDetector/Intrusion", "Area1", "IsIntrusion", "true"),
    )
    assert [p["area"] for p in _dispatched(resp, monkeypatch)] == [None, "Area1"]


def test_no_messages_on_an_empty_pull():
    assert onvif_events.parse_notifications(_response()) == []
