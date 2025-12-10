"""Custom API helper to bypass hikconnect library limitations."""
import json
import logging
import time
from typing import Any

import aiohttp

_LOGGER = logging.getLogger(__name__)

CALL_STATUS_MAPPING = {
    1: "idle",
    2: "ringing",
    3: "call in progress",
}

CALL_INFO_MAPPING = {
    "buildingNo": "building_number",
    "floorNo": "floor_number",
    "zoneNo": "zone_number",
    "unitNo": "unit_number",
    "devNo": "device_number",
    "devType": "device_type",
    "lockNum": "lock_number",
}

# Alternative endpoints to try
CALL_STATUS_ENDPOINTS = [
    "/v3/devconfig/v1/call/{serial}/status",  # Original endpoint
    "/v3/userdevices/v1/devices/{serial}/call/status",  # Alternative 1
    "/api/v3/devconfig/v1/call/{serial}/status",  # With api prefix
]


class HikConnectApiError(Exception):
    """Base exception for API errors."""
    def __init__(self, code: int, message: str):
        self.code = code
        self.message = message
        super().__init__(f"API error {code}: {message}")


class DeviceOfflineError(HikConnectApiError):
    """Device is offline (error 2003)."""
    pass


class DeviceNetworkError(HikConnectApiError):
    """Device network abnormal (error 2009)."""
    pass


def _get_headers(session_id: str, include_extra: bool = False) -> dict[str, str]:
    """Get request headers."""
    headers = {
        "clientType": "55",
        "lang": "en-US",
        "featureCode": "deadbeef",
        "sessionId": session_id,
        "Content-Type": "application/json",
        "Accept": "application/json",
    }

    if include_extra:
        # Additional headers that mobile app might use
        headers.update({
            "User-Agent": "Hik-Connect/5.0.0 (Android)",
            "X-Timestamp": str(int(time.time() * 1000)),
        })

    return headers


async def _try_endpoint(
    session: aiohttp.ClientSession,
    base_url: str,
    endpoint: str,
    session_id: str,
    device_serial: str,
    use_extra_headers: bool = False,
) -> dict[str, Any] | None:
    """Try a single endpoint and return result or None on failure."""
    url = base_url + endpoint.format(serial=device_serial)
    headers = _get_headers(session_id, use_extra_headers)

    _LOGGER.debug("Trying endpoint: %s", url)

    try:
        async with session.get(url, headers=headers) as response:
            res_json = await response.json()

        _LOGGER.debug("Response from %s: %s", endpoint, res_json)

        meta = res_json.get("meta", {})
        code = meta.get("code", 0)

        if code == 200:
            return res_json

        _LOGGER.debug("Endpoint %s returned code %d", endpoint, code)
        return None

    except Exception as e:
        _LOGGER.debug("Endpoint %s failed: %s", endpoint, e)
        return None


def _parse_call_status_response(res_json: dict[str, Any]) -> dict[str, Any]:
    """Parse successful call status response."""
    data_str = res_json.get("data")
    if not data_str:
        raise HikConnectApiError(0, "No data in response")

    data = json.loads(data_str) if isinstance(data_str, str) else data_str

    # Map call status
    call_status_code = data.get("callStatus", 0)
    status = CALL_STATUS_MAPPING.get(call_status_code, "unknown")
    if status == "unknown" and call_status_code != 0:
        _LOGGER.warning("Unknown call status code: %s", call_status_code)

    # Map caller info
    info = {}
    caller_info = data.get("callerInfo", {})
    for in_key, out_key in CALL_INFO_MAPPING.items():
        if in_key in caller_info:
            info[out_key] = caller_info[in_key]

    return {
        "status": status,
        "info": info,
    }


async def get_call_status_custom(
    session: aiohttp.ClientSession,
    base_url: str,
    session_id: str,
    device_serial: str,
) -> dict[str, Any]:
    """
    Get call status using direct API call with better error handling.

    Tries multiple endpoints and header combinations.
    """
    # First try: original endpoint with standard headers
    url = f"{base_url}/v3/devconfig/v1/call/{device_serial}/status"
    headers = _get_headers(session_id, include_extra=False)

    _LOGGER.debug("Fetching call status from: %s", url)

    async with session.get(url, headers=headers) as response:
        res_json = await response.json()

    _LOGGER.debug("Call status response: %s", res_json)

    meta = res_json.get("meta", {})
    code = meta.get("code", 0)
    message = meta.get("message", "Unknown error")

    # Handle known error codes
    if code == 2003:
        raise DeviceOfflineError(code, message)

    if code == 2009:
        # Try with extra headers before giving up
        _LOGGER.debug("Got error 2009, trying with additional headers...")
        headers_extra = _get_headers(session_id, include_extra=True)

        async with session.get(url, headers=headers_extra) as response:
            res_json = await response.json()

        meta = res_json.get("meta", {})
        code = meta.get("code", 0)
        message = meta.get("message", "Unknown error")

        if code == 2009:
            raise DeviceNetworkError(code, message)

    if code != 200:
        raise HikConnectApiError(code, message)

    return _parse_call_status_response(res_json)


async def get_call_status_isapi(
    local_ip: str,
    username: str = "admin",
    password: str = "",
) -> dict[str, Any]:
    """
    Get call status via local ISAPI (Hikvision device API).

    This bypasses the cloud entirely and talks directly to the device.
    Requires the device to be on the same network.
    """
    import httpx

    # ISAPI endpoint for intercom call status
    url = f"http://{local_ip}/ISAPI/VideoIntercom/callStatus"

    auth = httpx.DigestAuth(username, password) if password else None

    try:
        async with httpx.AsyncClient(timeout=3.0, verify=False) as client:
            response = await client.get(url, auth=auth)
            _LOGGER.debug("ISAPI response status: %d", response.status_code)

            if response.status_code == 200:
                text = response.text
                _LOGGER.debug("ISAPI call status response: %s", text)

                # Try to parse as JSON first (newer firmware)
                try:
                    data = json.loads(text)
                    # Handle {"CallStatus": {"status": "idle"}} format
                    if "CallStatus" in data:
                        status = data["CallStatus"].get("status", "unknown").lower()
                        return {"status": status, "info": {}}
                    # Handle {"status": "idle"} format
                    elif "status" in data:
                        return {"status": data["status"].lower(), "info": {}}
                except json.JSONDecodeError:
                    pass

                # Fallback to text parsing for XML responses
                text_lower = text.lower()
                if "idle" in text_lower:
                    return {"status": "idle", "info": {}}
                elif "ringing" in text_lower:
                    return {"status": "ringing", "info": {}}
                elif "ongoing" in text_lower or "in progress" in text_lower:
                    return {"status": "call in progress", "info": {}}

                return {"status": "unknown", "info": {}}

            elif response.status_code == 404:
                _LOGGER.debug("ISAPI endpoint not found at %s", url)
                return None

            _LOGGER.debug("ISAPI returned status %d", response.status_code)
            return None

    except Exception as e:
        _LOGGER.debug("ISAPI call failed: %s: %s", type(e).__name__, e)
        return None


async def get_device_connection_info(
    session: aiohttp.ClientSession,
    base_url: str,
    session_id: str,
) -> dict[str, dict[str, Any]]:
    """
    Fetch device connection info including local IPs.

    Returns dict mapping device serial -> connection info.
    """
    headers = _get_headers(session_id)
    # Use the same endpoint as hikconnect library
    url = f"{base_url}/v3/userdevices/v1/resources/pagelist"

    try:
        async with session.get(url, headers=headers) as response:
            res_json = await response.json()

        _LOGGER.debug("Device list response keys: %s", list(res_json.keys()))

        connection_infos = res_json.get("connectionInfos", {})
        if not connection_infos:
            _LOGGER.debug("No connectionInfos in response, full response: %s", res_json.get("meta"))

        return connection_infos

    except Exception as e:
        _LOGGER.debug("Failed to get connection info: %s", e)
        return {}


async def get_call_status_with_fallback(
    api,  # HikConnect instance
    device_serial: str,
    local_ip: str = "",
    local_password: str = "",
) -> dict[str, Any]:
    """
    Get call status, preferring local ISAPI over cloud API.

    Local ISAPI is faster and more reliable than cloud API which has issues.
    """
    # Try local ISAPI first if configured (faster and more reliable)
    if local_ip and local_password:
        result = await get_call_status_isapi(local_ip, "admin", local_password)
        if result:
            return result
        _LOGGER.debug("Local ISAPI failed, trying cloud API...")
    else:
        _LOGGER.debug("Local IP/password not configured, using cloud API only")

    # Fall back to cloud API
    base_url = api.BASE_URL
    session_id = api.client._default_headers.get("sessionId")

    if not session_id:
        raise HikConnectApiError(0, "No session ID available - not logged in")

    async with aiohttp.ClientSession() as session:
        return await get_call_status_custom(
            session=session,
            base_url=base_url,
            session_id=session_id,
            device_serial=device_serial,
        )
