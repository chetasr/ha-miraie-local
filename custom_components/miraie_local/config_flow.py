"""Config flow for MirAIe Local: MirAIe cloud login, options and reauth.

Setup is a cloud login (decision D3). The user enters their MirAIe mobile number
and password, and the flow pulls everything else from the cloud:

* ``POST https://auth.miraie.in/simplifi/v1/userManagement/login`` with
  ``{clientId, mobile, password, scope}`` returns the access token. The app-host
  path (``app.miraie.in/simplifi/v1/users/login``) answers 404, so auth.miraie.in
  is the only usable login host.
* ``GET .../homeManagement/homes`` returns the home, which carries the AES
  ``udpControlKey``/``udpcontrolIV`` and the ``multicastIP`` group.
* ``GET .../deviceManagement/devices/homeId/{homeId}`` returns the devices, which
  carry the ``deviceId`` and ``macAddress``. That ``macAddress`` is the AP MAC that
  ``DISC_REQ`` needs, not the STA/ARP MAC.

The AC's LAN address is then auto-discovered by broadcasting ``DISC_REQ`` (with a
manual override in the form), and a manual path is kept for offline testing.
"""

from __future__ import annotations

import logging
from typing import Any

import aiohttp
import voluptuous as vol
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import CONF_PASSWORD
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import (
    CONF_DEVICE_ID,
    CONF_HOST,
    CONF_IV,
    CONF_KEY,
    CONF_MOBILE,
    CONF_MULTICAST_IP,
    CONF_POLL_INTERVAL,
    DEFAULT_MULTICAST,
    DEFAULT_POLL_INTERVAL,
    DOMAIN,
    MIN_POLL_INTERVAL,
)
from .transport import MirAIeLocalTransport, async_discover

_LOGGER = logging.getLogger(__name__)

AUTH_URL = "https://auth.miraie.in/simplifi/v1/userManagement/login"
BASE_URL = "https://app.miraie.in/simplifi/v1"
CLIENT_ID = "PBcMcfG19njNCL8AOgvRzIC8AjQa"
SCOPE = "an_14214235325"
USER_AGENT = "okhttp/3.13.1"

# The cloud login only answers for a full E.164-style number.
_COUNTRY_CODE = "+91"

PROBE_TIMEOUT = 5.0

STEP_USER = "user"
STEP_CLOUD = "cloud"
STEP_MANUAL = "manual"
STEP_REAUTH_CONFIRM = "reauth_confirm"


class _CannotConnect(Exception):
    """The AC did not answer on the LAN."""


class _InvalidAuth(Exception):
    """The MirAIe cloud rejected the credentials."""


class _NoDevices(Exception):
    """The account has no MirAIe AC on it."""


def _e164(mobile: str) -> str:
    """Return the mobile number in the form the login endpoint expects."""
    digits = mobile.strip().replace(" ", "").replace("-", "")
    if digits.startswith("+"):
        return digits
    if digits.startswith("91") and len(digits) > 10:
        return f"+{digits}"
    return f"{_COUNTRY_CODE}{digits}"


async def _async_login(session: aiohttp.ClientSession, mobile: str, password: str) -> str:
    """Log in to MirAIe and return the access token."""
    async with session.post(
        AUTH_URL,
        json={
            "clientId": CLIENT_ID,
            "mobile": _e164(mobile),
            "password": password,
            "scope": SCOPE,
        },
        headers={"User-Agent": USER_AGENT},
    ) as resp:
        if resp.status in (401, 403):
            raise _InvalidAuth
        resp.raise_for_status()
        payload = await resp.json()
    token = payload.get("accessToken")
    if not token:
        raise _InvalidAuth
    return token


async def _async_first_home(session: aiohttp.ClientSession, token: str) -> dict[str, Any]:
    """Return the first home, which carries the AES key/IV and multicast group."""
    async with session.get(
        f"{BASE_URL}/homeManagement/homes",
        headers={"Authorization": f"Bearer {token}", "User-Agent": USER_AGENT},
    ) as resp:
        resp.raise_for_status()
        homes = await resp.json()
    if not homes:
        raise _NoDevices
    return homes[0]


async def _async_first_device(
    session: aiohttp.ClientSession, token: str, home_id: str
) -> dict[str, Any]:
    """Return the first AC on the home, which carries the deviceId and AP MAC."""
    async with session.get(
        f"{BASE_URL}/deviceManagement/devices/homeId/{home_id}",
        headers={"Authorization": f"Bearer {token}", "User-Agent": USER_AGENT},
    ) as resp:
        resp.raise_for_status()
        devices = await resp.json()
    if not devices:
        raise _NoDevices
    return devices[0]


async def _async_cloud_data(hass: HomeAssistant, mobile: str, password: str) -> dict[str, Any]:
    """Log in and return everything a config entry needs except the host."""
    session = async_get_clientsession(hass)
    try:
        token = await _async_login(session, mobile, password)
        home = await _async_first_home(session, token)
        device = await _async_first_device(session, token, home["homeId"])
    except aiohttp.ClientError as err:
        raise _CannotConnect from err
    data = {
        CONF_DEVICE_ID: device["deviceId"],
        CONF_KEY: home["udpControlKey"],
        CONF_IV: home["udpcontrolIV"],
        CONF_MULTICAST_IP: home.get("multicastIP") or DEFAULT_MULTICAST,
        CONF_MOBILE: _e164(mobile),
        "ap_mac": device.get("macAddress", ""),
    }
    # A malformed deviceId or key from the cloud would otherwise surface as an
    # unhandled ValueError from the probe ("unknown error" in the UI).
    if not _is_device_id(data[CONF_DEVICE_ID]) or not _is_hex(data[CONF_KEY], 32):
        raise _CannotConnect
    return data


async def _async_probe(hass: HomeAssistant, data: dict[str, Any], host: str) -> None:
    """Start a transport, ask once, stop. Raises _CannotConnect if silent.

    A malformed deviceId or key/IV also surfaces here (build_frame and
    bytes.fromhex raise ValueError), so those are mapped too rather than escaping
    as an unhandled exception and showing the user "unknown error".
    """
    try:
        transport = MirAIeLocalTransport(
            hass.loop,
            host,
            data[CONF_DEVICE_ID],
            bytes.fromhex(data[CONF_KEY]),
            bytes.fromhex(data[CONF_IV]),
            on_status=lambda _status: None,
            multicast=data.get(CONF_MULTICAST_IP, DEFAULT_MULTICAST),
            timeout=PROBE_TIMEOUT,
        )
        await transport.async_start()
    except (OSError, ValueError) as err:
        raise _CannotConnect from err
    try:
        await transport.async_query()
    except TimeoutError as err:
        raise _CannotConnect from err
    finally:
        await transport.async_stop()


class MirAIeLocalConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for MirAIe Local."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialise the flow state."""
        self._cloud: dict[str, Any] = {}
        self._mobile = ""

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Offer the normal cloud login, with a manual path for offline use."""
        return self.async_show_menu(step_id=STEP_USER, menu_options=["cloud", "manual"])

    async def async_step_cloud(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Ask for the MirAIe credentials and pull the rest from the cloud."""
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                cloud = await _async_cloud_data(
                    self.hass, user_input[CONF_MOBILE], user_input[CONF_PASSWORD]
                )
            except _InvalidAuth:
                errors["base"] = "invalid_auth"
            except _CannotConnect:
                errors["base"] = "cannot_connect"
            except _NoDevices:
                errors["base"] = "no_devices"
            else:
                self._cloud = cloud
                self._mobile = user_input[CONF_MOBILE]
                return await self.async_step_cloud_host()

        return self.async_show_form(
            step_id=STEP_CLOUD,
            data_schema=vol.Schema(
                {vol.Required(CONF_MOBILE): str, vol.Required(CONF_PASSWORD): str}
            ),
            errors=errors,
        )

    async def async_step_cloud_host(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Discover the AC on the LAN, allowing a manual address if that fails."""
        errors: dict[str, str] = {}
        if user_input is not None:
            host = user_input.get(CONF_HOST, "").strip()
            if not host:
                host = (
                    await async_discover(self._cloud["ap_mac"], self._cloud[CONF_DEVICE_ID]) or ""
                )
            if not host:
                errors["base"] = "cannot_connect"
            else:
                try:
                    await _async_probe(self.hass, self._cloud, host)
                except _CannotConnect, ValueError:
                    errors["base"] = "cannot_connect"
                else:
                    return self._async_create_entry(host)

        return self.async_show_form(
            step_id="cloud_host",
            data_schema=vol.Schema({vol.Optional(CONF_HOST, default=""): str}),
            errors=errors,
            description_placeholders={"device_id": self._cloud.get(CONF_DEVICE_ID, "")},
        )

    async def async_step_manual(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Advanced path: paste the host, deviceId, key and IV, with no network."""
        errors: dict[str, str] = {}
        if user_input is not None:
            data = {
                CONF_HOST: user_input[CONF_HOST].strip(),
                CONF_DEVICE_ID: user_input[CONF_DEVICE_ID].strip(),
                CONF_KEY: user_input[CONF_KEY].strip(),
                CONF_IV: user_input[CONF_IV].strip(),
                CONF_MULTICAST_IP: user_input.get(CONF_MULTICAST_IP) or DEFAULT_MULTICAST,
            }
            if not _is_device_id(data[CONF_DEVICE_ID]):
                errors["base"] = "invalid_device_id"
            elif not _is_hex(data[CONF_KEY], 32) or not _is_hex(data[CONF_IV], 16):
                errors["base"] = "invalid_key"
            else:
                try:
                    await _async_probe(self.hass, data, data[CONF_HOST])
                except _CannotConnect, ValueError:
                    errors["base"] = "cannot_connect"
                else:
                    return self._async_create_entry(data[CONF_HOST], data)

        return self.async_show_form(
            step_id=STEP_MANUAL,
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_HOST): str,
                    vol.Required(CONF_DEVICE_ID): str,
                    vol.Required(CONF_KEY): str,
                    vol.Required(CONF_IV): str,
                    vol.Optional(CONF_MULTICAST_IP, default=DEFAULT_MULTICAST): str,
                }
            ),
            errors=errors,
        )

    async def async_step_reauth(self, entry_data: dict[str, Any]) -> ConfigFlowResult:
        """Start reauthentication after the cloud rejected our token."""
        entry = self._get_reauth_entry()
        self._cloud = {CONF_DEVICE_ID: entry.data[CONF_DEVICE_ID]}
        self._mobile = entry.data.get(CONF_MOBILE, "")
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Re-run the cloud login and update the existing entry in place."""
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                cloud = await _async_cloud_data(
                    self.hass, user_input[CONF_MOBILE], user_input[CONF_PASSWORD]
                )
            except _InvalidAuth:
                errors["base"] = "invalid_auth"
            except _CannotConnect:
                errors["base"] = "cannot_connect"
            except _NoDevices:
                errors["base"] = "no_devices"
            else:
                return self.async_update_reload_and_abort(
                    self._get_reauth_entry(),
                    data_updates={
                        CONF_KEY: cloud[CONF_KEY],
                        CONF_IV: cloud[CONF_IV],
                        CONF_MULTICAST_IP: cloud[CONF_MULTICAST_IP],
                        CONF_MOBILE: cloud[CONF_MOBILE],
                    },
                )

        return self.async_show_form(
            step_id=STEP_REAUTH_CONFIRM,
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_MOBILE, default=self._mobile): str,
                    vol.Required(CONF_PASSWORD): str,
                }
            ),
            errors=errors,
        )

    def _async_create_entry(
        self, host: str, data: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Create the entry, refusing a device that is already configured."""
        entry_data = dict(data or self._cloud)
        entry_data.pop("ap_mac", None)
        entry_data[CONF_HOST] = host
        entry_data.setdefault(CONF_MULTICAST_IP, DEFAULT_MULTICAST)
        self._async_abort_entries_match({CONF_DEVICE_ID: entry_data[CONF_DEVICE_ID]})
        return self.async_create_entry(title=entry_data[CONF_DEVICE_ID], data=entry_data)

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        """Return the options flow that owns the poll interval."""
        return MirAIeLocalOptionsFlow()


def _is_hex(value: str, expected_bytes: int) -> bool:
    """Is ``value`` exactly ``expected_bytes`` of hex?"""
    try:
        return len(bytes.fromhex(value)) == expected_bytes
    except ValueError:
        return False


def _is_device_id(value: str) -> bool:
    """Is ``value`` the 12 hex CHARACTERS the frame header carries (6 bytes)?"""
    return len(value) == 12 and _is_hex(value, 6)


class MirAIeLocalOptionsFlow(OptionsFlow):
    """Options: the poll interval, whose minimum keeps the nudge cheap."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Set the poll interval (default 60 s, minimum 15 s)."""
        if user_input is not None:
            return self.async_create_entry(data=user_input)

        current = self.config_entry.options.get(CONF_POLL_INTERVAL, DEFAULT_POLL_INTERVAL)
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Optional(CONF_POLL_INTERVAL, default=current): vol.All(
                        vol.Coerce(int), vol.Range(min=MIN_POLL_INTERVAL)
                    )
                }
            ),
        )
