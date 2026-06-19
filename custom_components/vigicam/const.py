from datetime import timedelta
import logging

DOMAIN = "vigicam"
BRAND = "TP-Link"
DEFAULT_USERNAME = "admin"
DEFAULT_SCAN_INTERVAL = 30
TIMEOUT = 10
CONF_VERIFY_SSL = "verify_ssl"

# Password hash prefix required by the VIGI/Tapo local API
TAPO_PASSWORD_PREFIX = "TPCQ75NF2Y:"

# night_vision_mode values → human-readable labels for the Select entity
NIGHT_VISION_MODES = {
    "md_night_vision": "Auto (motion-triggered)",
    "inf_night_vision": "IR Always On",
    "wtl_night_vision": "Spotlight Always On",
    "human_triggered_color": "Colour (motion-triggered)",
    "auto_switch_night_vision": "Auto Switch",
}

LOGGER = logging.getLogger(f"custom_components.{DOMAIN}")

# ── Feature group options ──────────────────────────────────────────────────────

CONF_FEATURE_CAMERA_STREAM = "feature_camera_stream"
CONF_FEATURE_DETECTION_EVENTS = "feature_detection_events"
CONF_FEATURE_IMAGE_CONTROLS = "feature_image_controls"
CONF_STREAM_USE_MAIN = "stream_use_main"

DEFAULT_FEATURE_CAMERA_STREAM = True
DEFAULT_FEATURE_DETECTION_EVENTS = True
DEFAULT_FEATURE_IMAGE_CONTROLS = False
DEFAULT_STREAM_USE_MAIN = False  # sub-stream (stream2) by default — fast startup, Pi-friendly

# Unique-ID suffixes that belong to each feature group.
# Used by the entity cleanup function to remove stale entries when a
# feature group is disabled via the options flow.
CAMERA_STREAM_SUFFIXES: frozenset[str] = frozenset({"stream"})

DETECTION_EVENT_SUFFIXES: frozenset[str] = frozenset({
    # Coordinator-polled (VIGIBinarySensor uses key directly)
    "loop_recording",
    # Event-driven (VIGIEventBinarySensor uses f"event_{key}")
    "event_motion", "event_person", "event_tamper", "event_intrusion", "event_line_cross",
    "event_smart_event",
    # OpenAPI event sensors
    "event_vehicle", "event_audio_anomaly", "event_loitering", "event_scene_change",
    "event_object_left_taken", "event_area_entry", "event_area_exit",
    # Image entity
    "last_detection",
})

IMAGE_CONTROL_SUFFIXES: frozenset[str] = frozenset({
    # Numbers (key matches VIGINumberDescription.key)
    "luma", "contrast", "saturation", "chroma", "sharpness", "wd_gain", "exp_gain",
    # Selects
    "flip", "rotate", "flicker", "white_balance", "exposure_type",
    # Switches
    "wide_dynamic", "high_light_compensation", "dehaze", "eis",
    "auto_exp_antiflicker", "backlight", "ldc",
    "full_color_people_enhance", "full_color_vehicle_enhance",
})

# ── Repairs issue identifiers ──────────────────────────────────────────────────

REPAIRS_SD_CARD_MISSING = "sd_card_missing"
REPAIRS_FRIGATE_GONE = "frigate_camera_gone"

# Unique-ID suffixes for entities that have been superseded and should be
# removed from the registry on the next setup (one-time migration).
DEPRECATED_SUFFIXES: frozenset[str] = frozenset({
    "spotlight_intensity",  # replaced by the spotlight light entity brightness
})

# Unique-ID suffixes for all SD card sensor entities — used by the repair fix
# flow to remove stale entities when the user confirms the card is gone.
SD_ENTITY_SUFFIXES: frozenset[str] = frozenset({
    "sd_used_percent", "sd_total", "sd_free", "sd_status",
    "sd_record_duration", "sd_oldest_recording", "sd_record_capacity",
    "sd_video_free", "loop_recording",
})
