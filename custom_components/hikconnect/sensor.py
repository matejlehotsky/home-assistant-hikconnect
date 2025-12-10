"""Call status sensor for Hik-Connect devices."""
import asyncio
import logging
from datetime import timedelta

import aiohttp
from hikconnect.api import HikConnect
from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .api_helper import (
    get_call_status_with_fallback,
    HikConnectApiError,
    DeviceOfflineError,
    DeviceNetworkError,
)

_LOGGER = logging.getLogger(__name__)

SCAN_INTERVAL = timedelta(seconds=5)  # Increased to allow fallback attempts
SCAN_INTERVAL_TIMEOUT = timedelta(seconds=4.5)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up call status sensors."""
    data = hass.data[DOMAIN]
    api, coordinator = data["api"], data["coordinator"]
    local_ip = data.get("local_ip", "")
    local_password = data.get("local_password", "")

    new_entities = []
    for device_info in coordinator.data:
        new_entities.append(CallStatusSensor(api, device_info, local_ip, local_password, hass))

    if new_entities:
        async_add_entities(new_entities, update_before_add=True)


class CallStatusSensor(SensorEntity):
    """Represents a call status of an indoor station."""

    _attr_device_class = SensorDeviceClass.ENUM
    _attr_has_entity_name = True
    _attr_options = ["idle", "ringing", "ongoing", "call in progress"]
    _attr_translation_key = "call_status"

    def __init__(self, api: HikConnect, device_info: dict, local_ip: str = "", local_password: str = "", hass: HomeAssistant = None):
        """Initialize the sensor."""
        super().__init__()
        self._api = api
        self._device_info = device_info
        self._local_ip = local_ip
        self._local_password = local_password
        self._hass = hass
        self._attr_available = False
        self._attr_native_value = None
        self._attr_extra_state_attributes = {}
        self._last_error: str | None = None
        self._previous_status: str | None = None

    async def async_update(self) -> None:
        """Update the call status."""
        try:
            res = await asyncio.wait_for(
                get_call_status_with_fallback(
                    self._api,
                    self._device_info["serial"],
                    local_ip=self._local_ip,
                    local_password=self._local_password,
                ),
                SCAN_INTERVAL_TIMEOUT.seconds,
            )
            new_status = res["status"]

            # Fire event when status changes
            if self._hass and new_status != self._previous_status:
                event_data = {
                    "device_id": self._device_info["id"],
                    "device_serial": self._device_info["serial"],
                    "device_name": self._device_info["name"],
                    "status": new_status,
                    "previous_status": self._previous_status,
                }
                self._hass.bus.async_fire(f"{DOMAIN}_call_status_changed", event_data)

                # Fire specific event for ringing (easier for automations)
                if new_status == "ringing":
                    self._hass.bus.async_fire(f"{DOMAIN}_doorbell_ringing", event_data)

                self._previous_status = new_status

            self._attr_native_value = new_status
            self._attr_extra_state_attributes = res.get("info", {})
            self._attr_available = True
            self._last_error = None

        except DeviceNetworkError as e:
            # Error 2009 - device network abnormal
            # This is a known issue since HA 2025.12
            # Only log once to avoid spam
            if self._last_error != "2009":
                _LOGGER.warning(
                    "Call status unavailable for %s: %s (this is a known API issue)",
                    self._device_info["serial"],
                    e.message,
                )
                self._last_error = "2009"
            self._attr_available = False

        except DeviceOfflineError as e:
            if self._last_error != str(e):
                _LOGGER.debug("Device %s is offline", self._device_info["serial"])
                self._last_error = str(e)
            self._attr_available = False

        except HikConnectApiError as e:
            if self._last_error != str(e):
                _LOGGER.warning(
                    "API error for %s: code=%d, message=%s",
                    self._device_info["serial"],
                    e.code,
                    e.message,
                )
                self._last_error = str(e)
            self._attr_available = False

        except (asyncio.TimeoutError, TimeoutError) as e:
            _LOGGER.debug("Timeout getting call status for %s", self._device_info["serial"])
            self._attr_available = False

        except aiohttp.ClientError as e:
            _LOGGER.debug("Network error getting call status: %s", e)
            self._attr_available = False

        except Exception as e:
            _LOGGER.warning(
                "Unexpected error updating call status for %s: %s: %s",
                self._device_info["serial"],
                type(e).__name__,
                e,
            )
            self._attr_available = False

    @property
    def unique_id(self):
        """Return unique ID."""
        return "-".join((DOMAIN, self._device_info["id"], "call-status"))

    @property
    def device_info(self):
        """Return device info."""
        return {
            "identifiers": {(DOMAIN, self._device_info["id"])},
        }

    @property
    def icon(self):
        """Return icon based on call status."""
        if self.native_value == "idle":
            return "mdi:phone-hangup"
        elif self.native_value == "ringing":
            return "mdi:phone-ring"
        elif self.native_value in ("call in progress", "ongoing"):
            return "mdi:phone-in-talk"
        else:
            return "mdi:phone-alert"
