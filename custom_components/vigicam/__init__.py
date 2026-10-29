"""VIGI & InSight Camera integration for Home Assistant."""
from __future__ import annotations

import asyncio
import logging

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    CONF_HOST,
    CONF_PASSWORD,
    CONF_USERNAME,
    EVENT_HOMEASSISTANT_STOP,
    Platform,
)
from homeassistant.core import Event, HomeAssistant, ServiceCall, callback
from homeassistant.exceptions import (
    ConfigEntryAuthFailed,
    ConfigEntryNotReady,
    HomeAssistantError,
    ServiceValidationError,
)
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
    REPAIRS_WRONG_CAMERA,
)
from .frigate import detect_frigate_camera
from .coordinator import VIGICoordinator
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

_ENTITY_IDS = vol.Any(cv.entity_id, [cv.entity_id])

_PTZ_SCHEMA = vol.Schema({
    vol.Required("entity_id"): _ENTITY_IDS,
    vol.Required("direction"): vol.In(_DIRECTIONS),
    vol.Optional("speed", default=DEFAULT_SPEED): vol.All(
        vol.Coerce(float), vol.Range(min=0.0, max=1.0)
    ),
    vol.Optional("duration"): vol.All(vol.Coerce(float), vol.Range(min=0.1, max=60.0)),
})

_PTZ_STOP_SCHEMA = vol.Schema({
    vol.Required("entity_id"): _ENTITY_IDS,
})

_GOTO_PRESET_SCHEMA = vol.Schema({
    vol.Required("entity_id"): _ENTITY_IDS,
    vol.Required("preset"): cv.string,
})

_UPLOAD_AUDIO_SCHEMA = vol.Schema({
    vol.Required("entity_id"): _ENTITY_IDS,
    vol.Required("url"): cv.string,
    vol.Optional("slot", default=101): vol.In([101, 102, 103]),
    vol.Optional("name", default="custom"): cv.string,
    vol.Optional("play", default=False): cv.boolean,
})

_PLAY_AUDIO_SCHEMA = vol.Schema({
    vol.Required("entity_id"): _ENTITY_IDS,
    # 0 and 1 are the camera's built-in sounds, 101–103 the custom slots; nothing between.
    vol.Optional("slot", default=101): vol.All(vol.Coerce(int), vol.In([0, 1, 101, 102, 103])),
    vol.Optional("times", default=1): vol.All(vol.Coerce(int), vol.Range(min=1, max=10)),
    vol.Optional("pause", default=1.0): vol.All(vol.Coerce(float), vol.Range(min=0.5, max=30.0)),
})

_DELETE_AUDIO_SCHEMA = vol.Schema({
    vol.Required("entity_id"): _ENTITY_IDS,
    vol.Required("slot"): vol.In([101, 102, 103]),
})

_SPEAK_SCHEMA = vol.Schema({
    vol.Required("entity_id"): _ENTITY_IDS,
    vol.Required("message"): cv.string,
    vol.Optional("tts_engine", default="tts.cloud"): cv.string,
    vol.Optional("language", default=""): cv.string,
    vol.Optional("slot", default=101): vol.In([101, 102, 103]),
    vol.Optional("times", default=1): vol.All(vol.Coerce(int), vol.Range(min=1, max=50)),
    vol.Optional("pause", default=1.0): vol.All(vol.Coerce(float), vol.Range(min=0.5, max=30.0)),
})

_PLAY_FILE_SCHEMA = vol.Schema({
    vol.Required("entity_id"): _ENTITY_IDS,
    vol.Required("url"): cv.string,
    vol.Optional("slot", default=101): vol.In([101, 102, 103]),
    vol.Optional("times", default=1): vol.All(vol.Coerce(int), vol.Range(min=1, max=50)),
    vol.Optional("pause", default=1.0): vol.All(vol.Coerce(float), vol.Range(min=0.5, max=30.0)),
})

_PTZ_MOVE_TO_SCHEMA = vol.Schema({
    vol.Required("entity_id"): _ENTITY_IDS,
    vol.Required("pan"): vol.All(vol.Coerce(float), vol.Range(min=-1.0, max=1.0)),
    vol.Required("tilt"): vol.All(vol.Coerce(float), vol.Range(min=-1.0, max=1.0)),
    # Not truly optional: motorMove without z_coord is refused (-10010, C540V).
    vol.Optional("zoom", default=0.0): vol.All(vol.Coerce(float), vol.Range(min=-1.0, max=1.0)),
})

_PTZ_SAVE_PRESET_SCHEMA = vol.Schema({
    vol.Required("entity_id"): _ENTITY_IDS,
    vol.Required("name"): cv.string,
    vol.Optional("id"): vol.All(vol.Coerce(int), vol.Range(min=1, max=8)),
})

_PTZ_DELETE_PRESET_SCHEMA = vol.Schema({
    vol.Required("entity_id"): _ENTITY_IDS,
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

_WAV_HEADER_BYTES = 44
_WAV_BYTES_PER_SECOND = 8000 * 2  # 8 kHz mono 16-bit PCM


def _wav_duration(wav: bytes) -> float:
    """Duration in seconds of an 8 kHz mono 16-bit PCM WAV."""
    return (len(wav) - _WAV_HEADER_BYTES) / _WAV_BYTES_PER_SECOND


class _Outcome:
    """Per-camera problems in one service call, raised together once every camera has
    been tried.

    The cameras that worked still get the command; the call then fails, so an
    automation stops and its trace names the camera and the reason, instead of
    carrying on as if every camera had done it.
    """

    def __init__(self, service: str) -> None:
        self.service = service
        self.invalid: list[str] = []  # the request can't apply to this camera
        self.failed: list[str] = []   # the camera, or fetching the audio, failed

    def raise_if_any(self) -> None:
        if self.failed:
            raise HomeAssistantError(
                f"vigicam.{self.service} failed: " + "; ".join(self.failed + self.invalid)
            )
        if self.invalid:
            raise ServiceValidationError(
                f"vigicam.{self.service}: " + "; ".join(self.invalid)
            )


def _iter_targets(
    hass: HomeAssistant, call: ServiceCall, service: str
) -> list[tuple[str, dict]]:
    """Return [(entity_id, entry_data), ...] for every camera the call targets.

    entity_id accepts a single id or a list, so one service call can address
    several cameras. An entity that isn't a VIGICam camera, or a camera that fails the
    identity check, aborts the whole call before anything is sent to any camera.
    """
    raw = call.data["entity_id"]
    targets: list[tuple[str, dict]] = []
    for eid in (raw if isinstance(raw, list) else [raw]):
        data = _entry_data_for_entity(hass, eid)
        if data is None:
            raise ServiceValidationError(
                f"vigicam.{service}: {eid} is not a VIGICam camera entity"
            )
        if not data["coordinator"].identity_ok:
            # Services bypass entities, so unavailable entities don't stop them. Without
            # this a preset save would write into the other camera's preset table.
            raise HomeAssistantError(
                f"vigicam.{service}: a different camera is answering at "
                f"{data['ip']} — see Settings → Repairs"
            )
        targets.append((eid, data))
    return targets


async def _fetch_audio_bytes(hass: HomeAssistant, url: str) -> tuple[bytes, str]:
    """Load audio from *url*, returning (raw_bytes, resolved_source).

    HA's own /media/local/ and /local/ URLs are rewritten to filesystem paths —
    those endpoints require auth the shared client session does not carry. On
    HA OS the media dir is /media/ rather than /config/media/, so the base path
    comes from hass.config.media_dirs["local"].
    """
    from pathlib import Path

    if url.startswith(("http://", "https://")):
        media_base = getattr(hass.config, "media_dirs", {}).get(
            "local", str(Path(hass.config.config_dir) / "media")
        )
        www_base = str(Path(hass.config.config_dir) / "www")
        for seg, base in (("/media/local/", media_base), ("/local/", www_base)):
            if seg in url:
                url = str(Path(base) / url.split(seg, 1)[1].split("?")[0])
                _LOGGER.debug("vigicam: resolved media URL to path %s", url)
                break

    if url.startswith(("http://", "https://")):
        session = async_get_clientsession(hass)
        async with session.get(url) as resp:
            resp.raise_for_status()
            return await resp.read(), url

    # A local path must sit inside HA's allowlist (allowlist_external_dirs, media dirs,
    # www). Without this a service call could read any file the HA process can.
    # is_allowed_path touches the filesystem; HA documents it as blocking I/O.
    if not await hass.async_add_executor_job(hass.config.is_allowed_path, url):
        raise VIGIError(f"path not allowed by Home Assistant configuration: {url}")
    return await hass.async_add_executor_job(Path(url).read_bytes), url


async def _replace_and_play(
    api, slot: int, name: str, wav: bytes, duration: float, times: int, pause: float
) -> None:
    """Overwrite custom audio *slot* with *wav* and play it back."""
    try:
        await api.delete_audio(slot)
    except Exception:
        pass  # slot may already be empty — clearing it is best-effort
    await api.upload_audio(slot, name, wav)
    await api.play_audio(slot, times=times, pause=pause, audio_duration=duration)


async def communicate_or_kill(proc, timeout: float, *, input: bytes | None = None):
    """proc.communicate() with a timeout that actually kills the child.

    asyncio.wait_for only cancels the *waiting* task — the ffmpeg process keeps running,
    holding its RTSP session open. VIGI cameras allow only a handful of concurrent RTSP
    sessions, so orphans accumulate until streams and snapshots fail camera-wide.

    Returns (stdout, stderr), or (None, None) if it had to be killed.
    """
    try:
        return await asyncio.wait_for(proc.communicate(input=input), timeout=timeout)
    except asyncio.TimeoutError:
        try:
            proc.kill()
        except ProcessLookupError:
            pass          # already gone
        try:
            await proc.wait()   # reap it, so it does not linger as a zombie
        except ProcessLookupError:
            pass
        return None, None


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
    # A camera with no presets replies with an error rather than an empty list, so this
    # inspects errCode itself instead of treating a rejection as a failure.
    result = await openapi.call("getPresetPoint", raise_on_error=False)
    if openapi.err_code(result) != 0:
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
        speed = call.data["speed"]
        duration = call.data.get("duration")
        pan_v, tilt_v, zoom_v = _DIRECTION_VECTORS[call.data["direction"]]

        moving: list[VIGIOnvifPtz] = []
        outcome = _Outcome("ptz")
        for eid, data in _iter_targets(hass, call, "ptz"):
            if not data.get("onvif_ptz"):
                outcome.invalid.append(f"{eid} has no PTZ")
                continue
            ptz: VIGIOnvifPtz = data["onvif_ptz"]
            data["coordinator"].last_preset = None
            data["coordinator"].async_update_listeners()
            try:
                await ptz.continuous_move(pan_v * speed, tilt_v * speed, zoom_v * speed)
            except Exception as exc:
                outcome.failed.append(f"{eid}: {exc}")
                continue
            moving.append(ptz)

        # One shared sleep, then stop every camera that started moving.
        if duration and moving:
            await asyncio.sleep(duration)
            for ptz in moving:
                await ptz.stop()
        outcome.raise_if_any()

    async def handle_ptz_stop(call: ServiceCall) -> None:
        outcome = _Outcome("ptz_stop")
        for eid, data in _iter_targets(hass, call, "ptz_stop"):
            if not data.get("onvif_ptz"):
                continue
            try:
                await data["onvif_ptz"].stop()
            except Exception as exc:
                outcome.failed.append(f"{eid}: {exc}")
        outcome.raise_if_any()

    async def handle_goto_preset(call: ServiceCall) -> None:
        preset_name = call.data["preset"]
        outcome = _Outcome("goto_preset")
        for eid, data in _iter_targets(hass, call, "goto_preset"):
            # The coordinator's copy, not entry_data["presets"]: the latter is captured
            # once at setup and never refreshed, so a preset added or renamed on the
            # camera after HA started was never found here. The 0.7.4 refresh fix landed
            # on the coordinator only, leaving this service on the stale snapshot.
            presets: list[dict] = data["coordinator"].presets or []
            preset = next((p for p in presets if p["name"] == preset_name), None)
            if not preset:
                outcome.invalid.append(
                    f"no preset '{preset_name}' on {eid} "
                    f"(available: {', '.join(p['name'] for p in presets) or 'none'})"
                )
                continue
            try:
                await data["api"].goto_preset(preset["id"])
            except Exception as exc:
                outcome.failed.append(f"{eid}: {exc}")
                continue
            data["coordinator"].last_preset = preset_name
            data["coordinator"].async_update_listeners()
        outcome.raise_if_any()

    async def handle_upload_audio(call: ServiceCall) -> None:
        targets = _iter_targets(hass, call, "upload_audio")
        if not targets:
            return
        slot = call.data["slot"]
        name = call.data["name"]
        play = call.data["play"]

        # Fetch and transcode once, then push the same WAV to every camera.
        try:
            raw_bytes, source = await _fetch_audio_bytes(hass, call.data["url"])
            wav = await _audio_to_camera_wav(raw_bytes, source_label=source)
        except Exception as exc:
            raise HomeAssistantError(
                f"vigicam.upload_audio: could not load {call.data['url']}: {exc}"
            ) from exc
        duration = _wav_duration(wav)
        _LOGGER.debug("vigicam.upload_audio: %d B (%.1fs) to slot %d", len(wav), duration, slot)

        outcome = _Outcome("upload_audio")
        for eid, data in targets:
            try:
                await data["api"].upload_audio(slot, name, wav)
                if play:
                    await data["api"].play_audio(slot, audio_duration=duration)
            except Exception as exc:
                outcome.failed.append(f"{eid}: {exc}")
        outcome.raise_if_any()

    async def handle_play_audio(call: ServiceCall) -> None:
        outcome = _Outcome("play_audio")
        for eid, data in _iter_targets(hass, call, "play_audio"):
            try:
                await data["api"].play_audio(
                    slot_id=call.data["slot"],
                    times=call.data["times"],
                    pause=call.data["pause"],
                )
            except Exception as exc:
                outcome.failed.append(f"{eid}: {exc}")
        outcome.raise_if_any()

    async def handle_delete_audio(call: ServiceCall) -> None:
        slot = call.data["slot"]
        outcome = _Outcome("delete_audio")
        for eid, data in _iter_targets(hass, call, "delete_audio"):
            try:
                await data["api"].delete_audio(slot)
                _LOGGER.debug("vigicam.delete_audio: deleted slot %d on %s", slot, eid)
            except Exception as exc:
                outcome.failed.append(f"{eid}: {exc}")
        outcome.raise_if_any()

    async def _tts_to_camera_wav(tts_engine: str, message: str, language: str) -> bytes:
        """Generate TTS audio and convert it to 8 kHz mono 16-bit PCM WAV.

        The camera's format limit is WAV mono 8 kHz ≤15 s ≤256 KB (or MP3 ≤64 kbps ≤128 KB).
        Converting everything to 8 kHz WAV avoids bitrate guessing and is always in-spec.
        """
        from homeassistant.components import tts

        # HA's public route to raw TTS audio, the one its own Assist satellites use. The
        # engine is tried as given and without "tts.", so both an entity ID and a legacy
        # provider name ("cloud") work.
        errors: list[str] = []
        for engine in dict.fromkeys((tts_engine, tts_engine.removeprefix("tts."))):
            try:
                media_id = tts.generate_media_source_id(
                    hass, message, engine=engine, language=language or None
                )
                _ext, audio_bytes = await tts.async_get_media_source_audio(hass, media_id)
            except Exception as exc:  # noqa: BLE001  — engines raise their own types
                errors.append(f"{engine}: {exc}")
                continue
            if audio_bytes:
                return await _audio_to_camera_wav(audio_bytes, source_label=engine)
            errors.append(f"{engine}: no audio")
        raise VIGIError(f"no audio from TTS engine '{tts_engine}' ({'; '.join(errors)})")

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
            "-t", "15",
            "-i", "pipe:0",
            "-ar", "8000", "-ac", "1",
            "-f", "s16le",
            "-loglevel", "quiet",
            "pipe:1",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        # communicate_or_kill, not proc.communicate: a stalled ffmpeg on malformed input
        # would otherwise hang the service call forever and leave the child holding its pipes.
        pcm_data, stderr = await communicate_or_kill(proc, 20, input=audio_bytes)
        if pcm_data is None:
            # Killed on timeout — stderr is None too, so this must precede the check below.
            raise VIGIError(f"ffmpeg timed out converting {source_label}")
        if proc.returncode != 0 or not pcm_data:
            raise VIGIError(f"ffmpeg conversion failed for {source_label}: {stderr.decode()[:200]}")

        if len(pcm_data) >= 15 * _WAV_BYTES_PER_SECOND:
            _LOGGER.warning(
                "vigicam: %s is 15 s or longer; the camera plays at most 15 s, so it was cut "
                "there", source_label,
            )

        import struct
        _sr, _ch, _bits = 8000, 1, 16
        # Belt-and-braces cap at the camera's 256 KB limit; the -t 15 above
        # already keeps 8 kHz mono PCM to ~240 KB, so this rarely fires.
        _MAX_PCM = 256_000 - _WAV_HEADER_BYTES
        if len(pcm_data) > _MAX_PCM:
            _LOGGER.warning("vigicam: audio truncated to fit camera 256 KB limit (%s)", source_label)
            pcm_data = pcm_data[:_MAX_PCM]
        _data_size = len(pcm_data)
        wav_bytes = struct.pack(
            "<4sI4s4sIHHIIHH4sI",
            b"RIFF", 36 + _data_size, b"WAVE",
            b"fmt ", 16, 1, _ch, _sr, _sr * _ch * _bits // 8, _ch * _bits // 8, _bits,
            b"data", _data_size,
        ) + pcm_data

        return wav_bytes

    async def handle_speak(call: ServiceCall) -> None:
        targets = _iter_targets(hass, call, "speak")
        if not targets:
            return
        slot = call.data["slot"]

        # Generate the TTS clip once, then announce it on every camera.
        try:
            wav = await _tts_to_camera_wav(
                call.data["tts_engine"],
                call.data["message"],
                call.data.get("language", ""),
            )
        except Exception as exc:
            raise HomeAssistantError(f"vigicam.speak: TTS generation failed: {exc}") from exc
        duration = _wav_duration(wav)
        _LOGGER.debug("vigicam.speak: WAV %d B, %.1fs to slot %d", len(wav), duration, slot)

        outcome = _Outcome("speak")
        for eid, data in targets:
            try:
                await _replace_and_play(
                    data["api"], slot, "announce", wav, duration,
                    call.data["times"], call.data["pause"],
                )
            except Exception as exc:
                outcome.failed.append(f"{eid}: {exc}")
        outcome.raise_if_any()

    async def handle_play_file(call: ServiceCall) -> None:
        targets = _iter_targets(hass, call, "play_file")
        if not targets:
            return
        slot = call.data["slot"]

        # Fetch and transcode once, then play the same WAV on every camera.
        try:
            raw_bytes, source = await _fetch_audio_bytes(hass, call.data["url"])
            wav = await _audio_to_camera_wav(raw_bytes, source_label=source)
        except Exception as exc:
            raise HomeAssistantError(
                f"vigicam.play_file: could not load {call.data['url']}: {exc}"
            ) from exc
        duration = _wav_duration(wav)
        _LOGGER.debug("vigicam.play_file: WAV %d B, %.1fs to slot %d", len(wav), duration, slot)

        outcome = _Outcome("play_file")
        for eid, data in targets:
            try:
                await _replace_and_play(
                    data["api"], slot, f"file_{slot}", wav, duration,
                    call.data["times"], call.data["pause"],
                )
            except Exception as exc:
                outcome.failed.append(f"{eid}: {exc}")
        outcome.raise_if_any()

    async def handle_ptz_move_to(call: ServiceCall) -> None:
        coords = {
            "x_coord": call.data["pan"],
            "y_coord": call.data["tilt"],
            "z_coord": call.data["zoom"],
        }
        outcome = _Outcome("ptz_move_to")
        for eid, data in _iter_targets(hass, call, "ptz_move_to"):
            if not data.get("has_ptz") or not data.get("openapi"):
                outcome.invalid.append(f"{eid} needs PTZ and OpenAPI")
                continue
            try:
                await data["openapi"].call("motorMove", coords)
                data["coordinator"].last_preset = None
                data["coordinator"].async_update_listeners()
            except Exception as exc:
                outcome.failed.append(f"{eid}: {exc}")
        outcome.raise_if_any()

    async def handle_ptz_save_preset(call: ServiceCall) -> None:
        name = call.data["name"]
        outcome = _Outcome("ptz_save_preset")
        for eid, data in _iter_targets(hass, call, "ptz_save_preset"):
            if not data.get("has_ptz") or not data.get("openapi"):
                outcome.invalid.append(f"{eid} needs PTZ and OpenAPI")
                continue
            openapi = data["openapi"]
            # Re-read per camera: an auto-allocated id depends on that camera's presets.
            preset_id = call.data.get("id")
            try:
                if preset_id is None:
                    existing = await _openapi_get_presets(openapi)
                    used_ids = {int(p["id"]) for p in existing if p["id"].isdigit()}
                    preset_id = next((i for i in range(1, 9) if i not in used_ids), 1)
                await openapi.call("setPresetPoint", {"id": str(preset_id), "name": name})
                await data["coordinator"].async_refresh_presets()
                _LOGGER.debug(
                    "vigicam.ptz_save_preset: saved '%s' as id %d on %s", name, preset_id, eid
                )
            except Exception as exc:
                outcome.failed.append(f"{eid}: {exc}")
        outcome.raise_if_any()

    async def handle_ptz_delete_preset(call: ServiceCall) -> None:
        name = call.data["name"]
        outcome = _Outcome("ptz_delete_preset")
        for eid, data in _iter_targets(hass, call, "ptz_delete_preset"):
            if not data.get("has_ptz") or not data.get("openapi"):
                outcome.invalid.append(f"{eid} needs PTZ and OpenAPI")
                continue
            openapi = data["openapi"]
            try:
                presets = await _openapi_get_presets(openapi)
                preset = next((p for p in presets if p["name"] == name), None)
                if not preset:
                    outcome.invalid.append(
                        f"no preset '{name}' on {eid} "
                        f"(available: {', '.join(p['name'] for p in presets) or 'none'})"
                    )
                    continue
                await openapi.call("removePresetPoint", {"id": preset["id"]})
                await data["coordinator"].async_refresh_presets()
                _LOGGER.debug("vigicam.ptz_delete_preset: deleted preset '%s' on %s", name, eid)
            except Exception as exc:
                outcome.failed.append(f"{eid}: {exc}")
        outcome.raise_if_any()

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
        has_event_capture = await camera_api.supports_event_image_capture()
    except VIGIAuthError as exc:
        raise ConfigEntryAuthFailed(str(exc)) from exc
    except VIGIError as exc:
        raise ConfigEntryNotReady(str(exc)) from exc

    # Presets alone are not a PTZ test: a PTZ camera with none saved would look fixed.
    has_ptz = bool(presets) or await camera_api.check_ptz_support()
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
        config_entry=entry,
    )
    await coordinator.async_config_entry_first_refresh()

    # None means the first storage read failed. Fall back to the last answer we had rather
    # than "no card": otherwise a camera whose storage read is flaky at startup builds no SD
    # entities, sees the card on the next poll, reloads — and can repeat that every time,
    # each reload making a new ONVIF subscription.
    if coordinator.has_sd_card is None:
        has_sd_card = bool(entry.data.get("_has_sd_card", False))
    else:
        has_sd_card = coordinator.has_sd_card
        if entry.data.get("_has_sd_card") != has_sd_card:
            hass.config_entries.async_update_entry(
                entry, data={**entry.data, "_has_sd_card": has_sd_card}
            )

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
        "has_event_capture": has_event_capture,
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
        # HA does not unload config entries on shutdown, so without this the subscription
        # is abandoned on the camera on every restart (held up to an hour). Registered
        # before start so a stop during startup is not missed; the unsub is cleared once
        # it fires, as HA's own coordinator does, so a later unload doesn't remove it twice.
        stop_unsub: list = []

        async def _release_onvif(_event: Event) -> None:
            stop_unsub.clear()
            await onvif_events.async_stop()

        stop_unsub.append(hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, _release_onvif))
        entry.async_on_unload(lambda: stop_unsub and stop_unsub.pop()())
        await onvif_events.async_start()
        coordinator.stop_on_auth_failure.append(onvif_events.async_stop)
        if openapi_events:
            await openapi_events.async_start()
            coordinator.stop_on_auth_failure.append(openapi_events.async_stop)

    # Baseline is what the entities were built with. A card first seen later must still
    # trigger the False→True reload — SD entities are only created at setup.
    _prev_sd: dict[str, bool] = {"has_sd": has_sd_card}

    @callback
    def _on_coordinator_update() -> None:
        now_has_sd = coordinator.has_sd_card
        was_has_sd = _prev_sd["has_sd"]
        if now_has_sd is None or was_has_sd == now_has_sd:
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
            # @callback returning None: do not wrap in async_create_task.
            hass.config_entries.async_schedule_reload(entry.entry_id)

    entry_data["_sd_unsub"] = coordinator.async_add_listener(_on_coordinator_update)

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


async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Clear this camera's repair notices when it is deleted, so none are left behind."""
    from homeassistant.helpers import issue_registry as ir

    for issue in (REPAIRS_WRONG_CAMERA, REPAIRS_SD_CARD_MISSING, REPAIRS_FRIGATE_GONE):
        ir.async_delete_issue(hass, DOMAIN, f"{issue}_{entry.entry_id}")
