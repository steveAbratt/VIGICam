"""ONVIF pull responses are parsed whatever the attribute order, for every detection topic."""
import pytest

from vigicam_pure import onvif_events


def _message(
    topic: str, rule: str, field: str, value: str, value_first: bool = True, quote: str = '"',
    rule_item: str = "Rule",
) -> str:
    """A NotificationMessage laid out as an InSight S245 sent one (2026-11-06), Value before Name.

    Only People was captured live; the other topics follow the same layout with the item names
    the cameras' GetEventProperties lists for them.
    """

    def item(name: str, val: str) -> str:
        v, n = f"Value={quote}{val}{quote}", f"Name={quote}{name}{quote}"
        return f"<tt:SimpleItem {v + ' ' + n if value_first else n + ' ' + v}></tt:SimpleItem>"

    data = item(field, value) if field else ""
    return (
        "<wsnt:NotificationMessage>"
        '<wsnt:Topic Dialect="http://www.onvif.org/ver10/tev/topicExpression/ConcreteSet">'
        f"{topic}</wsnt:Topic><wsnt:Message>"
        '<tt:Message PropertyOperation="Initialized" UtcTime="2026-11-06T16:25:50Z">'
        f"<tt:Source>{item('VideoSourceConfigurationToken', 'vsconf')}"
        f"{item('VideoAnalyticsConfigurationToken', 'VideoAnalyticsToken')}"
        f"{item(rule_item, rule)}</tt:Source>"
        f"<tt:Data>{data}</tt:Data>"
        "</tt:Message></wsnt:Message></wsnt:NotificationMessage>"
    )


def _response(*messages: str) -> str:
    return (
        '<?xml version="1.0"?><SOAP-ENV:Envelope><SOAP-ENV:Body><tev:PullMessagesResponse>'
        + "".join(messages)
        + "</tev:PullMessagesResponse></SOAP-ENV:Body></SOAP-ENV:Envelope>"
    )


_PEOPLE = "tns1:RuleEngine/PeopleDetector/People"
_CELL = "tns1:RuleEngine/CellMotionDetector/Motion"
_ALARM = "tns1:VideoSource/MotionAlarm"

# Every rule topic GetEventProperties lists on a C540V and an S245, plus MotionAlarm.
_TOPICS = [
    (_CELL, "MyCellMotionDetectorRule", "IsMotion", "motion"),
    ("tns1:RuleEngine/TamperDetector/Tamper", "MyTamperDetectorRule", "IsTamper", "tamper"),
    ("tns1:RuleEngine/IntrusionDetector/Intrusion", "MyIntrusionDetectorRule", "IsIntrusion", "intrusion"),
    ("tns1:RuleEngine/LineCrossDetector/LineCross", "MyLineCrossDetectorRule", "IsLineCross", "line_cross"),
    (_PEOPLE, "MyPeopleDetectorRule", "IsPeople", "person"),
    ("tns1:RuleEngine/TPSmartEventDetector/TPSmartEvent", "MyTPSmartEventDetectorRule", "IsTPSmartEvent", "smart_event"),
    (_ALARM, "vsconf", "State", "motion"),  # its Source item is "Source", not "Rule"
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
    item = "Source" if topic == _ALARM else "Rule"
    resp = _response(
        _message(topic, rule, field, "true", value_first, rule_item=item),
        _message(topic, rule, field, "false", value_first, rule_item=item),
    )
    assert [(p["type"], p["active"]) for p in _dispatched(resp, monkeypatch)] == [
        (etype, True), (etype, False),
    ]


def test_single_quotes_and_entities():
    (event,) = onvif_events.parse_notifications(
        _response(_message(_PEOPLE, "Front &amp; Gate", "IsPeople", "false", quote="'"))
    )
    assert event["data"] == {"IsPeople": "false"}
    assert event["source"]["Rule"] == "Front & Gate"


def test_items_keep_source_and_data_apart():
    (event,) = onvif_events.parse_notifications(
        _response(_message(_PEOPLE, "MyPeopleDetectorRule", "IsPeople", "false"))
    )
    assert event["data"] == {"IsPeople": "false"}
    assert event["source"]["Rule"] == "MyPeopleDetectorRule"


def test_empty_value_is_kept():
    (event,) = onvif_events.parse_notifications(_response(_message(_PEOPLE, "", "IsPeople", "true")))
    assert event["source"]["Rule"] == ""


def test_unknown_data_field_is_not_a_detection(monkeypatch):
    assert _dispatched(_response(_message(_PEOPLE, "r", "Confidence", "90")), monkeypatch) == []


def test_no_data_still_counts_as_a_detection(monkeypatch):
    sent = _dispatched(_response(_message(_PEOPLE, "r", "", "")), monkeypatch)
    assert [(p["type"], p["active"]) for p in sent] == [("person", True)]


def test_motion_stays_on_while_either_motion_topic_is(monkeypatch):
    resp = _response(
        _message(_CELL, "MyCellMotionDetectorRule", "IsMotion", "true"),
        _message(_ALARM, "vsconf", "State", "true", rule_item="Source"),
        _message(_ALARM, "vsconf", "State", "false", rule_item="Source"),
        _message(_CELL, "MyCellMotionDetectorRule", "IsMotion", "false"),
    )
    assert [p["active"] for p in _dispatched(resp, monkeypatch)] == [True, True, True, False]


def test_default_rule_name_is_not_a_zone_but_a_named_one_is(monkeypatch):
    resp = _response(
        _message(_PEOPLE, "MyPeopleDetectorRule", "IsPeople", "true"),
        _message("tns1:RuleEngine/IntrusionDetector/Intrusion", "Area1", "IsIntrusion", "true"),
        _message("tns1:RuleEngine/IntrusionDetector/Intrusion", "MyDrivewayRule", "IsIntrusion", "true"),
    )
    assert [p["area"] for p in _dispatched(resp, monkeypatch)] == [None, "Area1", "MyDrivewayRule"]


def test_no_messages_on_an_empty_pull():
    assert onvif_events.parse_notifications(_response()) == []
