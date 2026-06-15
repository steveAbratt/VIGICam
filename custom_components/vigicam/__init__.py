"""VIGI & InSight Camera integration for Home Assistant."""
from __future__ import annotations

import asyncio
import logging

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_USERNAME, Platform
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers import config_validation as cv, entity_registry as er
from homeassistant.helpers.aiohttp_client import async_create_clientsession, async_get_clientsession

from .api import VIGIAuthError, VIGICamera, VIGIError
from .const import CONF_VERIFY_SSL, DOMAIN
from .coordinator import VIGICoordinator
from .onvif_events import VIGIOnvifEvents
from .onvif_ptz import DEFAULT_SPEED, VIGIOnvifPtz

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [
    Platform.CAMERA,
    Platform.BINARY_SENSOR,
    Platform.SWITCH,
    Platform.SELECT,
    Platform.SENSOR,
    Platform.BUTTON,
    Platform.NUMBER,
]

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
            _LOGGER.debug("vigicam.speak: WAV size %d B, uploading to slot %d", len(wav), slot)
            try:
                await data["api"].delete_audio(slot)
            except Exception:
                pass
            await data["api"].upload_audio(slot, "announce", wav)
            await data["api"].play_audio(slot, times=call.data["times"], pause=call.data["pause"])
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
            if url.startswith(("http://", "https://")):
                session = async_get_clientsession(hass)
                async with session.get(url) as resp:
                    resp.raise_for_status()
                    raw_bytes = await resp.read()
            else:
                from pathlib import Path
                raw_bytes = await hass.async_add_executor_job(Path(url).read_bytes)
            wav = await _audio_to_camera_wav(raw_bytes, source_label=url)
            _LOGGER.debug("vigicam.play_file: WAV %d B → slot %d", len(wav), slot)
            try:
                await data["api"].delete_audio(slot)
            except Exception:
                pass
            await data["api"].upload_audio(slot, f"file_{slot}", wav)
            await data["api"].play_audio(slot, times=call.data["times"], pause=call.data["pause"])
        except Exception as exc:
            _LOGGER.error("vigicam.play_file failed: %s", exc)

    hass.services.async_register(DOMAIN, "ptz", handle_ptz, schema=_PTZ_SCHEMA)
    hass.services.async_register(DOMAIN, "ptz_stop", handle_ptz_stop, schema=_PTZ_STOP_SCHEMA)
    hass.services.async_register(DOMAIN, "goto_preset", handle_goto_preset, schema=_GOTO_PRESET_SCHEMA)
    hass.services.async_register(DOMAIN, "upload_audio", handle_upload_audio, schema=_UPLOAD_AUDIO_SCHEMA)
    hass.services.async_register(DOMAIN, "play_audio", handle_play_audio, schema=_PLAY_AUDIO_SCHEMA)
    hass.services.async_register(DOMAIN, "delete_audio", handle_delete_audio, schema=_DELETE_AUDIO_SCHEMA)
    hass.services.async_register(DOMAIN, "speak", handle_speak, schema=_SPEAK_SCHEMA)
    hass.services.async_register(DOMAIN, "play_file", handle_play_file, schema=_PLAY_FILE_SCHEMA)


# ── Setup / teardown ──────────────────────────────────────────────────────────

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
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
    except VIGIAuthError as exc:
        raise ConfigEntryAuthFailed(str(exc)) from exc
    except VIGIError as exc:
        raise ConfigEntryNotReady(str(exc)) from exc

    has_ptz = len(presets) > 0
    onvif_ptz = VIGIOnvifPtz(ip, username, password) if has_ptz else None

    coordinator = VIGICoordinator(hass, camera_api, has_ptz=has_ptz)
    await coordinator.async_config_entry_first_refresh()

    onvif_events = VIGIOnvifEvents(hass, ip, username, password, entry.entry_id)

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = {
        "api": camera_api,
        "coordinator": coordinator,
        "onvif_events": onvif_events,
        "onvif_ptz": onvif_ptz,
        "device_info": device_info,
        "has_ptz": has_ptz,
        "presets": presets,
        "ip": ip,
        "username": username,
        "password": password,
        "entry_id": entry.entry_id,
    }

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    await onvif_events.async_start()
    _register_services(hass)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        data = hass.data[DOMAIN].pop(entry.entry_id)
        await data["onvif_events"].async_stop()
        if data.get("onvif_ptz"):
            await data["onvif_ptz"].close()
        await data["api"].close()
    return unloaded
