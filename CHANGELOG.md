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
