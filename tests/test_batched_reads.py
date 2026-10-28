"""get_many: one multi-module request, answered keys only.

The well-formed reply shapes are from a C540V; the malformed ones are synthetic.
"""
import asyncio

from vigicam_pure.api import VIGICamera


def _run(reply: dict, keys: list[str]) -> tuple[dict, dict]:
    cam = VIGICamera("192.0.2.1", "admin", "pw")
    sent: dict = {}

    async def fake_request(body: dict) -> dict:
        sent.update(body)
        return reply

    cam._request = fake_request
    return asyncio.run(cam.get_many(keys)), sent


def test_sections_of_one_module_share_a_request_entry():
    _, sent = _run({}, ["image_switch", "image_common", "led", "storage"])
    assert sent == {
        "method": "get",
        "image": {"name": ["switch", "common"]},
        "led": {"name": "config"},
        "harddisk_manage": {"table": "hd_info"},
    }


def test_unsupported_module_is_left_out_not_returned_empty():
    # The camera answers an unsupported module (-40106 when asked alone) as {}.
    got, _ = _run({"led": {"config": {"enabled": "on"}}, "lens_mask": {}, "error_code": 0},
                  ["led", "lens_mask"])
    assert got == {"led": {"enabled": "on"}}


def test_presets_parsed_and_an_empty_list_kept():
    got, _ = _run({"preset": {"preset": {"id": ["1", "2"], "name": ["Gate", "Back%20yard"]}}},
                  ["presets"])
    assert got["presets"] == [{"id": "1", "name": "Gate"}, {"id": "2", "name": "Back yard"}]
    # A camera with no presets saved: answered, and authoritative.
    got, _ = _run({"preset": {"preset": {"id": [], "name": []}}}, ["presets"])
    assert got == {"presets": []}


def test_storage_unwrapped_and_absent_when_unanswered():
    reply = {"harddisk_manage": {"hd_info": [{"hd_info_1": {"status": "normal"}}]}}
    assert _run(reply, ["storage"])[0] == {"storage": {"status": "normal"}}
    assert _run({"harddisk_manage": {}}, ["storage"])[0] == {}


def test_storage_without_hd_info_is_not_answered():
    # A failed storage read must not look like a missing card.
    assert _run({"harddisk_manage": {"err": 1}}, ["storage"])[0] == {}


def test_empty_preset_section_is_not_answered():
    assert _run({"preset": {"preset": {}}}, ["presets"])[0] == {}


def test_one_bad_shape_loses_that_key_only():
    reply = {"harddisk_manage": ["unexpected"], "led": {"config": {"enabled": "on"}}}
    assert _run(reply, ["storage", "led"])[0] == {"led": {"enabled": "on"}}


def test_single_preset_as_bare_strings():
    got, _ = _run({"preset": {"preset": {"id": "1", "name": "Gate"}}}, ["presets"])
    assert got["presets"] == [{"id": "1", "name": "Gate"}]


def test_storage_with_unusable_hd_info_is_not_answered():
    assert _run({"harddisk_manage": {"hd_info": None}}, ["storage"])[0] == {}


def test_preset_lists_as_objects_lose_only_presets():
    reply = {"preset": {"preset": {"id": {"a": 1}, "name": {"b": 2}}},
             "led": {"config": {"enabled": "on"}}}
    assert _run(reply, ["presets", "led"])[0] == {"led": {"enabled": "on"}}
