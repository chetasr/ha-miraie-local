"""Diagnostics support for MirAIe Local.

The config entry holds the AES key/IV that grant control of the AC to anyone on the
LAN, so they are redacted from the dump (as is the account mobile number).
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.const import CONF_PASSWORD
from homeassistant.core import HomeAssistant

from . import MirAIeLocalConfigEntry
from .const import CONF_IV, CONF_KEY, CONF_MOBILE

TO_REDACT = {CONF_KEY, CONF_IV, CONF_PASSWORD, CONF_MOBILE}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: MirAIeLocalConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry, with the secrets redacted."""
    coordinator = entry.runtime_data
    return {
        "entry": {
            "data": async_redact_data(dict(entry.data), TO_REDACT),
            "options": async_redact_data(dict(entry.options), TO_REDACT),
        },
        "status": coordinator.status,
        "last_update_success": coordinator.last_update_success,
    }
