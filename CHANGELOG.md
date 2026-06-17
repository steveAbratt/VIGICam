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

## [0.5.0b7] - 2026-06-17

### Fixed
- **Detection Person** and **Detection Vehicle** switches showed wrong state on
  cameras where smart detection is configured via the camera web UI or OpenAPI.
  The camera web UI writes smart detection state through OpenAPI; the JSON API
  `people_enabled` / `vehicle_enabled` fields are legacy and not kept in sync.
  When OpenAPI is available, these switches now read from
  `getPeopleDetectionSwitch` / `getVehicleDetectionSwitch` and write via
  `setPeopleDetectionSwitch` / `setVehicleDetectionSwitch`.
- OpenAPI is now probed before the first coordinator refresh so smart-detection
  state is already correct when HA entities are created on startup.

---

## [0.5.0b6] - 2026-06-17

### Fixed
- PTZ Preset dropdown always showed "unknown" — the camera has no API to report
  its current position, so the dropdown now tracks the last preset selected from
  HA. Selecting from the dropdown, calling `vigicam.goto_preset`, or using button
  jogs / `vigicam.ptz` / `vigicam.ptz_move_to` all update the dropdown correctly.
  State resets to unknown on HA restart (camera may have been moved externally).

---

## [0.5.0b5] - 2026-06-17

### Fixed
- OpenAPI SD card sensors (Recording Duration, Oldest Recording, Record Capacity
  Remaining, Video Space Free) and Uptime sensor all reported "unknown" — the
  coordinator was storing the full raw API response instead of unwrapping the
  inner `result` dict. Sensors now report correct values.
- With loop recording enabled and the video partition full, Record Capacity
  Remaining and Video Space Free correctly report `0`.

### Changed
- **USAGE.md** fully updated for v0.5.0: new "OpenAPI — unlocking additional
  sensors" setup section, automation examples for every binary sensor (all ONVIF
  and all 7 OpenAPI sensors), examples for every service, SD card sensor
  descriptions updated with NVR note and loop-recording behaviour.

---

## [0.5.0b4] - 2026-06-17

### Added
- **`vigicam.ptz_move_to`** service — move the camera to an absolute pan/tilt/zoom
  position (pan and tilt: -1.0 to 1.0, zoom: -1.0 to 1.0). Requires PTZ camera + OpenAPI.
- **`vigicam.ptz_save_preset`** service — save the camera's current position as a named
  preset (slot 1–8). If no slot `id` is given, the first unused slot is chosen automatically.
  Requires PTZ camera + OpenAPI.
- **`vigicam.ptz_delete_preset`** service — delete a preset by name.
  Requires PTZ camera + OpenAPI.
- PTZ Preset select entity refreshes automatically after save/delete (on next 30s poll).

### Notes
- The camera does not expose a current position API — no position sensor is possible.
  Use presets to track meaningful positions via the PTZ Preset select entity.

---

## [0.5.0b3] - 2026-06-17

### Added
- **SD Card Recording Duration** sensor — total hours of video stored on the SD card
  (requires OpenAPI + SD card present).
- **SD Card Oldest Recording** sensor — datetime of the earliest stored clip
  (requires OpenAPI + SD card present).
- **SD Card Record Capacity Remaining** sensor — hours of recording space left
  (diagnostic, requires OpenAPI + SD card present).
- **SD Card Video Space Free** sensor — GB of video storage free
  (diagnostic, requires OpenAPI + SD card present).
- **Uptime** sensor — hours since last camera reboot (diagnostic, hidden by default,
  requires OpenAPI).
- Coordinator now polls `getSdCardStatus` and `getDeviceStatus` via OpenAPI on every
  30s refresh cycle when OpenAPI is available.

---

## [0.5.0b2] - 2026-06-17

### Added
- **Vehicle Detected** binary sensor — fires on vehicle detection events via OpenAPI
  subscribeMsg (previously only available as part of the "Smart Detection" catch-all).
- **Audio Anomaly** binary sensor — fires on sound anomaly detection events.
- **Loitering** binary sensor — fires when loitering is detected (InSight S245 only).
- **Scene Change** binary sensor — fires when the camera scene changes unexpectedly
  (InSight S245 only).
- **Object Left or Taken** binary sensor — fires on abandoned/removed object events.
- **Area Entry** / **Area Exit** binary sensors — fire on configured area crossing events.
- All 7 sensors require OpenAPI to be enabled in camera settings; they appear automatically
  when `has_openapi=True`. Smart Detection (ONVIF) continues as a catch-all fallback.
- The integration enables `msg_push_enabled` for each detection type on first connect so
  events are guaranteed to fire via subscribeMsg.

---

## [0.5.0b1] - 2026-06-17

### Added
- **OpenAPI client** (`openapi.py`) — connects to the TP-Link IPC OpenAPI on port 20443
  using the two-step SHA-256 `doAuth` flow, with a 25-minute stok cache and automatic
  re-authentication. Lays the groundwork for Vehicle Detection, Audio Anomaly, and other
  split detection sensors (coming in v0.5.0b2).
- **OpenAPI feature detection** — at startup the integration probes port 20443; if
  available, `has_openapi=True` is set and the OpenAPI client is activated. If not
  available, an INFO log message explains how to enable it in the camera settings.
  The coordinator re-checks every 5 minutes so the flag activates without a restart if
  the user enables OpenAPI later.
- **SD card detection** (`has_sd_card`) — at startup the integration checks whether a
  usable SD card is present (status `normal` or `full`). SD card sensors (Used %, Total,
  Free, Status) are now only registered when an SD card is detected, keeping the device
  page clean for cameras on NVR-only storage.

---

## [0.4.0] - 2026-06-17

### Added
- **Last Detection image entity** (`image.<camera>_last_detection`) — updates automatically on every detection event (intrusion, line crossing, motion, person detected, smart detection). On cameras with Smart Frame support, downloads the AI-cropped Smart Frame from the SD card via WebSocket; on cameras without Smart Frame support (e.g. VIGI C540V), falls back to grabbing a still from the live RTSP stream. The `source` attribute indicates which method was used (`smart_frame` / `rtsp_snapshot`). Smart Frame images also expose `smart_frame_label` and `file_id` attributes.
- Smart Frame support is now detected automatically at startup — no manual configuration needed.

### Fixed
- Camera dashboard thumbnails were broken in recent HA versions — `FFmpegManager.get_image` no longer exists. Replaced with `asyncio.create_subprocess_exec` throughout.
- Setup failed with SSL certificate errors on cameras using the TP-Link internal CA (e.g. VIGI C320I). Two-stage fix: SSL errors now propagate unwrapped from `api.py` so the config flow's no-verify fallback session can run correctly.
- Config flow had no logger — all setup failures were silently swallowed with no debug output. Errors are now logged at debug level with full exception detail.

---

## [0.4.0b7] - 2026-06-17

### Added
- Last Detection image entity now works on cameras without Smart Frame support (e.g. VIGI C540V). When Smart Frame capture is unavailable, the entity falls back to grabbing a still from the live RTSP stream (stream1) via ffmpeg on any detection event — intrusion, line crossing, motion, person detected, or smart detection. The `source` attribute on the entity indicates `"smart_frame"` or `"rtsp_snapshot"` so you can tell which method was used.

---

## [0.4.0b6] - 2026-06-17

### Fixed
- Setup still fails with SSL error on cameras using the TP-Link internal CA (e.g. VIGI C320I). Root cause identified: `api.py`'s `_post` method was catching `ClientConnectorCertificateError` as a generic `aiohttp.ClientError` and wrapping it in `VIGIError`, so the config flow's SSL-fallback branch never ran. SSL errors now propagate unwrapped from `_post`, allowing Attempt 2 (no-verify session) to execute correctly.

---

## [0.4.0b5] - 2026-06-17

### Fixed
- Setup fails with SSL certificate error on cameras using the TP-Link internal CA (CN=TPRI-CA / CN=TPRI-DEVICE), including the VIGI C320I. The SSL fallback in the config flow was using `async_create_clientsession(verify_ssl=False)` which is unreliable across HA/aiohttp versions. Attempt 2 now uses `session=None` so `VIGICamera` creates its own session with an explicit no-verify SSL context — the same approach used successfully in the rest of the integration.

---

## [0.4.0b4] - 2026-06-17

### Fixed
- Config flow had no logger — connection failures during setup were silently swallowed with no debug output. Errors during `cannot_connect` and `invalid_auth` are now logged at debug level with full exception detail, making it possible to diagnose setup failures on unsupported or different-firmware cameras.

---

## [0.4.0b3] - 2026-06-17

### Added
- Smart Frame support is now detected automatically at startup. Cameras that do not support Smart Frame image capture (e.g. VIGI C540V) will not show the Last Detection image entity. Mirrors the existing PTZ capability detection pattern.

### Changed
- Documented Smart Frame model limitation in `docs/USAGE.md` — the entity will simply be absent on unsupported cameras rather than showing as unavailable.

---

## [0.4.0b2] - 2026-06-16

### Fixed
- Camera dashboard thumbnail was broken — `FFmpegManager.get_image` no longer exists in current HA versions. Replaced with `asyncio.create_subprocess_exec` (same pattern as Smart Frame downloads). Restores stream2 for low-bitrate dashboard thumbnails; stream1 remains the live view source.

### Added
- Documented the Last Detection image entity requirements in `docs/USAGE.md` (Smart Frame capture must be enabled; SD card must be formatted for image storage).

---

## [0.4.0b1] - 2026-06-16

### Added
- **Last Detection image entity** (`image.<camera>_last_detection`) — downloads the most
  recent AI-cropped Smart Frame from the camera's SD card via WebSocket
  (`wss://<ip>:8443/stream`) each time an ONVIF detection event fires. Shows the detected
  object as cropped by the camera's on-device AI, not a raw stream grab.
  Exposes `detection_type` (ONVIF trigger: `"person"`, `"smart_detection"`, etc.) and
  `smart_frame_label` (`"Person"`, `"Smart Detection"`, etc.) as state attributes.
  Requires Smart Frame capture enabled in camera settings (Event → Smart Frame) and an
  SD card inserted; image is unchanged if either is unavailable.
- Added `docs/camera_api_research.md` — local API research notes covering the JSON API,
  ONVIF, RTSP replay, Smart Frame WebSocket protocol, and SD recording behaviour.

---

## [0.3.26] - 2026-06-16

### Fixed
- **Smart Detection and Line Crossing sensors showed "Off" / "On"** instead of "Clear" /
  "Detected". Added `device_class=BinarySensorDeviceClass.MOTION` to both — consistent
  with Motion, Person, Tamper, and Intrusion which already had the correct device class.

### Docs
- Added a note to Smart Detection in USAGE.md clarifying that vehicle detection fires
  this sensor (not a separate entity) — the Detection Vehicle switch only enables the
  feature on camera; the ONVIF event topic is shared across all smart detection types.

---

## [0.3.25] - 2026-06-15

### Fixed
- **RTSP credentials redacted in logs** — if a snapshot grab fails, the error is now
  logged with the password replaced by `***` so credentials cannot appear in the HA log.
  The RTSP URL with credentials is only held in memory and never written to any log output.

---

## [0.3.24] - 2026-06-15

### Performance
- **Sub-stream thumbnails** — dashboard card previews and history snapshots now grab a
  frame from `stream2` (sub-stream, low bitrate) instead of `stream1` (HD). The live
  view when tapping the card still uses the full HD stream1. Significantly reduces CPU
  load on the HA host for thumbnail requests, especially on Raspberry Pi.

---

## [0.3.23] - 2026-06-15

### Added
- **`times` and `pause` inputs in the announce blueprint** (`camera_announce.yaml`) —
  Repeat count (default 1, max 50) and Pause between repeats (default 1 s) added to
  the VIGI Camera — Speak on Trigger blueprint.
- **New blueprint: VIGI Camera — Play Audio File on Trigger** (`camera_play_file.yaml`) —
  trigger-based automation to play a pre-recorded file from the HA media browser, a
  `www/` URL, a file path, or an external URL. Includes slot, repeat count, and pause
  inputs with guidance on getting the media browser URL.

---

## [0.3.22] - 2026-06-15

### Added
- **Device name suggestion during setup** — config flow now has a second step after
  credentials are validated. The camera's configured name (`dev_name` / `alias`,
  URL-decoded) is pre-filled so users can confirm or change it before the entry is
  created. Existing entries are unaffected.
- **MAC Address diagnostic sensor** — shows the camera's MAC address. Hidden by default;
  enable from entity settings. No extra API calls — pulled from the existing `device_info`
  poll.

---

## [0.3.21] - 2026-06-15

### Fixed
- **`times` parameter ignored in `play_file` and `speak`** — `play_audio` was sleeping
  only `pause` seconds (default 1 s) between plays. If the clip was longer than `pause`,
  the next `test_audio` fired while the camera was still playing, and the camera dropped
  it. Fix: `play_audio` now accepts `audio_duration` (calculated from WAV size) and sleeps
  for `audio_duration + pause` between plays, ensuring the clip always finishes first.
  `vigicam.play_audio` (existing slot, unknown duration) falls back to `pause`-only.

---

## [0.3.20] - 2026-06-15

### Fixed
- **`vigicam.play_file` "No such file" on HA OS** — HA OS mounts the media directory at
  `/media/` (a separate partition), not `/config/media/`. Path resolution now uses
  `hass.config.media_dirs.get("local")` which returns the correct base path per
  installation type. Fallback to `config_dir/media/` for Container/Core/Supervised.

---

## [0.3.19] - 2026-06-15

### Fixed
- **`vigicam.play_file` 401 Unauthorized on HA media browser URLs** — HA's `/media/local/`
  endpoint requires authentication that the shared aiohttp session does not carry. URLs
  matching `/media/local/` or `/local/` are now automatically resolved to their equivalent
  file paths on the HA host before fetching, avoiding the auth requirement entirely.
  External URLs are unaffected.

### Added
- **HACS brand icon** — added `custom_components/vigicam/brand/icon.png` (wall-mounted
  CCTV camera). HA 2026.3+ changed icon delivery for custom integrations to a `brand/`
  subdirectory inside the integration folder.

---

## [0.3.18] - 2026-06-15

### Added
- **`vigicam.play_file` service** — fetch any audio file (HTTP URL or absolute path on the
  HA host), convert to 8 kHz mono WAV via ffmpeg, upload to a camera slot, and play it.
  Accepts any format ffmpeg supports. Same 15 s / 256 KB limit as `vigicam.speak`.
  Files in `/config/www/` are reachable as `http://<ha-ip>:8123/local/filename`.
- **`times` and `pause` parameters** added to `vigicam.speak` and `vigicam.play_file` —
  repeat the audio N times with a configurable gap. Range 1–50 (matches camera UI range).
  Note: "Alarm Sound Repetitions" entity controls the alarm trigger button only; use
  `times` in the service call to repeat `speak`/`play_file` announcements.

---

## [0.3.17] - 2026-06-15

### Fixed
- **`vigicam.speak` ffmpeg conversion failing** — `pipe:1` output path was accidentally
  dropped when switching to `-f s16le` in 0.3.16. ffmpeg had no output destination and
  exited with an error before producing any PCM data.

---

## [0.3.16] - 2026-06-15

### Fixed
- **`vigicam.speak` upload rejected (-67306) — root cause fixed.** ffmpeg writing WAV to
  stdout (pipe) cannot seek back to fill in the correct RIFF/data chunk sizes, leaving a
  `0x7FFFFFFF` placeholder that the camera's strict header parser rejects. Fix: output raw
  PCM (`-f s16le`, no header) from ffmpeg and construct the WAV header in Python with the
  exact data size. The codec reverts to `pcm_s16le` 8 kHz mono which is confirmed to work.

---

## [0.3.15] - 2026-06-15

### Fixed
- **`vigicam.speak` upload rejected (-67306)** — switched ffmpeg codec from `pcm_s16le`
  (16-bit linear PCM) to `pcm_mulaw` (G.711 μ-law). IP cameras typically use μ-law as
  their native audio format for alert sounds; 16-bit PCM is rejected at the codec level.

---

## [0.3.14] - 2026-06-15

### Fixed
- **`vigicam.speak` upload rejected (-67306)** — ffmpeg now passes `-map_metadata -1` and
  `-fflags +bitexact` to produce a minimal WAV without extra metadata chunks that the camera
  firmware rejects.
- **Speak: delete slot before uploading** — clears the slot first to avoid rejection when the
  camera treats an already-occupied slot as immutable.

---

## [0.3.13] - 2026-06-15

### Fixed
- `hass.config.version` does not exist in HA 2026.x — replaced with `homeassistant.__version__`.
  This crash was preventing the TTS diagnostic log from printing, masking the root cause.
- Added approach 4: entity_components lookup (HA 2025.x+ entity-based TTS architecture).
- Added approach 5: brute-force scan of all hass.data values for any object with
  `async_get_tts_audio`, as a last resort that works regardless of storage key.
- Diagnostic error log now reliably prints the TTS-related `hass.data` keys so the
  correct access method can be identified if all else fails.

---

## [0.3.12] - 2026-06-15

### Fixed
- **`vigicam.speak` TTS lookup** — import `DATA_TTS_MANAGER` directly from HA so the
  correct `hass.data` key is used regardless of which version renamed it. Added a
  module-level `async_get_tts_audio` fallback (HA 2024.10+ public API). If all three
  approaches fail, the error log now includes the actual TTS-related `hass.data` keys
  present so the correct approach can be identified.

---

## [0.3.11] - 2026-06-15

### Fixed
- **`vigicam.speak` not working** — `tts.get_tts_audio` action is not registered in HA 2025.x+.
  The service now uses the internal TTS manager (`hass.data["tts_manager"]`) directly, which
  is available in all HA 2024.6+ versions and works with any configured TTS engine (Piper,
  Nabu Casa Cloud, etc.). `tts.get_tts_audio` is kept as a fallback for older builds.
- **Alarm Sound Repetitions** max value corrected from 10 → 50 (matches camera web UI range).

---

## [0.3.10] - 2026-06-15

### Fixed
- Blueprint (`camera_announce.yaml`) rejected by HA with "extra keys not allowed" — removed
  invalid `example:` key from the `message` input (not a valid blueprint input field).

---

## [0.3.9] - 2026-06-15

### Added
- **`vigicam.speak` service** — full TTS-to-camera pipeline in one service call.
  Accepts a text message, generates TTS audio via `tts.get_tts_audio`, converts to
  8 kHz mono WAV via ffmpeg (guaranteeing camera compatibility regardless of TTS engine
  output format/bitrate), uploads to the camera, and plays it. Requires HA 2024.6+.
- **Blueprint updated** (`blueprints/automation/vigicam/camera_announce.yaml`) — now uses
  `vigicam.speak` directly. Configure: trigger + camera + message template + TTS engine +
  language. The message field is a template selector so trigger data can be included.

### Notes
- `vigicam.speak` uses `tts.get_tts_audio` (HA 2024.6+, `return_response=True`).
  On older HA the service will log a clear error pointing to the version requirement.
- ffmpeg conversion uses the binary from HA's ffmpeg manager (`ffmpeg` dependency already
  declared in manifest). All TTS engine outputs (MP3, OGG, WAV at any rate) are resampled
  to 8 kHz mono PCM WAV, which is always within the camera's format limits.
- If the converted WAV exceeds 256 KB (about 15 seconds of speech at 8 kHz), the service
  logs an error asking you to shorten the message.

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
