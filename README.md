# VIGICam — TP-Link VIGI/InSight Camera Integration for Home Assistant

A Home Assistant custom integration for TP-Link VIGI and InSight cameras, providing
full local API access beyond basic RTSP streaming.

## Features

- **RTSP live stream** — HD and SD quality, accessible in dashboards and automations
- **Motion detection** — enable/disable, sensitivity control
- **Person and vehicle detection** — enable/disable per camera (if supported)
- **PTZ preset control** — one button per named preset (PTZ cameras only)
- **Night vision mode** — IR always on, spotlight always on, auto, colour
- **Spotlight intensity** — 1–4 levels
- **Alarm** — enable/disable audio and light alarm
- **Status LED** — enable/disable
- **SD card sensors** — used %, free space, total capacity, status
- **Speaker and microphone** — volume, mute
- **Tamper detection** — enable/disable (if supported by camera)

Entities are created dynamically based on what each camera actually supports — a fixed
camera won't show PTZ buttons, a camera without tamper detection won't show that switch.

## Tested Cameras

| Model | Type |
|-------|------|
| VIGI C540V | Outdoor PTZ, spotlight, IR |
| InSight S245 | Fixed outdoor, spotlight, IR, tamper detection |

Other VIGI and InSight cameras using the same local HTTPS API should also work.

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

## Adding a Camera

1. Go to **Settings → Devices & Services**
2. Click **+ Add Integration**
3. Search for **VIGI**
4. Enter:
   - **IP Address** — the camera's local IP (find it in your router or the VIGI app)
   - **Username** — `admin` (default)
   - **Password** — set during camera setup in the VIGI app
5. Click **Submit** — the integration validates the credentials before saving

Repeat for each camera. Each camera appears as a separate device.

## Viewing the Live Stream

After adding a camera, add a **Camera card** to any dashboard:

1. Open a dashboard → **Edit** → **+ Add Card**
2. Choose **Camera**
3. Select your camera entity (e.g. `camera.vigi_c540v_stream`)

The stream is also available in automations and scripts via the `camera` entity.

> The camera web interface is linked directly from the device info page —
> click **Visit** on the device card to open the camera's local admin UI.

## SD Card

Both sensors reporting total/free space and used percentage require the camera to have
a functioning SD card. If the card is full (100% used with loop recording active),
values will still be reported correctly. If no card is fitted, these sensors will be
unavailable.

## Detection Events

The switches control whether each detection type is active on the camera. Real-time
detection events (motion triggered, person appeared, vehicle detected) require ONVIF
event support — this is planned for a future release. For now, use the VIGI app or
HA's built-in ONVIF integration alongside this one for event notifications.

## Dependencies

`pycryptodome` is installed automatically by Home Assistant from the manifest.
No other dependencies are required.

## Development / Probing

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
  differ slightly (stok token location in the login response), which is handled here.

- **[yetanothercarbot/vigi_camera_lighting](https://github.com/yetanothercarbot/vigi_camera_lighting)**
  — useful reference for VIGI spotlight/night-vision endpoint behaviour.

- The **Home Assistant** developer community for integration architecture patterns.

This project is MIT licensed. See [LICENSE](LICENSE).
