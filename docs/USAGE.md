# VIGICam — Entity & Feature Reference

This page explains what every entity, service, and feature does in practical terms.
For installation instructions see the [README](../README.md).

---

## Contents

- [Switches — what they control](#switches)
- [Buttons — what they trigger](#buttons)
- [Numbers — adjustable settings](#numbers)
- [Select entities](#select-entities)
- [Sensors & diagnostics](#sensors--diagnostics)
- [Binary sensors — detection events](#binary-sensors--detection-events)
- [Services — automation and scripting](#services)
- [Blueprint: camera announcements](#blueprint-camera-announcements)
- [Dashboard tips](#dashboard-tips)

---

## Switches

Switches appear in the Controls section of the device page. They persist their state across
HA restarts and reflect the actual camera configuration.

### Alarm

The master alarm enable/disable. When **off**, detection events will not trigger any
alarm response (no flashing light, no sound). When **on**, the camera responds to
detection events according to the Alarm Light and Alarm Sound switches below.

> This does **not** prevent detection events from firing binary sensors in HA — it only
> controls the camera's own on-device response.

### Alarm Light

When on, the camera's spotlight flashes when an alarm is triggered. Requires Alarm to
also be on to activate during detection events. Can be turned off independently if you
want a sound-only alarm.

### Alarm Sound

When on, the camera plays its configured alarm sound through its speaker when triggered.
Which sound plays (Alarm Tone / Ring Tone / Custom audio) is set in the camera's web UI
under Active Defence → Sound Alarm → Sound Type. Use the camera's web UI to change
the sound type; this switch just enables or disables the audio response.

> **Alarm Sound Repetitions** (number entity, see below) controls how many times the
> sound plays per trigger. The default is 5 repetitions (~10 seconds total).

### Detection Motion

Enables or disables motion detection on the camera. When off, the camera stops detecting
motion — the Motion binary sensor in HA will stop updating and the camera will not
respond to movement.

### Detection Person

Enables or disables AI person detection. Requires motion detection to be active on some
firmware versions. When on, the camera uses on-device AI to distinguish people from
general motion — the Person Detected binary sensor will fire when a person is seen.

### Detection Vehicle

Same as Detection Person but for vehicles. On some cameras this is not available (the
entity won't be created).

### Detection Tamper

Enables or disables tamper detection. The camera fires an event when its field of view
is covered, moved significantly, or the lens is obscured. Requires camera support
(appears automatically if the camera reports it).

### Microphone Mute

Silences the camera's microphone input. Audio from the camera will not be captured in
recordings or the live stream.

### Speaker Mute

Silences the camera's speaker output. This overrides all other audio — muting the
speaker stops both alarm sounds and any audio played via `vigicam.speak` or
`vigicam.play_audio`.

### Status LED

Turns the camera's front status LED on or off. Useful at night or in locations where
you don't want a visible indicator that a camera is active.

---

## Buttons

Buttons perform a one-time action when pressed. They appear in the Controls section
alongside the switches.

### Alarm Trigger

Immediately fires the alarm as if a detection event had occurred — light flashes (if
Alarm Light is on) and/or sound plays (if Alarm Sound is on). The alarm runs for
`Alarm Sound Repetitions` cycles then stops automatically.

> This is a **manual test / instant trigger** — it bypasses the master Alarm switch,
> so it works even if Alarm is off. Useful for testing your alarm setup or triggering
> an alert from an HA automation regardless of detection settings.

### Alarm Stop

Cancels an in-progress alarm immediately. Press this to silence the alarm early rather
than waiting for it to finish its repetition cycle.

### PTZ Pan Left / Right / Tilt Up / Down / Zoom In / Zoom Out

*(PTZ cameras only — e.g. VIGI C540V)*

Each button jogs the camera for 1 second in that direction then stops. Use these on a
dashboard card to manually aim the camera.

For finer control (custom speed and duration) use the `vigicam.ptz` service from
automations or Developer Tools.

---

## Numbers

Number entities let you set a value from within HA without opening the camera's web UI.

### Speaker Volume

Sets the camera speaker output level (0–100%). Affects alarm sounds, TTS announcements
played via `vigicam.speak`, and any other audio the camera produces.

### Motion Sensitivity

Controls how sensitive motion detection is (0–100). At low values the camera ignores
small movements (animals, foliage in wind). At high values it triggers on minor
changes. Tune this to reduce false positives.

### Spotlight Intensity

Adjusts the brightness of the white-light spotlight (levels 1–4). Only relevant when
Night Vision Mode is set to Spotlight Always On or when the camera auto-switches to
spotlight mode at night.

### Alarm Sound Repetitions

Controls how many times the alarm sound plays each time the alarm fires. Default is 5
(~10 seconds total at ~2 seconds per play). Set to 1 for a single short alert, or
higher for a more persistent alarm.

This affects both detection-triggered alarms and the manual Alarm Trigger button. It
does **not** affect audio played via `vigicam.play_audio` or `vigicam.speak` — those
always play once per call.

---

## Select Entities

### Night Vision Mode

Switches how the camera handles low-light conditions:

| Option | What it does |
|--------|-------------|
| IR Auto | Camera decides when to switch to IR (night vision); default for most scenarios |
| IR Always On | Infrared always active — black and white image at all times |
| Spotlight Always On | White-light spotlight always on — colour image in the dark |
| Colour | Forces colour mode without spotlight (low-light performance degrades) |
| Off | No IR or spotlight — camera image in available light only |

### PTZ Preset *(PTZ cameras only)*

A dropdown of all named presets saved on the camera. Selecting a preset immediately
moves the camera to that position. Presets are created and named in the VIGI app or
the camera's web UI — this integration reads them automatically on startup.

For automations, use the `vigicam.goto_preset` service (which accepts a preset name)
rather than `select.select_option` (which requires an entity ID).

---

## Sensors & Diagnostics

### SD Card Used %

Percentage of SD card storage used. Calculated from the camera's accurate byte values
rather than its `percent` field (which is unreliable across firmware versions).

### SD Card Total / SD Card Free

Total and free SD card capacity in GB. Only available if a card is fitted.

### SD Card Status

The camera's reported card status (e.g. `normal`, `full`, `none`). Useful as a trigger
for a HA alert when the card fills up.

### Firmware Version *(diagnostic — hidden by default)*

The camera's firmware version string, cleaned to the version number only
(e.g. `2.2.0` rather than `2.2.0 Build 250904 Rel.60109n`).

To show it: open the entity in HA, click the settings gear, and enable it.

### IP Address *(diagnostic — hidden by default)*

The camera's current IP address as reported by the camera itself. Useful for confirming
the static IP assignment is active.

### Connection Type *(diagnostic — hidden by default)*

Whether the camera is using `Static` or `DHCP` addressing.

---

## Binary Sensors — Detection Events

These sensors fire in real time via an ONVIF event subscription that starts automatically
when the integration loads. They do not wait for the 30-second coordinator poll — events
arrive within a second or two of the camera detecting them.

All sensors auto-clear 15 seconds after the last event if the camera does not send an
explicit "no longer active" event.

### Motion

Fires when the camera detects any motion in the frame. Controlled by the Detection Motion
switch and Motion Sensitivity number entity.

### Person Detected

Fires when the camera's AI detects a person specifically. More precise than Motion —
animals and vehicles do not trigger this. Controlled by Detection Person.

### Tamper

Fires when the camera detects it has been covered, turned, or the lens has been
obscured. Requires the Detection Tamper switch to be on. Useful for a security alert
when someone tries to block the camera.

### Intrusion

Fires when someone enters a configured intrusion zone. Zones are drawn in the VIGI app
or camera web UI (Active Defence → Intrusion Detection). This entity only appears once
you have at least one intrusion zone configured and the ONVIF event is seen.

### Line Crossing

Fires when something crosses a configured line crossing rule. Lines are drawn in the
VIGI app or camera web UI (Active Defence → Line Crossing). If you have no lines
configured, this sensor will never fire — it is not the same as generic motion.

### Smart Detection

A catch-all sensor that fires for: vehicle detection, sound detection, loitering,
abandoned objects, and scene change events. These five types all share a single ONVIF
event topic (`IsTPSmartEvent`) on VIGI cameras and cannot be separated at the ONVIF
level. If you need to distinguish them, check the VIGI app's event log.

### Loop Recording

Indicates whether the camera's SD card loop recording is active. This is polled (not
real-time) — it updates on the 30-second coordinator cycle. Provided for monitoring
purposes only; loop recording cannot be enabled/disabled via the local API.

---

## Services

Services are called from automations, scripts, or Developer Tools → Services.
All services require an `entity_id` of a camera entity from this integration.

---

### `vigicam.speak` — TTS announcement *(recommended)*

The easiest way to play a spoken message through the camera. Handles everything
automatically: generates TTS audio, converts it to the camera-compatible format,
uploads it, and plays it.

```yaml
service: vigicam.speak
data:
  entity_id: camera.vigi_c540v_stream
  message: "Motion detected at the front gate"
  tts_engine: tts.cloud       # your configured TTS entity
  language: en-GB             # optional — defaults to engine default
  slot: 101                   # optional — 101, 102, or 103 (default 101)
```

**Message templates are supported:**
```yaml
message: "{{ now().strftime('%-I:%M %p') }} — person detected at the stables"
```

**Limits:** Keep messages to around 10 seconds or less. The camera's hard limit is
15 seconds / 256 KB of audio. Messages that exceed this will fail with a log error —
`vigicam.speak` will not play anything in that case. A typical spoken sentence is
3–5 seconds.

---

### `vigicam.upload_audio` — Upload a file to a camera slot

Uploads an audio file to one of three custom slots on the camera (101, 102, 103).
The file is fetched from a URL accessible to HA (local file, TTS proxy URL, etc.).

```yaml
service: vigicam.upload_audio
data:
  entity_id: camera.vigi_c540v_stream
  url: "http://your-ha-ip:8123/local/gate_alert.wav"
  slot: 102           # 101, 102, or 103
  name: gate_alert    # optional label stored on camera
  play: false         # set true to play immediately after upload
```

**Supported formats:**

| Format | Max duration | Max file size | Sample rate |
|--------|-------------|--------------|------------|
| WAV (mono PCM) | 15 s | 256 KB | 8 kHz exactly |
| MP3 (mono) | 15 s | 128 KB | ≤ 64 kbps |

Use `vigicam.speak` instead if you want TTS — it handles format conversion automatically.

**Slot guide:**

| Slot | Recommended use |
|------|----------------|
| 101 | Dynamic / TTS content — overwrite each time |
| 102 | Fixed sound A — upload once, keep permanently |
| 103 | Fixed sound B — upload once, keep permanently |

---

### `vigicam.play_audio` — Play a camera audio slot

Plays an audio slot through the camera speaker. Useful in automations where you have
pre-uploaded a fixed sound to slot 102 or 103 and want to trigger it.

```yaml
service: vigicam.play_audio
data:
  entity_id: camera.vigi_c540v_stream
  slot: 102     # 0 = Alarm Tone, 1 = Ring Tone, 101–103 = custom uploaded
  times: 2      # optional — how many times to play (default 1)
  pause: 1.5    # optional — seconds between repeats (default 1.0)
```

Built-in slots 0 and 1 play the camera's built-in Alarm Tone and Ring Tone respectively,
without triggering the full alarm response.

---

### `vigicam.delete_audio` — Remove a custom audio slot

Deletes a custom audio slot from the camera. Safe to call on an empty slot.

```yaml
service: vigicam.delete_audio
data:
  entity_id: camera.vigi_c540v_stream
  slot: 101
```

---

### `vigicam.ptz` — Move the camera *(PTZ cameras only)*

Starts continuous movement in a direction. If `duration` is given the camera stops
automatically; otherwise call `vigicam.ptz_stop` to stop it.

```yaml
service: vigicam.ptz
data:
  entity_id: camera.vigi_c540v_stream
  direction: right     # left / right / up / down / zoom_in / zoom_out
  speed: 0.4           # 0.1–1.0 (default 0.3)
  duration: 3          # seconds before auto-stop (optional)
```

---

### `vigicam.ptz_stop` — Stop camera movement *(PTZ cameras only)*

```yaml
service: vigicam.ptz_stop
data:
  entity_id: camera.vigi_c540v_stream
```

---

### `vigicam.goto_preset` — Go to a named PTZ preset *(PTZ cameras only)*

Moves the camera to a saved preset by name. Preset names must match exactly as they
appear in the PTZ Preset select entity (case-sensitive).

```yaml
service: vigicam.goto_preset
data:
  entity_id: camera.vigi_c540v_stream
  preset: "Full Stable Yard"
```

---

## Blueprint: Camera Announcements

The integration includes an automation blueprint that sets up the full TTS announcement
pipeline with a simple form — no YAML required.

### Installing the blueprint

**Option A — Import from GitHub (recommended):**

1. In HA go to **Settings → Automations & Scenes → Blueprints**
2. Click **Import Blueprint** (bottom right)
3. Paste this URL:
   ```
   https://raw.githubusercontent.com/steveAbratt/VIGICam/main/blueprints/automation/vigicam/camera_announce.yaml
   ```
4. Click **Preview**, then **Import Blueprint**

**Option B — Manual copy:**

Copy the `blueprints/` folder from the repository into your HA config directory
(alongside `custom_components/`), then go to Developer Tools → YAML → Reload All YAML.

---

### Creating an announcement automation

After importing the blueprint:

1. Go to **Settings → Automations & Scenes → Blueprints**
2. Find **VIGI Camera — Speak on Trigger** and click **Create Automation**
3. Fill in the fields:

| Field | What to set |
|-------|------------|
| **Trigger** | Whatever should fire the announcement — a motion sensor going `on`, a time pattern, a button press, etc. |
| **Camera** | Select your VIGI camera entity |
| **Announcement message** | The text to speak. Can be a fixed phrase or a Jinja2 template. **Keep under ~10 seconds of speech** (camera hard limit: 15 s / 256 KB). |
| **TTS engine** | Your configured TTS entity (e.g. `tts.cloud` for Nabu Casa, `tts.piper` for local Piper) |
| **Language** | Optional language code (e.g. `en-GB`). Leave blank for engine default. |
| **Audio slot** | Which custom slot to use (101–103). Slot 101 is fine for all TTS use — it gets overwritten each time. |

4. Click **Save**, give the automation a name, and enable it.

---

### Example: motion-triggered announcement

Trigger: `binary_sensor.vigi_c540v_motion` changes to `on`
Message: `"Motion detected at the stables — {{ now().strftime('%-I:%M %p') }}"`
TTS engine: `tts.cloud`
Language: `en-GB`

This creates a spoken alert with the time of detection, played immediately through the
camera's speaker every time motion is detected.

> **Tip:** Add a condition to the automation (after importing the blueprint) to prevent
> announcements at night or when you are home — use the HA automation editor to add
> condition blocks below the blueprint's generated action.

---

## Dashboard Tips

### PTZ control card

Combine the live stream with directional jog buttons:

```yaml
type: vertical-stack
cards:
  - type: camera
    entity: camera.vigi_c540v_stream
    live_view: true
  - type: grid
    columns: 3
    square: true
    cards:
      - type: button
        entity: button.vigi_c540v_ptz_pan_left
        show_name: false
        icon: mdi:pan-left
      - type: button
        entity: button.vigi_c540v_ptz_tilt_up
        show_name: false
        icon: mdi:pan-up
      - type: button
        entity: button.vigi_c540v_ptz_pan_right
        show_name: false
        icon: mdi:pan-right
      - type: button
        entity: button.vigi_c540v_ptz_zoom_out
        show_name: false
        icon: mdi:magnify-minus
      - type: button
        entity: button.vigi_c540v_ptz_tilt_down
        show_name: false
        icon: mdi:pan-down
      - type: button
        entity: button.vigi_c540v_ptz_zoom_in
        show_name: false
        icon: mdi:magnify-plus
  - type: entity
    entity: select.vigi_c540v_ptz_preset
    name: Go to preset
```

> Replace `vigi_c540v` with your camera's entity slug. Find it by opening any entity on
> the device page and copying the prefix before the first `_ptz_` or `_motion` etc.

### Alarm control card

Quick card showing alarm status and manual trigger:

```yaml
type: entities
title: Stables Camera Alarm
entities:
  - entity: switch.vigi_c540v_alarm
    name: Alarm enabled
  - entity: switch.vigi_c540v_alarm_sound
    name: Sound
  - entity: switch.vigi_c540v_alarm_light
    name: Light flash
  - entity: number.vigi_c540v_alarm_sound_repetitions
    name: Repeat count
  - entity: button.vigi_c540v_alarm_trigger
    name: Trigger now
  - entity: button.vigi_c540v_alarm_stop
    name: Stop alarm
```
