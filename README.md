# VIGICam — TP-Link VIGI/InSight Camera Integration for Home Assistant

A Home Assistant custom integration for TP-Link VIGI and InSight cameras, providing
full local API access beyond basic RTSP streaming.

## Features

- RTSP video stream (HD + SD)
- Motion / person / vehicle detection (read + enable/disable)
- PTZ preset control (named presets on supported cameras)
- Night vision mode select (IR / white LED spotlight / auto)
- Spotlight intensity control (1–4)
- LED and alarm control
- SD card storage sensors (used %, free space, total, status)
- Speaker and microphone volume/mute
- Tamper detection (supported cameras)

## Tested Cameras

| Model | Type |
|-------|------|
| VIGI C540V | PTZ, spotlight, IR |
| InSight S245 | Fixed, spotlight, IR, tamper |

Other VIGI and InSight cameras using the same local HTTPS API should also work.
The integration reads the camera model and capabilities at setup time — PTZ controls
and tamper detection only appear for cameras that support them.

## Installation

### Via HACS (recommended)
1. In HACS, go to **Integrations → ⋮ → Custom repositories**
2. Add `https://github.com/steveAbratt/VIGICam` as type **Integration**
3. Install **VIGI & InSight Cameras** and restart Home Assistant

### Manual
Copy `custom_components/vigicam/` into your HA config's `custom_components/` directory,
then restart Home Assistant.

## Setup

1. Go to **Settings → Devices & Services → Add Integration**
2. Search for **VIGI**
3. Enter the camera's IP address, username (default: `admin`), and password

Each camera is added as a separate integration entry.

## Dependencies

`pycryptodome` is installed automatically by Home Assistant from the manifest.

## Development

```bash
pip3 install pycryptodome
python3 probe/probe_vigi.py <camera-ip> admin <password>
```

---

## Attribution & Acknowledgements

This integration was built independently but draws on the groundwork laid by others
in the TP-Link camera and Home Assistant communities:

- **[JurajNyiri/HomeAssistant-Tapo-Control](https://github.com/JurajNyiri/HomeAssistant-Tapo-Control)**
  (MIT License) — the gold standard TP-Link camera integration for Home Assistant.
  The entity architecture, coordinator pattern, and platform structure of this project
  were inspired by this work. VIGI cameras are not supported upstream (by policy, not
  technical incompatibility), which motivated this separate integration.

- **[JurajNyiri/pytapo](https://github.com/JurajNyiri/pytapo)**
  (MIT License) — Python library for the Tapo local API. The authentication flow in
  `api.py` is based on the protocol documented and implemented in pytapo. VIGI cameras
  differ slightly (stok token location in the login response), handled in this integration.

- **[yetanothercarbot/vigi_camera_lighting](https://github.com/yetanothercarbot/vigi_camera_lighting)**
  — useful reference for VIGI spotlight/night-vision endpoint behaviour.

- The **Home Assistant** developer community for integration architecture patterns.

This project is MIT licensed. See [LICENSE](LICENSE).
