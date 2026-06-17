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
- **Detection events** — real-time motion, person, tamper, intrusion, line crossing, smart detection via ONVIF (fires within seconds, not the 30 s poll cycle)
- **Split detection sensors** — individual binary sensors for vehicle, audio anomaly, loitering, scene change, object left/taken, area entry, area exit *(requires [OpenAPI](#openapi--extended-detection--sensors))*
- **Alarm control** — enable/disable alarm, sound and light independently, set repeat count, trigger or stop manually
- **Camera announcements** — speak any text through the camera speaker via `vigicam.speak` (TTS → format conversion → upload → play, all automatic)
- **Play pre-recorded files** — play any audio file (WAV, MP3, OGG…) via `vigicam.play_file`; accepts HA media browser URLs, `www/` URLs, file paths, or external URLs
- **PTZ control** — pan/tilt/zoom buttons, named presets, continuous move, absolute positioning, save/delete presets *(absolute position and preset management require [OpenAPI](#openapi--extended-detection--sensors))*
- **Night vision** — switch between IR auto, IR always on, spotlight, colour, off
- **Audio management** — upload custom sounds, play on demand, delete slots
- **Storage monitoring** — SD card used %, free space, total capacity, status, loop recording state; recording duration, oldest recording, capacity remaining *(extended sensors require [OpenAPI](#openapi--extended-detection--sensors))*
- **Diagnostics** — firmware version, IP address, connection type, uptime *(uptime requires [OpenAPI](#openapi--extended-detection--sensors))*

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

## OpenAPI — extended detection & sensors

Several features require the camera's local OpenAPI (HTTPS port 20443) to be enabled:

1. Open the camera's web UI (`http://<camera-ip>`) and log in
2. Go to **Settings → Network → OpenAPI** and enable it
3. Reload the integration — the additional entities appear automatically

**What it unlocks:** Vehicle / Audio Anomaly / Loitering / Scene Change / Object Left or Taken / Area Entry / Area Exit binary sensors, SD card recording duration and capacity sensors, Uptime diagnostic, and the `vigicam.ptz_move_to` / `vigicam.ptz_save_preset` / `vigicam.ptz_delete_preset` services.

> Requires firmware 2.1.x or later. If the OpenAPI menu is missing, update the camera firmware via the VIGI app or camera web UI.

---

## Camera Announcements (TTS)

Speak a message through the camera's speaker from any automation:

```yaml
service: vigicam.speak
data:
  entity_id: camera.vigi_c540v_stream
  message: "Person detected at the front door — {{ now().strftime('%-I:%M %p') }}"
  tts_engine: tts.cloud
  language: en-GB
  times: 2      # optional — repeat the announcement N times
  pause: 1.5    # optional — seconds between repeats
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
3. Click **Create Automation** — fill in trigger, camera, message, TTS engine, repeat count. Done.

---

## Playing Pre-recorded Files

Play any audio file through the camera speaker using `vigicam.play_file`. Accepts WAV, MP3, OGG, or any other format ffmpeg can read — the integration converts it automatically.

**From the HA media browser** (upload via sidebar → Media → My media):

```yaml
service: vigicam.play_file
data:
  entity_id: camera.vigi_c540v_stream
  url: http://192.168.1.x:8123/media/local/alert.wav
  times: 2
  pause: 1.0
```

**From a file path on the HA host:**

```yaml
url: /config/media/alert.wav
```

Media browser URLs (`/media/local/`) and HA www URLs (`/local/`) are automatically resolved to file paths — no token or authentication setup required. External URLs are fetched directly.

**Limit:** 15 seconds / 256 KB after conversion to 8 kHz mono WAV.

### Blueprint

A ready-made blueprint for this too — upload your file to the media browser, paste the URL, done:

1. **Settings → Automations → Blueprints → Import Blueprint**
2. Paste:
   ```
   https://raw.githubusercontent.com/steveAbratt/VIGICam/main/blueprints/automation/vigicam/camera_play_file.yaml
   ```
3. Click **Create Automation** — fill in trigger, camera, audio URL, repeat count. Done.

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

The following services require [OpenAPI enabled](#openapi--extended-detection--sensors):

```yaml
service: vigicam.ptz_move_to      # move to an absolute pan/tilt/zoom position
data:
  entity_id: camera.vigi_c540v_stream
  pan: 120.0
  tilt: -10.0
  zoom: 1.0
```

```yaml
service: vigicam.ptz_save_preset  # save current position as a named preset
data:
  entity_id: camera.vigi_c540v_stream
  name: "Entrance View"
```

```yaml
service: vigicam.ptz_delete_preset
data:
  entity_id: camera.vigi_c540v_stream
  name: "Old Position"
```

---

## Full Documentation

For a plain-language explanation of every entity, button, switch, sensor, and service
— including what they actually do, automation examples, and dashboard tips — see:

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
