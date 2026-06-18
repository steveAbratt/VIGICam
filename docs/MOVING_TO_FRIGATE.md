# Moving from standalone VIGICam to VIGICam + Frigate

This guide is for existing VIGICam users who want to add Frigate to their setup.
If you are installing both from scratch, see [FRIGATE_SETUP.md](FRIGATE_SETUP.md) instead.

---

## What changes when you add Frigate

In your current (standalone) setup, VIGICam provides everything:

- Camera stream
- Detection events (motion, person, intrusion, etc.)
- Camera hardware controls (alarm, spotlight, PTZ, speaker, etc.)

After adding Frigate, you will redistribute responsibility:

| Feature | Before | After |
|---------|--------|-------|
| Camera stream | VIGICam | Frigate (recommended) |
| Object detection (generic motion/person/vehicle) | VIGICam | Frigate (more accurate) |
| Zone-based detection (intrusion, line crossing, loitering) | VIGICam | VIGICam (Frigate cannot replicate these) |
| Camera hardware controls | VIGICam | VIGICam (unchanged) |
| 24/7 recording with clips | Not available | Frigate |

The camera hardware controls — alarm, spotlight, speaker, PTZ, SD card, night vision
mode — are not affected by adding Frigate. They stay exactly as they are.

---

## Before you start

**Note down any automations that use VIGICam camera or detection entities.** When you
disable Camera Stream in VIGICam, the `camera.*_stream` entity is removed. When you
disable Detection Events, the binary sensor entities are removed. Any automations using
those entities will break — you will need to update them to point at Frigate's entities
instead.

Common entities to check in automations:
- `camera.vigi_*_stream` — will be replaced by Frigate's camera entity
- `binary_sensor.vigi_*_motion` — will be replaced by Frigate's occupancy sensor
- `binary_sensor.vigi_*_person_detected` — will be replaced by Frigate's person occupancy sensor
- `binary_sensor.vigi_*_vehicle_detected` — will be replaced by Frigate's car occupancy sensor
- `binary_sensor.vigi_*_smart_detection` — no direct Frigate equivalent; keep VIGICam on for zone detection instead
- `image.vigi_*_last_detection` — Frigate has its own snapshot entity

**Zone-based sensors are only available through VIGICam.** If you have automations
using intrusion, line crossing, loitering, audio anomaly, or scene change — keep
Detection Events enabled in VIGICam. Frigate does not replicate these.

---

## Migration steps

### Step 1 — Install and configure Frigate

Set up Frigate with your camera's RTSP stream before changing anything in VIGICam.
Confirm detection is working and clips are recording. The camera continues to work
normally through VIGICam during this time.

### Step 2 — Update automations that use camera stream entities

Anywhere you use `camera.vigi_*_stream` in an automation action (e.g. `camera.record`,
`camera.snapshot`, `camera.play_stream`), update the entity_id to Frigate's camera
entity. Frigate creates a camera entity per configured camera.

### Step 3 — Update automations that use motion/person/vehicle detection

Replace VIGICam binary sensor triggers with the equivalent Frigate occupancy or
detection sensors. Frigate creates `binary_sensor.*_person_occupancy` style entities.

If you use on-camera zone events (intrusion, line crossing) keep those — they have no
Frigate equivalent and must remain on VIGICam.

### Step 4 — Disable Camera Stream and Detection Events in VIGICam

Once all automations have been updated:

1. Go to **Settings → Devices & Services → VIGI & InSight Cameras**
2. Click **Configure** on the camera entry
3. Turn off **Camera Stream** (Frigate handles this now)
4. Turn off **Detection Events** — *only if you are not using any zone-based sensors*
   (intrusion, line crossing, loitering, audio anomaly, scene change, area entry/exit)
5. Click **Submit**

VIGICam reloads immediately. The stream entity and detection binary sensors are removed.
Frigate's entities take over.

> **Keeping zone events:** If you want Frigate for generic motion/person/vehicle but
> still want VIGICam's zone-based detection (intrusion etc.), leave Detection Events
> **on** in VIGICam. You will have both sets of detection sensors. Use the one that
> suits each automation — Frigate for accurate generic object detection, VIGICam for
> zone/rule-based events.

### Step 5 — Verify

- Check that no automations are broken (check the Automation trace viewer in HA)
- Confirm Frigate is recording and its detection sensors are updating
- Confirm VIGICam still controls the camera hardware (test the alarm, spotlight, etc.)

---

## What to do if something breaks

**Automations stopped triggering after the migration**

An automation is still pointing at a VIGICam entity that no longer exists. Check
Settings → Automations for any automations with unavailable entities (shown in orange
or with a warning icon). Update the trigger/condition entity to the Frigate equivalent.

**The "Frigate camera link lost" repair notice appeared**

This can happen if you later remove or reconfigure Frigate. The notice guides you to
re-enable Camera Stream and Detection Events in VIGICam. Follow the steps in the notice,
or dismiss it if you intentionally removed Frigate.

**I want to go back to standalone VIGICam**

Simply re-enable Camera Stream and Detection Events in the Configure options. VIGICam
will recreate all the entities on the next reload. If you also removed Frigate, the
repair notice will clear automatically on the next HA restart.

---

## Summary

| Task | Done? |
|------|-------|
| Install Frigate and confirm it is working | |
| Note all automations using VIGICam stream or detection entities | |
| Update stream entity references to Frigate entities | |
| Update generic motion/person/vehicle triggers to Frigate sensors | |
| Keep or note which zone-based VIGICam sensors you still need | |
| Disable Camera Stream in VIGICam Configure | |
| Disable Detection Events in VIGICam Configure (if no zone sensors needed) | |
| Test that Frigate detection and recording works | |
| Test that VIGICam hardware controls still work | |
| Verify no broken automations | |
