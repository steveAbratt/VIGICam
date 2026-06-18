# VIGICam — Entity & Feature Reference

This page explains what every entity, service, and feature does in practical terms.
For installation instructions see the [README](../README.md).

---

## Contents

- [Feature groups — configuring what entities are created](#feature-groups)
- [OpenAPI — unlocking additional sensors](#openapi--unlocking-additional-sensors)
- [Switches — what they control](#switches)
- [Buttons — what they trigger](#buttons)
- [Numbers — adjustable settings](#numbers)
- [Light entities — spotlight](#light-entities)
- [Select entities](#select-entities)
- [Image controls — advanced camera tuning](#image-controls)
- [Sensors & diagnostics](#sensors--diagnostics)
- [Binary sensors — detection events](#binary-sensors--detection-events)
- [Image entities — last detection snapshot](#image-entities--last-detection-snapshot)
- [Services — automation and scripting](#services)
- [Blueprint: camera announcements](#blueprint-camera-announcements)
- [Blueprint: play audio file](#blueprint-play-audio-file)
- [Playing pre-recorded files](#playing-pre-recorded-files)
- [Dashboard tips](#dashboard-tips)

---

## Feature groups

VIGICam groups its entities into three feature groups that you can enable or disable
independently. This is useful if you are also running Frigate (to avoid duplicate
stream and detection entities) or if you only want the hardware controls without the
full entity set.

### Configuring feature groups

Go to **Settings → Devices & Services → VIGI & InSight Cameras**, click **Configure**
on the camera entry, and use the toggles. Changes take effect immediately — the
integration reloads and creates or removes entities accordingly.

| Feature group | What it includes | Default |
|---------------|-----------------|---------|
| **Camera Stream** | The RTSP live stream entity used in dashboards and `camera.*` services | On |
| **Detection Events** | All binary sensors — motion, person, intrusion, line crossing, smart detection, vehicle, audio anomaly, and all OpenAPI detection sensors. Also controls the ONVIF event subscription. | On |
| **Image Controls** | Camera tuning entities (brightness, contrast, WDR, flip, etc.) in the Configuration category | Off |

> **Frigate users:** If Frigate is already providing the stream and object detection,
> disable Camera Stream and Detection Events to avoid duplicate entities. VIGICam will
> still provide all hardware controls (alarm, spotlight, speaker, PTZ, SD card sensors,
> etc.). See [FRIGATE_SETUP.md](FRIGATE_SETUP.md) for a step-by-step guide.

### Entity cleanup

When you turn a feature group off, VIGICam removes the entities for that group from the
HA entity registry immediately. If you later turn the group back on, those entities are
recreated. There is no manual cleanup needed.

### Notifications

VIGICam raises notifications in HA's **Problems** section (Settings → System → Repairs)
for the following events:

- **SD card removed** — raised when the SD card disappears at runtime. Dismiss if it is
  temporary; use Fix to remove the stale SD card entities if you have removed the card
  permanently.
- **Frigate camera link lost** — raised if Frigate was previously detected at this
  camera's IP address but is no longer present. Guides you to re-enable Camera Stream
  and Detection Events if needed.

---

## OpenAPI — unlocking additional sensors

TP-Link VIGI cameras include a local HTTPS API on port 20443 (the "OpenAPI") in addition
to the standard ONVIF interface. When enabled, this unlocks a second set of sensors and
services that are not available over ONVIF.

### What it unlocks

| Feature | Requires OpenAPI |
|---------|-----------------|
| Vehicle Detected binary sensor | ✓ |
| Audio Anomaly Detected binary sensor | ✓ |
| Loitering Detected binary sensor | ✓ |
| Scene Change Detected binary sensor | ✓ |
| Object Left or Taken binary sensor | ✓ |
| Area Entry Detected binary sensor | ✓ |
| Area Exit Detected binary sensor | ✓ |
| SD Card Recording Duration sensor | ✓ |
| Oldest Recording timestamp sensor | ✓ |
| Record Capacity Remaining sensor | ✓ |
| Video Space Free sensor | ✓ |
| Uptime diagnostic sensor | ✓ |
| `vigicam.ptz_move_to` service | ✓ |
| `vigicam.ptz_save_preset` service | ✓ |
| `vigicam.ptz_delete_preset` service | ✓ |

### How to enable OpenAPI on the camera

1. Open the camera's web UI (browse to `http://<camera-ip>`) and log in.
2. Go to **Settings → Network → OpenAPI**.
3. Enable the **OpenAPI** toggle and click **Save**.

That is all — the integration probes port 20443 at startup and activates the additional
sensors automatically. No further configuration in HA is needed.

> **Firmware note:** OpenAPI requires firmware 2.1.x or later. If the Settings → Network
> menu does not have an OpenAPI section, update the camera firmware via the VIGI app or
> camera web UI.

### Verifying it is active

After enabling OpenAPI on the camera, reload the integration (Settings → Devices & Services
→ VIGICam → three-dot menu → Reload). The additional entities will appear on the device
page. If they do not appear, check the HA log — a connection failure to port 20443 will be
logged under `custom_components.vigicam`.

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

### Privacy Mask

Activates the camera's lens mask — the entire camera image is replaced with a black
screen. The camera continues to operate normally (recording, events) but no image is
transmitted or displayed. Turns off to restore the full image.

Useful for scheduled periods when you do not want the camera recording a room
(bedtime, when trusted people are present, etc.).

#### Example: blank the camera when the household is home

```yaml
automation:
  alias: "Privacy mode when someone arrives home"
  trigger:
    - platform: state
      entity_id: person.your_name
      to: "home"
  action:
    - service: switch.turn_on
      target:
        entity_id: switch.vigi_c540v_privacy_mask
```

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

## Light Entities

### Spotlight

Controls the camera's white-light spotlight. Turning it on switches Night Vision Mode
to Spotlight Always On. Turning it off switches Night Vision Mode to IR Always On (not
off — the camera still needs a night vision mode active).

The brightness slider maps the spotlight's 4 intensity levels (1–4) to the HA range
(1–255) so standard HA light controls work as expected.

> **Night Vision Mode:** The spotlight and IR mode are managed together via Night Vision
> Mode. Setting the Spotlight entity to on forces spotlight mode; turning it off returns
> to IR mode. If you want a different off behaviour (e.g. Auto), change Night Vision Mode
> directly using the Night Vision Mode select entity.

#### Example: turn spotlight on at sunset, off at sunrise

```yaml
automation:
  alias: "Spotlight on at sunset"
  trigger:
    - platform: sun
      event: sunset
  action:
    - service: light.turn_on
      target:
        entity_id: light.vigi_c540v_spotlight
      data:
        brightness: 200   # ~level 3 of 4

automation:
  alias: "Spotlight off at sunrise"
  trigger:
    - platform: sun
      event: sunrise
  action:
    - service: light.turn_off
      target:
        entity_id: light.vigi_c540v_spotlight
```

#### Example: dim spotlight for motion, bright on alarm

```yaml
automation:
  alias: "Dim spotlight on person detection, then brighten on alarm trigger"
  trigger:
    - platform: state
      entity_id: binary_sensor.vigi_c540v_person_detected
      to: "on"
  action:
    - service: light.turn_on
      target:
        entity_id: light.vigi_c540v_spotlight
      data:
        brightness: 80    # level 1 — minimal illumination
    - delay: "00:00:05"
    - service: light.turn_on
      target:
        entity_id: light.vigi_c540v_spotlight
      data:
        brightness: 255   # level 4 — full brightness
```

---

## Image Controls

Image controls are a set of advanced camera tuning entities — brightness, contrast,
WDR, lens correction, and others. They are **disabled by default** and must be enabled
in the integration options.

### Enabling image controls

1. Go to **Settings → Devices & Services → VIGI & InSight Cameras**
2. Click **Configure** on the camera entry
3. Enable **Image Controls**
4. Click **Submit**

After enabling, the new entities appear in the **Configuration** category on the device
page. Each entity is hidden by default and must be individually enabled via the entity's
settings gear if you want it on a dashboard.

### Number entities (0–100 sliders)

| Entity | What it controls |
|--------|-----------------|
| **Image Brightness** | Overall image brightness |
| **Contrast** | Difference between light and dark areas |
| **Saturation** | Colour intensity — 0 is greyscale, 100 is vivid |
| **Chroma** | Colour balance adjustment |
| **Sharpness** | Edge sharpening — higher values increase perceived detail but can introduce noise |
| **WDR Gain** | Wide Dynamic Range gain — controls how aggressively WDR flattens highlights and shadows |
| **Exposure Gain** | Manual gain boost for low-light conditions |

### Switch entities

| Entity | What it controls |
|--------|-----------------|
| **WDR** | Wide Dynamic Range — balances bright and dark areas in the same frame. Useful for cameras facing windows or doorways |
| **HLC** | High Light Compensation — reduces glare from headlights, spotlights, and other strong light sources |
| **Dehaze** | Reduces atmospheric haze in the image. Useful for outdoor cameras in foggy or misty conditions |
| **EIS** | Electronic Image Stabilisation — reduces camera shake. Useful if the camera vibrates |
| **Auto Exposure Anti-flicker** | Prevents flicker caused by artificial lighting at 50 or 60 Hz |
| **Backlight Compensation** | Improves visibility of subjects in front of bright backgrounds |
| **Lens Distortion Correction** | Corrects the barrel distortion from wide-angle lenses |
| **Full Colour People Enhance** | AI enhancement for person subjects in full-colour (spotlight) night mode |
| **Full Colour Vehicle Enhance** | AI enhancement for vehicle subjects in full-colour (spotlight) night mode |

### Select entities

| Entity | Options | What it controls |
|--------|---------|-----------------|
| **Flip** | off, center, flip, mirror | Vertical/horizontal image orientation |
| **Rotate** | off, 90, 180, 270 | Image rotation in degrees |
| **Flicker** | 50hz, 60hz | Sets the anti-flicker frequency to match your mains power frequency |
| **White Balance** | auto, nature, manual, lock | Colour temperature correction mode |
| **Exposure Type** | auto, manual | Whether the camera manages exposure automatically or uses manual exposure gain |

> **Tip:** Most users only need to change Flicker (set to match your country's mains
> frequency — 50 Hz for most of Europe and Asia, 60 Hz for North America) and
> occasionally Flip or Rotate if the camera is mounted upside down. The rest can be
> left at their camera defaults unless you have a specific image quality issue to solve.

---

## Sensors & Diagnostics

> **SD card vs NVR:** All SD card sensors (and the additional OpenAPI SD sensors below)
> only appear when the integration detects an SD card installed in the camera at startup.
> If your camera stores recordings on an NVR or NAS instead of a local SD card, these
> entities will not be created.

### SD Card Used %

Percentage of SD card storage used. Calculated from the camera's accurate byte values
rather than its `percent` field (which is unreliable across firmware versions).

### SD Card Total / SD Card Free

Total and free SD card capacity in GB. Only available if a card is fitted.

### SD Card Status

The camera's reported card status (e.g. `normal`, `full`, `none`). Useful as a trigger
for a HA alert when the card fills up.

#### Example: notify when the SD card is full

```yaml
automation:
  trigger:
    - platform: state
      entity_id: sensor.vigi_c540v_sd_card_status
      to: "full"
  action:
    - service: notify.mobile_app_your_phone
      data:
        title: "SD Card Full"
        message: "Front Gate SD card is full — enable loop recording or swap the card"
```

### SD Card Recording Duration *(requires [OpenAPI](#openapi--unlocking-additional-sensors))*

Total duration of all video recordings stored on the SD card, in hours. Updates on the
30-second poll cycle.

### Oldest Recording *(requires [OpenAPI](#openapi--unlocking-additional-sensors))*

Timestamp of the oldest video recording on the SD card. As the card fills and loop
recording overwrites old footage, this value advances forward. Useful for knowing how
far back your recordings go.

### Record Capacity Remaining *(requires [OpenAPI](#openapi--unlocking-additional-sensors))*

Estimated remaining recording time based on the current video bitrate and available
space, in hours. Drops as the card fills; resets when loop recording overwrites old
footage.

> **Loop recording:** If loop recording is enabled and the video partition is full,
> this sensor reports `0` — this is correct. The camera is continuously overwriting old
> footage rather than writing to free space.

### Video Space Free *(requires [OpenAPI](#openapi--unlocking-additional-sensors))*

Free storage on the SD card allocated specifically for video recordings, in GB. May
differ from **SD Card Free** if the card is formatted with split storage (separate
space for video and Smart Frame images).

> **Loop recording:** If loop recording is enabled and the video partition is full,
> this sensor reports `0` — the camera is using all video space and continuously
> overwriting old footage. Use **SD Card Recording Duration** instead to see how much
> footage is stored.

### Firmware Version *(diagnostic — hidden by default)*

The camera's firmware version string, cleaned to the version number only
(e.g. `2.2.0` rather than `2.2.0 Build 250904 Rel.60109n`).

To show it: open the entity in HA, click the settings gear, and enable it.

### IP Address *(diagnostic — hidden by default)*

The camera's current IP address as reported by the camera itself. Useful for confirming
the static IP assignment is active.

### Connection Type *(diagnostic — hidden by default)*

Whether the camera is using `Static` or `DHCP` addressing.

### Uptime *(OpenAPI — diagnostic — hidden by default)*

The camera's uptime in hours since its last boot. Useful for detecting unexpected
reboots — a sudden drop to zero means the camera restarted.

**Requirements:** [OpenAPI enabled](#openapi--unlocking-additional-sensors).

To show it: open the entity in HA, click the settings gear, and enable it.

#### Example: alert on unexpected camera reboot

```yaml
automation:
  trigger:
    - platform: numeric_state
      entity_id: sensor.vigi_c540v_uptime
      below: 0.1    # just rebooted — uptime under ~6 minutes
  condition:
    - condition: template
      value_template: "{{ (now() - this.last_changed).total_seconds() > 300 }}"
  action:
    - service: notify.mobile_app_your_phone
      data:
        title: "Camera Rebooted"
        message: "Front Gate camera appears to have restarted"
```

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

#### Example: record a clip to HA when motion fires

```yaml
automation:
  trigger:
    - platform: state
      entity_id: binary_sensor.vigi_c540v_motion
      to: "on"
  action:
    - service: camera.record
      target:
        entity_id: camera.vigi_c540v_stream
      data:
        filename: /config/www/clips/motion_{{ now().strftime('%Y%m%d_%H%M%S') }}.mp4
        duration: 10
```

### Person Detected

Fires when the camera's AI detects a person specifically. More precise than Motion —
animals and vehicles do not trigger this. Controlled by Detection Person.

#### Example: send a notification with a snapshot when a person is detected

```yaml
automation:
  trigger:
    - platform: state
      entity_id: binary_sensor.vigi_c540v_person_detected
      to: "on"
  action:
    - service: notify.mobile_app_your_phone
      data:
        title: "Person Detected"
        message: "Someone is at the front door"
        data:
          image: /api/camera_proxy/camera.vigi_c540v_stream
```

### Tamper

Fires when the camera detects it has been covered, turned, or the lens has been
obscured. Requires the Detection Tamper switch to be on. Useful for a security alert
when someone tries to block the camera.

#### Example: alert when the camera is tampered with

```yaml
automation:
  trigger:
    - platform: state
      entity_id: binary_sensor.vigi_c540v_tamper
      to: "on"
  action:
    - service: notify.mobile_app_your_phone
      data:
        title: "Camera Tamper Alert"
        message: "Front Gate camera has been covered or moved"
```

### Intrusion

Fires when someone enters a configured intrusion zone. Zones are drawn in the VIGI app
or camera web UI (Active Defence → Intrusion Detection). This entity only appears once
you have at least one intrusion zone configured and the ONVIF event is seen.

#### Example: flash the alarm light on intrusion

```yaml
automation:
  trigger:
    - platform: state
      entity_id: binary_sensor.vigi_c540v_intrusion
      to: "on"
  action:
    - service: switch.turn_on
      target:
        entity_id: switch.vigi_c540v_alarm_light
    - delay: "00:00:10"
    - service: switch.turn_off
      target:
        entity_id: switch.vigi_c540v_alarm_light
```

### Line Crossing

Fires when something crosses a configured line crossing rule. Lines are drawn in the
VIGI app or camera web UI (Active Defence → Line Crossing). If you have no lines
configured, this sensor will never fire — it is not the same as generic motion.

#### Example: announce when someone crosses the driveway line

```yaml
automation:
  trigger:
    - platform: state
      entity_id: binary_sensor.vigi_c540v_line_crossing
      to: "on"
  action:
    - service: vigicam.speak
      data:
        entity_id: camera.vigi_c540v_stream
        message: "Someone has crossed the driveway boundary"
        tts_engine: tts.cloud
```

### Smart Detection

A catch-all sensor that fires for: vehicle detection, sound detection, loitering,
abandoned objects, and scene change events. These five types all share a single ONVIF
event topic (`IsTPSmartEvent`) on VIGI cameras and cannot be separated at the ONVIF
level.

> **Want individual sensors?** When [OpenAPI is enabled](#openapi--unlocking-additional-sensors),
> separate binary sensors are created for each type — Vehicle Detected, Audio Anomaly,
> Loitering, Scene Change, Object Left or Taken, Area Entry, and Area Exit. The Smart
> Detection sensor continues to fire alongside them.

#### Example: trigger the alarm on any smart detection event

```yaml
automation:
  trigger:
    - platform: state
      entity_id: binary_sensor.vigi_c540v_smart_detection
      to: "on"
  action:
    - service: button.press
      target:
        entity_id: button.vigi_c540v_alarm_trigger
```

### Vehicle Detected *(requires [OpenAPI](#openapi--unlocking-additional-sensors))*

Fires when the camera's AI specifically detects a vehicle. Unlike Smart Detection which
groups all smart event types, this sensor fires only for vehicle events and clears
independently.

**Requirements:** OpenAPI enabled and vehicle detection active on the camera (Detection
Vehicle switch on).

#### Example: alert on vehicle detection at night

```yaml
automation:
  trigger:
    - platform: state
      entity_id: binary_sensor.vigi_c540v_vehicle_detected
      to: "on"
  condition:
    - condition: sun
      after: sunset
      before: sunrise
  action:
    - service: vigicam.speak
      data:
        entity_id: camera.vigi_c540v_stream
        message: "Vehicle detected outside"
        tts_engine: tts.cloud
```

### Audio Anomaly Detected *(requires [OpenAPI](#openapi--unlocking-additional-sensors))*

Fires when the camera detects an unusual sound — e.g. breaking glass, a car alarm, or
raised voices. The sensitivity and sound types that trigger it are configured in the
camera's web UI or VIGI app under **Event → Audio Anomaly Detection**.

#### Example: notify on audio anomaly

```yaml
automation:
  trigger:
    - platform: state
      entity_id: binary_sensor.vigi_c540v_audio_anomaly_detected
      to: "on"
  action:
    - service: notify.mobile_app_your_phone
      data:
        title: "Audio Anomaly"
        message: "Unusual sound detected at Front Gate"
```

### Loitering Detected *(requires [OpenAPI](#openapi--unlocking-additional-sensors))*

Fires when a person or vehicle remains in the camera's field of view longer than the
configured loitering time threshold. The threshold is set in the camera's web UI or
VIGI app under **Event → Loitering Detection**.

#### Example: turn on a light when someone loiters

```yaml
automation:
  trigger:
    - platform: state
      entity_id: binary_sensor.vigi_c540v_loitering_detected
      to: "on"
  action:
    - service: light.turn_on
      target:
        entity_id: light.driveway
```

### Scene Change Detected *(requires [OpenAPI](#openapi--unlocking-additional-sensors))*

Fires when the camera detects a significant change to its scene — typically the camera
being moved, re-aimed, or having something large placed in front of it. Distinct from
[Tamper](#tamper) detection which focuses on the lens being covered.

#### Example: alert when the camera is moved

```yaml
automation:
  trigger:
    - platform: state
      entity_id: binary_sensor.vigi_c540v_scene_change_detected
      to: "on"
  action:
    - service: notify.mobile_app_your_phone
      data:
        title: "Camera Moved"
        message: "Front Gate camera scene has changed unexpectedly"
```

### Object Left or Taken *(requires [OpenAPI](#openapi--unlocking-additional-sensors))*

Fires when the camera detects that an object has been left in the frame (abandoned
object) or that an object that was present has been removed. The camera monitors a
defined zone for objects appearing or disappearing over time.

#### Example: alert on an abandoned object

```yaml
automation:
  trigger:
    - platform: state
      entity_id: binary_sensor.vigi_c540v_object_left_taken
      to: "on"
  action:
    - service: notify.mobile_app_your_phone
      data:
        title: "Object Alert"
        message: "Something left or removed at Front Gate"
```

### Area Entry Detected *(requires [OpenAPI](#openapi--unlocking-additional-sensors))*

Fires when a person or vehicle enters a configured virtual area. Areas are drawn in
the VIGI app or camera web UI under **Event → Area Entry Detection**. Unlike
[Line Crossing](#line-crossing) which fires on crossing a line, this fires when
something enters an enclosed zone.

#### Example: sound the alarm on area entry

```yaml
automation:
  trigger:
    - platform: state
      entity_id: binary_sensor.vigi_c540v_area_entry_detected
      to: "on"
  action:
    - service: vigicam.play_audio
      data:
        entity_id: camera.vigi_c540v_stream
        slot: 0       # built-in alarm tone
        times: 3
```

### Area Exit Detected *(requires [OpenAPI](#openapi--unlocking-additional-sensors))*

Fires when a person or vehicle exits a configured virtual area. Mirrors Area Entry
Detected but triggers on departure. Useful for detecting when a vehicle leaves a
monitored zone.

#### Example: notify when a vehicle leaves the property

```yaml
automation:
  trigger:
    - platform: state
      entity_id: binary_sensor.vigi_c540v_area_exit_detected
      to: "on"
  action:
    - service: notify.mobile_app_your_phone
      data:
        title: "Vehicle Left"
        message: "Vehicle exited the monitored area"
```

### Loop Recording

Indicates whether the camera's SD card loop recording is active. This is polled (not
real-time) — it updates on the 30-second coordinator cycle. Provided for monitoring
purposes only; loop recording cannot be enabled/disabled via the local API.

#### Example: alert when loop recording stops unexpectedly

```yaml
automation:
  trigger:
    - platform: state
      entity_id: binary_sensor.vigi_c540v_loop_recording
      to: "off"
      for: "00:02:00"   # wait 2 min to avoid false alerts on startup
  action:
    - service: notify.mobile_app_your_phone
      data:
        title: "Recording Stopped"
        message: "Front Gate loop recording is no longer active"
```

---

## Image Entities — Last Detection Snapshot

### Last Detection

Shows a snapshot from the most recent detection event. The entity appears on all cameras
and updates automatically each time any detection fires — intrusion, line crossing,
motion, person detected, or smart detection. It uses the same real-time ONVIF
subscription as the binary sensors, so it refreshes within seconds of a detection,
not on the 30-second poll cycle.

#### How the image is captured

The integration detects at startup whether your camera supports Smart Frame capture and
uses the best available method:

**Smart Frame (AI-cropped image)** — used when the camera supports it (most VIGI cameras
with an SD card formatted for split storage). The image is an AI-cropped close-up of the
detected subject (person, vehicle, etc.) rather than the full frame. The camera saves this
to the SD card; the integration downloads it ~3 seconds after the event.

**RTSP snapshot (full-frame still)** — used as a fallback on cameras that do not support
Smart Frame (e.g. VIGI C540V). When a detection event fires, the integration grabs a
single frame from the live RTSP stream ~2 seconds after the event. The image is the full
camera frame at the moment of capture.

The `source` attribute on the entity tells you which method was used.

**Attributes available on the entity:**

| Attribute | Example | Description |
|---|---|---|
| `detection_type` | `motion`, `smart_event` | ONVIF event type that triggered the grab |
| `source` | `smart_frame`, `rtsp_snapshot` | Which capture method was used |
| `smart_frame_label` | `Person`, `Smart Detection` | AI label (Smart Frame only) |
| `file_id` | `00010000000260` | Internal SD card file ID (Smart Frame only) |

#### Requirements for Smart Frame capture

Smart Frame images require both of the following to be configured on the camera:

1. **Smart Frame capture enabled** — in the camera's web UI or VIGI app go to
   **Event → Smart Frame** (also called Smart Capture) and turn it on.

2. **SD card formatted for image capture** — go to **Storage → Format** and choose the
   format option that includes image storage. If the card has only been used for video
   recording it may need to be reformatted.

> If Smart Frame capture is not configured, the integration falls back to RTSP snapshot
> automatically — the entity will still populate, just with a full-frame still instead of
> an AI-cropped image.

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
  times: 3                    # optional — how many times to play (default 1)
  pause: 2.0                  # optional — seconds between repeats (default 1.0)
```

**Message templates are supported:**
```yaml
message: "{{ now().strftime('%-I:%M %p') }} — person detected at the front door"
```

**Limits:** Keep messages to around 10 seconds or less. The camera's hard limit is
15 seconds / 256 KB of audio. Messages that exceed this will fail with a log error —
`vigicam.speak` will not play anything in that case. A typical spoken sentence is
3–5 seconds.

---

### `vigicam.play_file` — Play a pre-recorded audio file

Fetches an audio file, converts it to the camera-compatible format (8 kHz mono WAV
via ffmpeg), uploads it to a slot, and plays it. Works with any format ffmpeg supports
— WAV, MP3, OGG, etc.

This is the service to use when you have a fixed sound clip you want to play rather
than generated speech. Record your clip on any device, drop it into HA, and play it
on demand from any automation.

```yaml
service: vigicam.play_file
data:
  entity_id: camera.vigi_c540v_stream
  url: http://192.168.1.x:8123/media/local/alert.wav
  slot: 102       # optional — 101, 102, or 103 (default 101)
  times: 2        # optional — how many times to play (default 1)
  pause: 1.5      # optional — seconds between repeats (default 1.0)
```

**Limit:** The camera hard limit is 15 seconds / 256 KB after conversion. Longer files
will fail with a log error. A 15-second clip at 8 kHz mono 16-bit is around 240 KB.

---

#### Ways to specify the file

**1. HA media browser (recommended for most users)**

Upload your file via the HA Media section (sidebar → Media → My media → Upload) then
reference it with the media browser URL. The integration resolves these URLs internally
without requiring a token — you do not need to set up authentication:

```yaml
url: http://192.168.1.x:8123/media/local/alert.wav
```

Files uploaded via the media browser are stored at `/config/media/` on the HA host.

**2. HA `www/` folder (publicly accessible static files)**

Files placed in your HA config's `www/` folder are served at `/local/` without
authentication. This also works directly:

```yaml
url: http://192.168.1.x:8123/local/alert.wav
```

Place the file in `/config/www/` on the HA host.

**3. Absolute file path on the HA host**

If you know the file's path on the machine running Home Assistant, you can reference
it directly — useful in automations templated by a script:

```yaml
url: /config/media/alert.wav
```

Any absolute path readable by the HA process works, including `/config/www/`, addon
media directories, or NAS mounts.

**4. External URL**

Any URL accessible from the HA host works — a NAS web server, a public CDN, etc.:

```yaml
url: http://192.168.1.100:8080/sounds/alert.mp3
```

---

#### Choosing a slot

| Slot | Recommended use |
|------|----------------|
| 101 | Dynamic content — `vigicam.speak` and `vigicam.play_file` both default to this. Gets overwritten each time, which is fine. |
| 102 | Fixed sound A — upload once (or from an HA startup automation), play from slot without re-uploading. |
| 103 | Fixed sound B — same pattern as 102. |

If you use the same slot for both `speak` and `play_file`, whichever runs last wins —
the previous upload is replaced. Use different slots if you need both at the same time.

---

#### Example: play a clip on person detection

```yaml
automation:
  trigger:
    - platform: state
      entity_id: binary_sensor.vigi_c540v_person_detected
      to: "on"
  action:
    - service: vigicam.play_file
      data:
        entity_id: camera.vigi_c540v_stream
        url: http://192.168.1.x:8123/media/local/there_you_are.wav
        times: 2
        pause: 1.0
```

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

#### Example: pre-load a chime on HA startup

Upload a fixed chime to slot 102 when HA starts so it is ready to play without
delay in any automation:

```yaml
automation:
  trigger:
    - platform: homeassistant
      event: start
  action:
    - service: vigicam.upload_audio
      data:
        entity_id: camera.vigi_c540v_stream
        url: http://192.168.1.x:8123/media/local/chime.wav
        slot: 102
        name: chime
```

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

#### Example: play a pre-uploaded clip on motion

Upload your clip once using `vigicam.upload_audio` (or `vigicam.play_file`), then
trigger it cheaply on every event without re-uploading:

```yaml
automation:
  trigger:
    - platform: state
      entity_id: binary_sensor.vigi_c540v_motion
      to: "on"
  action:
    - service: vigicam.play_audio
      data:
        entity_id: camera.vigi_c540v_stream
        slot: 102
        times: 1
```

---

### `vigicam.delete_audio` — Remove a custom audio slot

Deletes a custom audio slot from the camera. Safe to call on an empty slot.

```yaml
service: vigicam.delete_audio
data:
  entity_id: camera.vigi_c540v_stream
  slot: 101
```

#### Example: clear all custom slots on HA shutdown

```yaml
automation:
  trigger:
    - platform: homeassistant
      event: shutdown
  action:
    - repeat:
        count: 3
        sequence:
          - service: vigicam.delete_audio
            data:
              entity_id: camera.vigi_c540v_stream
              slot: "{{ 101 + repeat.index - 1 }}"
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

#### Example: pan right for 2 seconds when a button is pressed

```yaml
automation:
  trigger:
    - platform: state
      entity_id: input_button.pan_right
  action:
    - service: vigicam.ptz
      data:
        entity_id: camera.vigi_c540v_stream
        direction: right
        speed: 0.5
        duration: 2
```

---

### `vigicam.ptz_stop` — Stop camera movement *(PTZ cameras only)*

Stops any in-progress PTZ movement immediately. Use this after a `vigicam.ptz` call
that did not include a `duration`.

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

#### Example: return to home position at night

```yaml
automation:
  trigger:
    - platform: sun
      event: sunset
  action:
    - service: vigicam.goto_preset
      data:
        entity_id: camera.vigi_c540v_stream
        preset: "Home"
```

---

### `vigicam.ptz_move_to` — Move to absolute position *(PTZ cameras only, requires [OpenAPI](#openapi--unlocking-additional-sensors))*

Moves the camera to an absolute pan/tilt/zoom position. Useful for precise positioning
without needing a named preset, or for building patrol automations across exact coordinates.

```yaml
service: vigicam.ptz_move_to
data:
  entity_id: camera.vigi_c540v_stream
  pan: 120.0    # horizontal angle (degrees)
  tilt: -10.0   # vertical angle (degrees, negative = down)
  zoom: 1.0     # zoom level (1.0 = wide)
```

> **Tip:** Use the VIGI app's PTZ control to move the camera to the position you want,
> then save it as a preset with `vigicam.ptz_save_preset`. Read back the saved preset's
> pan/tilt/zoom to find the coordinate values for future `ptz_move_to` calls.

---

### `vigicam.ptz_save_preset` — Save current position as a preset *(PTZ cameras only, requires [OpenAPI](#openapi--unlocking-additional-sensors))*

Saves the camera's current position as a named preset. The preset name appears in the
**PTZ Preset** select entity immediately after saving and can then be used with
`vigicam.goto_preset`.

```yaml
service: vigicam.ptz_save_preset
data:
  entity_id: camera.vigi_c540v_stream
  name: "Entrance View"
```

#### Example: move to a position then save it

```yaml
automation:
  trigger:
    - platform: time
      at: "22:00:00"
  action:
    - service: vigicam.ptz_move_to
      data:
        entity_id: camera.vigi_c540v_stream
        pan: 180.0
        tilt: -5.0
        zoom: 1.0
    - delay: "00:00:02"
    - service: vigicam.ptz_save_preset
      data:
        entity_id: camera.vigi_c540v_stream
        name: "Night Watch"
```

---

### `vigicam.ptz_delete_preset` — Delete a PTZ preset *(PTZ cameras only, requires [OpenAPI](#openapi--unlocking-additional-sensors))*

Removes a named preset from the camera. The preset will no longer appear in the PTZ
Preset select entity after deletion.

```yaml
service: vigicam.ptz_delete_preset
data:
  entity_id: camera.vigi_c540v_stream
  name: "Old Position"
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
| **Repeat count** | How many times to play the announcement. Default 1. The integration waits for the clip to finish before playing again. |
| **Pause between repeats** | Extra gap between each play in seconds. Default 1 s. Only applies when Repeat count > 1. |

4. Click **Save**, give the automation a name, and enable it.

---

### Example: motion-triggered announcement

Trigger: `binary_sensor.vigi_c540v_motion` changes to `on`
Message: `"Motion detected at the front door — {{ now().strftime('%-I:%M %p') }}"`
TTS engine: `tts.cloud`
Language: `en-GB`

This creates a spoken alert with the time of detection, played immediately through the
camera's speaker every time motion is detected.

> **Tip:** Add a condition to the automation (after importing the blueprint) to prevent
> announcements at night or when you are home — use the HA automation editor to add
> condition blocks below the blueprint's generated action.

---

## Blueprint: Play Audio File

The integration includes a second blueprint for playing a pre-recorded audio file through
the camera on any trigger — no TTS engine required.

### Installing the blueprint

**Option A — Import from GitHub (recommended):**

1. In HA go to **Settings → Automations & Scenes → Blueprints**
2. Click **Import Blueprint** (bottom right)
3. Paste this URL:
   ```
   https://raw.githubusercontent.com/steveAbratt/VIGICam/main/blueprints/automation/vigicam/camera_play_file.yaml
   ```
4. Click **Preview**, then **Import Blueprint**

**Option B — Manual copy:**

Copy the `blueprints/` folder from the repository into your HA config directory
(alongside `custom_components/`), then go to Developer Tools → YAML → Reload All YAML.

---

### Getting your audio file URL

1. Upload the file via the HA sidebar → **Media** → **My media** → Upload
2. Find the file in the media browser, click the three-dot menu → **Copy URL**
3. The URL will look like: `http://192.168.1.x:8123/media/local/alert.wav`
4. Paste this into the **Audio file URL** field in the blueprint

Files in the HA `www/` folder (`/local/` URLs) and absolute file paths on the HA host
also work — see [vigicam.play_file](#vigicamplay_file--play-a-pre-recorded-audio-file) for all options.

---

### Creating a play-file automation

After importing the blueprint:

1. Go to **Settings → Automations & Scenes → Blueprints**
2. Find **VIGI Camera — Play Audio File on Trigger** and click **Create Automation**
3. Fill in the fields:

| Field | What to set |
|-------|------------|
| **Trigger** | Whatever should fire the playback — a motion sensor, a button press, a time pattern, etc. |
| **Camera** | Select your VIGI camera entity |
| **Audio file URL or path** | URL from the media browser, a `/local/` URL, or an absolute file path on the HA host |
| **Audio slot** | Slot to upload to (101–103). Slot 101 is fine unless you need to keep a fixed sound separate from TTS. |
| **Repeat count** | How many times to play the clip. Default 1. |
| **Pause between repeats** | Extra gap between each play in seconds. Default 1 s. |

4. Click **Save**, give the automation a name, and enable it.

---

### Example: play a clip on person detection

Trigger: `binary_sensor.vigi_c540v_person_detected` changes to `on`
Audio file URL: `http://192.168.1.x:8123/media/local/there_you_are.wav`
Repeat count: `2`
Pause: `1`

This plays the clip twice every time a person is detected.

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
title: Front Door Camera Alarm
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
