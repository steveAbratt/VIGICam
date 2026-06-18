"""Config flow for VIGI & InSight cameras."""
from __future__ import annotations

import logging
import urllib.parse

import aiohttp
import voluptuous as vol
from homeassistant import config_entries
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_USERNAME
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import VIGIAuthError, VIGICamera, VIGIError
from .const import (
    CONF_FEATURE_CAMERA_STREAM,
    CONF_FEATURE_DETECTION_EVENTS,
    CONF_FEATURE_IMAGE_CONTROLS,
    CONF_VERIFY_SSL,
    DEFAULT_FEATURE_CAMERA_STREAM,
    DEFAULT_FEATURE_DETECTION_EVENTS,
    DEFAULT_FEATURE_IMAGE_CONTROLS,
    DEFAULT_USERNAME,
    DOMAIN,
)
from .frigate import detect_frigate_camera

_LOGGER = logging.getLogger(__name__)

STEP_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_HOST): str,
        vol.Optional(CONF_USERNAME, default=DEFAULT_USERNAME): str,
        vol.Required(CONF_PASSWORD): str,
    }
)


async def _test_credentials(hass, ip: str, username: str, password: str) -> tuple[dict, bool]:
    """Validate credentials. Returns (device_info, verify_ssl).

    Tries SSL verification first (for cameras with proper certs), then falls
    back to unverified. Most VIGI/InSight cameras use a TP-Link internal CA
    (CN=TPRI-CA) that is not in the system trust store.
    """
    # Attempt 1: verified (proper SSL cert) using HA's shared session
    session = async_get_clientsession(hass)
    cam = VIGICamera(ip, username, password, session=session)
    try:
        await cam.authenticate()
        return await cam.get_device_info(), True
    except (aiohttp.ClientSSLError, aiohttp.ClientConnectorCertificateError):
        pass  # Untrusted/self-signed cert — retry with SSL verification disabled
    except (VIGIAuthError, VIGIError):
        raise  # Real error, surface it

    # Attempt 2: no SSL verification. Use session=None so VIGICamera creates its
    # own session with an explicit no-verify SSL context. async_create_clientsession
    # with verify_ssl=False is not reliable across all HA/aiohttp versions.
    cam2 = VIGICamera(ip, username, password, session=None)
    try:
        await cam2.authenticate()
        return await cam2.get_device_info(), False
    finally:
        await cam2.close()


def _suggested_name(info: dict, fallback: str) -> str:
    """Return the camera's configured name, URL-decoded, falling back to IP."""
    for key in ("dev_name", "alias"):
        raw = info.get(key)
        if raw:
            decoded = urllib.parse.unquote(raw).strip()
            if decoded:
                return decoded
    return fallback


def _options_schema(current: dict, suggest_frigate_defaults: bool = False) -> vol.Schema:
    # When Frigate is newly detected and the user has never saved options,
    # pre-set Camera Stream and Detection Events to off to avoid duplicates.
    if suggest_frigate_defaults:
        stream_default = False
        detection_default = False
    else:
        stream_default = current.get(CONF_FEATURE_CAMERA_STREAM, DEFAULT_FEATURE_CAMERA_STREAM)
        detection_default = current.get(CONF_FEATURE_DETECTION_EVENTS, DEFAULT_FEATURE_DETECTION_EVENTS)

    return vol.Schema({
        vol.Required(CONF_FEATURE_CAMERA_STREAM, default=stream_default): bool,
        vol.Required(CONF_FEATURE_DETECTION_EVENTS, default=detection_default): bool,
        vol.Required(
            CONF_FEATURE_IMAGE_CONTROLS,
            default=current.get(CONF_FEATURE_IMAGE_CONTROLS, DEFAULT_FEATURE_IMAGE_CONTROLS),
        ): bool,
    })


def _capabilities_text(entry_data: dict, has_frigate: bool) -> str:
    """Return a markdown bullet list of detected camera capabilities."""
    def _row(detected: bool, label: str) -> str:
        return f"- {label}: {'✓' if detected else '✗'}"

    return "\n".join([
        _row(entry_data.get("has_sd_card", False),      "SD card"),
        _row(entry_data.get("has_ptz", False),           "PTZ controls"),
        _row(entry_data.get("has_openapi", False),       "OpenAPI (extended sensors)"),
        _row(entry_data.get("has_smart_frames", False),  "Smart Frame capture"),
        _row(has_frigate,                                "Frigate integration"),
    ])


class VIGIOptionsFlow(config_entries.OptionsFlow):
    """Options flow — feature group toggles for a configured camera."""

    async def async_step_init(self, user_input=None):
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        ip = self.config_entry.data[CONF_HOST]
        has_frigate = detect_frigate_camera(self.hass, ip) is not None

        entry_data: dict = self.hass.data.get(DOMAIN, {}).get(self.config_entry.entry_id, {})
        capabilities = (
            _capabilities_text(entry_data, has_frigate)
            if entry_data
            else "_(Camera not currently loaded — reload to refresh)_"
        )

        frigate_note = (
            "Frigate is detected at this IP address. "
            "Camera Stream and Detection Events have been pre-set to **off** to avoid "
            "duplicate entities — adjust below if needed."
            if (has_frigate and not self.config_entry.options)
            else (
                "Frigate is detected at this IP address. "
                "Consider disabling Camera Stream and Detection Events if Frigate handles those."
                if has_frigate
                else ""
            )
        )

        # Only suggest Frigate defaults the very first time Configure is opened
        # (options dict empty) and Frigate is present.
        suggest_frigate = has_frigate and not self.config_entry.options

        return self.async_show_form(
            step_id="init",
            data_schema=_options_schema(dict(self.config_entry.options), suggest_frigate),
            description_placeholders={
                "capabilities": capabilities,
                "note": frigate_note,
            },
        )


class VIGIConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Config flow to add a VIGI or InSight camera."""

    VERSION = 1

    @staticmethod
    def async_get_options_flow(config_entry):
        return VIGIOptionsFlow()

    def __init__(self) -> None:
        self._host: str = ""
        self._username: str = ""
        self._password: str = ""
        self._verify_ssl: bool = False
        self._device_info: dict = {}
        self._suggested_name: str = ""

    async def async_step_user(self, user_input=None):
        errors: dict[str, str] = {}

        if user_input is not None:
            ip = user_input[CONF_HOST].strip()
            username = user_input.get(CONF_USERNAME, DEFAULT_USERNAME)
            password = user_input[CONF_PASSWORD]
            try:
                info, verify_ssl = await _test_credentials(
                    self.hass, ip, username, password
                )
            except VIGIAuthError as exc:
                _LOGGER.debug("Authentication failed for %s: %s", ip, exc)
                errors["base"] = "invalid_auth"
            except (VIGIError, aiohttp.ClientError, Exception) as exc:
                _LOGGER.debug("Connection failed for %s: %s", ip, exc, exc_info=True)
                errors["base"] = "cannot_connect"
            else:
                unique_id = (info.get("mac") or ip).replace(":", "").lower()
                await self.async_set_unique_id(unique_id)
                self._abort_if_unique_id_configured()

                self._host = ip
                self._username = username
                self._password = password
                self._verify_ssl = verify_ssl
                self._device_info = info
                self._suggested_name = _suggested_name(info, ip)
                return await self.async_step_name()

        return self.async_show_form(
            step_id="user",
            data_schema=STEP_SCHEMA,
            errors=errors,
        )

    async def async_step_name(self, user_input=None):
        """Confirm or edit the name for this camera in Home Assistant."""
        if user_input is not None:
            title = user_input.get("name", "").strip() or self._suggested_name
            return self.async_create_entry(
                title=title,
                data={
                    CONF_HOST: self._host,
                    CONF_USERNAME: self._username,
                    CONF_PASSWORD: self._password,
                    CONF_VERIFY_SSL: self._verify_ssl,
                },
            )

        return self.async_show_form(
            step_id="name",
            data_schema=vol.Schema({
                vol.Required("name", default=self._suggested_name): str,
            }),
            description_placeholders={"suggested": self._suggested_name},
        )
