"""The deep module: the only place in the integration that touches a UDP socket.

Everything the rest of the integration needs to know about the MirAIe local control
channel sits behind this small interface::

    transport = MirAIeLocalTransport(loop, host, device_id, key, iv, on_status=cb)
    await transport.async_start()          # bind :5002, join multicast, receive
    await transport.async_send(command("temp", 25.0))
    status = await transport.async_query()  # no-op {} -> the device re-broadcasts
    await transport.async_stop()

It hides framing (:mod:`protocol`), AES (:mod:`crypto`), discovery, multicast
membership, the receive loop and JSON decoding. Callers never see a byte.

How the channel actually behaves (verified against the physical unit):

* The AC broadcasts its status from UDP ``:5001`` to ``255.255.255.255:5002`` with
  direction byte ``0x02`` after every accepted app->device frame and on every state
  change. Sending an empty ``{}`` is therefore the local state query.
* Our own frames are sent unicast to ``<host>:5001`` with direction byte ``0x01``.
  A frame with ``0x02`` is silently rejected by the device, so this module always
  builds app->device frames through :func:`protocol.build_frame`'s default direction.
* The device only speaks on change, so a caller that wants a refresh must send the
  no-op query rather than wait for an unsolicited broadcast.

Every control body must also carry a client envelope, ``{"sid": <counter>,
"cnt": "ios"}``, or the device SILENTLY IGNORES the frame: a frame with neither
field, or with only one of them, still elicits a status broadcast (so it looks like
it was received) but is never applied. This is stamped by the transport, which owns
one counter per connection; the entity never sees it.

Why the socket is built by hand: broadcast datagrams are delivered to *every* socket
bound to the port (verified on Linux), which is what lets this integration coexist
with the ``miraie-test`` debug scripts. However, two sockets only co-bind when their
options agree - ``SO_REUSEADDR`` plus ``SO_REUSEPORT`` on our side binds alongside
either style, whereas mixing the two fails with ``EADDRINUSE``.
``loop.create_datagram_endpoint`` refuses both options at once, so the socket is
created here and handed over via ``sock=``.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import socket
from collections.abc import Callable
from typing import Any, cast

from .const import BROADCAST_PORT, DEFAULT_MULTICAST, DEFAULT_PORT
from .protocol import (
    DIR_DEV_TO_APP,
    build_disc_req,
    build_frame,
    parse_frame,
)

_LOGGER = logging.getLogger(__name__)

BROADCAST_ADDRESS = "255.255.255.255"
DEFAULT_TIMEOUT = 5.0

# Every app->device control body must carry this client envelope or the device
# silently ignores the command (verified against the physical unit: neither field, or
# either field alone, is ignored - only both together are applied).
CLIENT_TYPE = "ios"

# The device's discovery reply is intermittent, so the request is repeated this
# often until the deadline rather than sent once.
_DISCOVERY_RETRY = 0.5

# The 12 ASCII hex deviceId characters inside the 18-byte frame header.
_DEVICE_ID_SLICE = slice(3, 15)


def _configure_socket(sock: socket.socket, bind_port: int) -> socket.socket:
    """Set the reuse/broadcast options, bind, and go non-blocking.

    Both reuse options are set so the bind succeeds whether or not a debug script
    is already listening on the port (see the module docstring).
    """
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    with contextlib.suppress(OSError):
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    sock.bind(("0.0.0.0", bind_port))
    sock.setblocking(False)
    return sock


def _make_socket(bind_port: int) -> socket.socket:
    """Create a non-blocking UDP socket able to broadcast and share the port."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        _configure_socket(sock, bind_port)
    except OSError:
        sock.close()
        raise
    return sock


class _ReceiveProtocol(asyncio.DatagramProtocol):
    """One endpoint's protocol, tagged with the generation that made it.

    ``async_stop`` closes the endpoint, but asyncio delivers ``connection_lost``
    later. If the same instance were started again in between, that stale callback
    would clear the live transport, so each endpoint carries its generation and only
    the matching one is allowed to clear it.
    """

    def __init__(self, owner: MirAIeLocalTransport, generation: int) -> None:
        self._owner = owner
        self._generation = generation

    def connection_made(self, transport: asyncio.BaseTransport) -> None:
        self._owner._connection_made(self._generation, cast("asyncio.DatagramTransport", transport))

    def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
        self._owner.datagram_received(data, addr)

    def error_received(self, exc: Exception) -> None:
        self._owner._error_received(exc)

    def connection_lost(self, exc: Exception | None) -> None:
        self._owner._connection_lost(self._generation)


class MirAIeLocalTransport:
    """UDP transport for a single MirAIe AC.

    ``datagram_received`` is public so tests can drive it without a real socket.
    """

    def __init__(
        self,
        loop: asyncio.AbstractEventLoop,
        host: str,
        device_id: str,
        key: bytes,
        iv: bytes,
        *,
        on_status: Callable[[dict[str, Any]], None],
        multicast: str = DEFAULT_MULTICAST,
        port: int = DEFAULT_PORT,
        broadcast_port: int = BROADCAST_PORT,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> None:
        """Store the per-device settings; no I/O happens until ``async_start``."""
        self._loop = loop
        self._host = host
        self._device_id = device_id
        self._key = key
        self._iv = iv
        self._on_status = on_status
        self._multicast = multicast
        self._port = port
        self._broadcast_port = broadcast_port
        self._timeout = timeout
        self._udp_transport: asyncio.DatagramTransport | None = None
        self._waiters: list[asyncio.Future[dict[str, Any]]] = []
        self._last_status: dict[str, Any] | None = None
        self._sid = 0
        self._generation = 0

    # ---- protocol callbacks -------------------------------------------

    def _connection_made(self, generation: int, transport: asyncio.DatagramTransport) -> None:
        """The endpoint for ``generation`` exists; join the multicast group."""
        if generation != self._generation:
            transport.close()  # a stale endpoint we already replaced
            return
        self._udp_transport = transport
        self._join_multicast()

    def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
        """Decode a datagram and, if it is this device's status, publish it."""
        status = self._decode(data, addr)
        if status is None:
            return
        self._last_status = status
        for waiter in self._waiters:
            if not waiter.done():
                waiter.set_result(status)
        self._on_status(status)

    def _error_received(self, exc: Exception) -> None:
        """A UDP error (e.g. ICMP port unreachable) is not fatal; log it."""
        _LOGGER.debug("UDP error for %s: %s", self._host, exc)

    def _connection_lost(self, generation: int) -> None:
        """Only the live endpoint may clear the transport (see _ReceiveProtocol)."""
        if generation == self._generation:
            self._udp_transport = None

    # ---- public interface ---------------------------------------------

    @property
    def last_status(self) -> dict[str, Any] | None:
        """The most recent status broadcast, or ``None`` before the first one."""
        return self._last_status

    async def async_start(self) -> None:
        """Bind the broadcast port, join the multicast group, start receiving."""
        if self._udp_transport is not None:
            return
        sock = _make_socket(self._broadcast_port)
        self._generation += 1
        generation = self._generation
        try:
            await self._loop.create_datagram_endpoint(
                lambda: _ReceiveProtocol(self, generation), sock=sock
            )
        except Exception:
            sock.close()
            raise
        _LOGGER.debug("listening for %s status on UDP :%s", self._device_id, self._broadcast_port)

    async def async_stop(self) -> None:
        """Stop receiving. Safe to call when never started or already stopped."""
        transport, self._udp_transport = self._udp_transport, None
        if transport is not None:
            transport.close()
        for waiter in self._waiters:
            if not waiter.done():
                waiter.cancel()
        self._waiters.clear()

    async def async_send(self, payload: dict[str, Any]) -> None:
        """Frame and send one control body to the AC.

        ``payload`` holds the command fields, e.g. from :func:`protocol.command`.
        The client envelope (``sid``, ``cnt``) is stamped on here because the
        device ignores a control frame without it; see the module docstring. The
        frame goes out as app->device (direction byte ``0x01``) unicast to
        ``<host>:5001``, which is what the device accepts.
        """
        if self._udp_transport is None:
            raise RuntimeError("transport is not started; call async_start() first")
        self._sid += 1
        body = json.dumps({**payload, "sid": str(self._sid), "cnt": CLIENT_TYPE})
        frame = build_frame(self._key, self._iv, self._device_id, body)
        self._udp_transport.sendto(frame, (self._host, self._port))

    async def async_query(self) -> dict[str, Any]:
        """Send a no-op ``{}`` and return the status broadcast it triggers.

        The device only broadcasts on change, so this is the local state query: it
        never carries a real command, and deliberately goes out as a bare ``{}``
        (not via :meth:`async_send`) because that is the exact body verified to
        elicit a broadcast from the physical unit. Raises ``TimeoutError`` if the
        device does not answer, which the coordinator treats as unavailable.
        """
        waiter: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
        self._waiters.append(waiter)
        try:
            if self._udp_transport is None:
                raise RuntimeError("transport is not started; call async_start() first")
            frame = build_frame(self._key, self._iv, self._device_id, "{}")
            self._udp_transport.sendto(frame, (self._host, self._port))
            async with asyncio.timeout(self._timeout):
                return await waiter
        finally:
            with contextlib.suppress(ValueError):
                self._waiters.remove(waiter)

    # ---- internals ---------------------------------------------------

    def _join_multicast(self) -> None:
        """Join the cloud-supplied multicast group as insurance (decision D6)."""
        transport = self._udp_transport
        if not self._multicast or transport is None:
            return
        sock = transport.get_extra_info("socket")
        if sock is None:
            return
        membership = socket.inet_aton(self._multicast) + socket.inet_aton("0.0.0.0")
        try:
            sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, membership)
        except OSError as err:
            # The device was only ever observed broadcasting, so this is optional.
            _LOGGER.debug("could not join multicast %s: %s", self._multicast, err)

    def _decode(self, data: bytes, addr: tuple[str, int]) -> dict[str, Any] | None:
        """Return the status body in ``data``, or ``None`` if it is not ours."""
        try:
            direction, plaintext = parse_frame(self._key, self._iv, data)
        except ValueError as err:
            _LOGGER.debug("ignoring malformed frame from %s: %s", addr, err)
            return None
        if direction != DIR_DEV_TO_APP:
            # Our own app->device frame, echoed back by the broadcast address.
            _LOGGER.debug("ignoring app->device frame from %s", addr)
            return None
        frame_device = data[_DEVICE_ID_SLICE].decode("ascii", "replace")
        if frame_device.lower() != self._device_id.lower():
            # A second AC on the LAN broadcasts to the same port.
            _LOGGER.debug("ignoring status from %s for device %s", addr, frame_device)
            return None
        try:
            status = json.loads(plaintext.rstrip(b"\x00"))
        except (UnicodeDecodeError, ValueError) as err:
            _LOGGER.debug("ignoring undecodable status from %s: %s", addr, err)
            return None
        if not isinstance(status, dict):
            _LOGGER.debug("ignoring non-object status from %s", addr)
            return None
        return status


async def async_discover(
    ap_mac: str,
    device_id: str,
    *,
    loop: asyncio.AbstractEventLoop | None = None,
    timeout: float = DEFAULT_TIMEOUT,
    broadcast_port: int = BROADCAST_PORT,
    port: int = DEFAULT_PORT,
) -> str | None:
    """Broadcast ``DISC_REQ_<AP MAC>`` and return the replying AC's IP address.

    Used by the config flow to find the AC on the LAN once the cloud has supplied
    the deviceId. Returns ``None`` if no matching device answers. The request must be
    zero-padded to 32 bytes and must be sent from the same port that listens, which
    is why this binds ``broadcast_port`` before sending.
    """
    request = build_disc_req(ap_mac)
    loop = loop or asyncio.get_running_loop()
    found: asyncio.Future[str] = asyncio.get_running_loop().create_future()
    # The verified-working probe sends to the broadcast address on BOTH ports; the
    # device's own listener is on 5001 but the 5002 form is what answers reliably.
    targets = ((BROADCAST_ADDRESS, port), (BROADCAST_ADDRESS, broadcast_port))

    class _DiscoveryProtocol(asyncio.DatagramProtocol):
        def connection_made(self, transport: asyncio.DatagramTransport) -> None:
            self.transport = transport
            self.send_request()

        def send_request(self) -> None:
            for target in targets:
                with contextlib.suppress(OSError):
                    self.transport.sendto(request, target)

        def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
            if found.done() or not _is_discovery_reply(data, addr):
                return
            found.set_result(addr[0])

        def error_received(self, exc: Exception) -> None:
            _LOGGER.debug("discovery error: %s", exc)

    def _is_discovery_reply(data: bytes, addr: tuple[str, int]) -> bool:
        """Is ``data`` a device->app frame from the device we are looking for?

        The body is deliberately not decrypted: the reply we want and a status
        broadcast both come from the same device at the same address, so the
        direction byte plus the deviceId already identify it, and requiring a
        DISC_RSP body would mean this helper could not run without the key/IV.
        """
        if len(data) < 18 or data[15] != DIR_DEV_TO_APP:
            return False
        frame_device = data[_DEVICE_ID_SLICE].decode("ascii", "replace")
        return frame_device.lower() == device_id.lower()

    _LOGGER.debug("discovering %s via %s:%s", device_id, BROADCAST_ADDRESS, port)

    sock = _make_socket(broadcast_port)
    try:
        transport, protocol = await loop.create_datagram_endpoint(_DiscoveryProtocol, sock=sock)
    except Exception:
        sock.close()
        raise
    try:
        deadline = asyncio.get_running_loop().time() + timeout
        while True:
            done, _ = await asyncio.wait({found}, timeout=_DISCOVERY_RETRY)
            if done:
                return found.result()
            if asyncio.get_running_loop().time() >= deadline:
                _LOGGER.debug("no discovery reply from %s", device_id)
                return None
            protocol.send_request()
    finally:
        transport.close()
