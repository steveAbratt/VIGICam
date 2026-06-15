"""Config flow for VIGI & InSight cameras."""
from __future__ import annotations

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_USERNAME

from .api import VIGIAuthError, VIGICamera, VIGIError
from .const import DEFAULT_USERNAME, DOMAIN

STEP_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_HOST): str,
        vol.Optional(CONF_USERNAME, default=DEFAULT_USERNAME): str,
        vol.Required(CONF_PASSWORD): str,
    }
)


async def _test_credentials(ip: str, username: str, password: str) -> dict:
    """Attempt auth and return device_info. Raises VIGIAuthError or VIGIError."""
    cam = VIGICamera(ip.strip(), username, password)
    try:
        await cam.authenticate()
        return await cam.get_device_info()
    finally:
        await cam.close()


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
                info = await _test_credentials(ip, username, password)
            except VIGIAuthError:
                errors["base"] = "invalid_auth"
            except VIGIError:
                errors["base"] = "cannot_connect"
            else:
                # Use MAC as unique ID so we survive IP changes
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
                    },
                )

        return self.async_show_form(
            step_id="user",
            data_schema=STEP_SCHEMA,
            errors=errors,
        )
