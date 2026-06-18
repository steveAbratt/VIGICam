# VIGICam — TP-Link VIGI & InSight Cameras for Home Assistant

Full local control of TP-Link VIGI and InSight cameras — real-time smart detection, PTZ,
two-way audio announcements, image configuration, and deep camera management.
No cloud account. No subscription. No dependency on TP-Link servers.

**→ [Full entity & feature reference](docs/USAGE.md)**

---

## Why VIGI and InSight cameras?

Most home security cameras are a compromise: consumer-grade hardware, cloud-dependent
software, and WiFi connectivity that drops out the moment you need it most. VIGI and
InSight cameras take a different approach.

**Built to stay put.** PoE-powered and hardwired, these cameras draw power and data
through a single cable. No batteries to change, no WiFi negotiation on every boot, no
coverage gap when the router reboots. Once installed, they work indefinitely.

**Business-spec hardware at competitive prices.** Metal housings, proper IP66/67 weather
ratings, and firmware update paths you'd expect from enterprise networking gear — TP-Link's
background in business networking shows. The InSight line is their higher-specification
tier: more detection capability, tamper detection, and construction quality suited to
demanding outdoor installations.

**AI on the camera, not a server.** Person, vehicle, intrusion zone, line crossing, audio
anomaly, and loitering detection all run on the device itself. You get smart detection
events without a GPU, without a separate NVR, and without sending footage off-premises.

**Ubiquiti-class features without Ubiquiti pricing.** Configurable detection zones, PTZ
with named presets, two-way audio, smart capture, PoE infrastructure compatibility — the
feature set competes with cameras three times the price.

---

## Standalone or with Frigate — your choice

**Standalone (no NVR required):** VIGICam uses the camera's own on-device AI for
detection events and Smart Frame image captures. Motion, person detection, intrusion
zones, and line crossing all fire in real time through ONVIF, directly into Home
Assistant automations. This is a capable, self-contained setup that needs nothing beyond
the camera and Home Assistant.

**Alongside Frigate *(coming in v0.6)*:** When you add Frigate for recording and
advanced detection, VIGICam steps back gracefully — streams and detection hand off to
Frigate while VIGICam retains exclusive control over the functions Frigate can't reach:
PTZ, spotlight, image tuning, announcements, and alarms. Entities merge onto the Frigate
device automatically.

---

## Tested Cameras

| Model | Type | Notes |
|-------|------|-------|
| VIGI C540V | Outdoor PTZ, 4MP, spotlight + IR | Full feature set including PTZ |
| InSight S245 | Fixed outdoor, 4MP, spotlight + IR, tamper | InSight higher-spec line |

Other VIGI and InSight models using the same local HTTPS API should work. Entities are
created dynamically — only capabilities the camera actually reports appear in HA.

---

## Features

### Detection & events
- **Real-time detection** — motion, person, tamper, intrusion, line crossing, smart
  detection fire within seconds via ONVIF pull-point subscription — not the 30 s poll cycle
- **Split detection sensors** — individual binary sensors for vehicle, audio anomaly,
  loitering, scene change, object left/taken, area entry, area exit
  *(requires [OpenAPI](#openapi--extended-detection--sensors))*
- **Smart Frame captures** — last-detection snapshot image entity per camera, updated
  each time a detection event fires

### Camera controls
- **Spotlight** — on/off with brightness control
- **Night vision** — switch between IR auto, IR always on, spotlight, colour, off
- **Alarm control** — enable/disable alarm, sound and light independently, set repeat
  count, trigger or stop manually
- **PTZ** — pan/tilt/zoom buttons, named presets, continuous move, absolute positioning,
  save/delete presets *(absolute position and preset management require
  [OpenAPI](#openapi--extended-detection--sensors))*

### Audio
- **Camera announcements** — speak any text through the camera speaker via
  `vigicam.speak` (TTS → resampled WAV → upload → play, fully automatic)
- **Play pre-recorded files** — play any audio file (WAV, MP3, OGG…) via
  `vigicam.play_file`; accepts HA media browser URLs, `www/` paths, file paths,
  or external URLs
- **Custom sound management** — upload, play on demand, and delete custom audio slots

### Monitoring & diagnostics
- **Live stream** — RTSP HD stream in dashboards and automations
- **Storage monitoring** — SD card used %, free space, total capacity, status, loop
  recording state, recording duration, oldest recording, capacity remaining
  *(extended sensors require [OpenAPI](#openapi--extended-detection--sensors))*
- **Diagnostics** — firmware version, IP address, connection type, MAC address, uptime
  *(uptime requires [OpenAPI](#openapi--extended-detection--sensors))*

---

## Installation via HACS

1. Open **HACS** in Home Assistant
2. Click **⋮** → **Custom repositories**
3. Enter `https://github.com/steveAbratt/VIGICam` — type: Integration
4. Find **VIGI & InSight Cameras** in HACS and click **Download**
5. Restart Home Assistant

## Manual Installation

Copy `custom_components/vigicam/` into your HA `config/custom_components/` directory
and restart.

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

**What it unlocks:** Vehicle / Audio Anomaly / Loitering / Scene Change / Object Left
or Taken / Area Entry / Area Exit binary sensors, extended SD card sensors (recording
duration, oldest recording, capacity remaining), the Uptime diagnostic, and the
`vigicam.ptz_move_to` / `vigicam.ptz_save_preset` / `vigicam.ptz_delete_preset`
services.

> Requires firmware 2.1.x or later. If the OpenAPI menu is missing, update the camera
> firmware via the VIGI app or camera web UI.

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

Handles everything automatically: TTS generation → resampled to 8 kHz mono WAV via
ffmpeg → uploaded to camera → played. Works with any configured HA TTS engine.

**Limit:** Keep messages under ~10 seconds (camera hard limit: 15 s / 256 KB).

### Blueprint

1. **Settings → Automations → Blueprints → Import Blueprint**
2. Paste:
   ```
   https://raw.githubusercontent.com/steveAbratt/VIGICam/main/blueprints/automation/vigicam/camera_announce.yaml
   ```
3. Click **Create Automation** — fill in trigger, camera, message, TTS engine, repeat
   count. Done.

---

## Playing Pre-recorded Files

Play any audio file through the camera speaker using `vigicam.play_file`. Accepts WAV,
MP3, OGG, or any other format ffmpeg can read — the integration converts automatically.

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

Media browser URLs (`/media/local/`) and HA www URLs (`/local/`) are resolved to file
paths automatically. External URLs are fetched directly.

### Blueprint

1. **Settings → Automations → Blueprints → Import Blueprint**
2. Paste:
   ```
   https://raw.githubusercontent.com/steveAbratt/VIGICam/main/blueprints/automation/vigicam/camera_play_file.yaml
   ```

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
  preset: "Full Stable Yard"
```

The following require [OpenAPI enabled](#openapi--extended-detection--sensors):

```yaml
service: vigicam.ptz_move_to
data:
  entity_id: camera.vigi_c540v_stream
  pan: 120.0
  tilt: -10.0
  zoom: 1.0
```

```yaml
service: vigicam.ptz_save_preset
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

For a plain-language explanation of every entity, button, switch, sensor, and service —
including what they actually do, automation examples, and dashboard tips — see:

**[docs/USAGE.md](docs/USAGE.md)**

---

## Dependencies

`pycryptodome` and `ffmpeg` are handled automatically by Home Assistant from the
manifest.

---

## Attribution

- **[JurajNyiri/HomeAssistant-Tapo-Control](https://github.com/JurajNyiri/HomeAssistant-Tapo-Control)** (MIT) — entity architecture and coordinator pattern inspiration
- **[JurajNyiri/pytapo](https://github.com/JurajNyiri/pytapo)** (MIT) — authentication flow reference
- **[yetanothercarbot/vigi_camera_lighting](https://github.com/yetanothercarbot/vigi_camera_lighting)** — spotlight/night-vision endpoint reference
- **[Komzpa/ha-vigi-control](https://github.com/Komzpa/ha-vigi-control)** — image control API field reference and Frigate integration approach

MIT licensed. See [LICENSE](LICENSE).
