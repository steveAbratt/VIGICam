"""VIGI & InSight Camera integration for Home Assistant."""
from __future__ import annotations

import asyncio
import logging

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_USERNAME, Platform
from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers import config_validation as cv, entity_registry as er
from homeassistant.helpers.aiohttp_client import async_create_clientsession, async_get_clientsession

from .api import VIGIAuthError, VIGICamera, VIGIError
from .const import (
    CAMERA_STREAM_SUFFIXES,
    CONF_FEATURE_CAMERA_STREAM,
    CONF_FEATURE_DETECTION_EVENTS,
    CONF_FEATURE_IMAGE_CONTROLS,
    CONF_VERIFY_SSL,
    DEFAULT_FEATURE_CAMERA_STREAM,
    DEFAULT_FEATURE_DETECTION_EVENTS,
    DEFAULT_FEATURE_IMAGE_CONTROLS,
    DEPRECATED_SUFFIXES,
    DETECTION_EVENT_SUFFIXES,
    DOMAIN,
    IMAGE_CONTROL_SUFFIXES,
    REPAIRS_FRIGATE_GONE,
    REPAIRS_SD_CARD_MISSING,
    SD_ENTITY_SUFFIXES,
)
from .frigate import detect_frigate_camera
from .coordinator import VIGICoordinator, _detect_sd_card
from .onvif_events import VIGIOnvifEvents
from .onvif_ptz import DEFAULT_SPEED, VIGIOnvifPtz
from .openapi import VIGIOpenAPI, try_openapi
from .openapi_events import VIGIOpenAPIEventListener

_LOGGER = logging.getLogger(__name__)

# All platforms the integration can ever create — individual setup functions
# gate their own entities on feature flags and capability detection.
PLATFORMS = [
    Platform.CAMERA,
    Platform.BINARY_SENSOR,
    Platform.LIGHT,
    Platform.SWITCH,
    Platform.SELECT,
    Platform.SENSOR,
    Platform.BUTTON,
    Platform.NUMBER,
    Platform.IMAGE,
]

# ── Feature helpers ───────────────────────────────────────────────────────────

def _feature(entry: ConfigEntry, key: str, default: bool) -> bool:
    """Return the current option value for a feature flag."""
    return entry.options.get(key, default)


def _cleanup_stale_entities(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Remove entity registry entries for disabled feature groups.

    Called at the start of async_setup_entry so that entities belonging to
    a feature group the user has turned off are removed cleanly rather than
    left as unavailable stubs.
    """
    options = entry.options
    registry = er.async_get(hass)

    # Build the set of unique-ID suffixes that should no longer exist.
    remove_suffixes: set[str] = set()
    if not options.get(CONF_FEATURE_CAMERA_STREAM, DEFAULT_FEATURE_CAMERA_STREAM):
        remove_suffixes |= CAMERA_STREAM_SUFFIXES
    if not options.get(CONF_FEATURE_DETECTION_EVENTS, DEFAULT_FEATURE_DETECTION_EVENTS):
        remove_suffixes |= DETECTION_EVENT_SUFFIXES
    if not options.get(CONF_FEATURE_IMAGE_CONTROLS, DEFAULT_FEATURE_IMAGE_CONTROLS):
        remove_suffixes |= IMAGE_CONTROL_SUFFIXES

    # Always remove deprecated entities regardless of feature flags.
    remove_suffixes |= DEPRECATED_SUFFIXES

    for reg_entry in er.async_entries_for_config_entry(registry, entry.entry_id):
        # Unique IDs are "{device_id}_{suffix}" — extract the suffix part.
        parts = reg_entry.unique_id.split("_", 1)
        suffix = parts[1] if len(parts) == 2 else reg_entry.unique_id
        if suffix in remove_suffixes:
            _LOGGER.debug(
                "Removing stale entity %s (feature group disabled)", reg_entry.entity_id
            )
            registry.async_remove(reg_entry.entity_id)


async def _raise_sd_card_repair(hass: HomeAssistant, entry: ConfigEntry, camera_name: str) -> None:
    """Create a Repairs issue when the SD card disappears at runtime."""
    try:
        from homeassistant.helpers.issue_registry import IssueSeverity, async_create_issue
        async_create_issue(
            hass,
            DOMAIN,
            f"{REPAIRS_SD_CARD_MISSING}_{entry.entry_id}",
            is_fixable=False,
            severity=IssueSeverity.WARNING,
            translation_key=REPAIRS_SD_CARD_MISSING,
            translation_placeholders={"camera_name": camera_name},
        )
    except Exception as exc:  # noqa: BLE001
        _LOGGER.debug("Could not create SD card repair issue: %s", exc)


async def _clear_sd_card_repair(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Delete the SD card repair issue when the card is reinserted."""
    try:
        from homeassistant.helpers.issue_registry import async_delete_issue
        async_delete_issue(hass, DOMAIN, f"{REPAIRS_SD_CARD_MISSING}_{entry.entry_id}")
    except Exception as exc:  # noqa: BLE001
        _LOGGER.debug("Could not delete SD card repair issue: %s", exc)


async def _raise_frigate_repair(hass: HomeAssistant, entry: ConfigEntry, camera_name: str) -> None:
    """Notify user that Frigate has disappeared and detection events should be re-enabled."""
    try:
        from homeassistant.helpers.issue_registry import IssueSeverity, async_create_issue
        async_create_issue(
            hass,
            DOMAIN,
            f"{REPAIRS_FRIGATE_GONE}_{entry.entry_id}",
            is_fixable=False,
            severity=IssueSeverity.WARNING,
            translation_key=REPAIRS_FRIGATE_GONE,
            translation_placeholders={"camera_name": camera_name},
        )
    except Exception as exc:  # noqa: BLE001
        _LOGGER.debug("Could not create Frigate repair issue: %s", exc)


async def _clear_frigate_repair(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Delete the Frigate repair issue when Frigate is detected again."""
    try:
        from homeassistant.helpers.issue_registry import async_delete_issue
        async_delete_issue(hass, DOMAIN, f"{REPAIRS_FRIGATE_GONE}_{entry.entry_id}")
    except Exception as exc:  # noqa: BLE001
        _LOGGER.debug("Could not delete Frigate repair issue: %s", exc)


# ── Service schemas ────────────────────────────────────────────────────────────

_DIRECTIONS = ("left", "right", "up", "down", "zoom_in", "zoom_out")

_PTZ_SCHEMA = vol.Schema({
    vol.Required("entity_id"): cv.entity_id,
    vol.Required("direction"): vol.In(_DIRECTIONS),
    vol.Optional("speed", default=DEFAULT_SPEED): vol.All(
        vol.Coerce(float), vol.Range(min=0.0, max=1.0)
    ),
    vol.Optional("duration"): vol.All(vol.Coerce(float), vol.Range(min=0.1, max=60.0)),
})

_PTZ_STOP_SCHEMA = vol.Schema({
    vol.Required("entity_id"): cv.entity_id,
})

_GOTO_PRESET_SCHEMA = vol.Schema({
    vol.Required("entity_id"): cv.entity_id,
    vol.Required("preset"): cv.string,
})

_UPLOAD_AUDIO_SCHEMA = vol.Schema({
    vol.Required("entity_id"): cv.entity_id,
    vol.Required("url"): cv.string,
    vol.Optional("slot", default=101): vol.In([101, 102, 103]),
    vol.Optional("name", default="custom"): cv.string,
    vol.Optional("play", default=False): cv.boolean,
})

_PLAY_AUDIO_SCHEMA = vol.Schema({
    vol.Required("entity_id"): cv.entity_id,
    vol.Optional("slot", default=101): vol.All(vol.Coerce(int), vol.Range(min=0, max=103)),
    vol.Optional("times", default=1): vol.All(vol.Coerce(int), vol.Range(min=1, max=10)),
    vol.Optional("pause", default=1.0): vol.All(vol.Coerce(float), vol.Range(min=0.5, max=30.0)),
})

_DELETE_AUDIO_SCHEMA = vol.Schema({
    vol.Required("entity_id"): cv.entity_id,
    vol.Required("slot"): vol.In([101, 102, 103]),
})

_SPEAK_SCHEMA = vol.Schema({
    vol.Required("entity_id"): cv.entity_id,
    vol.Required("message"): cv.string,
    vol.Optional("tts_engine", default="tts.cloud"): cv.string,
    vol.Optional("language", default=""): cv.string,
    vol.Optional("slot", default=101): vol.In([101, 102, 103]),
    vol.Optional("times", default=1): vol.All(vol.Coerce(int), vol.Range(min=1, max=50)),
    vol.Optional("pause", default=1.0): vol.All(vol.Coerce(float), vol.Range(min=0.5, max=30.0)),
})

_PLAY_FILE_SCHEMA = vol.Schema({
    vol.Required("entity_id"): cv.entity_id,
    vol.Required("url"): cv.string,
    vol.Optional("slot", default=101): vol.In([101, 102, 103]),
    vol.Optional("times", default=1): vol.All(vol.Coerce(int), vol.Range(min=1, max=50)),
    vol.Optional("pause", default=1.0): vol.All(vol.Coerce(float), vol.Range(min=0.5, max=30.0)),
})

_PTZ_MOVE_TO_SCHEMA = vol.Schema({
    vol.Required("entity_id"): cv.entity_id,
    vol.Required("pan"): vol.All(vol.Coerce(float), vol.Range(min=-1.0, max=1.0)),
    vol.Required("tilt"): vol.All(vol.Coerce(float), vol.Range(min=-1.0, max=1.0)),
    vol.Optional("zoom", default=0.0): vol.All(vol.Coerce(float), vol.Range(min=-1.0, max=1.0)),
})

_PTZ_SAVE_PRESET_SCHEMA = vol.Schema({
    vol.Required("entity_id"): cv.entity_id,
    vol.Required("name"): cv.string,
    vol.Optional("id"): vol.All(vol.Coerce(int), vol.Range(min=1, max=8)),
})

_PTZ_DELETE_PRESET_SCHEMA = vol.Schema({
    vol.Required("entity_id"): cv.entity_id,
    vol.Required("name"): cv.string,
})

_DIRECTION_VECTORS: dict[str, tuple[float, float, float]] = {
    "left":     (-1.0, 0.0, 0.0),
    "right":    ( 1.0, 0.0, 0.0),
    "up":       ( 0.0, 1.0, 0.0),
    "down":     ( 0.0,-1.0, 0.0),
    "zoom_in":  ( 0.0, 0.0, 1.0),
    "zoom_out": ( 0.0, 0.0,-1.0),
}


# ── Helpers ───────────────────────────────────────────────────────────────────

def _entry_data_for_entity(hass: HomeAssistant, entity_id: str) -> dict | None:
    """Return entry_data for the config entry that owns *entity_id*, or None."""
    ent_reg = er.async_get(hass)
    entry = ent_reg.async_get(entity_id)
    if entry and entry.config_entry_id:
        return hass.data[DOMAIN].get(entry.config_entry_id)
    return None


async def _openapi_get_presets(openapi) -> list[dict]:
    """Return [{id, name}, ...] from OpenAPI getPresetPoint."""
    import urllib.parse as _up
    result = await openapi.call("getPresetPoint")
    if result.get("errCode") != 0:
        return []
    ids = result.get("id", [])
    names = result.get("name", [])
    return [
        {"id": str(ids[i]), "name": _up.unquote(str(names[i]))}
        for i in range(min(len(ids), len(names)))
    ]


# ── Service handlers ──────────────────────────────────────────────────────────

def _register_services(hass: HomeAssistant) -> None:
    """Register domain services once (guarded so re-entry is a no-op)."""
    if hass.services.has_service(DOMAIN, "ptz"):
        return

    async def handle_ptz(call: ServiceCall) -> None:
        data = _entry_data_for_entity(hass, call.data["entity_id"])
        if not data or not data.get("onvif_ptz"):
            _LOGGER.error(
                "vigicam.ptz: no PTZ support for %s", call.data["entity_id"]
            )
            return
        ptz: VIGIOnvifPtz = data["onvif_ptz"]
        direction = call.data["direction"]
        speed = call.data["speed"]
        duration = call.data.get("duration")
        pan_v, tilt_v, zoom_v = _DIRECTION_VECTORS[direction]
        data["coordinator"].last_preset = None
        data["coordinator"].async_update_listeners()
        await ptz.continuous_move(pan_v * speed, tilt_v * speed, zoom_v * speed)
        if duration:
            await asyncio.sleep(duration)
            await ptz.stop()

    async def handle_ptz_stop(call: ServiceCall) -> None:
        data = _entry_data_for_entity(hass, call.data["entity_id"])
        if not data or not data.get("onvif_ptz"):
            return
        await data["onvif_ptz"].stop()

    async def handle_goto_preset(call: ServiceCall) -> None:
        data = _entry_data_for_entity(hass, call.data["entity_id"])
        if not data:
            _LOGGER.error(
                "vigicam.goto_preset: cannot find camera for %s",
                call.data["entity_id"],
            )
            return
        preset_name = call.data["preset"]
        presets: list[dict] = data.get("presets", [])
        preset = next((p for p in presets if p["name"] == preset_name), None)
        if not preset:
            _LOGGER.error(
                "vigicam.goto_preset: preset '%s' not found (available: %s)",
                preset_name, [p["name"] for p in presets],
            )
            return
        await data["api"].goto_preset(preset["id"])
        data["coordinator"].last_preset = preset_name
        data["coordinator"].async_update_listeners()

    async def handle_upload_audio(call: ServiceCall) -> None:
        data = _entry_data_for_entity(hass, call.data["entity_id"])
        if not data:
            _LOGGER.error("vigicam.upload_audio: cannot find camera for %s", call.data["entity_id"])
            return
        url = call.data["url"]
        slot = call.data["slot"]
        name = call.data["name"]
        play = call.data["play"]
        try:
            session = async_get_clientsession(hass)
            async with session.get(url) as resp:
                resp.raise_for_status()
                audio_bytes = await resp.read()
            await data["api"].upload_audio(slot, name, audio_bytes)
            _LOGGER.debug("vigicam.upload_audio: uploaded %d bytes to slot %d", len(audio_bytes), slot)
            if play:
                await data["api"].play_audio(slot)
        except Exception as exc:
            _LOGGER.error("vigicam.upload_audio failed: %s", exc)

    async def handle_play_audio(call: ServiceCall) -> None:
        data = _entry_data_for_entity(hass, call.data["entity_id"])
        if not data:
            _LOGGER.error("vigicam.play_audio: cannot find camera for %s", call.data["entity_id"])
            return
        try:
            await data["api"].play_audio(
                slot_id=call.data["slot"],
                times=call.data["times"],
                pause=call.data["pause"],
            )
        except Exception as exc:
            _LOGGER.error("vigicam.play_audio failed: %s", exc)

    async def handle_delete_audio(call: ServiceCall) -> None:
        data = _entry_data_for_entity(hass, call.data["entity_id"])
        if not data:
            _LOGGER.error("vigicam.delete_audio: cannot find camera for %s", call.data["entity_id"])
            return
        try:
            await data["api"].delete_audio(call.data["slot"])
            _LOGGER.debug("vigicam.delete_audio: deleted slot %d", call.data["slot"])
        except Exception as exc:
            _LOGGER.error("vigicam.delete_audio failed: %s", exc)

    async def _tts_to_camera_wav(tts_engine: str, message: str, language: str) -> bytes:
        """Generate TTS audio and convert it to 8 kHz mono 16-bit PCM WAV.

        The camera's format limit is WAV mono 8 kHz ≤15 s ≤256 KB (or MP3 ≤64 kbps ≤128 KB).
        Converting everything to 8 kHz WAV avoids bitrate guessing and is always in-spec.
        """
        audio_bytes: bytes | None = None

        # Approach 1: import DATA_TTS_MANAGER from HA so we always use the right key,
        # regardless of which HA version renamed it.
        try:
            from homeassistant.components.tts import DATA_TTS_MANAGER as _TTS_KEY  # type: ignore[attr-defined]
            _mgr = hass.data.get(_TTS_KEY)
        except ImportError:
            _mgr = None

        # Approach 1b: fall back to known key strings if the import didn't work.
        if _mgr is None:
            for _key in ("tts_manager", "tts"):
                _mgr = hass.data.get(_key)
                if _mgr is not None and hasattr(_mgr, "async_get_tts_audio"):
                    break
                _mgr = None

        if _mgr is not None and hasattr(_mgr, "async_get_tts_audio"):
            for _engine in (tts_engine, tts_engine.removeprefix("tts.")):
                try:
                    _ext, audio_bytes = await _mgr.async_get_tts_audio(
                        _engine, message, language=language or None, options={},
                    )
                    if audio_bytes:
                        break
                except Exception:
                    audio_bytes = None

        # Approach 2: module-level async_get_tts_audio (HA 2024.10+ public API).
        if not audio_bytes:
            try:
                from homeassistant.components.tts import async_get_tts_audio as _tts_fn  # type: ignore[attr-defined]
                _ext, audio_bytes = await _tts_fn(
                    hass, tts_engine, message, language=language or None,
                )
            except (ImportError, Exception):
                audio_bytes = None

        # Approach 3: tts.get_tts_audio service (available in some HA builds).
        if not audio_bytes:
            tts_kwargs: dict = {"engine_id": tts_engine, "message": message}
            if language:
                tts_kwargs["language"] = language
            try:
                tts_resp = await hass.services.async_call(
                    "tts", "get_tts_audio", tts_kwargs,
                    blocking=True, return_response=True,
                )
                tts_url = (tts_resp or {}).get("url")
                if tts_url:
                    session = async_get_clientsession(hass)
                    async with session.get(tts_url) as resp:
                        resp.raise_for_status()
                        audio_bytes = await resp.read()
            except Exception:
                audio_bytes = None

        # Approach 4: entity_components lookup (HA 2025.x+ entity-based TTS).
        if not audio_bytes:
            try:
                _ec = hass.data.get("entity_components", {})
                _tts_component = _ec.get("tts") if hasattr(_ec, "get") else None
                if _tts_component and hasattr(_tts_component, "get_entity"):
                    _tts_entity = _tts_component.get_entity(tts_engine)
                    if _tts_entity and hasattr(_tts_entity, "async_get_tts_audio"):
                        _lang = language or getattr(_tts_entity, "default_language", "") or ""
                        _ext, audio_bytes = await _tts_entity.async_get_tts_audio(
                            message, _lang, {}
                        )
            except Exception:
                audio_bytes = None

        # Approach 5: brute-force scan hass.data for any object with async_get_tts_audio.
        if not audio_bytes:
            for _val in hass.data.values():
                if hasattr(_val, "async_get_tts_audio"):
                    for _engine in (tts_engine, tts_engine.removeprefix("tts.")):
                        try:
                            _ext, audio_bytes = await _val.async_get_tts_audio(
                                _engine, message, language=language or None, options={},
                            )
                            if audio_bytes:
                                break
                        except Exception:
                            audio_bytes = None
                    if audio_bytes:
                        break

        if not audio_bytes:
            import homeassistant as _ha
            _ha_version = getattr(_ha, "__version__", "unknown")
            _tts_keys = []
            try:
                _tts_keys = [
                    str(k) for k in hass.data
                    if isinstance(k, str) and "tts" in k.lower()
                ]
            except Exception:
                pass
            _LOGGER.error(
                "vigicam.speak: all TTS access methods failed for '%s' on HA %s. "
                "TTS-related hass.data keys: %s. "
                "Please report this at https://github.com/steveAbratt/VIGICam/issues",
                tts_engine, _ha_version, _tts_keys,
            )
            raise VIGIError(
                f"Could not get TTS audio from '{tts_engine}' (HA {_ha_version}). "
                "Check the HA log for diagnostic keys."
            )

        return await _audio_to_camera_wav(audio_bytes, source_label=tts_engine)

    async def _audio_to_camera_wav(audio_bytes: bytes, source_label: str = "audio") -> bytes:
        """Convert any ffmpeg-readable audio to 8 kHz mono 16-bit PCM WAV.

        Outputs raw PCM from ffmpeg then builds the WAV header in Python so the size
        fields are correct — ffmpeg cannot seek back on a pipe and would leave 0x7FFFFFFF.
        """
        try:
            from homeassistant.components.ffmpeg import get_ffmpeg_manager
            ffmpeg_bin = get_ffmpeg_manager(hass).binary
        except Exception:
            ffmpeg_bin = "ffmpeg"

        proc = await asyncio.create_subprocess_exec(
            ffmpeg_bin,
            "-i", "pipe:0",
            "-ar", "8000", "-ac", "1",
            "-f", "s16le",
            "-loglevel", "quiet",
            "pipe:1",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        pcm_data, stderr = await proc.communicate(input=audio_bytes)
        if proc.returncode != 0 or not pcm_data:
            raise VIGIError(f"ffmpeg conversion failed for {source_label}: {stderr.decode()[:200]}")

        import struct
        _sr, _ch, _bits = 8000, 1, 16
        _data_size = len(pcm_data)
        wav_bytes = struct.pack(
            "<4sI4s4sIHHIIHH4sI",
            b"RIFF", 36 + _data_size, b"WAVE",
            b"fmt ", 16, 1, _ch, _sr, _sr * _ch * _bits // 8, _ch * _bits // 8, _bits,
            b"data", _data_size,
        ) + pcm_data

        if len(wav_bytes) > 256_000:
            raise VIGIError(
                f"Converted WAV is {len(wav_bytes) // 1024} KB — camera limit is 256 KB / 15 s. "
                "Use a shorter audio clip."
            )

        return wav_bytes

    async def handle_speak(call: ServiceCall) -> None:
        data = _entry_data_for_entity(hass, call.data["entity_id"])
        if not data:
            _LOGGER.error("vigicam.speak: cannot find camera for %s", call.data["entity_id"])
            return
        slot = call.data["slot"]
        try:
            wav = await _tts_to_camera_wav(
                call.data["tts_engine"],
                call.data["message"],
                call.data.get("language", ""),
            )
            audio_duration = (len(wav) - 44) / (8000 * 2)  # PCM bytes → seconds
            _LOGGER.debug("vigicam.speak: WAV %d B, %.1fs → slot %d", len(wav), audio_duration, slot)
            try:
                await data["api"].delete_audio(slot)
            except Exception:
                pass
            await data["api"].upload_audio(slot, "announce", wav)
            await data["api"].play_audio(
                slot,
                times=call.data["times"],
                pause=call.data["pause"],
                audio_duration=audio_duration,
            )
        except Exception as exc:
            _LOGGER.error("vigicam.speak failed: %s", exc)

    async def handle_play_file(call: ServiceCall) -> None:
        data = _entry_data_for_entity(hass, call.data["entity_id"])
        if not data:
            _LOGGER.error("vigicam.play_file: cannot find camera for %s", call.data["entity_id"])
            return
        url = call.data["url"]
        slot = call.data["slot"]
        try:
            from pathlib import Path as _Path
            # Convert HA internal media/www URLs to file paths — these endpoints
            # require auth that the shared client session doesn't carry.
            # On HA OS the media dir is /media/ (not /config/media/), so we use
            # hass.config.media_dirs["local"] for the correct base path.
            if url.startswith(("http://", "https://")):
                _media_base = getattr(hass.config, "media_dirs", {}).get(
                    "local", str(_Path(hass.config.config_dir) / "media")
                )
                for _seg, _base in (("/media/local/", _media_base), ("/local/", str(_Path(hass.config.config_dir) / "www"))):
                    if _seg in url:
                        _rel = url.split(_seg, 1)[1].split("?")[0]
                        url = str(_Path(_base) / _rel)
                        _LOGGER.debug("vigicam.play_file: resolved to path %s", url)
                        break

            if url.startswith(("http://", "https://")):
                session = async_get_clientsession(hass)
                async with session.get(url) as resp:
                    resp.raise_for_status()
                    raw_bytes = await resp.read()
            else:
                raw_bytes = await hass.async_add_executor_job(_Path(url).read_bytes)
            wav = await _audio_to_camera_wav(raw_bytes, source_label=url)
            audio_duration = (len(wav) - 44) / (8000 * 2)  # PCM bytes → seconds
            _LOGGER.debug("vigicam.play_file: WAV %d B, %.1fs → slot %d", len(wav), audio_duration, slot)
            try:
                await data["api"].delete_audio(slot)
            except Exception:
                pass
            await data["api"].upload_audio(slot, f"file_{slot}", wav)
            await data["api"].play_audio(
                slot,
                times=call.data["times"],
                pause=call.data["pause"],
                audio_duration=audio_duration,
            )
        except Exception as exc:
            _LOGGER.error("vigicam.play_file failed: %s", exc)

    async def handle_ptz_move_to(call: ServiceCall) -> None:
        data = _entry_data_for_entity(hass, call.data["entity_id"])
        if not data or not data.get("has_ptz") or not data.get("openapi"):
            _LOGGER.error(
                "vigicam.ptz_move_to: requires PTZ + OpenAPI for %s",
                call.data["entity_id"],
            )
            return
        try:
            await data["openapi"].call("motorMove", {
                "x_coord": call.data["pan"],
                "y_coord": call.data["tilt"],
                "z_coord": call.data["zoom"],
            })
            data["coordinator"].last_preset = None
            data["coordinator"].async_update_listeners()
        except Exception as exc:
            _LOGGER.error("vigicam.ptz_move_to failed: %s", exc)

    async def handle_ptz_save_preset(call: ServiceCall) -> None:
        data = _entry_data_for_entity(hass, call.data["entity_id"])
        if not data or not data.get("has_ptz") or not data.get("openapi"):
            _LOGGER.error(
                "vigicam.ptz_save_preset: requires PTZ + OpenAPI for %s",
                call.data["entity_id"],
            )
            return
        openapi = data["openapi"]
        name = call.data["name"]
        preset_id = call.data.get("id")
        try:
            if preset_id is None:
                existing = await _openapi_get_presets(openapi)
                used_ids = {int(p["id"]) for p in existing if p["id"].isdigit()}
                preset_id = next((i for i in range(1, 9) if i not in used_ids), 1)
            await openapi.call("setPresetPoint", {"id": str(preset_id), "name": name})
            data["coordinator"].presets = []  # invalidate cache; select refreshes on next poll
            _LOGGER.debug("vigicam.ptz_save_preset: saved '%s' as id %d", name, preset_id)
        except Exception as exc:
            _LOGGER.error("vigicam.ptz_save_preset failed: %s", exc)

    async def handle_ptz_delete_preset(call: ServiceCall) -> None:
        data = _entry_data_for_entity(hass, call.data["entity_id"])
        if not data or not data.get("has_ptz") or not data.get("openapi"):
            _LOGGER.error(
                "vigicam.ptz_delete_preset: requires PTZ + OpenAPI for %s",
                call.data["entity_id"],
            )
            return
        openapi = data["openapi"]
        name = call.data["name"]
        try:
            presets = await _openapi_get_presets(openapi)
            preset = next((p for p in presets if p["name"] == name), None)
            if not preset:
                _LOGGER.error(
                    "vigicam.ptz_delete_preset: preset '%s' not found (available: %s)",
                    name, [p["name"] for p in presets],
                )
                return
            await openapi.call("removePresetPoint", {"id": preset["id"]})
            data["coordinator"].presets = []
            _LOGGER.debug("vigicam.ptz_delete_preset: deleted preset '%s'", name)
        except Exception as exc:
            _LOGGER.error("vigicam.ptz_delete_preset failed: %s", exc)

    hass.services.async_register(DOMAIN, "ptz", handle_ptz, schema=_PTZ_SCHEMA)
    hass.services.async_register(DOMAIN, "ptz_stop", handle_ptz_stop, schema=_PTZ_STOP_SCHEMA)
    hass.services.async_register(DOMAIN, "goto_preset", handle_goto_preset, schema=_GOTO_PRESET_SCHEMA)
    hass.services.async_register(DOMAIN, "ptz_move_to", handle_ptz_move_to, schema=_PTZ_MOVE_TO_SCHEMA)
    hass.services.async_register(DOMAIN, "ptz_save_preset", handle_ptz_save_preset, schema=_PTZ_SAVE_PRESET_SCHEMA)
    hass.services.async_register(DOMAIN, "ptz_delete_preset", handle_ptz_delete_preset, schema=_PTZ_DELETE_PRESET_SCHEMA)
    hass.services.async_register(DOMAIN, "upload_audio", handle_upload_audio, schema=_UPLOAD_AUDIO_SCHEMA)
    hass.services.async_register(DOMAIN, "play_audio", handle_play_audio, schema=_PLAY_AUDIO_SCHEMA)
    hass.services.async_register(DOMAIN, "delete_audio", handle_delete_audio, schema=_DELETE_AUDIO_SCHEMA)
    hass.services.async_register(DOMAIN, "speak", handle_speak, schema=_SPEAK_SCHEMA)
    hass.services.async_register(DOMAIN, "play_file", handle_play_file, schema=_PLAY_FILE_SCHEMA)


# ── Setup / teardown ──────────────────────────────────────────────────────────

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    # Remove entities that belong to disabled feature groups before creating
    # new ones — prevents unavailable stubs accumulating after options changes.
    _cleanup_stale_entities(hass, entry)

    ip = entry.data[CONF_HOST]
    username = entry.data[CONF_USERNAME]
    password = entry.data[CONF_PASSWORD]
    verify_ssl = entry.data.get(CONF_VERIFY_SSL, False)

    if verify_ssl:
        session = async_get_clientsession(hass)
    else:
        session = async_create_clientsession(hass, verify_ssl=False)

    camera_api = VIGICamera(ip, username, password, session=session)
    try:
        await camera_api.authenticate()
        device_info = await camera_api.get_device_info()
        presets = await camera_api.get_presets()
        has_smart_frames = await camera_api.supports_smart_frames()
    except VIGIAuthError as exc:
        raise ConfigEntryAuthFailed(str(exc)) from exc
    except VIGIError as exc:
        raise ConfigEntryNotReady(str(exc)) from exc

    has_ptz = len(presets) > 0
    onvif_ptz = VIGIOnvifPtz(ip, username, password) if has_ptz else None

    # Probe OpenAPI before the first coordinator refresh so smart-detection
    # switch state is available when platform entities are created.
    has_openapi = await try_openapi(ip, username, password)
    openapi: VIGIOpenAPI | None = VIGIOpenAPI(ip, username, password) if has_openapi else None
    if not has_openapi:
        _LOGGER.info(
            "VIGICam: OpenAPI not available on %s. "
            "Enable it in camera Settings → Network → OpenAPI to unlock "
            "Vehicle Detection, Audio Anomaly, and other split detection sensors.",
            ip,
        )

    coordinator = VIGICoordinator(
        hass, camera_api,
        has_ptz=has_ptz,
        has_openapi=has_openapi,
        openapi=openapi,
    )
    await coordinator.async_config_entry_first_refresh()

    # Determine SD card presence from first-refresh storage data.
    has_sd_card = _detect_sd_card(coordinator.data.get("storage", {}))
    coordinator.has_sd_card = has_sd_card

    # Clear any stale SD card repair issue if the card is now present.
    if has_sd_card:
        await _clear_sd_card_repair(hass, entry)

    camera_name = (
        device_info.get("dev_name") or device_info.get("alias") or ip
    )

    # Detect whether a Frigate camera is running at the same IP.
    frigate_camera = detect_frigate_camera(hass, ip)
    had_frigate = entry.data.get("_frigate_linked", False)

    if frigate_camera:
        # Frigate is present — clear any stale "gone" repair and record the link.
        await _clear_frigate_repair(hass, entry)
        if not had_frigate:
            hass.config_entries.async_update_entry(
                entry, data={**entry.data, "_frigate_linked": True}
            )
    elif had_frigate:
        # Frigate was previously linked but is no longer detected.
        _LOGGER.info(
            "VIGICam: Frigate no longer detected for %s — raising repair notice", ip
        )
        await _raise_frigate_repair(hass, entry, camera_name)

    # Only start event listeners when detection events are enabled.
    want_detection = _feature(entry, CONF_FEATURE_DETECTION_EVENTS, DEFAULT_FEATURE_DETECTION_EVENTS)
    onvif_events = VIGIOnvifEvents(hass, ip, username, password, entry.entry_id)
    openapi_events = (
        VIGIOpenAPIEventListener(hass, openapi, entry.entry_id)
        if (has_openapi and want_detection)
        else None
    )

    entry_data = {
        "api": camera_api,
        "coordinator": coordinator,
        "onvif_events": onvif_events,
        "onvif_ptz": onvif_ptz,
        "openapi_events": openapi_events,
        "device_info": device_info,
        "camera_name": camera_name,
        "has_ptz": has_ptz,
        "has_smart_frames": has_smart_frames,
        "has_sd_card": has_sd_card,
        "has_openapi": has_openapi,
        "has_frigate": frigate_camera is not None,
        "openapi": openapi,
        "presets": presets,
        "ip": ip,
        "username": username,
        "password": password,
        "entry_id": entry.entry_id,
    }
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = entry_data

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    if want_detection:
        await onvif_events.async_start()
        if openapi_events:
            await openapi_events.async_start()

    # Watch for SD card transitions during normal operation.
    _prev_sd: dict[str, bool] = {"has_sd": has_sd_card}

    @callback
    def _on_coordinator_update() -> None:
        now_has_sd = coordinator.has_sd_card
        was_has_sd = _prev_sd["has_sd"]
        if was_has_sd == now_has_sd:
            return
        _prev_sd["has_sd"] = now_has_sd
        if not now_has_sd:
            _LOGGER.info("VIGICam: SD card removed from %s — raising repair notice", ip)
            hass.async_create_task(
                _raise_sd_card_repair(hass, entry, camera_name)
            )
        else:
            _LOGGER.info("VIGICam: SD card reinserted in %s — scheduling reload", ip)
            hass.async_create_task(_clear_sd_card_repair(hass, entry))
            hass.async_create_task(
                hass.config_entries.async_schedule_reload(entry.entry_id)
            )

    entry_data["_sd_unsub"] = coordinator.async_add_listener(_on_coordinator_update)

    # Re-register a listener so options changes trigger a reload.
    entry.async_on_unload(entry.add_update_listener(_async_options_updated))

    _register_services(hass)
    return True


async def _async_options_updated(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload the config entry when options are changed."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        data = hass.data[DOMAIN].pop(entry.entry_id)
        if unsub := data.get("_sd_unsub"):
            unsub()
        await data["onvif_events"].async_stop()
        if data.get("openapi_events"):
            await data["openapi_events"].async_stop()
        if data.get("onvif_ptz"):
            await data["onvif_ptz"].close()
        await data["api"].close()
    return unloaded
