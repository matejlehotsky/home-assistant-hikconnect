"""Call status sensor for Hik-Connect devices."""
import asyncio
import logging
from datetime import timedelta

import aiohttp
from hikconnect.api import HikConnect
from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import (
    CoordinatorEntity,
    DataUpdateCoordinator,
)
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
        new_entities.append(CallStatusSensor(api, device_info, local_ip, local_password))
        new_entities.append(LocalIpSensor(coordinator, device_info["id"]))
        new_entities.append(WanIpSensor(coordinator, device_info["id"]))
        new_entities.append(WifiSignalSensor(coordinator, device_info["id"]))

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
            self._attr_native_value = res["status"]
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


class _DeviceFieldSensor(CoordinatorEntity, SensorEntity):
    """Base sensor backed by a single coordinator device field."""

    _field: str = ""
    _suffix: str = ""
    _icon: str = ""
    _name_suffix: str = ""

    # Diagnostic, off by default.
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False

    def __init__(self, coordinator: DataUpdateCoordinator, device_id: str):
        super().__init__(coordinator)
        self._device_id = device_id
        self._attr_unique_id = "-".join((DOMAIN, device_id, self._suffix))

    @property
    def _device_info_data(self) -> dict:
        for device in self.coordinator.data or []:
            if device.get("id") == self._device_id:
                return device
        return {}

    @property
    def name(self):
        name = self._device_info_data.get("name") or self._device_id
        return f"{name} {self._name_suffix}"

    @property
    def native_value(self):
        return self._device_info_data.get(self._field)

    @property
    def available(self) -> bool:
        return super().available and self.native_value is not None

    @property
    def device_info(self):
        return {
            "identifiers": {(DOMAIN, self._device_id)},
        }

    @property
    def icon(self):
        return self._icon


class LocalIpSensor(_DeviceFieldSensor):
    """LAN IP address reported by Hik-Connect cloud."""

    _field = "local_ip"
    _suffix = "local-ip"
    _icon = "mdi:ip-network"
    _name_suffix = "local IP"


class WanIpSensor(_DeviceFieldSensor):
    """Public/WAN IP address reported by Hik-Connect cloud."""

    _field = "wan_ip"
    _suffix = "wan-ip"
    _icon = "mdi:wan"
    _name_suffix = "WAN IP"


class WifiSignalSensor(_DeviceFieldSensor):
    """WiFi signal strength reported by the device (0-100)."""

    _field = "wifi_signal"
    _suffix = "wifi-signal"
    _icon = "mdi:wifi-strength-3"
    _name_suffix = "WiFi signal"

    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_state_class = SensorStateClass.MEASUREMENT
