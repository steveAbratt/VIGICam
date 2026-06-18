"""Helpers for detecting a Frigate camera integration at a given IP address."""
from __future__ import annotations

from dataclasses import dataclass

from homeassistant.core import HomeAssistant


@dataclass
class FrigateCamera:
    """A Frigate camera discovered at a given IP address."""

    config_entry_id: str
    camera_name: str


def detect_frigate_camera(hass: HomeAssistant, ip: str) -> FrigateCamera | None:
    """Return FrigateCamera if Frigate has a camera stream at *ip*, otherwise None.

    Checks Frigate config entry data first (camera name → ffmpeg input path),
    then falls back to the entity registry for Frigate platform entities whose
    unique_id contains the IP (covers older Frigate builds).
    """
    try:
        for ce in hass.config_entries.async_entries("frigate"):
            cameras: dict = ce.data.get("cameras") or ce.options.get("cameras") or {}
            for camera_name, cam_cfg in cameras.items():
                inputs = ((cam_cfg.get("ffmpeg") or {}).get("inputs") or [])
                for inp in inputs:
                    if ip in inp.get("path", ""):
                        return FrigateCamera(
                            config_entry_id=ce.entry_id,
                            camera_name=camera_name,
                        )
    except Exception:  # noqa: BLE001
        pass

    # Fallback: scan entity registry for Frigate platform entities whose
    # unique_id embeds the IP (some Frigate versions include the RTSP URL).
    try:
        from homeassistant.helpers import entity_registry as er
        for ent in er.async_get(hass).entities.values():
            if ent.platform == "frigate" and ip in (ent.unique_id or ""):
                return FrigateCamera(
                    config_entry_id=ent.config_entry_id or "",
                    camera_name="",
                )
    except Exception:  # noqa: BLE001
        pass

    return None
