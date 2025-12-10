"""Camera platform for Hik-Connect devices."""
import logging

import httpx
from homeassistant.components.camera import Camera
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Set up camera entities."""
    data = hass.data[DOMAIN]
    coordinator = data["coordinator"]
    local_ip = data.get("local_ip", "")
    local_password = data.get("local_password", "")

    # Only add camera if local IP is configured
    if not local_ip or not local_password:
        _LOGGER.debug("Local IP/password not configured, skipping camera setup")
        return

    new_entities = []
    for device_info in coordinator.data:
        new_entities.append(HikConnectCamera(device_info, local_ip, local_password))

    if new_entities:
        async_add_entities(new_entities, update_before_add=True)


class HikConnectCamera(Camera):
    """Represents a Hik-Connect doorbell camera."""

    _attr_has_entity_name = True
    _attr_translation_key = "camera"

    def __init__(self, device_info: dict, local_ip: str, local_password: str):
        """Initialize the camera."""
        super().__init__()
        self._device_info = device_info
        self._local_ip = local_ip
        self._local_password = local_password
        self._username = "admin"
        self._attr_is_streaming = False
        self._attr_is_recording = False

    @property
    def unique_id(self):
        """Return unique ID."""
        return "-".join((DOMAIN, self._device_info["id"], "camera"))

    @property
    def device_info(self):
        """Return device info."""
        return {
            "identifiers": {(DOMAIN, self._device_info["id"])},
        }

    async def async_camera_image(
        self, width: int | None = None, height: int | None = None
    ) -> bytes | None:
        """Return a still image from the camera."""
        url = f"http://{self._local_ip}/ISAPI/Streaming/channels/101/picture"

        try:
            async with httpx.AsyncClient(
                timeout=10.0,
                verify=False,
                auth=httpx.DigestAuth(self._username, self._local_password),
            ) as client:
                response = await client.get(url)

                if response.status_code == 200:
                    return response.content
                else:
                    _LOGGER.warning(
                        "Failed to get camera image: HTTP %d", response.status_code
                    )
                    return None

        except httpx.TimeoutException:
            _LOGGER.warning("Timeout getting camera image from %s", self._local_ip)
            return None
        except Exception as e:
            _LOGGER.warning("Error getting camera image: %s", e)
            return None

    @property
    def brand(self) -> str:
        """Return the camera brand."""
        return "Hikvision"

    @property
    def model(self) -> str:
        """Return the camera model."""
        return self._device_info.get("model", "Video Intercom")
