"""Tests for the MirAIe Local config flow (cloud login, options, reauth).

No network and no real socket: the MirAIe cloud calls are served by
``aioclient_mock`` and discovery/probing is monkeypatched, so these tests never
touch the physical AC.
"""

from __future__ import annotations

import pytest
from homeassistant import config_entries
from homeassistant.const import CONF_PASSWORD
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.miraie_local import config_flow as cf
from custom_components.miraie_local.const import (
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
)

DEVICE_ID = "8c1419857fd4"
AP_MAC = "b0:81:84:4e:a3:a1"
HOST = "10.10.1.125"
# Synthetic AES material - never the real udpControlKey/udpControlIV.
KEY_HEX = "ab" * 32
IV_HEX = "cd" * 16

HOME = {
    "homeId": "0f1e2d3c4b",
    "udpControlKey": KEY_HEX,
    "udpcontrolIV": IV_HEX,
    "multicastIP": DEFAULT_MULTICAST,
}
DEVICE = {"deviceId": DEVICE_ID, "macAddress": AP_MAC, "deviceName": "AC"}


@pytest.fixture
def cloud(aioclient_mock):
    """Serve the three real MirAIe cloud calls."""
    aioclient_mock.post(cf.AUTH_URL, json={"accessToken": "tok"})
    aioclient_mock.get(f"{cf.BASE_URL}/homeManagement/homes", json=[HOME])
    aioclient_mock.get(
        f"{cf.BASE_URL}/deviceManagement/devices/homeId/{HOME['homeId']}", json=[DEVICE]
    )
    return aioclient_mock


@pytest.fixture
def no_network(monkeypatch):
    """Stub discovery and probing so no UDP socket is opened."""

    async def _discover(ap_mac, device_id, **kwargs):
        return HOST

    async def _probe(hass, data, host):
        return None

    monkeypatch.setattr(cf, "async_discover", _discover)
    monkeypatch.setattr(cf, "_async_probe", _probe)


async def start_user_flow(hass):
    return await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )


async def test_user_flow_cloud(hass, enable_custom_integrations, cloud, no_network):
    """Menu -> cloud login -> discovery -> a config entry with the cloud data."""
    result = await start_user_flow(hass)
    assert result["type"] is FlowResultType.MENU

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "cloud"}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "cloud"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_MOBILE: "9876543210", CONF_PASSWORD: "secret"}
    )
    # The cloud answers, so we move on to discovering the AC on the LAN.
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "cloud_host"

    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == {
        CONF_DEVICE_ID: DEVICE_ID,
        CONF_KEY: KEY_HEX,
        CONF_IV: IV_HEX,
        CONF_MULTICAST_IP: DEFAULT_MULTICAST,
        CONF_MOBILE: "+919876543210",
        CONF_HOST: HOST,
    }
    # The AP MAC is a discovery input, not part of the entry.
    assert "ap_mac" not in result["data"]


async def test_cloud_login_uses_the_verified_endpoints(
    hass, enable_custom_integrations, cloud, no_network
):
    """The login is auth.miraie.in with the documented body, not app.miraie.in."""
    result = await start_user_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "cloud"}
    )
    await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_MOBILE: "9876543210", CONF_PASSWORD: "secret"}
    )

    login = cloud.mock_calls[0]
    assert str(login[1]) == cf.AUTH_URL
    # AiohttpClientMocker flattens the JSON body into the call tuple.
    assert login[2]["mobile"] == "+919876543210"
    assert login[2]["clientId"] == cf.CLIENT_ID
    assert login[2]["scope"] == cf.SCOPE


async def test_user_flow_bad_credentials(
    hass, enable_custom_integrations, aioclient_mock, no_network
):
    """A 401 shows an invalid_auth form error rather than raising."""
    aioclient_mock.post(cf.AUTH_URL, status=401)

    result = await start_user_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "cloud"}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_MOBILE: "9876543210", CONF_PASSWORD: "wrong"}
    )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_auth"}


async def test_no_devices_on_the_account(
    hass, enable_custom_integrations, aioclient_mock, no_network
):
    """An account with no AC reports no_devices instead of crashing."""
    aioclient_mock.post(cf.AUTH_URL, json={"accessToken": "tok"})
    aioclient_mock.get(f"{cf.BASE_URL}/homeManagement/homes", json=[])

    result = await start_user_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "cloud"}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_MOBILE: "9876543210", CONF_PASSWORD: "secret"}
    )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "no_devices"}


async def test_cannot_connect_when_the_ac_never_answers(
    hass, enable_custom_integrations, cloud, no_network, monkeypatch
):
    """Discovery finding nothing (or a silent AC) is cannot_connect, not a crash."""

    async def _discover(ap_mac, device_id, **kwargs):
        return None

    monkeypatch.setattr(cf, "async_discover", _discover)

    result = await start_user_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "cloud"}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_MOBILE: "9876543210", CONF_PASSWORD: "secret"}
    )
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "cannot_connect"}


async def test_cannot_connect_when_the_probe_is_silent(
    hass, enable_custom_integrations, cloud, no_network, monkeypatch
):
    """A discovered address that does not answer the probe is cannot_connect."""

    async def _probe(hass_, data, host):
        raise cf._CannotConnect

    monkeypatch.setattr(cf, "_async_probe", _probe)

    result = await start_user_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "cloud"}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_MOBILE: "9876543210", CONF_PASSWORD: "secret"}
    )
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "cannot_connect"}


async def test_manual_advanced_path_needs_no_network(hass, enable_custom_integrations, no_network):
    """The advanced path creates an entry with no cloud calls at all."""
    result = await start_user_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "manual"}
    )
    assert result["step_id"] == "manual"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_HOST: HOST,
            CONF_DEVICE_ID: DEVICE_ID,
            CONF_KEY: KEY_HEX,
            CONF_IV: IV_HEX,
        },
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_HOST] == HOST
    assert result["data"][CONF_DEVICE_ID] == DEVICE_ID
    assert result["data"][CONF_KEY] == KEY_HEX
    assert result["data"][CONF_IV] == IV_HEX
    assert result["data"][CONF_MULTICAST_IP] == DEFAULT_MULTICAST


async def test_manual_rejects_a_bad_key_length(hass, enable_custom_integrations, no_network):
    """A key that is not 32 bytes would silently pick AES-128, so refuse it."""
    result = await start_user_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "manual"}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_HOST: HOST,
            CONF_DEVICE_ID: DEVICE_ID,
            CONF_KEY: "ab" * 16,  # 16 bytes, not 32
            CONF_IV: IV_HEX,
        },
    )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_key"}


async def test_duplicate_device_aborts(hass, enable_custom_integrations, cloud, no_network):
    """The same AC cannot be added twice."""
    MockConfigEntry(
        domain=DOMAIN, data={CONF_DEVICE_ID: DEVICE_ID, CONF_HOST: HOST}
    ).add_to_hass(hass)

    result = await start_user_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "cloud"}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_MOBILE: "9876543210", CONF_PASSWORD: "secret"}
    )
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_reauth_updates_the_key_in_place(
    hass, enable_custom_integrations, aioclient_mock, no_network
):
    """ConfigEntryAuthFailed starts a reauth flow that re-runs the cloud login."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_DEVICE_ID: DEVICE_ID, CONF_HOST: HOST, CONF_KEY: "00" * 32, CONF_IV: IV_HEX},
    )
    entry.add_to_hass(hass)

    aioclient_mock.post(cf.AUTH_URL, json={"accessToken": "tok"})
    aioclient_mock.get(f"{cf.BASE_URL}/homeManagement/homes", json=[HOME])
    aioclient_mock.get(
        f"{cf.BASE_URL}/deviceManagement/devices/homeId/{HOME['homeId']}", json=[DEVICE]
    )

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": config_entries.SOURCE_REAUTH, "entry_id": entry.entry_id},
        data=entry.data,
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "reauth_confirm"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_MOBILE: "9876543210", CONF_PASSWORD: "secret"}
    )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert entry.data[CONF_KEY] == KEY_HEX  # the stale key was replaced


async def test_options_poll_interval(hass, enable_custom_integrations, no_network):
    """The options flow owns the nudge interval, defaulting to 60 s."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_DEVICE_ID: DEVICE_ID, CONF_HOST: HOST},
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "init"

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_POLL_INTERVAL: 30}
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == {CONF_POLL_INTERVAL: 30}


async def test_options_default_is_the_silence_nudge(hass, enable_custom_integrations, no_network):
    """The default interval is 60 s of silence, not a heartbeat."""
    assert DEFAULT_POLL_INTERVAL == 60
    assert cf.MIN_POLL_INTERVAL == 15

    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_DEVICE_ID: DEVICE_ID, CONF_HOST: HOST},
        options={CONF_POLL_INTERVAL: 30},
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    schema = result["data_schema"].schema
    default = next(
        key.default() for key in schema if key.schema == CONF_POLL_INTERVAL
    )
    assert default == 30


async def test_options_reject_a_too_short_interval(hass, enable_custom_integrations, no_network):
    """Below the 15 s floor voluptuous rejects the value."""
    entry = MockConfigEntry(
        domain=DOMAIN, data={CONF_DEVICE_ID: DEVICE_ID, CONF_HOST: HOST}
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    with pytest.raises(Exception):  # noqa: B017 - voluptuous Invalid
        await hass.config_entries.options.async_configure(
            result["flow_id"], {CONF_POLL_INTERVAL: 5}
        )


async def test_e164_normalisation():
    """The login endpoint only answers for a full number."""
    assert cf._e164("9876543210") == "+919876543210"
    assert cf._e164("+919876543210") == "+919876543210"
    assert cf._e164("919876543210") == "+919876543210"
    assert cf._e164(" 76839 14466 ") == "+919876543210"


async def test_hex_validation():
    assert cf._is_hex(KEY_HEX, 32)
    assert cf._is_hex(IV_HEX, 16)
    assert not cf._is_hex(KEY_HEX, 16)
    assert not cf._is_hex("zz" * 32, 32)
