"""The MirAIe Local integration - local UDP control of a Panasonic MirAIe AC."""
from .const import DOMAIN

__all__ = ["DOMAIN"]

async def async_setup_entry(hass, entry):
    """Set up MirAIe Local from a config entry."""
    return True

async def async_unload_entry(hass, entry):
    """Unload a MirAIe Local config entry."""
    return True
