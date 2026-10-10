"""Tests for the diagnostics dump."""

from __future__ import annotations

from homeassistant.core import HomeAssistant

from custom_components.miraie_local.const import CONF_DEVICE_ID, CONF_HOST
from custom_components.miraie_local.diagnostics import (
    TO_REDACT,
    async_get_config_entry_diagnostics,
)


async def test_diagnostics_redact_the_secrets(hass: HomeAssistant, setup_entry):
    """The key/IV grant control of the AC, so they must never be dumped."""
    diagnostics = await async_get_config_entry_diagnostics(hass, setup_entry)

    assert diagnostics["entry"]["data"]["key"] == "**REDACTED**"
    assert diagnostics["entry"]["data"]["iv"] == "**REDACTED**"
    # Anything that is not a secret is still useful and must survive.
    assert diagnostics["entry"]["data"][CONF_DEVICE_ID] == "8c1419857fd4"
    assert diagnostics["entry"]["data"][CONF_HOST] == "10.10.1.125"


async def test_diagnostics_never_contain_the_secrets(hass: HomeAssistant, setup_entry):
    """Belt and braces: no secret value appears anywhere in the dump."""
    diagnostics = await async_get_config_entry_diagnostics(hass, setup_entry)
    dumped = repr(diagnostics)

    assert setup_entry.data["key"] not in dumped
    assert setup_entry.data["iv"] not in dumped
    assert {"key", "iv", "password", "mobile"} == TO_REDACT


async def test_diagnostics_include_the_status(hass, setup_entry):
    """The current status is the useful part of the dump."""
    setup_entry.runtime_data.handle_status({"ps": "on", "rmtmp": "26.5"})
    await hass.async_block_till_done()

    diagnostics = await async_get_config_entry_diagnostics(hass, setup_entry)

    assert diagnostics["status"]["ps"] == "on"
    assert diagnostics["status"]["rmtmp"] == "26.5"
    assert diagnostics["last_update_success"] is True
