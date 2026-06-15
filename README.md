# VIGICam — TP-Link VIGI/InSight Camera Integration for Home Assistant

Local API integration for TP-Link VIGI and InSight cameras. Full entity control,
real-time detection events, PTZ, and TTS announcements through the camera speaker —
no cloud required.

**→ [Full entity & feature reference](docs/USAGE.md)**

---

## Tested Cameras

| Model | Type |
|-------|------|
| VIGI C540V | Outdoor PTZ, 4MP, spotlight + IR |
| InSight S245 | Fixed outdoor, 4MP, spotlight + IR, tamper detection |

Other VIGI and InSight models using the same local HTTPS API should also work.
Entities are created dynamically — only capabilities the camera actually supports appear.

---

## Features

- **Live stream** — RTSP HD stream in dashboards and automations
- **Detection events** — real-time motion, person, vehicle, tamper, intrusion, line crossing, smart detection via ONVIF (fires within seconds, not the 30 s poll cycle)
- **Alarm control** — enable/disable alarm, sound and light independently, set repeat count, trigger or stop manually
- **Camera announcements** — speak any text through the camera speaker via `vigicam.speak` (TTS → format conversion → upload → play, all automatic)
- **PTZ control** — pan/tilt/zoom buttons, named presets, continuous move service
- **Night vision** — switch between IR auto, IR always on, spotlight, colour, off
- **Audio management** — upload custom sounds, play on demand, delete slots
- **Storage monitoring** — SD card used %, free space, total capacity, status, loop recording state
- **Diagnostics** — firmware version, IP address, connection type

---

## Installation via HACS

1. Open **HACS** in Home Assistant
2. Click **⋮** → **Custom repositories**
3. Enter `https://github.com/steveAbratt/VIGICam` — type: Integration
4. Find **VIGI & InSight Cameras** in HACS and click **Download**
5. Restart Home Assistant

## Manual Installation

Copy `custom_components/vigicam/` into your HA config directory and restart.

---

## Adding a Camera

1. **Settings → Devices & Services → + Add Integration**
2. Search for **VIGI**
3. Enter the camera's IP address, username (`admin`), and password
4. Click **Submit** — credentials are validated before saving

Repeat for each camera. Each appears as a separate HA device.

---

## Camera Announcements (TTS)

Speak a message through the camera's speaker from any automation:

```yaml
service: vigicam.speak
data:
  entity_id: camera.vigi_c540v_stream
  message: "Person detected at the stables — {{ now().strftime('%-I:%M %p') }}"
  tts_engine: tts.cloud
  language: en-GB
```

Handles everything automatically: TTS generation → resampled to 8 kHz mono WAV
via ffmpeg → uploaded to camera → played. Works with any configured HA TTS engine.

**Limit:** Keep messages under ~10 seconds (camera hard limit: 15 s / 256 KB).

### Blueprint

A ready-made automation blueprint turns this into a simple form:

1. **Settings → Automations → Blueprints → Import Blueprint**
2. Paste:
   ```
   https://raw.githubusercontent.com/steveAbratt/VIGICam/main/blueprints/automation/vigicam/camera_announce.yaml
   ```
3. Click **Create Automation** — fill in trigger, camera, message, TTS engine. Done.

---

## PTZ Services

```yaml
service: vigicam.ptz
data:
  entity_id: camera.vigi_c540v_stream
  direction: right   # left / right / up / down / zoom_in / zoom_out
  speed: 0.4
  duration: 3        # seconds (omit to move until vigicam.ptz_stop is called)
```

```yaml
service: vigicam.goto_preset
data:
  entity_id: camera.vigi_c540v_stream
  preset: "Full Stable Yard"   # name exactly as it appears in the PTZ Preset select entity
```

---

## Full Documentation

For a plain-language explanation of every entity, button, switch, sensor, and service
— including what they actually do and when to use them — see:

**[docs/USAGE.md](docs/USAGE.md)**

---

## Dependencies

`pycryptodome` and `ffmpeg` are handled automatically by Home Assistant from the manifest.

---

## Attribution

- **[JurajNyiri/HomeAssistant-Tapo-Control](https://github.com/JurajNyiri/HomeAssistant-Tapo-Control)** (MIT) — entity architecture and coordinator pattern inspiration
- **[JurajNyiri/pytapo](https://github.com/JurajNyiri/pytapo)** (MIT) — authentication flow reference
- **[yetanothercarbot/vigi_camera_lighting](https://github.com/yetanothercarbot/vigi_camera_lighting)** — spotlight/night-vision endpoint reference

MIT licensed. See [LICENSE](LICENSE).
