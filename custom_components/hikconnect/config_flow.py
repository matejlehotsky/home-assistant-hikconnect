import logging
import urllib.parse

import voluptuous as vol
from hikconnect.api import HikConnect
from hikconnect.exceptions import LoginError
from homeassistant import config_entries, core

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

_schema = {
    vol.Required("username"): str,
    vol.Required("password"): str,
    vol.Required("base_url", default=HikConnect.BASE_URL): str,
    vol.Optional("local_ip", default=""): str,
    vol.Optional("local_password", default=""): str,
}
DATA_SCHEMA = vol.Schema(_schema)


class InvalidBaseURL(ValueError):
    """Used to drive ConfigFlow from validate_input()."""


async def validate_input(hass: core.HomeAssistant, data: dict):
    """
    Validate the user input by logging into Hik-Connect.

    BEWARE: It mutates `data` to normalize `base_url`.

    Data has the keys from DATA_SCHEMA with values provided by the user.
    """
    url = urllib.parse.urlparse(data["base_url"])
    if url.path or url.params or url.query or url.fragment:
        raise InvalidBaseURL()
    if url.scheme not in ("http", "https"):
        raise InvalidBaseURL()
    data["base_url"] = f"{url.scheme}://{url.netloc}"  # normalize URL

    async with HikConnect() as api:
        api.BASE_URL = data["base_url"]
        await api.login(data["username"], data["password"])


class ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):

    VERSION = 2
    CONNECTION_CLASS = config_entries.CONN_CLASS_CLOUD_POLL

    @staticmethod
    def async_get_options_flow(config_entry):
        """Get the options flow for this handler."""
        return OptionsFlowHandler(config_entry)

    async def async_step_user(self, user_input=None):
        """Handle the initial step of config flow initiated by user manually."""
        errors = {}
        if user_input is not None:
            try:
                await validate_input(self.hass, user_input)
                unique_id = user_input["username"]
                _LOGGER.info("Adding Hik-Connect config entry with unique_id=%s", unique_id)
                await self.async_set_unique_id(unique_id)
                self._abort_if_unique_id_configured()
                return self.async_create_entry(title=unique_id, data=user_input)
            except LoginError as e:
                # to show hikconnect library exception in logs
                _LOGGER.exception("Hik-Connect login failed")
                errors["base"] = "login_failed"
            except InvalidBaseURL:
                errors["base_url"] = "invalid_base_url"
            except Exception:  # NOQA
                _LOGGER.exception("Unexpected exception")
                errors["base"] = "unknown"

        return self.async_show_form(step_id="user", data_schema=DATA_SCHEMA, errors=errors)


class OptionsFlowHandler(config_entries.OptionsFlow):
    """Handle options flow for Hik-Connect."""

    def __init__(self, config_entry):
        """Initialize options flow."""
        self._config_entry = config_entry

    async def async_step_init(self, user_input=None):
        """Manage the options."""
        if user_input is not None:
            # Update config entry data with new values
            new_data = {**self._config_entry.data, **user_input}
            self.hass.config_entries.async_update_entry(
                self._config_entry, data=new_data
            )
            return self.async_create_entry(title="", data=user_input)

        # Build schema with current values as defaults
        options_schema = vol.Schema({
            vol.Optional(
                "local_ip",
                default=self._config_entry.data.get("local_ip", ""),
            ): str,
            vol.Optional(
                "local_password",
                default=self._config_entry.data.get("local_password", ""),
            ): str,
        })

        return self.async_show_form(step_id="init", data_schema=options_schema)
