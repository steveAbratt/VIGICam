# VIGICam — TP-Link VIGI/InSight Camera Integration for Home Assistant

A Home Assistant custom integration for TP-Link VIGI and InSight cameras, providing
full local API access beyond basic RTSP streaming — no cloud required.

---

## Tested Cameras

| Model | Type | Notes |
|-------|------|-------|
| VIGI C540V | Outdoor PTZ, 4MP, spotlight + IR | Pan/tilt/zoom, 6 presets tested |
| InSight S245 | Fixed outdoor, 4MP, spotlight + IR | Tamper detection, loitering/scene change via ONVIF |

Other VIGI and InSight models using the same local HTTPS API should also work.
Entity types are created dynamically — only entities the camera actually supports are registered.

---

## Features by Entity Type

### Camera
| Entity | Description |
|--------|-------------|
| Stream | Live RTSP stream (HD `stream1`), accessible in dashboards and automations |

### Switches
| Entity | Description | Cameras |
|--------|-------------|---------|
| Alarm | Master alarm enable/disable | All |
| Alarm Light | Flash the spotlight when an alarm triggers | All with spotlight |
| Alarm Sound | Play the built-in alarm tone when triggered | All with speaker |
| Detection Motion | Enable/disable motion detection | All |
| Detection Person | Enable/disable AI person detection | Most |
| Detection Tamper | Enable/disable camera tamper alert | S245 + others |
| Detection Vehicle | Enable/disable AI vehicle detection | Most |
| Microphone Mute | Mute the camera's microphone | All |
| Speaker Mute | Mute the camera's speaker output | All |
| Status LED | Turn the camera's status LED on/off | All |

### Sensors
| Entity | Description | Category |
|--------|-------------|----------|
| SD Card Used | Storage used as a percentage | — |
| SD Card Total | Total SD card capacity (GB) | — |
| SD Card Free | Free SD card space (GB) | — |
| SD Card Status | Card status string from firmware | — |
| Firmware Version | Camera firmware version | Diagnostic (hidden by default) |
| IP Address | Camera's current IP address | Diagnostic |
| Connection Type | `DHCP` or `Static` | Diagnostic |

### Binary Sensors
| Entity | Trigger | Auto-clears |
|--------|---------|-------------|
| Loop Recording | SD card loop recording is active (polled) | — |
| Motion | Motion detected (ONVIF real-time) | 15 s |
| Person Detected | Person in frame (ONVIF real-time) | 15 s |
| Tamper | Camera tampered/covered (ONVIF real-time) | 15 s |
| Intrusion | Intrusion zone entered (ONVIF real-time) | 15 s |
| Line Crossing | Defined line crossed (ONVIF real-time) | 15 s |
| Smart Detection | Vehicle / sound / loitering / abandoned object / scene change (ONVIF catch-all) | 15 s |

ONVIF binary sensors update in real time via a pull-point subscription — they do not
wait for the 30-second coordinator poll. Detection zones (intrusion areas, line paths)
are configured in the VIGI app or camera web UI, not through this integration.

**Note:** Line Crossing and Smart Detection are two separate sensors for two different
ONVIF topics. Line Crossing fires when configured line crossing zones are triggered.
Smart Detection is a catch-all for vehicle, sound, loitering, abandoned object, and
scene change — all five share a single ONVIF topic and cannot be separated further.


### Numbers
| Entity | Range | Description |
|--------|-------|-------------|
| Speaker Volume | 0–100 | Camera speaker output level |
| Motion Sensitivity | 1–100 | Threshold for motion detection |
| Spotlight Intensity | 1–4 | White-light spotlight brightness |

### Select
| Entity | Options | Description |
|--------|---------|-------------|
| Night Vision Mode | IR Auto, IR Always On, Spotlight Always On, Colour, Off | Switches IR/spotlight behaviour |
| PTZ Preset *(PTZ only)* | All named presets | Move camera to a saved position |

### Buttons *(PTZ cameras only)*
Six jog buttons: **PTZ Pan Left**, **PTZ Pan Right**, **PTZ Tilt Up**, **PTZ Tilt Down**, **PTZ Zoom In**, **PTZ Zoom Out**.
Each press moves the camera for 1 second then stops automatically.
For custom duration, use the `vigicam.ptz` service instead.

---

## PTZ Services

Three services are available for PTZ cameras. Find them in **Developer Tools → Services**
or call them from automations and scripts.

### `vigicam.ptz` — Continuous move

```yaml
service: vigicam.ptz
data:
  entity_id: camera.vigi_c540v_stream
  direction: right          # left / right / up / down / zoom_in / zoom_out
  speed: 0.4                # 0.1–1.0 (default 0.3)
  duration: 3               # seconds before auto-stop (optional)
```

If `duration` is omitted the camera moves until `vigicam.ptz_stop` is called.

### `vigicam.ptz_stop` — Stop movement

```yaml
service: vigicam.ptz_stop
data:
  entity_id: camera.vigi_c540v_stream
```

### `vigicam.goto_preset` — Go to a named preset

```yaml
service: vigicam.goto_preset
data:
  entity_id: camera.vigi_c540v_stream
  preset: "Full Stable Yard"    # exactly as shown in the PTZ Preset select entity
```

This is the recommended way to move to a preset from an automation, especially when
there are many presets. The preset name must match exactly (case-sensitive).

---

## PTZ Dashboard Controls

HA's camera card shows the live stream but doesn't overlay PTZ controls.
The cleanest approach is a vertical stack combining the camera stream with a direction
pad below it. Copy this YAML into a Manual card in your dashboard editor:

```yaml
type: vertical-stack
cards:
  - type: camera
    entity: camera.vigi_c540v_stream
    live_view: true

  - type: grid
    columns: 3
    square: true
    cards:
      - type: button
        entity: button.vigi_c540v_ptz_left
        show_name: false
        icon: mdi:pan-left
      - type: button
        entity: button.vigi_c540v_ptz_up
        show_name: false
        icon: mdi:pan-up
      - type: button
        entity: button.vigi_c540v_ptz_right
        show_name: false
        icon: mdi:pan-right
      - type: button
        entity: button.vigi_c540v_ptz_zoom_out
        show_name: false
        icon: mdi:magnify-minus
      - type: button
        entity: button.vigi_c540v_ptz_down
        show_name: false
        icon: mdi:pan-down
      - type: button
        entity: button.vigi_c540v_ptz_zoom_in
        show_name: false
        icon: mdi:magnify-plus

  - type: entity
    entity: select.vigi_c540v_ptz_preset
    name: Go to preset
```

> Replace `vigi_c540v` with your camera's entity slug. To find it: open the device page
> in HA, click any button entity, and copy the entity ID prefix.

---

## Installation via HACS

1. Open **HACS** in Home Assistant
2. Click **⋮ (three dots)** in the top-right → **Custom repositories**
3. Enter `https://github.com/steveAbratt/VIGICam` as the repository URL
4. Set type to **Integration** and click **Add**
5. Find **VIGI & InSight Cameras** in the HACS integrations list and click **Download**
6. **Restart Home Assistant**

## Manual Installation

Copy the `custom_components/vigicam/` folder into your HA config directory:

```
/config/custom_components/vigicam/
```

Restart Home Assistant after copying.

---

## Adding a Camera

1. Go to **Settings → Devices & Services**
2. Click **+ Add Integration**
3. Search for **VIGI**
4. Enter:
   - **IP Address** — the camera's local IP (find it in your router or the VIGI app)
   - **Username** — `admin` (default)
   - **Password** — set during camera setup in the VIGI app
5. Click **Submit** — the integration validates credentials before saving

Repeat for each camera. Each camera is a separate device with its own entity set.

---

## SD Card

Storage sensors require a functioning SD card. Used %, total, and free values are
calculated from the camera's accurate byte fields rather than its `percent` field,
which is inconsistent across firmware versions.

If loop recording is active and the card is full, the **SD Card Used** sensor will
correctly report ~100% and **Loop Recording** will be `On`.

---

## ONVIF Events

Real-time detection binary sensors use an ONVIF pull-point subscription that starts
automatically when the integration loads. The subscription renews every hour and
reconnects automatically after errors.

Detection zone configuration (intrusion areas, line crossing paths) is done in the
**VIGI app** or the camera's **web UI** — this integration reports when they fire
but cannot configure the zones themselves.

**Smart Detection** covers multiple event types that cannot be separated at the ONVIF
level: vehicle, sound, loitering, abandoned object, and scene change all trigger the
same `IsTPSmartEvent` topic.

---

## Dependencies

`pycryptodome` is installed automatically by Home Assistant from the manifest.
No other Python dependencies are required.

---

## Attribution & Acknowledgements

- **[JurajNyiri/HomeAssistant-Tapo-Control](https://github.com/JurajNyiri/HomeAssistant-Tapo-Control)** (MIT)
  — entity architecture, coordinator pattern, and platform structure inspiration.
  VIGI cameras are not supported upstream by policy, which motivated this integration.

- **[JurajNyiri/pytapo](https://github.com/JurajNyiri/pytapo)** (MIT)
  — authentication flow reference. VIGI cameras differ slightly (stok token at top level).

- **[yetanothercarbot/vigi_camera_lighting](https://github.com/yetanothercarbot/vigi_camera_lighting)**
  — useful reference for VIGI spotlight/night-vision endpoint behaviour.

- The **Home Assistant** developer community for integration architecture patterns.

MIT licensed. See [LICENSE](LICENSE).
