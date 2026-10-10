"""Push coordinator: status broadcasts in, a nudge only after silence.

The AC broadcasts its state on every change, so the coordinator is push-driven: the
transport calls :meth:`MirAIeLocalCoordinator.handle_status` for each broadcast and
that publishes the new state to the entities.

The poll is NOT a heartbeat. ``DataUpdateCoordinator`` resets the time until its
next poll every time ``async_set_updated_data`` is called, so each broadcast pushes
the nudge further away; the nudge only fires after a full interval with NO broadcast.
That covers a cold start after an HA restart, a broadcast missed on a lossy LAN, and
liveness. The nudge body is the no-op ``{}``, which makes the device re-broadcast
without being a command (decision D5).

Availability is deliberately generous: the AC drops off the network for about a minute
around a power-off, so a single unanswered nudge is not treated as a failure.
"""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import (
    DEFAULT_POLL_INTERVAL,
    DOMAIN,
    MISSED_NUDGES_BEFORE_UNAVAILABLE,
)
from .transport import MirAIeLocalTransport

_LOGGER = logging.getLogger(__name__)


class MirAIeLocalCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Hold the latest AC status and nudge the device only after silence."""

    def __init__(
        self,
        hass: HomeAssistant,
        *,
        update_interval_seconds: int = DEFAULT_POLL_INTERVAL,
    ) -> None:
        """Set up the coordinator; the transport is attached by setup."""
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=update_interval_seconds),
        )
        self.transport: MirAIeLocalTransport | None = None
        self._misses = 0

    @property
    def status(self) -> dict[str, Any]:
        """The latest known status, or an empty mapping before the first one."""
        return self.data or {}

    def handle_status(self, status: dict[str, Any]) -> None:
        """Publish a broadcast from the AC (the transport's callback).

        ``async_set_updated_data`` both notifies the entities and resets the time
        until the next poll, which is what makes the nudge fire only after silence.
        A broadcast also clears the miss counter.
        """
        self._misses = 0
        self.async_set_updated_data(status)

    async def _async_update_data(self) -> dict[str, Any]:
        """Nudge the AC after silence and return the status it broadcasts back.

        The body is a no-op ``{}`` (never a command). Raises ``UpdateFailed`` when
        the device stays silent: always before the first status is known, so setup
        fails and Home Assistant retries it, and after
        ``MISSED_NUDGES_BEFORE_UNAVAILABLE`` consecutive misses otherwise,
        which marks the entities unavailable.
        """
        transport = self.transport
        if transport is None:
            raise UpdateFailed("transport is not running")
        try:
            status = await transport.async_query()
        except TimeoutError as err:
            self._misses += 1
            if self.data is None or self._misses >= MISSED_NUDGES_BEFORE_UNAVAILABLE:
                raise UpdateFailed(
                    f"no status broadcast from the AC ({self._misses} missed nudges)"
                ) from err
            # Tolerate a brief dropout (the AC vanishes for ~1 min on power-off)
            # and keep showing the last known state.
            _LOGGER.debug("nudge %s unanswered, keeping the last known state", self._misses)
            return self.data
        self._misses = 0
        return status
