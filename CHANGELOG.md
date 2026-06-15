# Changelog

All notable changes to VIGICam are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).
Versions follow [Semantic Versioning](https://semver.org/): MAJOR.MINOR.PATCH.

- **PATCH** — bug fixes, no new entities or config changes
- **MINOR** — new entities, new features, backwards-compatible
- **MAJOR** — breaking changes that require re-adding the integration

---

## [Unreleased]

---

## [0.3.8] - 2026-06-15

### Added
- **`vigicam.delete_audio` service** — delete a custom audio slot (101, 102, or 103) from
  the camera. Safe to call on an already-empty slot (camera returns success).
- **`Alarm Sound Repetitions` number entity** — controls how many times the alarm sound
  plays per trigger (was only configurable via the camera web UI). Range 1–10.
  This is what controls the "10 second loop" behaviour of the Alarm Trigger button.
- **`vigicam.play_audio` now supports `times` and `pause` parameters** — repeat the audio
  N times with a configurable gap between plays (default 1s). Useful for announcements
  that should be heard twice.
- **Blueprint: VIGI Camera — Announce on Trigger** (`blueprints/automation/vigicam/camera_announce.yaml`) —
  importable automation blueprint. Configure a trigger + camera + slot; the automation
  plays the pre-uploaded audio when the trigger fires. Repeat count configurable.

### Notes
- Custom audio behaviour summary: `play_audio` / `test_audio` plays a slot once (or N times
  with pause). The Alarm Trigger button uses `manual_msg_alarm` which loops the configured
  alarm type based on `sound_alarm_times` — that is now the Alarm Sound Repetitions entity.
  These are two separate playback paths with different use cases.
- 3 custom slots (101–103). Listing slots is not supported on tested firmware (-40106) but
  upload, play, and delete all work. Overwriting is safe — just re-upload to the same slot.
- Blueprint uses `tts.speak` trigger selector requiring HA 2024.6+.

---

## [0.3.7] - 2026-06-15

### Added
- **`vigicam.upload_audio` service** — uploads an audio file (via URL) to a custom slot on
  the camera (slots 101, 102, or 103). Set `play: true` to play immediately after upload.
  Supported formats: WAV mono 8 kHz ≤15 s ≤256 KB; MP3 mono ≤15 s ≤128 KB ≤64 kbps.
- **`vigicam.play_audio` service** — plays any audio slot through the camera speaker.
  Built-in: slot 0 = Alarm Tone, slot 1 = Ring Tone. Custom: slots 101–103 (uploaded first).

### Notes
- Upload is a two-step API sequence discovered from the camera's web UI JavaScript:
  `DO system/upload_usr_def_audio` to acquire an upload URL, then multipart POST to it.
- TTS workflow: generate TTS audio in HA → use the TTS proxy URL as `url` in
  `vigicam.upload_audio` → camera plays the spoken message through its speaker.
- The camera converts uploaded audio internally; download size may differ from upload size.
- `get usr_def_audio_alarm/usr_def_audio` returns -40106 on tested firmware (listing not
  supported), but upload and playback both work fine.

---

## [0.3.6] - 2026-06-15

### Added
- **Alarm Trigger button** — press to fire the camera's configured alarm sound immediately
  (plays for 10 seconds then auto-stops; works regardless of whether the master Alarm
  switch is on — this is a manual test/trigger, not detection-linked).
- **Alarm Stop button** — press to cancel an in-progress alarm trigger early.

### Notes
- Trigger uses `msg_alarm.manual_msg_alarm {action: start/stop}`, discovered in the
  camera's web UI JavaScript source. The 10-second countdown matches the camera's
  built-in behaviour; calling stop cancels it at any point.
- Sound type (Alarm Tone / Ring Tone / Custom) is whatever is configured in the camera's
  web UI under Active Defence → Sound Alarm. Changing that setting changes what plays
  when the button is pressed.
- Both buttons appear for any camera that reports alarm support (not PTZ-only).

---

## [0.3.5] - 2026-06-15

### Changed
- **Entity name grouping** — related entities now sort together alphabetically in the HA
  device view:
  - Detection switches renamed with `Detection` prefix: `Detection Motion`, `Detection Person`,
    `Detection Tamper`, `Detection Vehicle` (were `Motion Detection` etc.)
  - Alarm switches renamed with `Alarm` prefix: `Alarm Light`, `Alarm Sound`
    (were `Light Alarm`, `Sound Alarm`); `Alarm` unchanged.
  - PTZ jog buttons renamed with `PTZ` prefix: `PTZ Pan Left`, `PTZ Pan Right`,
    `PTZ Tilt Up`, `PTZ Tilt Down`, `PTZ Zoom In`, `PTZ Zoom Out` (were `Pan Left` etc.)
  - Result: Controls section shows three clear clusters — Alarm (A), Detection (D), and
    PTZ (PT) — instead of being scattered across the alphabet.

### Notes
- Display names change; existing entity IDs in the HA registry are preserved for
  existing installations (HA stores entity IDs by `unique_id`, not by name). New
  installations will get entity IDs matching the new names.

---

## [0.3.4] - 2026-06-15

### Added
- **ONVIF PTZ services** — three new HA services for PTZ cameras:
  - `vigicam.ptz` — continuous move in a direction (`left/right/up/down/zoom_in/zoom_out`)
    with configurable `speed` (0.0–1.0) and optional `duration` (auto-stops after N seconds).
    If no duration is given, call `vigicam.ptz_stop` to stop.
  - `vigicam.ptz_stop` — stop all camera movement immediately.
  - `vigicam.goto_preset` — move to a named preset by name (e.g. `"Full Stable Yard"`);
    cleaner alternative to `select.select_option` for automations with many presets.
- **PTZ direction buttons** — six button entities per PTZ camera (Pan Left/Right, Tilt
  Up/Down, Zoom In/Out) that each jog the camera for 1 second then stop. Useful for
  dashboard control cards; for custom durations use the `vigicam.ptz` service.

### Changed
- Per-preset "Go to {name}" buttons removed — they never worked (bug fixed in 0.3.3)
  and don't scale to cameras with many presets. Use the **PTZ Preset select entity** or
  the new `vigicam.goto_preset` service for preset navigation.

### Notes
- ONVIF PTZ uses `profile_1` (mainStream). All tested VIGI firmware versions use this
  token; it is not configurable per-camera currently.
- Continuous move does not time out on the camera side — always call `ptz_stop` (or
  use the `duration` parameter) to prevent runaway movement.

---

## [0.3.3] - 2026-06-15

### Fixed
- **PTZ preset buttons and select never created** — `get_presets()` read field names
  `pan/tilt/zoom` but the camera API returns `position_pan/position_tilt/position_zoom`.
  The IndexError was swallowed by the except clause, so `get_presets()` always returned
  `[]`, causing `has_ptz = False` at startup. Preset buttons and the PTZ Preset select
  entity were therefore never registered for any camera. Now only reads `id` and `name`
  (neither buttons nor select use the position values).
- **Preset names shown URL-encoded** — camera returns `"Zoomed%20Stable%20View"`;
  now decoded with `urllib.parse.unquote()` so entity names and select options show
  the correct human-readable name.

---

## [0.3.2] - 2026-06-15

### Added
- **IP Address** diagnostic sensor — shows the camera's current IP address (from `network.wan`).
- **Connection Type** diagnostic sensor — shows `DHCP` or `Static`, indicating whether the
  camera is using a dynamic or fixed IP address.

---

## [0.3.1] - 2026-06-15

### Added
- **Intrusion** binary sensor (ONVIF `IsIntrusion`) — fires when intrusion zones are
  triggered (configured via VIGI app or web UI)
- **Line Crossing** binary sensor (ONVIF `IsLineCross`) — fires when line crossing rules
  are triggered
- **Smart Detection** binary sensor (ONVIF `IsTPSmartEvent`) — TP-Link catch-all topic
  covering vehicle, sound, loitering, abandoned object and scene change detection; these
  cannot be distinguished at the ONVIF level
- **Light Alarm** switch — controls `light_alarm_enabled` in `msg_alarm` (the camera's
  flashing light response to detection events)
- **Sound Alarm** switch — controls `sound_alarm_enabled` in `msg_alarm`

### Fixed
- ONVIF event dispatch was checking `Value`/`IsMotion` for all event types. VIGI cameras
  use per-topic boolean fields (`IsTamper`, `IsPeople`, `IsIntrusion`, `IsLineCross`,
  `IsTPSmartEvent`). Dispatch now scans the data dict for any `Is*` field.
- ONVIF topic keyword map updated with verified topic names from `GetEventProperties`
  on both cameras; more specific matches (TPSmartEvent, CellMotion) now take priority
  over generic ones.

### Notes
- Smart detection zone/area configuration (line crossing paths, intrusion regions, etc.)
  is not exposed by the local JSON API (-40106). Use the VIGI app or camera web UI to
  configure rules; this integration reports when they fire.
- Vehicle, sound, loitering, abandoned object, scene change detection all share the
  `IsTPSmartEvent` ONVIF topic — they cannot be separated at this level.

---

## [0.3.0] - 2026-06-15

### Added
- **Real-time motion/person/vehicle/tamper binary sensors** via ONVIF pull-point
  subscription. On startup, a background task subscribes to the camera's ONVIF event
  service and long-polls for events every 8 seconds. Events fire immediately rather than
  waiting for the 30-second coordinator cycle.
- Detected state auto-clears after 15 seconds if the camera does not send an explicit
  "active=false" event.
- Subscription auto-renews 5 minutes before the 1-hour expiry. Re-subscribes on error
  after a 15-second backoff.

### Notes
- Auth: VIGI cameras require WS-Security PasswordDigest = SHA1(nonce + created +
  raw_password). Using SHA1(password) first — as the `onvif-zeep-async` library does —
  returns NotAuthorized on these cameras. No external ONVIF library needed.
- Subscription address is returned on port 1024; pull calls go to that address, not
  the main service URL on port 80.
- ONVIF topic → entity mapping uses keyword matching. Unknown topics are logged at DEBUG
  level — add to `TOPIC_KEYWORD_MAP` in `onvif_events.py` as needed.

---

## [0.2.2] - 2026-06-15

### Added
- **Loop Recording** binary sensor — shows whether loop recording is active on the SD card.
  Read from `loop_record_status` in `harddisk_manage` response; cannot be set via the local
  API (all write attempts return -40101). Only created if the camera reports the field.

### Fixed
- **SD Card Used %** was reporting the camera's `percent` field which is inconsistent
  across firmware versions (camera 1 reports 0%, camera 2 reports 100%, both with full
  disks). Now calculated from `total_space_accurate` and `free_space_accurate` (byte
  values) for reliable results across all cameras.

---

## [0.2.1] - 2026-06-15

### Fixed
- SD card sensors (used %, total, free, status) all showing **unknown** — the camera
  wraps disk data one level deeper than expected: `hd_info[0]` returns
  `{"hd_info_1": {...}}`, not the disk dict directly. `get_storage()` now unwraps
  that extra nesting level.
- `_parse_gb()` failed to parse storage values — camera returns `"116.8GB"` and `"0B"`
  but the old parser only stripped `G/M/K` suffixes, not `B`, so `float("116.8GB")`
  raised ValueError and returned None. Now handles GB, MB, KB, B, and G suffixes.

---

## [0.2.0] - 2026-06-15

### Added
- HACS custom repository support (`hacs.json`)
- Tamper detection switch (InSight S245 and other cameras that support it)
- Dynamic entity creation — entities are only registered if the camera reports
  support for that feature in its first API response; works across all camera models
- SSL auto-detection in config flow — tries verified connection first (proper certs),
  falls back to unverified for self-signed certs; stores preference per camera
- Camera thumbnail on device page via ffmpeg frame grab from RTSP stream
- "Visit" link on device info page (`configuration_url` → camera web UI)

### Fixed
- All sensors showing **unknown** — `ssl.create_default_context()` made blocking
  `load_default_certs()` calls inside the async event loop (detected by HA's loop
  guard on Python 3.14+); refactored to use HA's `async_get/create_clientsession`
  helpers which handle SSL context creation correctly
- Binary sensors were showing config-enabled state as live events — motion/person/
  vehicle/tamper all read `enabled: on/off` and HA displayed this as "Detected",
  which was misleading; replaced with switches (which is the correct entity type
  for enable/disable controls)
- Tamper was a binary sensor showing "Tampering: detected" at all times because
  tamper detection was enabled on the camera; moved to a switch
- Firmware version showed full build string ("2.2.0 Build 250904 Rel.60109n") —
  now cleaned to the version number only ("2.2.0")
- Model name included hardware revision ("VIGI C540V 1.0") — now stripped ("VIGI C540V")
- Storage sensors (SD card) showing unknown — coordinator only caught `VIGIError`;
  broader exception handling added; `hd_info` handled as both list and dict (firmware
  version difference between camera models)

### Notes
- Binary sensor platform is now a placeholder — real-time motion/person/vehicle/tamper
  events require ONVIF event subscriptions, planned for a future release

---

## [0.1.0] - 2026-06-15

### Added
- Initial release
- Local HTTPS API client (`api.py`) with VIGI-specific auth (stok at top level,
  not nested under `result` as in Tapo cameras)
- `DataUpdateCoordinator` polling all camera state every 30 seconds
- Config flow — IP, username, password with live credential validation
- **camera** — RTSP HD stream (`stream1`) with `CameraEntityFeature.STREAM`
- **switch** — motion detection, person detection, vehicle detection, status LED,
  alarm, speaker mute, microphone mute
- **select** — night vision mode (5 options), PTZ preset (PTZ cameras only)
- **sensor** — SD card used %, total GB, free GB, status, firmware version
- **button** — one per named PTZ preset (PTZ cameras only, auto-detected)
- **number** — speaker volume, motion sensitivity, spotlight intensity
- **binary_sensor** — placeholder for future ONVIF event support
- MIT licence with attribution to Tapo Control, pytapo, and vigi_camera_lighting
