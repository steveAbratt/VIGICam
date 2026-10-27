# VIGICam — Entity & Feature Reference

This page explains what every entity, service, and feature does in practical terms.
For installation instructions see the [README](../README.md).

---

## Contents

- [Feature groups — configuring what entities are created](#feature-groups)
  - [Stream quality — sub-stream vs main HD stream](#stream-quality)
- [OpenAPI — unlocking additional sensors](#openapi--unlocking-additional-sensors)
- [Switches — what they control](#switches)
- [Buttons — what they trigger](#buttons)
- [Numbers — adjustable settings](#numbers)
- [Light entities — spotlight](#light-entities)
- [Select entities](#select-entities)
- [Image controls — advanced camera tuning](#image-controls)
- [Sensors & diagnostics](#sensors--diagnostics)
- [Binary sensors — detection events](#binary-sensors--detection-events)
- [Capability diagnostic sensors](#capability-diagnostic-sensors)
- [Image entities — last detection snapshot](#image-entities--last-detection-snapshot)
- [Services — automation and scripting](#services)
- [Blueprint: camera announcements](#blueprint-camera-announcements)
- [Blueprint: play audio file](#blueprint-play-audio-file)
- [Playing pre-recorded files](#vigicamplay_file--play-a-pre-recorded-audio-file)
- [Dashboard tips](#dashboard-tips)
- [Troubleshooting](#troubleshooting)

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

| Feature group / option | What it controls | Default |
|------------------------|-----------------|---------|
| **Camera Stream** | The RTSP live stream entity used in dashboards and `camera.*` services | On |
| **Use main stream (HD, stream1)** | Which RTSP stream is used for live view — see [Stream quality](#stream-quality) below | Off (sub-stream) |
| **Detection Events** | All binary sensors — motion, person, intrusion, line crossing, smart detection, vehicle, audio anomaly, and all OpenAPI detection sensors. Also controls the ONVIF event subscription. | On |
| **Image Controls** | Camera tuning entities (brightness, contrast, WDR, flip, etc.) in the Configuration category | Off |

> **Frigate users:** If Frigate is already providing the stream and object detection,
> disable Camera Stream and Detection Events to avoid duplicate entities. VIGICam will
> still provide all hardware controls (alarm, spotlight, speaker, PTZ, SD card sensors,
> etc.). See [FRIGATE_SETUP.md](FRIGATE_SETUP.md) for a step-by-step guide.

### Stream quality

VIGI cameras expose two RTSP streams:

| Stream | Path | Resolution | Bitrate | Best for |
|--------|------|-----------|---------|---------|
| **Sub-stream** | `stream2` | Typically 640×360 or 1280×720 | Low | Raspberry Pi and other low-powered HA hosts; dashboard live view; fast startup |
| **Main stream** | `stream1` | Full sensor resolution (e.g. 4 MP / 2560×1440) | High | Powerful hardware; maximum image detail |

**The default is the sub-stream.** HA transcodes the RTSP stream to HLS to serve it to the browser, and on a Raspberry Pi the main stream's high bitrate can cause slow startup, stuttering, and buffering. The sub-stream starts in under a second and plays without interruption on any hardware.

**To switch to the main HD stream:**
1. Go to **Settings → Devices & Services → VIGI & InSight Cameras**
2. Click **Configure** on the camera entry
3. Enable **Use main stream (HD, stream1)**
4. Click **Submit**

The integration reloads immediately and the live view will use stream1.

> **Dashboard tip:** Regardless of which stream is selected, dashboard camera cards show a
> static snapshot by default. To see a true live feed, edit the card and set
> **Camera View** to **Live**. This tells HA to render the HLS stream directly
> in the card rather than polling for a new image every ~10 seconds.

> **Snapshots are always from the sub-stream.** The still image grabbed for notifications
> and the `camera.snapshot` service always uses stream2, regardless of this setting.
> This keeps snapshot grabs fast and lightweight on all hardware.

### Entity cleanup

When you turn a feature group off, VIGICam removes the entities for that group from the
HA entity registry immediately. If you later turn the group back on, those entities are
recreated. There is no manual cleanup needed.

### Notifications

VIGICam raises notifications in HA's **Problems** section (Settings → System → Repairs)
for the following events:

- **SD card removed** — raised when the SD card disappears at runtime. Dismiss it if the
  removal is temporary. If you have removed the card for good, delete the unwanted SD card
  entities from the device page, then dismiss the notice.
- **Frigate camera link lost** — raised if Frigate was previously detected at this
  camera's IP address but is no longer present. Guides you to re-enable Camera Stream
  and Detection Events if needed. Frigate auto-detection is best-effort and may not recognise your Frigate setup — set the feature groups yourself rather than relying on it.

---

## OpenAPI — unlocking additional sensors

TP-Link VIGI cameras include a local HTTPS API on port 20443 (the "OpenAPI") in addition
to the standard ONVIF interface. When enabled, this unlocks a second set of sensors and
services that are not available over ONVIF.

### What it unlocks

| Feature | Requires OpenAPI |
|---------|-----------------|
| Vehicle Detected binary sensor | ✓ |
| Audio Anomaly binary sensor | ✓ |
| Loitering binary sensor | ✓ |
| Scene Change binary sensor | ✓ |
| Object Left or Taken binary sensor | ✓ |
| Area Entry binary sensor | ✓ |
| Area Exit binary sensor | ✓ |
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

> **Known issue on some firmware** ([#79](https://github.com/steveAbratt/VIGICam/issues/79)):
> on the C540S and C350 this switch flickers and does not change the camera's behaviour, while
> **Alarm Light** and **Alarm Sound** work correctly. The field appears to be accepted and
> stored but ignored by those firmware versions. Use the two output switches as the real
> control — turning both off disables the alarm as effectively as the master switch would.

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

> **These two are special, and are the most common source of "it reverts when I toggle it".**
> Unlike every other switch, Person and Vehicle detection are exposed through *two*
> independent subsystems — the camera's JSON API and [OpenAPI](#openapi--unlocking-additional-sensors).
> VIGICam writes to whichever one it is reading the state from, and if that write is refused
> it retries through the other. Some firmware answers the OpenAPI *getter* while rejecting
> the matching *setter*, which is what made these toggles spring back on earlier versions.
>
> **Still unresolved on some firmware.** v0.7.5b1 made the rejection visible in the log and
> v0.7.5b4 sends the camera's whole settings object back rather than the single field, but
> neither is confirmed to fix it — a C350 rejects the write with error `-10009`. If a toggle
> reverts after updating, see
> [Troubleshooting](#troubleshooting) — a debug log is the only way to see the camera's
> reason for refusing.

### Detection Tamper

Enables or disables tamper detection. The camera fires an event when its field of view
is covered, moved significantly, or the lens is obscured. Requires camera support
(appears automatically if the camera reports it).

### Microphone Mute

Silences the camera's microphone input. Audio from the camera will not be captured in
recordings or the live stream.

> **These are *mute* switches, so `on` means muted — silent.** The VIGI app shows the same
> setting the other way round, as an *enable* toggle, so the app being off is this switch
> being on. The two look inverted side by side; they are not. If you want "audio is working"
> semantics in a dashboard, use a template switch rather than expecting this one to flip.

### Speaker Mute

Silences the camera's speaker output. **On = muted**, as above. This overrides all other
audio — muting the speaker stops both alarm sounds and any audio played via `vigicam.speak`
or `vigicam.play_audio`.

### Status LED

Turns the camera's front status LED on or off. Useful at night or in locations where
you don't want a visible indicator that a camera is active.

> **Not every model that reports this field has a visible LED.** The entity is created
> whenever the camera exposes the setting, and some models (the C350 among them) expose it
> without having the hardware. There is no capability flag that distinguishes them, so on
> those cameras the switch is harmless but does nothing observable.

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

Switches how the camera handles low-light conditions. **The options you see depend on your
camera's firmware** — there are two generations, and VIGICam builds the list from what your
camera actually reports.

**Older firmware** (C540V, S245 and similar — a single writable `night_vision_mode`):

| Option | What it does |
|--------|-------------|
| Auto (motion-triggered) | Camera decides when to switch to IR, triggered by motion |
| Colour (motion-triggered) | Colour image with the spotlight when a person or vehicle is detected (newer firmware calls this Human/Vehicle-Triggered Full-Color) |
| IR Always On | Infrared always active — black and white image at all times |
| Spotlight Always On | White-light spotlight always on — colour image in the dark |
| Auto Switch | Camera switches automatically between day and night profiles |

**Newer firmware** (C340, C340i, C350 and similar — a writable `pre_night_vision_mode`
alongside a read-only status field):

| Option | What it does |
|--------|-------------|
| Auto IR | Camera decides when to switch to IR; the usual default |
| IR Always On | Infrared always active — black and white image at all times |
| White LED Always On | Spotlight always on — colour image in the dark |
| Auto Color | Camera keeps colour, using the spotlight as needed |
| Human/Vehicle-Triggered Full-Color | Spotlight and colour only when a person or vehicle is seen |
| Off | No IR or spotlight — camera image in available light only |
| Custom | Whatever schedule is configured in the camera's own web UI |

Models with no white LED should show only the four options that don't need one: Auto IR,
IR Always On, Off and Custom. A C340i owner reports seeing all seven
([#78](https://github.com/steveAbratt/VIGICam/issues/78)) — if yours does, pick one of those
four.

> **Why the split matters.** On the newer firmware the camera reports *two* night-vision
> fields: the writable preference and a read-only status saying which emitter is currently
> lit. The mapping between them is many-to-one — Auto IR, IR Always On, Off and Custom all
> report the same status — so the selected mode cannot be recovered from the status field.
> Versions before **v0.7.4b2** wrote the read-only one, which the camera silently ignored.
> If night vision, spotlight or status LED changes don't take effect, update first.

### PTZ Preset *(PTZ cameras only)*

A dropdown of all named presets saved on the camera. Selecting a preset immediately
moves the camera to that position. Presets are created and named in the VIGI app or
the camera's web UI — this integration reads them automatically on startup.

For automations, use the `vigicam.goto_preset` service (which accepts a preset name)
rather than `select.select_option` (which requires an entity ID).

---

## Light Entities

### Spotlight

Controls the camera's white-light spotlight. **The spotlight is not an independent output —
it *is* a night vision mode**, so this entity works by changing Night Vision Mode, and the
two always move together.

| | Older firmware | Newer firmware (C340 / C350 class) |
|---|---|---|
| Spotlight **on** | Spotlight Always On | White LED Always On |
| Spotlight **off** | IR Always On | **Auto IR** |

Off deliberately returns to an IR mode rather than disabling night vision, so you keep a
working camera in the dark instead of a blind one. Note the difference between the two
columns: on newer firmware, off lands on *Auto IR*, not *IR Always On*.

The brightness slider maps the spotlight's 4 intensity levels (1–4) to the HA range
(1–255) so standard HA light controls work as expected. Brightness 1–63 is level 1, 64–127 level 2, 128–191 level 3 and
192–255 level 4.

> If you want a different off behaviour, set [Night Vision Mode](#night-vision-mode)
> directly instead of using this entity — it has the full list of modes your camera supports.

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
        brightness: 160   # level 3 of 4

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
        brightness: 40    # level 1 — minimal illumination
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

After enabling, the new entities are added to the device but are **disabled by default**.
To use them, go to the device page, scroll to the bottom and click **"X entities not
shown"**, then enable the specific entities you want. Each entity must be individually
enabled via its settings gear icon.

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
space for video and event images).

> **Loop recording:** If loop recording is enabled and the video partition is full,
> this sensor reports `0` — the camera is using all video space and continuously
> overwriting old footage. Use **SD Card Recording Duration** instead to see how much
> footage is stored.

### Firmware Version *(diagnostic — hidden by default)*

The camera's firmware version string, cleaned to the version number only
(e.g. `2.2.0` rather than `2.2.0 Build 250904 Rel.60109n`).

To show it: open the entity in HA, click the settings gear, and enable it.

### IP Address *(diagnostic)*

The camera's current IP address as reported by the camera itself. Useful for confirming
the static IP assignment is active. It goes unavailable along with everything else when the
camera can't be reached, so it can't be used to find a camera that has moved.

### Connection Type *(diagnostic)*

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

When the camera's ONVIF event identifies which detection zone fired (e.g. `Area1`, `Line1`
as configured in the VIGI app), that name is exposed as a `detection_zone` attribute on
the sensor. Use this in automation conditions to distinguish between different zones on the
same camera — for example, triggering a different response for the front gate versus the
back garden.

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
or camera web UI (Active Defence → Intrusion Detection). The entity is always created;
until a zone is configured it simply stays Clear.

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

### Audio Anomaly *(requires [OpenAPI](#openapi--unlocking-additional-sensors))*

Fires when the camera detects an unusual sound — e.g. breaking glass, a car alarm, or
raised voices. The sensitivity and sound types that trigger it are configured in the
camera's web UI or VIGI app under **Event → Audio Anomaly Detection**.

#### Example: notify on audio anomaly

```yaml
automation:
  trigger:
    - platform: state
      entity_id: binary_sensor.vigi_c540v_audio_anomaly
      to: "on"
  action:
    - service: notify.mobile_app_your_phone
      data:
        title: "Audio Anomaly"
        message: "Unusual sound detected at Front Gate"
```

### Loitering *(requires [OpenAPI](#openapi--unlocking-additional-sensors))*

Fires when a person or vehicle remains in the camera's field of view longer than the
configured loitering time threshold. The threshold is set in the camera's web UI or
VIGI app under **Event → Loitering Detection**.

#### Example: turn on a light when someone loiters

```yaml
automation:
  trigger:
    - platform: state
      entity_id: binary_sensor.vigi_c540v_loitering
      to: "on"
  action:
    - service: light.turn_on
      target:
        entity_id: light.driveway
```

### Scene Change *(requires [OpenAPI](#openapi--unlocking-additional-sensors))*

Fires when the camera detects a significant change to its scene — typically the camera
being moved, re-aimed, or having something large placed in front of it. Distinct from
[Tamper](#tamper) detection which focuses on the lens being covered.

#### Example: alert when the camera is moved

```yaml
automation:
  trigger:
    - platform: state
      entity_id: binary_sensor.vigi_c540v_scene_change
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
      entity_id: binary_sensor.vigi_c540v_object_left_or_taken
      to: "on"
  action:
    - service: notify.mobile_app_your_phone
      data:
        title: "Object Alert"
        message: "Something left or removed at Front Gate"
```

### Area Entry *(requires [OpenAPI](#openapi--unlocking-additional-sensors))*

Fires when a person or vehicle enters a configured virtual area. Areas are drawn in
the VIGI app or camera web UI under **Event → Area Entry Detection**. Unlike
[Line Crossing](#line-crossing) which fires on crossing a line, this fires when
something enters an enclosed zone.

#### Example: sound the alarm on area entry

```yaml
automation:
  trigger:
    - platform: state
      entity_id: binary_sensor.vigi_c540v_area_entry
      to: "on"
  action:
    - service: vigicam.play_audio
      data:
        entity_id: camera.vigi_c540v_stream
        slot: 0       # built-in alarm tone
        times: 3
```

### Area Exit *(requires [OpenAPI](#openapi--unlocking-additional-sensors))*

Fires when a person or vehicle exits a configured virtual area. Mirrors Area Entry
but triggers on departure. Useful for detecting when a vehicle leaves a
monitored zone.

#### Example: notify when a vehicle leaves the property

```yaml
automation:
  trigger:
    - platform: state
      entity_id: binary_sensor.vigi_c540v_area_exit
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

## Capability Diagnostic Sensors

Each camera device includes five diagnostic binary sensors that report which capabilities
were detected at startup. These appear in the **Diagnostics** section of the device page
(below the main entity list) and are intended for automation conditions — they let you
write a single automation that works across cameras with different hardware.

| Sensor | On means | Off means |
|--------|----------|-----------|
| **PTZ** | Camera supports pan/tilt/zoom via ONVIF | Fixed camera — no PTZ controls created |
| **OpenAPI** | OpenAPI is enabled and reachable on port 20443 | OpenAPI disabled or not yet enabled on camera |
| **Event Image Capture** | Camera saves a full-frame still to SD card at the moment of each detection event | SD card capture not configured; Last Detection will use RTSP fallback |
| **SD Card** | An SD card is detected in the camera | No SD card fitted or card not readable |
| **ONVIF Events** | A pull-point subscription is currently held | No subscription — failed, or detection events are turned off. **Note this reports only that a subscription exists.** It can read `on` while polls are timing out and events are being missed, so check the log for `ONVIF poll error` / `ONVIF subscribe failed` as well |

> **Note:** PTZ, OpenAPI, and Event Image Capture are detected once at startup and do not
> change until the integration reloads. SD Card and ONVIF Events reflect live state and
> update with each coordinator poll.

### Using capability sensors in automations

Capability sensors let you write conditional automations that adapt to different cameras
without needing a separate automation for each one.

#### Example: only move the camera to a patrol preset if it has PTZ

```yaml
automation:
  trigger:
    - platform: time
      at: "22:00:00"
  condition:
    - condition: state
      entity_id: binary_sensor.vigi_c540v_ptz
      state: "on"
  action:
    - service: vigicam.goto_preset
      data:
        entity_id: camera.vigi_c540v_stream
        preset: "Night Watch"
```

#### Example: alert when the ONVIF subscription drops

```yaml
automation:
  trigger:
    - platform: state
      entity_id: binary_sensor.vigi_c540v_onvif_events
      to: "off"
      for: "00:01:00"   # wait 1 min to avoid alerts during a normal reload
  action:
    - service: notify.mobile_app_your_phone
      data:
        title: "Camera Events Offline"
        message: "Front Gate ONVIF subscription has dropped — detection sensors may not update"
```

#### Example: send different notifications depending on whether event image capture is configured

```yaml
automation:
  trigger:
    - platform: state
      entity_id: binary_sensor.vigi_c540v_person_detected
      to: "on"
  action:
    - choose:
        - conditions:
            - condition: state
              entity_id: binary_sensor.vigi_c540v_event_image_capture
              state: "on"
          sequence:
            - service: notify.mobile_app_your_phone
              data:
                title: "Person Detected"
                message: "Event-synchronised image available"
                data:
                  image: /api/image_proxy/image.vigi_c540v_last_detection
      default:
        - service: notify.mobile_app_your_phone
          data:
            title: "Person Detected"
            message: "Snapshot available"
            data:
              image: /api/image_proxy/image.vigi_c540v_last_detection
```

---

## Image Entities — Last Detection Snapshot

### Last Detection

Shows a snapshot from the most recent detection event. The entity appears on all cameras
and updates automatically each time any detection fires — intrusion, line crossing,
motion, person detected, or smart detection. It uses the same real-time ONVIF
subscription as the binary sensors, so it refreshes within seconds of a detection,
not on the 30-second poll cycle.

For users who do not want the complexity or extra hardware of a dedicated NVR like
Frigate, the Last Detection entity provides a lightweight alternative that runs
entirely on the camera. When event image capture is configured, the camera saves a
full-frame still at the exact instant of detection — so the image shows exactly what
triggered the event, not a frame grabbed a second or two later from a live stream.
No GPU, no separate server, no additional integration required.

#### Reviewing past events — VIGI app vs Home Assistant

The Last Detection entity in HA holds only the **most recent** image. It is not a
history — it is overwritten on every new detection.

**For a timeline of past events, use the VIGI app.** On cameras with event image
capture configured, the app's **Playback → Capture Playback** screen shows a
timestamped grid of all stored event images you can scroll back through. This is the intended tool
for reviewing overnight events and is far more capable than anything you can build in HA
for this purpose.

**For a HA-based history of RTSP snapshots**, the [Gallery Card](https://github.com/TarheelGrad1998/gallery-card)
(available via HACS) combined with an automation is the most practical approach:

```yaml
automation:
  alias: "Save detection snapshot to gallery"
  trigger:
    - platform: state
      entity_id: binary_sensor.vigi_c540v_motion
      to: "on"
  action:
    - service: camera.snapshot
      target:
        entity_id: camera.vigi_c540v_stream
      data:
        filename: /config/www/cameras/front_gate/{{ now().strftime('%Y%m%d_%H%M%S') }}.jpg
```

Point the Gallery Card at `/local/cameras/front_gate/` to browse the saved images on
your dashboard. Images accumulate indefinitely — add a separate cleanup automation or
cron job to remove files older than a few days if storage is a concern.

#### How the image is captured

The integration detects at startup whether your camera supports event image capture and
uses the best available method:

**Event capture (SD card image)** — used when the camera has an SD card formatted with
a capture partition and Upload Capture enabled in the event settings. The camera saves a
full-frame still at the exact instant of detection; the integration downloads it ~3 seconds
after the event fires. Because the image is written by the camera at detection time, it
reliably shows the subject that triggered the event.

**RTSP snapshot (live stream grab)** — used as a fallback on cameras without event image
capture configured (e.g. VIGI C540V, or any camera where the SD card lacks a capture
partition). When a detection event fires, the integration grabs a frame from the live
RTSP stream ~2 seconds after the notification arrives. Fast-moving subjects may have
already left the frame.

The `source` attribute on the entity tells you which method was used.

**Attributes available on the entity:**

| Attribute | Example | Description |
|---|---|---|
| `detection_type` | `motion`, `smart_event` | ONVIF event type that triggered the grab |
| `source` | `event_capture`, `rtsp_snapshot` | Which capture method was used |
| `detection_zone` | `Area1`, `Line2` | Detection zone name from the camera, when available |
| `event_label` | `Person`, `Smart Detection` | AI classification label (event capture only) |
| `file_id` | `00010000000260` | Internal SD card file ID (event capture only) |

#### Setting up event image capture

Event image capture requires four steps in the camera's web UI or VIGI app. Do them in
order — changing the capture capacity allocation requires reformatting the disk.

> **Not all cameras support this.** Saving event images to SD card requires an SD card
> and firmware support for a split storage partition (separate space for video and images).
> Some models, the VIGI C540V among them, can upload event images via FTP but cannot save them
> to the SD card. On those cameras the integration always uses the RTSP snapshot fallback.
> Check the **Event Image Capture** diagnostic sensor on the device page — if it shows
> **Off**, your camera model or SD card setup does not support this feature.

> **Note on "Smart Frame":** The VIGI app has a setting called Smart Frame under Event
> settings. This is a separate feature — it draws a bounding box around detected objects
> on the live video feed and in app notifications. It is not related to saving images to
> the SD card and does not need to be enabled for event image capture to work.

**Step 1 — Enable event-triggered capture**

Go to **Storage → Capture Management** and turn on **Event-triggered capture**.

This tells the camera to save an AI-cropped image to the SD card each time a detection
event fires.

**Step 2 — Allocate storage space for images**

Go to **Storage → Storage Management**. If your camera supports image capture, you will
see a **Capture Capacity** slider (this option does not appear on cameras that lack the
feature). Increase it from 0% to your preferred allocation — 5% is enough for most
users and stores several hundred AI-cropped images before the oldest are overwritten.

**Step 3 — Format the disk**

After changing Capture Capacity, the disk must be reformatted to apply the new partition
layout. Format from **Storage → Storage Management → Format disk**. This erases all
existing recordings — do this before the camera goes into service, or be prepared to
lose stored footage.

**Step 4 — Enable upload to SD card**

Go to **Event → Smart Event** and turn on **Upload Capture** (labelled as
*Upload to FTP/SD card* on some firmware versions). This is the per-event-type toggle
that controls whether captured images are actually written to storage. Without this step
the camera allocates space for images but never writes them.

> If event image capture is not set up, the integration falls back to RTSP snapshot
> automatically — the Last Detection entity will still populate with a frame grabbed from
> the live stream. The `source` attribute on the entity tells you which method was used:
> `event_capture` or `rtsp_snapshot`.

---

## Services

Services are called from automations, scripts, or Developer Tools → Services.
All services take an `entity_id`. **Any entity belonging to the camera works** —
`camera.<name>_stream`, `sensor.<name>_ip_address`, anything — so the services keep working
when the Camera Stream feature group is turned off. The examples below use the stream entity;
substitute another if you have turned it off.

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
15 seconds / 256 KB of audio. Longer messages are **trimmed at 15 seconds and cut off
mid-sentence**, without a log message. A typical spoken sentence is 3–5 seconds.

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

**Limit:** The camera hard limit is 15 seconds / 256 KB after conversion. Longer files are
trimmed at 15 seconds and cut off, without a log message. A 15-second clip at 8 kHz mono
16-bit is around 240 KB.

---

#### Ways to specify the file

**1. HA media browser (recommended for most users)**

Upload your file via the HA Media section (sidebar → Media → My media → Upload) then
reference it with the media browser URL. The integration resolves these URLs internally
without requiring a token — you do not need to set up authentication:

```yaml
url: http://192.168.1.x:8123/media/local/alert.wav
```

Files uploaded via the media browser are stored in `/media/` on HA OS, Supervised and
Container installs, or in `/config/media/` on a Core (Python venv) install.

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
url: /media/alert.wav
```

The path must be inside a folder Home Assistant allows: the media folder, `/config/www/`,
or a folder you have added to `allowlist_external_dirs` in `configuration.yaml`. Anything
else is refused with "path not allowed by Home Assistant configuration". A NAS mount works
only if it sits under one of those, or is added to the allowlist.

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
  preset: "Front Gate"
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

Moves the camera to an absolute pan/tilt/zoom position. Useful for positioning without a
named preset, or for patrol automations that step through fixed positions.

All three values run from **-1.0 to 1.0 — they are not degrees**, and values outside that
range are rejected. `zoom` is optional, but leaving it out sends `0.0`, which moves the zoom
rather than leaving it where it is.

```yaml
service: vigicam.ptz_move_to
data:
  entity_id: camera.vigi_c540v_stream
  pan: 0.5     # -1.0 to 1.0
  tilt: -0.1   # -1.0 to 1.0
  zoom: 0.0    # -1.0 to 1.0
```

> **Tip:** finding the right values takes some trial and error, because VIGICam doesn't
> currently show a preset's coordinates (the camera does report them). For a position you'll
> reuse, it's usually easier to frame it in the VIGI app, save it as a preset, and call
> `vigicam.goto_preset` instead.

---

### `vigicam.ptz_save_preset` — Save current position as a preset *(PTZ cameras only, requires [OpenAPI](#openapi--unlocking-additional-sensors))*

Saves the camera's current position as a named preset, in slot `id` (1–8). Without an `id`,
the first free slot is used — and **if all eight are in use, slot 1 is overwritten**. The new
preset appears in the **PTZ Preset** select entity on the next poll (within about 30 seconds).
**Until then the whole preset list is briefly empty**, so a `vigicam.goto_preset` straight after
saving or deleting will report the preset as not found. Wait for the next poll first.

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
        pan: 0.9
        tilt: -0.05
        zoom: 0.0
    - delay: "00:00:02"
    - service: vigicam.ptz_save_preset
      data:
        entity_id: camera.vigi_c540v_stream
        name: "Night Watch"
        id: 8              # a fixed slot, so a nightly run doesn't fill and then overwrite slot 1
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
| **Camera** | Any entity of the camera — its IP Address sensor works if Camera Stream is off |
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

Files in the HA `www/` folder (`/local/` URLs) and file paths on the HA host inside an
allowed folder also work — see [vigicam.play_file](#vigicamplay_file--play-a-pre-recorded-audio-file) for all options.

---

### Creating a play-file automation

After importing the blueprint:

1. Go to **Settings → Automations & Scenes → Blueprints**
2. Find **VIGI Camera — Play Audio File on Trigger** and click **Create Automation**
3. Fill in the fields:

| Field | What to set |
|-------|------------|
| **Trigger** | Whatever should fire the playback — a motion sensor, a button press, a time pattern, etc. |
| **Camera** | Any entity of the camera — its IP Address sensor works if Camera Stream is off |
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

---

## Troubleshooting

### A control shows the right state but won't change it

This is the most common complaint, and it is almost always a **model difference** rather than
something wrong with your setup. The entity reads the camera correctly, you toggle it, and a
few seconds later it springs back to where it was.

Three different causes produce that same symptom, and they are worth telling apart:

| What's happening | How to recognise it |
|---|---|
| The camera **refused** the write | The toggle settles **back to the old value** after the next poll |
| The camera **accepted but ignores** the field | The toggle settles on the **new value**, but the camera's behaviour never changes |
| The setting is **read-only** on your firmware | Same as refused, and the camera's own web UI shows the setting greyed out or absent |

**First, update.** Several of these have been fixed, and each fix needed a specific version:

- Night vision, spotlight, status LED on C340/C350-class firmware — **v0.7.4b2**
- Person and Vehicle detection — **v0.7.4b5**, then **v0.7.5b1** (made the refusal visible),
  then **v0.7.5b4** (sends the full settings object). Still unconfirmed on firmware that
  rejects the write with `-10009` — see [#92](https://github.com/steveAbratt/VIGICam/issues/92)

**Then check the one case that isn't a bug.** Microphone Mute and Speaker Mute are *mute*
switches: `on` means silent. The VIGI app shows the same setting as an enable toggle, so the
two read as opposites while both being correct. See [Microphone Mute](#microphone-mute).

**Known and still open:** the master [Alarm](#alarm) switch on C540S and C350 firmware
([#79](https://github.com/steveAbratt/VIGICam/issues/79)).

### Detection events arrive sometimes and not others

If the VIGI app and your NVR see every detection but Home Assistant misses some at random,
and the log shows `ONVIF subscribe failed` or `ONVIF poll error … re-subscribing in 15 s`
repeating, the camera has run out of event subscriptions.

Detection events reach Home Assistant over an ONVIF pull-point subscription, and these
cameras allow only a handful at a time. **Every other ONVIF client on the same camera shares
that budget** — an NVR, Synology Surveillance Station, a second Home Assistant. During each
re-subscribe gap nothing is listening, so anything that happens in that window is lost. That
is why the VIGI app still sees everything: it does not use ONVIF.

1. **Update to v0.7.5b4 or later.** Earlier versions abandoned a subscription without
   releasing it, so each failure left one stranded on the camera for up to an hour and the
   problem fed itself.
2. **Then restart Home Assistant and power-cycle the cameras.** The subscriptions already
   stranded by the old version cannot be released by the new one — the camera holds them
   until they expire. Until they clear you will still see failures, which is the old leak
   draining rather than the fix failing. Rebooting the camera clears it at once.
3. **Reduce the number of ONVIF clients.** If your NVR takes detection from the camera over
   ONVIF, switching it to its own analysis frees the budget.

The **ONVIF Events** diagnostic sensor can read `on` throughout this fault — it reports that
a subscription exists, not that polling is succeeding. The log is the reliable signal.

### I changed my camera's password

Home Assistant notices and asks for the new one: Settings → **Devices & Services** →
**VIGI & InSight Cameras**, on the camera flagged as needing attention. Enter the camera's
**local** admin password — not your TP-Link ID. Your entities, history and automations are
kept.

If Home Assistant asks for a password you haven't changed, check the address the prompt shows:
the camera may have moved, and you may be talking to a different camera. See *My camera's IP
address changed* below.

### My camera's IP address changed

If your router gave the camera a new address, its entities show **unavailable**. If Home
Assistant was running when it happened, the log reports `Error fetching vigicam <old address>
data: Camera not responding`. If Home Assistant has restarted since — usual after a power cut —
the camera shows **Failed to set up – Retrying setup** on the integration page instead. Either
way the fix is the same: **add the camera again at its new address. Do not delete the old one
first.**

If two cameras **swapped** addresses — likely after a power cut if they share a password —
nothing shows as unavailable: each camera in Home Assistant shows the other's picture and its
switches act on the other camera. Add *both* cameras again at their current addresses.

1. Find the new address in your router's client list or in the VIGI app.
2. Settings → **Devices & Services** → **+ Add Integration** → **VIGI & InSight Cameras**, and
   enter the new IP address, username and password.
3. Home Assistant recognises the camera by its MAC address and moves the existing entry to the
   new address. Its entities, history and automations are kept, and it reconnects within a few
   seconds.

To stop this happening again, give each camera a fixed address (a DHCP reservation) in your
router.

### Do not delete and re-add a camera to fix it

Deleting a camera throws away its entity customisations — renamed entity IDs, areas, disabled
entities — and any automation, script or dashboard card that refers to them stops working
until you fix it by hand. Both problems above have a fix that keeps everything.

### Getting a diagnostics report

Every model-specific bug in this integration has been solved the same way — by reading what
the camera actually returns. If something misbehaves, this is the single most useful thing you
can attach to an issue:

> Settings → **Devices & Services** → **VIGICam** → ⋮ → **Download diagnostics**

It contains your model, firmware, the detected capabilities and the full raw payload every
entity is built from. **Credentials, IP and MAC addresses, serial numbers and the camera's own
alias are redacted automatically**, so it is safe to paste into a public issue.

### Getting a debug log as well

When a control won't change, the diagnostics report shows the *state* but not the camera's
*reason* for refusing. For that, capture a log of the failing action:

1. Settings → **Devices & Services** → **VIGICam** → ⋮ → **Enable debug logging**
2. Toggle the entity that misbehaves, once
3. ⋮ → **Disable debug logging** — Home Assistant downloads the log automatically

A refused OpenAPI call is logged with the method name, the camera's error code and the
parameters sent, and a write that fell back to the other API is logged as a warning. Those
lines are usually enough to fix the problem without any further back-and-forth.

### Where to report

- **A model that works** — add it to
  [the tested-cameras discussion](https://github.com/steveAbratt/VIGICam/discussions/76)
- **Something broken** — [open an issue](https://github.com/steveAbratt/VIGICam/issues) with
  the diagnostics report attached

The most useful reports separate "reads the right state" from "actually applies the change",
entity by entity. That distinction is what pinned down both detection-toggle bugs.
