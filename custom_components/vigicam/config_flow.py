"""Config flow for VIGI & InSight cameras."""
from __future__ import annotations

import urllib.parse

import aiohttp
import voluptuous as vol
from homeassistant import config_entries
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_USERNAME
from homeassistant.helpers.aiohttp_client import async_create_clientsession, async_get_clientsession

from .api import VIGIAuthError, VIGICamera, VIGIError
from .const import CONF_VERIFY_SSL, DEFAULT_USERNAME, DOMAIN

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
    back to unverified (self-signed certs, which is most VIGI/InSight cameras).
    """
    # Attempt 1: verified (proper SSL cert)
    session = async_get_clientsession(hass)
    cam = VIGICamera(ip, username, password, session=session)
    try:
        await cam.authenticate()
        return await cam.get_device_info(), True
    except aiohttp.ClientSSLError:
        pass  # Self-signed cert — retry without verification
    except (VIGIAuthError, VIGIError):
        raise  # Real error, surface it

    # Attempt 2: unverified (self-signed cert)
    noverify_session = async_create_clientsession(hass, verify_ssl=False)
    cam = VIGICamera(ip, username, password, session=noverify_session)
    await cam.authenticate()
    return await cam.get_device_info(), False


def _suggested_name(info: dict, fallback: str) -> str:
    """Return the camera's configured name, URL-decoded, falling back to IP."""
    for key in ("dev_name", "alias"):
        raw = info.get(key)
        if raw:
            decoded = urllib.parse.unquote(raw).strip()
            if decoded:
                return decoded
    return fallback


class VIGIConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Config flow to add a VIGI or InSight camera."""

    VERSION = 1

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
            except VIGIAuthError:
                errors["base"] = "invalid_auth"
            except (VIGIError, aiohttp.ClientError, Exception):
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
