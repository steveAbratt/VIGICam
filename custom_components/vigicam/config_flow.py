"""Config flow for VIGI & InSight cameras."""
from __future__ import annotations

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


class VIGIConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Config flow to add a VIGI or InSight camera."""

    VERSION = 1

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

                title = info.get("dev_name") or info.get("alias") or ip
                return self.async_create_entry(
                    title=title,
                    data={
                        CONF_HOST: ip,
                        CONF_USERNAME: username,
                        CONF_PASSWORD: password,
                        CONF_VERIFY_SSL: verify_ssl,
                    },
                )

        return self.async_show_form(
            step_id="user",
            data_schema=STEP_SCHEMA,
            errors=errors,
        )
