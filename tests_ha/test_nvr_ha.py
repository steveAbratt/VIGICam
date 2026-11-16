"""NVR entry end to end in Home Assistant, with the NVR client faked."""
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.vigicam.const import (
    CONF_DEVICE_TYPE, CONF_WEBHOOK_ID, DEVICE_TYPE_NVR, DOMAIN, EVENT_NVR,
)

DEVICES = [
    {"id": 1, "name": "Front Door", "alias": "IPC1", "online": "1", "ip": "192.168.0.2"},
    {"id": 2, "name": "Yard", "alias": "IPC2", "online": "0", "ip": "192.168.0.3"},
]
MODULES = {m: 1 for m in (
    "channel_management", "disk", "event", "system", "alarm_device", "active_defense",
    "alarm_output",
)}


@pytest.fixture
def nvr():
    """Patch every VIGINVR network call."""
    p = "custom_components.vigicam.nvr_api.VIGINVR"
    with patch(f"{p}.login", AsyncMock()), \
         patch(f"{p}.close", AsyncMock()), \
         patch(f"{p}.module_list", AsyncMock(return_value=MODULES)), \
         patch(f"{p}.added_devices", AsyncMock(return_value=DEVICES)) as devs, \
         patch(f"{p}.disks", AsyncMock(return_value=[
             {"id": 1, "slot": 0, "total_space": "1863 GB", "free_space": "698 GB",
              "status": "normal", "rw_attr": "rw"}])), \
         patch(f"{p}.get_alarm_mode", AsyncMock(return_value="alarm")), \
         patch(f"{p}.get_arm_status", AsyncMock(return_value="disarmed")), \
         patch(f"{p}.set_arm_status", AsyncMock()) as set_arm, \
         patch(f"{p}.event_servers", AsyncMock(return_value=[
             {"id": 1, "ip_or_domain": "10.0.0.5", "port": 80, "url": "/other",
              "protocol": "HTTP", "picture_switch": "off"}])), \
         patch(f"{p}.set_event_server", AsyncMock()) as set_srv, \
         patch(f"{p}.snapshot", AsyncMock(return_value=b"\xff\xd8jpeg")):
        yield {"set_event_server": set_srv, "set_arm": set_arm, "devices": devs}


async def test_config_flow_menu_to_nvr(hass: HomeAssistant, nvr):
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] is FlowResultType.MENU
    assert result["menu_options"] == ["camera", "nvr"]
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"next_step_id": "nvr"})
    assert result["step_id"] == "nvr"
    with patch("custom_components.vigicam.async_setup_entry", AsyncMock(return_value=True)):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {"host": "192.168.0.240", "username": "admin", "password": "pw", "name": "Home NVR"},
        )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Home NVR"
    assert result["data"][CONF_DEVICE_TYPE] == DEVICE_TYPE_NVR
    assert result["data"][CONF_WEBHOOK_ID]


async def test_camera_path_still_reachable(hass: HomeAssistant):
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"next_step_id": "camera"})
    assert result["type"] is FlowResultType.FORM and result["step_id"] == "camera"


async def test_setup_entities_and_pushed_event(hass: HomeAssistant, hass_client_no_auth, nvr):
    await async_setup_component(hass, "http", {"http": {}})
    hass.config.internal_url = "http://192.168.1.10:8123"
    entry = MockConfigEntry(
        domain=DOMAIN, title="Home NVR", unique_id="nvr_192.168.0.240",
        data={CONF_DEVICE_TYPE: DEVICE_TYPE_NVR, "host": "192.168.0.240", "username": "admin",
              "password": "pw", "name": "Home NVR", CONF_WEBHOOK_ID: "hook123"},
    )
    entry.add_to_hass(hass)
    with patch("homeassistant.components.stream.async_setup", AsyncMock(return_value=True)), \
         patch("homeassistant.components.ffmpeg.async_setup", AsyncMock(return_value=True)):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    # Slot 1 belongs to something else, so slot 2 is used and pointed at the webhook.
    nvr["set_event_server"].assert_awaited_once_with(
        2, "192.168.1.10", 8123, "/api/webhook/hook123", https=False, pictures=False
    )

    states = {s.entity_id: s for s in hass.states.async_all()}
    assert hass.states.get("camera.front_door") is not None
    assert hass.states.get("camera.yard").state == "unavailable"  # channel offline
    assert hass.states.get("binary_sensor.front_door_online").state == "on"
    assert hass.states.get("binary_sensor.yard_online").state == "off"
    assert hass.states.get("binary_sensor.front_door_motion").state == "off"
    assert hass.states.get("sensor.home_nvr_disk_1_free_space").state == "698.0"
    assert hass.states.get("sensor.home_nvr_cameras_online").state == "1"
    assert hass.states.get("switch.home_nvr_alarm_mode").state == "on"
    assert hass.states.get("switch.front_door_active_defense").state == "off"
    assert "binary_sensor.front_door_tamper" not in states  # disabled by default

    # Toggle active defense.
    await hass.services.async_call(
        "switch", "turn_on", {"entity_id": "switch.front_door_active_defense"}, blocking=True
    )
    nvr["set_arm"].assert_awaited_with(1, True)

    # A pushed line crossing by a person on channel 1.
    fired = []
    hass.bus.async_listen(EVENT_NVR, lambda e: fired.append(e.data))
    client = await hass_client_no_auth()
    boundary = "--VIGI-OPENAPI-MSG-PUSH"
    body = (
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"event\"\r\n\r\n"
        '{"device_id":"x","device_name":"Home%20NVR","messages":[{"type":1,"sub_type":[4,101],'
        '"channel":1,"localtime":"2026-11-17 07:40:00"}]}\r\n'
        f"--{boundary}--\r\n"
    )
    resp = await client.post(
        "/api/webhook/hook123", data=body.encode(),
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
    )
    assert resp.status == 200
    await hass.async_block_till_done()
    assert hass.states.get("binary_sensor.front_door_line_crossing").state == "on"
    assert hass.states.get("binary_sensor.front_door_person_detected").state == "on"
    assert hass.states.get("binary_sensor.front_door_motion").state == "off"
    assert fired and fired[0]["channel"] == 1 and fired[0]["events"] == ["line_cross", "person"]
    assert fired[0]["nvr"] == "Home NVR"

    # Snapshot through the NVR.
    from homeassistant.components.camera import async_get_image
    image = await async_get_image(hass, "camera.front_door")
    assert image.content == b"\xff\xd8jpeg"

    assert await hass.config_entries.async_unload(entry.entry_id)


async def _setup(hass, entry):
    with patch("homeassistant.components.stream.async_setup", AsyncMock(return_value=True)), \
         patch("homeassistant.components.ffmpeg.async_setup", AsyncMock(return_value=True)):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()


def _entry(**options):
    return MockConfigEntry(
        domain=DOMAIN, title="Home NVR", unique_id="nvr_192.168.0.240", options=options,
        data={CONF_DEVICE_TYPE: DEVICE_TYPE_NVR, "host": "192.168.0.240", "username": "admin",
              "password": "pw", "name": "Home NVR", CONF_WEBHOOK_ID: "hook123"},
    )


async def test_event_sensor_turns_off_after_hold(hass: HomeAssistant, nvr):
    from datetime import timedelta
    from homeassistant.util import dt as dt_util
    from pytest_homeassistant_custom_component.common import async_fire_time_changed
    from custom_components.vigicam.nvr_events import handle_event

    hass.config.internal_url = "http://192.168.1.10:8123"
    entry = _entry(nvr_event_hold=5)
    entry.add_to_hass(hass)
    await _setup(hass, entry)
    handle_event(hass, entry.entry_id, "Home NVR",
                 {"messages": [{"type": 1, "sub_type": [2], "channel": 1}]})
    await hass.async_block_till_done()
    assert hass.states.get("binary_sensor.front_door_motion").state == "on"
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=6))
    await hass.async_block_till_done()
    assert hass.states.get("binary_sensor.front_door_motion").state == "off"


async def test_slots_full_raises_repair_and_leaves_nvr_alone(hass: HomeAssistant, nvr):
    from homeassistant.helpers import issue_registry as ir

    hass.config.internal_url = "http://192.168.1.10:8123"
    full = [{"id": i, "ip_or_domain": f"10.0.0.{i}", "port": 80, "url": "/x"} for i in (1, 2, 3)]
    entry = _entry()
    entry.add_to_hass(hass)
    with patch("custom_components.vigicam.nvr_api.VIGINVR.event_servers", AsyncMock(return_value=full)):
        await _setup(hass, entry)
    nvr["set_event_server"].assert_not_awaited()
    assert ir.async_get(hass).async_get_issue(DOMAIN, f"nvr_event_server_{entry.entry_id}")


async def test_existing_slot_reused_and_removed_on_delete(hass: HomeAssistant, nvr):
    hass.config.internal_url = "http://192.168.1.10:8123"
    ours = [{"id": 3, "ip_or_domain": "192.168.1.10", "port": 8123, "url": "/api/webhook/hook123",
             "protocol": "HTTP", "picture_switch": "off"}]
    entry = _entry()
    entry.add_to_hass(hass)
    with patch("custom_components.vigicam.nvr_api.VIGINVR.event_servers", AsyncMock(return_value=ours)), \
         patch("custom_components.vigicam.nvr_api.VIGINVR.delete_event_server", AsyncMock()) as delete:
        await _setup(hass, entry)
        nvr["set_event_server"].assert_not_awaited()  # already correct, nothing written
        assert await hass.config_entries.async_remove(entry.entry_id)
        await hass.async_block_till_done()
    delete.assert_awaited_once_with(3)


async def test_new_channel_triggers_reload(hass: HomeAssistant, nvr):
    hass.config.internal_url = "http://192.168.1.10:8123"
    entry = _entry(nvr_event_push=False)
    entry.add_to_hass(hass)
    await _setup(hass, entry)
    assert hass.states.get("binary_sensor.front_door_motion") is None  # events off
    nvr["devices"].return_value = DEVICES + [{"id": 3, "name": "Garage", "online": "1"}]
    coordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]
    with patch("homeassistant.components.stream.async_setup", AsyncMock(return_value=True)), \
         patch("homeassistant.components.ffmpeg.async_setup", AsyncMock(return_value=True)):
        await coordinator.async_refresh()
        await hass.async_block_till_done()
    assert hass.states.get("camera.garage") is not None


async def test_nvr_options_flow(hass: HomeAssistant, nvr):
    entry = _entry()
    entry.add_to_hass(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["step_id"] == "nvr_options"
    with patch("custom_components.vigicam.async_setup_entry", AsyncMock(return_value=True)):
        result = await hass.config_entries.options.async_configure(
            result["flow_id"],
            {"stream_use_main": True, "nvr_event_push": True, "nvr_event_hold": 30,
             "nvr_event_host": " 192.168.1.10:8123 "},
        )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options["nvr_event_host"] == "192.168.1.10:8123"
