"""Config flow for VIGI & InSight cameras."""
from __future__ import annotations

import logging
import urllib.parse

import aiohttp
import voluptuous as vol
from homeassistant import config_entries
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_HOST, CONF_PASSWORD, CONF_USERNAME
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import VIGIAuthError, VIGICamera, VIGIError
from .const import (
    CAMERA_STREAM_SUFFIXES,
    CONF_FEATURE_CAMERA_STREAM,
    CONF_FEATURE_DETECTION_EVENTS,
    CONF_FEATURE_IMAGE_CONTROLS,
    CONF_STREAM_USE_MAIN,
    CONF_VERIFY_SSL,
    DEFAULT_FEATURE_CAMERA_STREAM,
    DEFAULT_FEATURE_DETECTION_EVENTS,
    DEFAULT_FEATURE_IMAGE_CONTROLS,
    DEFAULT_STREAM_USE_MAIN,
    DEFAULT_USERNAME,
    DETECTION_EVENT_SUFFIXES,
    DOMAIN,
    IMAGE_CONTROL_SUFFIXES,
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
        vol.Required(
            CONF_STREAM_USE_MAIN,
            default=current.get(CONF_STREAM_USE_MAIN, DEFAULT_STREAM_USE_MAIN),
        ): bool,
        vol.Required(CONF_FEATURE_DETECTION_EVENTS, default=detection_default): bool,
        vol.Required(
            CONF_FEATURE_IMAGE_CONTROLS,
            default=current.get(CONF_FEATURE_IMAGE_CONTROLS, DEFAULT_FEATURE_IMAGE_CONTROLS),
        ): bool,
    })


def _enabled_entities_being_removed(
    hass, entry: config_entries.ConfigEntry, new_options: dict
) -> list[str]:
    """Return entity IDs that are currently enabled and will be removed by new_options.

    Only looks at feature groups that are being newly turned off — groups that were
    already off are ignored.
    """
    old = entry.options
    newly_off: set[str] = set()

    if (old.get(CONF_FEATURE_CAMERA_STREAM, DEFAULT_FEATURE_CAMERA_STREAM)
            and not new_options.get(CONF_FEATURE_CAMERA_STREAM, DEFAULT_FEATURE_CAMERA_STREAM)):
        newly_off |= CAMERA_STREAM_SUFFIXES

    if (old.get(CONF_FEATURE_DETECTION_EVENTS, DEFAULT_FEATURE_DETECTION_EVENTS)
            and not new_options.get(CONF_FEATURE_DETECTION_EVENTS, DEFAULT_FEATURE_DETECTION_EVENTS)):
        newly_off |= DETECTION_EVENT_SUFFIXES

    if (old.get(CONF_FEATURE_IMAGE_CONTROLS, DEFAULT_FEATURE_IMAGE_CONTROLS)
            and not new_options.get(CONF_FEATURE_IMAGE_CONTROLS, DEFAULT_FEATURE_IMAGE_CONTROLS)):
        newly_off |= IMAGE_CONTROL_SUFFIXES

    if not newly_off:
        return []

    registry = er.async_get(hass)
    affected = []
    for reg_entry in er.async_entries_for_config_entry(registry, entry.entry_id):
        parts = reg_entry.unique_id.split("_", 1)
        suffix = parts[1] if len(parts) == 2 else reg_entry.unique_id
        if suffix in newly_off and reg_entry.disabled_by is None:
            affected.append(reg_entry.entity_id)

    return sorted(affected)


def _capabilities_text(entry_data: dict, has_frigate: bool) -> str:
    """Return a compact single-line capability summary for use in data_description."""
    def _item(detected: bool, label: str) -> str:
        return f"{label} {'✓' if detected else '✗'}"

    if not entry_data:
        return "_(camera not loaded — reload to refresh)_"

    return "  ·  ".join([
        _item(entry_data.get("has_sd_card", False),     "SD card"),
        _item(entry_data.get("has_ptz", False),          "PTZ"),
        _item(entry_data.get("has_openapi", False),      "OpenAPI"),
        _item(entry_data.get("has_event_capture", False), "Event Image Capture"),
        _item(has_frigate,                               "Frigate"),
    ])


class VIGIOptionsFlow(config_entries.OptionsFlow):
    """Options flow — feature group toggles for a configured camera."""

    def __init__(self) -> None:
        self._pending_options: dict = {}
        self._entities_to_warn: list[str] = []

    async def async_step_init(self, user_input=None):
        if user_input is not None:
            to_warn = _enabled_entities_being_removed(
                self.hass, self.config_entry, user_input
            )
            if to_warn:
                self._pending_options = user_input
                self._entities_to_warn = to_warn
                return await self.async_step_confirm()
            return self.async_create_entry(title="", data=user_input)

        ip = self.config_entry.data[CONF_HOST]
        has_frigate = detect_frigate_camera(self.hass, ip) is not None

        entry_data: dict = self.hass.data.get(DOMAIN, {}).get(self.config_entry.entry_id, {})
        capabilities = _capabilities_text(entry_data, has_frigate)

        # Prefix with a newline so the note reads as a separate paragraph in data_description.
        frigate_note = (
            "Frigate detected — Camera Stream and Detection Events pre-set to off. "
            "Adjust below if needed.\n\n"
            if (has_frigate and not self.config_entry.options)
            else (
                "Frigate detected at this IP — consider disabling Camera Stream and "
                "Detection Events.\n\n"
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

    async def async_step_confirm(self, user_input=None):
        if user_input is not None:
            return self.async_create_entry(title="", data=self._pending_options)

        entity_list = "\n".join(f"- `{eid}`" for eid in self._entities_to_warn)

        return self.async_show_form(
            step_id="confirm",
            data_schema=vol.Schema({}),
            description_placeholders={"entity_list": entity_list},
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
            except Exception as exc:  # noqa: BLE001
                # Deliberately broad: the config flow must never crash on an unexpected
                # exception type, and the user has nothing to act on beyond "cannot connect".
                # The traceback is logged because it is what a bug report needs.
                _LOGGER.debug("Connection failed for %s: %s", ip, exc, exc_info=True)
                errors["base"] = "cannot_connect"
            else:
                unique_id = (info.get("mac") or ip).replace(":", "").lower()
                await self.async_set_unique_id(unique_id)
                # updates= lets a camera whose DHCP address changed be fixed by adding it
                # again at the new IP: the existing entry is updated and reloaded in place,
                # keeping its entities, instead of aborting with "already configured".
                self._abort_if_unique_id_configured(updates={CONF_HOST: ip})

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

    async def async_step_reauth(self, entry_data):
        """Start re-authentication when the camera rejects the stored credentials.

        Home Assistant calls this after ConfigEntryAuthFailed. Without it the user who
        changes a camera password gets an UnknownStep traceback and no way to enter the
        new one short of deleting and re-adding the camera — which would assign new
        entity unique IDs and orphan their automations.
        """
        self._host = entry_data[CONF_HOST]
        self._username = entry_data.get(CONF_USERNAME, DEFAULT_USERNAME)
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input=None):
        """Ask for the new password and update the existing entry in place."""
        errors: dict[str, str] = {}
        # Not _get_reauth_entry(): that helper only exists on newer Home Assistant, and
        # this lookup has been stable for far longer.
        entry = self.hass.config_entries.async_get_entry(self.context["entry_id"])
        if entry is None:
            # The reauth card waits for the user, so the entry can disappear underneath
            # it — deleted and re-added in another tab, for instance. Abort rather than
            # raising AttributeError inside the flow.
            return self.async_abort(reason="reauth_entry_gone")

        if user_input is not None:
            username = user_input.get(CONF_USERNAME, self._username).strip()
            password = user_input[CONF_PASSWORD]
            try:
                info, verify_ssl = await _test_credentials(
                    self.hass, self._host, username, password
                )
            except VIGIAuthError as exc:
                _LOGGER.debug("Reauth failed for %s: %s", self._host, exc)
                errors["base"] = "invalid_auth"
            except Exception as exc:  # noqa: BLE001
                _LOGGER.debug(
                    "Reauth connection failed for %s: %s", self._host, exc, exc_info=True
                )
                errors["base"] = "cannot_connect"
            else:
                # Verify we are still talking to the SAME camera before binding
                # credentials to this entry. On a camera VLAN with DHCP, the address this
                # entry holds can be reassigned to a different camera; since most installs
                # use one password everywhere, reauth would then succeed against the wrong
                # device and silently repoint every entity under this device at it.
                found_id = (info.get("mac") or self._host).replace(":", "").lower()
                if entry.unique_id and found_id != entry.unique_id:
                    _LOGGER.warning(
                        "Reauth for %s reached a different camera (expected %s, found %s)",
                        self._host, entry.unique_id, found_id,
                    )
                    return self.async_abort(reason="wrong_camera")

                # Deliberately NOT touching unique_id. Entity unique IDs derive from it,
                # so changing it here would create a duplicate device and orphan every
                # automation the user has — the exact harm reauth exists to avoid.
                # Explicit merged data rather than data_updates=, which is only
                # supported on newer Home Assistant. CONF_HOST is carried over
                # unchanged — reauth replaces credentials, never the address.
                self.hass.config_entries.async_update_entry(
                    entry,
                    data={
                        **entry.data,
                        CONF_USERNAME: username,
                        CONF_PASSWORD: password,
                        CONF_VERIFY_SSL: verify_ssl,
                    },
                )
                # Reload explicitly when the entry is not loaded. The usual reauth case is a
                # password changed and HA restarted, so setup failed before reaching the line
                # that registers the update listener — relying on that listener (as an
                # unreleased b5 draft did) left the camera "failed" after the dialog said it
                # had reconnected. When the entry IS loaded the listener reloads it, so
                # scheduling here too would reload twice.
                if entry.state is not ConfigEntryState.LOADED:
                    self.hass.config_entries.async_schedule_reload(entry.entry_id)
                return self.async_abort(reason="reauth_successful")

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema({
                vol.Required(CONF_USERNAME, default=self._username): str,
                vol.Required(CONF_PASSWORD): str,
            }),
            errors=errors,
            description_placeholders={
                "host": self._host,
                "name": entry.title,
            },
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
