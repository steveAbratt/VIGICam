# VIGICam — Implementation Plan

This document is the single source of truth for what has been built, what is planned,
and how decisions were made. It is written for both contributors and Claude sessions —
any new session should read this file before starting work.

---

## How to resume a Claude session

1. Read this file fully.
2. Read `docs/camera_api_research.md` for the low-level API detail.
3. Check `CHANGELOG.md` for the most recent released version.
4. Check git status — any untracked/modified files indicate in-progress work.
5. The current stable release is tracked in `custom_components/vigicam/manifest.json`.

---

## Current state — v0.4.0 (stable)

### Protocols in use

| Protocol | Port | Purpose |
|---|---|---|
| Local JSON API (HTTPS) | 443 | All control: switches, numbers, PTZ, audio, SD card, device info |
| ONVIF (HTTP) | 80 | Real-time detection events (sub-second push subscription) |
| RTSP | 554 | Live video stream, dashboard thumbnails, RTSP snapshot fallback |
| WebSocket (WSS) | 8443 | Smart Frame image download |
| OpenAPI (HTTPS) | 20443 | **New — optional, must be enabled in camera settings** |

### Current entities (all cameras)

**Switches**
- Alarm — master alarm enable
- Alarm Light — flashing spotlight on alarm
- Alarm Sound — audio on alarm
- Detection Motion — motion detection on/off
- Detection Person — AI person detection on/off
- Detection Vehicle — vehicle detection on/off (switch only; event fires as Smart Detection via ONVIF)
- Detection Tamper — tamper detection on/off
- Microphone Mute — mute mic input
- Speaker Mute — mute speaker output
- Status LED — front LED on/off

**Buttons**
- Alarm Trigger — manually fire alarm
- Alarm Stop — stop in-progress alarm
- PTZ Pan Left / Pan Right / Tilt Up / Tilt Down / Zoom In / Zoom Out *(PTZ cameras only)*

**Numbers**
- Speaker Volume (0–100)
- Motion Sensitivity (0–100)
- Spotlight Intensity (1–4)
- Alarm Sound Repetitions (1–50)

**Select**
- Night Vision Mode (IR Auto / IR Always On / Spotlight Always On / Colour / Off)
- PTZ Preset *(PTZ cameras only)*

**Sensors**
- SD Card Used % — percentage full
- SD Card Total — total capacity in GB
- SD Card Free — free capacity in GB
- SD Card Status — camera-reported status (normal / full / none)
- Firmware Version *(diagnostic, hidden by default)*
- IP Address *(diagnostic, hidden by default)*
- Connection Type *(diagnostic, hidden by default)*

**Binary sensors** (real-time via ONVIF)
- Motion
- Person Detected
- Tamper
- Intrusion
- Line Crossing
- Smart Detection ← catch-all: vehicle + sound + loitering + scene change (cannot be split at ONVIF level)
- Loop Recording *(polled, 30s cycle)*

**Image entities**
- Last Detection — Smart Frame (AI-cropped) on supported cameras, RTSP still fallback otherwise

**Camera**
- `<name> Stream` — live RTSP stream

### Capability flags (stored in entry data at setup)
- `has_ptz` — camera supports PTZ (detected via ONVIF profiles)
- `has_smart_frames` — camera supports Smart Frame SD capture (detected via `get_media_list` probe)
- `has_sd_card` — SD card is present and usable (detected via `getSdCardStatus` on JSON API; status `"none"` or error → False)
- `has_openapi` — camera has OpenAPI enabled and reachable on port 20443
- `has_nvr` — camera reports an active NVR connection (investigated in Phase 3; flag reserved)

---

## What the OpenAPI adds

Port 20443, HTTPS. Auth: `doAuth` two-step SHA-256 flow → stok. Each request needs a
fresh TCP connection (camera closes after each response). Stok expires after 30 minutes.

See `docs/camera_api_research.md` — OpenAPI section — for the full auth flow and
probe results from both cameras.

### New capability flag
- `has_openapi` — camera has OpenAPI enabled and reachable on port 20443

### New entities / features the OpenAPI unlocks

**Split-out detection binary sensors** (via `subscribeMsg` — OpenAPI event push)

These replace the "Smart Detection" catch-all for cameras with OpenAPI enabled:

| New entity | Was previously | OpenAPI event name |
|---|---|---|
| Vehicle Detected | Bundled in Smart Detection | `VehicleDetection` |
| Audio Anomaly | Bundled in Smart Detection | `AudioAnomalyDetection` |
| Loitering | Bundled in Smart Detection | `LoiterDetection` |
| Scene Change | Bundled in Smart Detection | `SceneChangeDetection` |
| Object Left/Taken | Bundled in Smart Detection | `DropAndTakeDetection` |
| Area Entry | Bundled in Smart Detection | `AreaEntryDetection` |
| Area Exit | Bundled in Smart Detection | `AreaLeaveDetection` |

When OpenAPI is enabled, Smart Detection becomes a true catch-all fallback for any
event not covered by the above. When OpenAPI is disabled, Smart Detection continues to
fire for all of the above as before — no regression.

**Richer SD card sensors** (via `getSdCardStatus`)

Only registered when `has_sd_card=True`. The existing v0.4.0 SD card sensors (Used %, Total, Free,
Status) will also be gated on `has_sd_card` in v0.5.0 — cameras on NVR-only storage won't see
irrelevant "SD Card: None" clutter.

| New entity | Description | Default visibility |
|---|---|---|
| SD Card Record Duration | Total hours of video stored | Visible |
| SD Card Oldest Recording | Datetime of earliest stored clip | Visible |
| SD Card Record Capacity | How much more recording time remains | Diagnostic |
| SD Card Video Space Total | Total GB allocated for video | Diagnostic |
| SD Card Video Space Free | Free GB for video | Diagnostic |

**NVR diagnostics** (investigated Phase 3)

When a camera is managed by a TP-Link NVR, useful diagnostic info may be available (NVR IP,
connection state, last seen). Methods to probe: `getNVRInfo`, network status calls. If found,
add `has_nvr` flag and a diagnostic-hidden "NVR Connection" sensor. Low priority — useful but
not essential.

**PTZ services** (via `motorMove`, `setPresetPoint`, `removePresetPoint`)

| New service | Description |
|---|---|
| `vigicam.ptz_move_to` | Move to absolute x/y/z position (-1.0 to 1.0) |
| `vigicam.ptz_save_preset` | Save current PTZ position as a named preset |
| `vigicam.ptz_delete_preset` | Delete a named preset |

**Diagnostics**
- Uptime — seconds since last boot (from `getDeviceStatus`), diagnostic/hidden

**Video clip retrieval** (via `searchVideoList` + stream download interface)

Service: `vigicam.get_detection_clip`
- Finds the most recent recording clip matching an event type
- Downloads it from the camera
- Saves to HA media directory or fires as an HA media event
- Use case: send a 10-second vehicle detection clip to a phone via companion app

---

## Architecture decisions

### 1. OpenAPI is additive, never a dependency

The integration must work fully without OpenAPI enabled. Everything OpenAPI adds is
on top of the existing ONVIF + JSON API foundation. No existing entity should change
behaviour because OpenAPI is enabled or disabled.

### 2. ONVIF remains the primary event source

`subscribeMsg` requires OpenAPI to be enabled — users on default settings won't have
it. ONVIF always works. Keep both running in parallel when OpenAPI is available:
- ONVIF fires: Motion, Person, Tamper, Intrusion, LineCrossing, SmartDetection
- subscribeMsg fires: VehicleDetected, AudioAnomaly, Loitering, SceneChange, etc.
- SmartDetection fires for anything subscribeMsg misses (if OpenAPI dies mid-session)

### 3. Feature detection at setup + periodic re-check

At integration setup:
1. Existing: probe PTZ (ONVIF), probe smart frames (get_media_list)
2. New: probe OpenAPI (attempt doAuth to port 20443, non-fatal on failure)

On coordinator refresh (every 30s):
- If `has_openapi=False`: attempt a quick TCP connect to port 20443 every 5 minutes
- If port opens, retry doAuth — if successful, set `has_openapi=True` and register new entities
- Log at INFO level: "OpenAPI now available — Vehicle Detected and other sensors added"

### 4. Notification when a feature would be available

When `has_openapi=False` and port 20443 is closed, add a HA repair/notification:
> "Enable OpenAPI in camera settings to unlock Vehicle Detected, Audio Anomaly, and
>  other detection sensors. Go to camera web UI → Settings → Network → OpenAPI."

### 5. Entity naming — user-facing, never protocol-facing

Rules:
- No protocol names in entity names (no "ONVIF", "OpenAPI", "RTSP")
- No internal API method names (no "getMotionDetectionSwitch")
- Human-readable, consistent with HA conventions
- Detection sensors: noun or adjective phrase, not past tense ("Motion" not "Motion Detected")
  - Exception: "Person Detected", "Vehicle Detected" (established HA pattern)
- Diagnostic entities: hidden by default, user must enable in HA UI

### 6. Entity grouping in HA device page

Group related entities logically:
- Detection (all binary sensors together)
- Alarm & Response (switches + buttons for alarm)
- Camera Settings (night vision, sensitivity, LED)
- SD & Storage (all SD card sensors)
- PTZ (buttons, preset select, services) — PTZ cameras only
- Diagnostics (firmware, IP, uptime) — hidden by default

### 7. Lightweight on Raspberry Pi

- No polling loops tighter than the existing 30s coordinator cycle
- `subscribeMsg` uses a single persistent connection per camera, not polled
- OpenAPI calls use fresh TCP connections (unavoidable per camera behaviour) — minimise call frequency
- No background tasks beyond: coordinator poll, ONVIF subscription, subscribeMsg connection
- Avoid storing video in memory — stream to file or pipe directly to HA media

### 8. Stok management for OpenAPI

- Cache stok with a 25-minute TTL (spec says 30 minutes — be conservative)
- Re-authenticate automatically on `-10002 Unauthorized` response
- Store stok in the coordinator, not in each entity

---

## Entity naming reference

### Binary sensors

| Entity name | Device class | ONVIF topic / OpenAPI event | Notes |
|---|---|---|---|
| Motion | motion | `RuleEngine/MotionRegionDetector/Motion` | Always |
| Person Detected | motion | `RuleEngine/TPSmartEvent/IsPersonDetection` | Always |
| Vehicle Detected | motion | `VehicleDetection` (subscribeMsg) | OpenAPI only |
| Tamper | tamper | `RuleEngine/TamperDetector/Tamper` | Always |
| Intrusion | motion | `RuleEngine/TPSmartEvent/IsIntrusionDetection` | Always |
| Line Crossing | motion | `RuleEngine/TPSmartEvent/IsLineCrossingDetection` | Always |
| Smart Detection | motion | `RuleEngine/TPSmartEvent/IsTPSmartEvent` | Always (catch-all) |
| Audio Anomaly | sound | `AudioAnomalyDetection` (subscribeMsg) | OpenAPI only |
| Loitering | motion | `LoiterDetection` (subscribeMsg) | OpenAPI, S245 only |
| Scene Change | problem | `SceneChangeDetection` (subscribeMsg) | OpenAPI, S245 only |
| Object Left or Taken | motion | `DropAndTakeDetection` (subscribeMsg) | OpenAPI only |
| Area Entry | motion | `AreaEntryDetection` (subscribeMsg) | OpenAPI only |
| Area Exit | motion | `AreaLeaveDetection` (subscribeMsg) | OpenAPI only |
| Loop Recording | running | polled (JSON API) | Always |

### Sensors (additions)

| Entity name | Unit | Source | Default |
|---|---|---|---|
| SD Card Recording Duration | h | OpenAPI `getSdCardStatus`.record_duration | Visible |
| SD Card Oldest Recording | datetime | OpenAPI `getSdCardStatus`.record_start_time | Visible |
| SD Card Record Capacity Remaining | h | OpenAPI `getSdCardStatus`.record_free_duration | Diagnostic |
| SD Card Video Space Free | GB | OpenAPI `getSdCardStatus`.video_free_space | Diagnostic |
| Uptime | h | OpenAPI `getDeviceStatus`.uptime | Diagnostic |

### Services (additions)

| Service | Description | Required params | Optional params |
|---|---|---|---|
| `vigicam.ptz_move_to` | Move to absolute position | `entity_id`, `pan` (-1.0–1.0), `tilt` (-1.0–1.0) | `zoom` (0.0–1.0) |
| `vigicam.ptz_save_preset` | Save current position as preset | `entity_id`, `name` | `id` (1–8) |
| `vigicam.ptz_delete_preset` | Delete a preset by name | `entity_id`, `name` | — |
| `vigicam.get_detection_clip` | Get video clip of last detection | `entity_id` | `event_type`, `minutes_back` |

---

## Build phases

### Phase 1 — OpenAPI infrastructure + SD card detection  `[DONE v0.5.0b1]`
New file: `custom_components/vigicam/openapi.py`
- `VIGIOpenAPI` class: doAuth, call(), stok cache (25-min TTL), re-auth on -10002
- Probe function: `try_openapi(ip, user, password) -> bool`
- Modify `__init__.py`:
  - Probe OpenAPI at setup, store `has_openapi` in entry data
  - Probe SD card at setup via JSON API `getSdCardStatus`, store `has_sd_card` in entry data
    - `has_sd_card=True` if `status` is `"normal"` or `"full"`; False if `"none"` or error
  - Re-check `has_sd_card` on each coordinator refresh (card may be inserted/removed)
- Modify coordinator: periodic re-check for OpenAPI becoming available (every 5 min when False)
- Add HA repair issue when port 20443 is closed

### Phase 2 — Split detection sensors via subscribeMsg  `[DONE v0.5.0b2]`
New file: `custom_components/vigicam/openapi_events.py`
- `VIGIOpenAPIEventListener`: persistent HTTPS connection, multipart/mixed parser
- Heartbeat keepalive, reconnect on drop
- Fires HA dispatcher signals per event type
- Modify `binary_sensor.py`: register OpenAPI sensors when `has_openapi=True`
- Smart Detection continues firing as ONVIF catch-all

### Phase 3 — Richer SD card + uptime sensors  `[DONE v0.5.0b3]`
- Gate ALL SD card sensors (existing v0.4.0 ones too) on `has_sd_card`
  - Cameras on NVR-only storage will no longer show "SD Card Status: None" etc.
- Modify `sensor.py`: add SD card record duration, oldest recording, record capacity (all `has_sd_card` gated)
- Add uptime sensor (diagnostic, hidden)
- Investigate NVR diagnostics: probe `getNVRInfo` and related methods; add `has_nvr` flag and
  "NVR Connection" diagnostic sensor if the camera exposes useful data
- All powered from existing coordinator poll (no new polling)

### Phase 4 — PTZ absolute position + preset management  `[DONE v0.5.0b4]`
- Modify `services.py`: add `ptz_move_to`, `ptz_save_preset`, `ptz_delete_preset`
- These call OpenAPI directly (not coordinator-polled)
- Require `has_ptz` + `has_openapi`
- Document: pan/tilt range is -1.0 to 1.0 (camera-native units from `getPresetPoint`)

### Phase 5 — Video clip retrieval  `[TODO]`
- Modify `services.py`: add `get_detection_clip`
- Uses `searchVideoList` to find clip by event type within a time window
- Downloads via RTSP MULTITRANS stream interface → saves to `/config/media/vigicam/`
- Returns media path via service response or HA persistent notification
- Requires `has_openapi`

### Phase 6 — Automation examples + USAGE.md update  `[TODO]`
- Each new feature gets a dedicated section in `docs/USAGE.md` with:
  - What it does
  - Requirements (OpenAPI? PTZ? Specific model?)
  - At least one working YAML automation example
  - Attribute list where relevant

---

## USAGE.md automation example template

Each feature section in USAGE.md should follow this structure:

```markdown
### Feature Name

Brief description of what it does in plain English.

**Requirements:** [OpenAPI enabled? PTZ camera? Specific model?]

**Attributes:**
| Attribute | Example | Description |

#### Example: [short descriptive title]

What this automation achieves in one sentence.

```yaml
automation:
  trigger: ...
  condition: ...
  action: ...
```

> **Tip:** [one practical tip or common gotcha]
```

---

## Known gotchas

- OpenAPI requires a fresh TCP connection per request (unusual — do not reuse connections)
- OpenAPI stok expires after 30 minutes — cache with 25-minute TTL and re-auth proactively
- `subscribeMsg` must be sent on a persistent connection that stays open — use a dedicated long-lived session
- Each detection type's `msg_push_enabled` must be `"on"` for subscribeMsg to fire that event type
  - Our integration should set `msg_push_enabled: on` for all types we care about when OpenAPI first enables
- ONVIF subscription address comes back on port 1024 (not 80) — already handled
- PTZ presets use `id` (integer string "1"–"8") not name in `gotoPresetPoint`
- OpenAPI `getMediaList` only supports `media_type: "video"` — no image download (Smart Frames still need the WebSocket route)
- RTSP replay stream limit: 1 concurrent client (429 on second connect)
- C540V does not support LoiterDetection or SceneChangeDetection (returns -10030)
- `people_enhance_ver` / `vehicle_enhance_ver` from `getEventEnhanceCapability` are bitmasks, not version numbers

---

## Version history

| Version | Key changes |
|---|---|
| 0.4.0 | Last Detection image entity (Smart Frame + RTSP fallback), SSL setup fix (C320I), dashboard thumbnail fix |
| 0.3.x | PTZ, audio services, blueprints, detection sensors, ONVIF events |

---

## Files to never commit

- `CLAUDE.md` — local workflow, gitignored
- `rituals/` — local workflow, gitignored
- `.credentials.json` — camera passwords
- `vigicam-context.md` — personal IPs/MACs
- `probe_*.py` — local probing scripts, gitignored (add to .gitignore if not there)
- `tmp/` — local research files (PDF docs etc.), gitignore
