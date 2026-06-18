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


def _options_schema(current: dict) -> vol.Schema:
    return vol.Schema({
        vol.Required(
            CONF_FEATURE_CAMERA_STREAM,
            default=current.get(CONF_FEATURE_CAMERA_STREAM, DEFAULT_FEATURE_CAMERA_STREAM),
        ): bool,
        vol.Required(
            CONF_FEATURE_DETECTION_EVENTS,
            default=current.get(CONF_FEATURE_DETECTION_EVENTS, DEFAULT_FEATURE_DETECTION_EVENTS),
        ): bool,
        vol.Required(
            CONF_FEATURE_IMAGE_CONTROLS,
            default=current.get(CONF_FEATURE_IMAGE_CONTROLS, DEFAULT_FEATURE_IMAGE_CONTROLS),
        ): bool,
    })


class VIGIOptionsFlow(config_entries.OptionsFlow):
    """Options flow — feature group toggles for a configured camera."""

    async def async_step_init(self, user_input=None):
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        frigate_note = _detect_frigate_camera(self.hass, self.config_entry.data[CONF_HOST])
        description = (
            "Configure which feature groups are active for this camera."
        )
        if frigate_note:
            description += (
                "\n\nFrigate is detected with a camera at this IP address. "
                "Consider disabling Camera Stream and Detection Events to avoid "
                "duplicate entities — Frigate handles those surfaces."
            )

        return self.async_show_form(
            step_id="init",
            data_schema=_options_schema(dict(self.config_entry.options)),
            description_placeholders={"note": description},
        )


def _detect_frigate_camera(hass, ip: str) -> bool:
    """Return True if Frigate has a camera configured at *ip*."""
    try:
        from homeassistant.helpers import entity_registry as er
        registry = er.async_get(hass)
        for entry in registry.entities.values():
            if entry.platform == "frigate" and ip in (entry.config_entry_id or ""):
                return True
        # Also check via config entries directly
        for ce in hass.config_entries.async_entries("frigate"):
            cameras = ce.data.get("cameras", {})
            for cam_cfg in cameras.values():
                host = (cam_cfg.get("ffmpeg", {}).get("inputs") or [{}])[0].get("path", "")
                if ip in host:
                    return True
    except Exception:  # noqa: BLE001
        pass
    return False


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
