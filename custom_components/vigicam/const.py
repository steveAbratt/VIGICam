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
