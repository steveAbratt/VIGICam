# Setting Up VIGICam with Frigate

This guide explains the recommended architecture for running VIGICam alongside Frigate
and how to configure each integration to avoid duplicate entities.

---

## Why run both?

VIGICam and Frigate cover different things:

| What you need | Use |
|---------------|-----|
| Live camera stream in HA dashboards | Either — Frigate is more efficient for many streams |
| On-camera AI detection (person, vehicle, intrusion, line crossing) | VIGICam — events come from the camera in real time via ONVIF |
| Frigate AI detection (object recognition, zones, masks) | Frigate |
| Camera controls (night vision, alarm, spotlight, speaker) | VIGICam — Frigate does not control camera hardware |
| SD card sensors and recording status | VIGICam |
| 24/7 continuous recording with motion clips | Frigate |
| PTZ control | VIGICam |

In a typical combined setup, Frigate handles video recording and its own object
detection while VIGICam handles camera hardware control and the camera's own on-device
AI events.

---

## Recommended architecture

```
Camera (RTSP)
  ├── Frigate ← streams video, runs object detection, records clips
  └── VIGICam ← controls camera hardware, receives on-camera events
```

Both integrations talk to the camera independently. VIGICam uses the camera's local
HTTPS API and ONVIF event subscription. Frigate uses the RTSP stream. There is no
conflict between them.

---

## Step-by-step setup

### 1. Add the camera to Frigate first

Configure Frigate with your camera's RTSP stream as you normally would. Give the camera
a name in Frigate's config — you will use this name to identify it later.

Confirm Frigate is receiving the stream and detection is working before proceeding.

### 2. Install VIGICam

If you have not already, install VIGICam via HACS and add the camera:

1. In HA go to **Settings → Devices & Services → Add Integration → VIGI & InSight Cameras**
2. Enter the camera's local IP address and admin credentials
3. The integration will auto-detect the camera's name from its settings

### 3. Configure VIGICam feature groups

VIGICam installs with Camera Stream and Detection Events enabled by default. Since
Frigate is already providing the stream and its own detection, you likely want to
disable those in VIGICam to avoid duplicates:

1. Go to **Settings → Devices & Services → VIGI & InSight Cameras**
2. Find your camera entry and click **Configure**
3. Set the options as follows:

| Feature group | Frigate users — recommended setting |
|---------------|-------------------------------------|
| **Camera Stream** | **Off** — Frigate's stream entity is sufficient |
| **Detection Events** | **Off** — Frigate handles detection; if you also want the camera's own AI events (intrusion zones, line crossing, loitering etc.) leave this **On** |
| **Image Controls** | Your choice — off by default, turn on if you want to tune image settings from HA |

> **On-camera AI vs Frigate AI:** VIGICam's detection events come from the camera's
> own on-device AI (configured in the VIGI app or camera web UI). These include
> intrusion zones, line crossing, and loitering — features Frigate's general object
> detection does not replicate. Many Frigate users keep VIGICam's Detection Events
> **on** for these zone-based sensors even though they turn off Motion and Person
> Detected (which Frigate covers more accurately).

4. Click **Submit** — the integration reloads immediately with the new configuration.

### 4. Verify there are no duplicate entities

After saving:
- Check **Settings → Devices & Services → VIGI & InSight Cameras → [your camera]**
- With Camera Stream off, there should be no `camera.*_stream` entity from VIGICam
- Frigate's camera and object detection entities remain under Frigate's own device

---

## What VIGICam still provides when Camera Stream and Detection Events are off

Even with both disabled, VIGICam continues to provide:

- **Night Vision Mode** select — switch between IR, spotlight, and colour modes on schedule
- **Spotlight** light entity — control brightness from any HA automation
- **Alarm** switches and buttons — arm/disarm the camera's alarm, trigger it from HA
- **Speaker** services (`vigicam.speak`, `vigicam.play_file`) — play audio through the camera
- **PTZ** controls (PTZ cameras only) — pan/tilt/zoom, presets, patrol automations
- **Privacy Mask** switch — blank the camera feed on demand
- **SD card sensors** — used %, total, free, recording status
- **Status LED** switch
- **Microphone/Speaker Mute** switches
- **Diagnostic sensors** — firmware, IP, uptime

These hardware controls are entirely separate from streaming and detection — they are
always available through VIGICam regardless of the feature group settings.

---

## Automation example: Frigate detection → VIGICam alarm

Use Frigate's object detection to trigger the camera's hardware alarm through VIGICam:

```yaml
automation:
  alias: "Person detected by Frigate — trigger camera alarm"
  trigger:
    - platform: state
      entity_id: binary_sensor.front_gate_person_occupancy   # Frigate entity
      to: "on"
  condition:
    - condition: sun
      after: sunset
      before: sunrise
  action:
    - service: button.press
      target:
        entity_id: button.vigi_c540v_alarm_trigger
    - service: vigicam.speak
      data:
        entity_id: camera.vigi_c540v_stream
        message: "Person detected at the front gate"
        tts_engine: tts.cloud
```

---

## Automation example: use camera's zone events to tag Frigate clips

VIGICam's intrusion and line crossing sensors know *which zone* was triggered. Combine
them with Frigate for richer notification context:

```yaml
automation:
  alias: "Intrusion detected — notify with Frigate clip"
  trigger:
    - platform: state
      entity_id: binary_sensor.vigi_c540v_intrusion
      to: "on"
  action:
    - delay: "00:00:05"   # wait for Frigate to capture the clip
    - service: notify.mobile_app_your_phone
      data:
        title: "Intrusion Alert"
        message: "Zone intrusion detected"
        data:
          image: /api/frigate/notifications/{{ trigger.entity_id.split('.')[1] }}/snapshot.jpg
```

---

## Troubleshooting

**VIGICam shows a "Frigate camera link lost" repair notice after this setup**

This notice appears if VIGICam previously detected Frigate at this IP but no longer sees
it (e.g. after a Frigate reinstall or config change). Once Frigate is running again, the
notice clears automatically on the next HA restart or VIGICam reload.

If you removed Frigate entirely, dismiss the notice and re-enable Camera Stream and
Detection Events in VIGICam's Configure options if you want those features back.

**Both integrations show the same camera stream**

Camera Stream is still on in VIGICam. Go to **Settings → Devices & Services →
VIGI & InSight Cameras → [your camera] → Configure** and turn Camera Stream off.

**Person/motion sensors appear in both Frigate and VIGICam**

Detection Events is still on in VIGICam. Disable it in the Configure options if you
do not need the camera's own zone-based sensors. If you want zone events (intrusion,
line crossing) but not generic motion/person, you cannot selectively disable individual
binary sensors — either all detection events are on, or all are off.
