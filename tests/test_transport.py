"""Tests for the MirAIe UDP transport (the only module that touches a socket).

The key/IV used here are SYNTHETIC (``bytes(range(32))`` / ``bytes(range(16))``)
and must never be replaced with the real ``udpControlKey``/``udpControlIV``.

No real socket is opened: a fake event loop and a fake datagram transport stand in
for the kernel, so these tests exercise the framing, filtering and JSON decoding
without needing UDP port 5002 to be free.
"""

import asyncio
import json
import socket

import pytest

from custom_components.miraie_local import transport as transport_module
from custom_components.miraie_local.const import (
    BROADCAST_PORT,
    DEFAULT_MULTICAST,
    DEFAULT_PORT,
)
from custom_components.miraie_local.protocol import (
    DIR_APP_TO_DEVICE,
    DIR_DEV_TO_APP,
    build_frame,
    command,
)
from custom_components.miraie_local.transport import (
    BROADCAST_ADDRESS,
    MirAIeLocalTransport,
    async_discover,
)

KEY = bytes(range(32))  # synthetic
IV = bytes(range(16))  # synthetic
DEV = "8c1419857fd4"  # deviceId is cleartext on the LAN, so not a secret
OTHER_DEV = "aabbccddeeff"
HOST = "10.10.1.125"
AC = (HOST, DEFAULT_PORT)

# A status body shaped like the real broadcast (same keys the cloud status topic uses).
STATUS = {
    "ts": "1791640218",
    "rssi": -50,
    "ps": "on",
    "actmp": "24.0",
    "rmtmp": "28.5",
    "acmd": "cool",
    "acfs": "auto",
    "lcmd": "actmp",
    "sid": "3",
    "cnt": "ios",
    "V": "3.02",
}


class FakeSocket:
    """Records the socket options the transport sets."""

    def __init__(self):
        self.options: list[tuple[int, int, object]] = []
        self.bound: tuple[str, int] | None = None
        self.blocking: bool | None = None
        self.closed = False

    def setsockopt(self, level, optname, value):
        self.options.append((level, optname, value))

    def bind(self, addr):
        self.bound = addr

    def setblocking(self, flag):
        self.blocking = flag

    def close(self):
        self.closed = True


class FakeDatagramTransport:
    """Records ``sendto`` calls and reports a socket for multicast joins."""

    def __init__(self, sock):
        self.sock = sock
        self.sent: list[tuple[bytes, tuple[str, int]]] = []
        self.closed = False

    def sendto(self, data, addr):
        self.sent.append((data, addr))

    def get_extra_info(self, name, default=None):
        return self.sock if name == "socket" else default

    def close(self):
        self.closed = True


class FakeLoop:
    """Minimal stand-in for ``loop.create_datagram_endpoint``."""

    def __init__(self):
        self.sock = FakeSocket()
        self.transport = FakeDatagramTransport(self.sock)
        self.protocols = []
        self.socks = []

    async def create_datagram_endpoint(self, factory, **kwargs):
        protocol = factory()
        self.protocols.append(protocol)
        self.socks.append(kwargs.get("sock"))
        # Real asyncio calls connection_made itself once the endpoint exists.
        protocol.connection_made(self.transport)
        return self.transport, protocol


@pytest.fixture
def fake_loop(monkeypatch):
    """A fake loop whose ``_make_socket`` never opens a real socket."""
    loop = FakeLoop()
    monkeypatch.setattr(transport_module, "_make_socket", lambda port: loop.sock)
    return loop


def make_transport(fake_loop, **kwargs):
    statuses: list[dict] = []
    transport = MirAIeLocalTransport(
        fake_loop,
        HOST,
        DEV,
        KEY,
        IV,
        on_status=statuses.append,
        timeout=0.05,
        **kwargs,
    )
    return transport, statuses


def status_frame(device_id=DEV, status=None, key=KEY, iv=IV):
    """Build a device->app frame the way the AC does."""
    return build_frame(
        key,
        iv,
        device_id,
        json.dumps(STATUS if status is None else status),
        direction=DIR_DEV_TO_APP,
    )


# --------------------------------------------------------------------------
# sending
# --------------------------------------------------------------------------


async def test_async_send_stamps_the_client_envelope(fake_loop):
    """Without sid+cnt the device SILENTLY IGNORES the frame (real-device finding).

    Verified on the physical unit: a control frame with neither field, or with only
    one of them, still elicits a status broadcast (so it looks received) but is never
    applied. Only sid+cnt together are applied.
    """
    transport, _ = make_transport(fake_loop)
    await transport.async_start()
    await transport.async_send(command("on"))

    expected = build_frame(
        KEY,
        IV,
        DEV,
        json.dumps({"ki": 1, "ps": "on", "sid": "1", "cnt": "ios"}),
        direction=DIR_APP_TO_DEVICE,
    )
    assert fake_loop.transport.sent == [(expected, AC)]
    assert expected[15] == DIR_APP_TO_DEVICE == 0x01
    assert len(expected) % 16 == 2  # 18-byte header + whole AES blocks
    assert transport_module.CLIENT_TYPE == "ios"


async def test_async_send_increments_sid_per_command(fake_loop):
    transport, _ = make_transport(fake_loop)
    await transport.async_start()
    await transport.async_send({"ki": 1, "ps": "on"})
    await transport.async_send({"ki": 1, "ps": "off"})

    sids = [
        json.loads(transport_module.parse_frame(KEY, IV, frame)[1].rstrip(b"\x00"))["sid"]
        for frame, _ in fake_loop.transport.sent
    ]
    assert sids == ["1", "2"]


async def test_async_send_does_not_mutate_the_callers_payload(fake_loop):
    transport, _ = make_transport(fake_loop)
    await transport.async_start()
    payload = {"ki": 1, "ps": "on"}

    await transport.async_send(payload)

    assert payload == {"ki": 1, "ps": "on"}


async def test_async_send_before_start_raises(fake_loop):
    transport, _ = make_transport(fake_loop)
    with pytest.raises(RuntimeError):
        await transport.async_send({"ps": "on"})


# --------------------------------------------------------------------------
# receiving
# --------------------------------------------------------------------------


async def test_datagram_received_delivers_decrypted_status(fake_loop):
    transport, statuses = make_transport(fake_loop)
    await transport.async_start()
    frame = status_frame()

    transport.datagram_received(frame, AC)

    assert statuses == [STATUS]
    assert frame[15] == DIR_DEV_TO_APP == 0x02
    assert transport.last_status == STATUS


async def test_datagram_received_ignores_malformed_frame(fake_loop):
    transport, statuses = make_transport(fake_loop)
    await transport.async_start()

    transport.datagram_received(b"garbage", AC)
    transport.datagram_received(b"", AC)

    assert statuses == []


async def test_datagram_received_ignores_our_own_echo(fake_loop):
    """Our own app->device frame echoes back from the broadcast address."""
    transport, statuses = make_transport(fake_loop)
    await transport.async_start()
    own = build_frame(KEY, IV, DEV, json.dumps(STATUS), direction=DIR_APP_TO_DEVICE)

    transport.datagram_received(own, AC)

    assert statuses == []


async def test_datagram_received_ignores_other_device(fake_loop):
    """A second AC on the same LAN broadcasts to the same port."""
    transport, statuses = make_transport(fake_loop)
    await transport.async_start()

    transport.datagram_received(status_frame(device_id=OTHER_DEV), ("10.10.1.126", 5001))

    assert statuses == []


async def test_datagram_received_ignores_undecryptable_body(fake_loop):
    """Right device, right key, but the plaintext is not JSON."""
    transport, statuses = make_transport(fake_loop)
    await transport.async_start()
    frame = build_frame(KEY, IV, DEV, b"\x01\x02not json", direction=DIR_DEV_TO_APP)

    transport.datagram_received(frame, AC)

    assert statuses == []


async def test_datagram_received_ignores_non_object_json(fake_loop):
    transport, statuses = make_transport(fake_loop)
    await transport.async_start()
    frame = build_frame(KEY, IV, DEV, b'["a"]', direction=DIR_DEV_TO_APP)

    transport.datagram_received(frame, AC)

    assert statuses == []


async def test_datagram_received_ignores_wrong_key(fake_loop):
    """A frame encrypted with another key must not decrypt into a status."""
    transport, statuses = make_transport(fake_loop)
    await transport.async_start()
    frame = build_frame(bytes(range(1, 33)), IV, DEV, json.dumps(STATUS), direction=DIR_DEV_TO_APP)

    transport.datagram_received(frame, AC)

    assert statuses == []


# --------------------------------------------------------------------------
# query (the silence-triggered nudge)
# --------------------------------------------------------------------------


async def test_async_query_sends_noop_and_returns_status(fake_loop):
    transport, _ = make_transport(fake_loop)
    await transport.async_start()

    task = asyncio.ensure_future(transport.async_query())
    await asyncio.sleep(0)  # let async_query send its frame

    sent_frame, sent_addr = fake_loop.transport.sent[0]
    assert sent_addr == AC
    assert sent_frame[15] == DIR_APP_TO_DEVICE
    assert json.loads(
        transport_module.parse_frame(KEY, IV, sent_frame)[1].rstrip(b"\x00")
    ) == {}

    transport.datagram_received(status_frame(), AC)

    assert await task == STATUS


async def test_async_query_raises_when_no_broadcast_arrives(fake_loop):
    transport, _ = make_transport(fake_loop)
    await transport.async_start()

    with pytest.raises(TimeoutError):
        await transport.async_query()


async def test_async_query_ignores_another_devices_status(fake_loop):
    transport, _ = make_transport(fake_loop)
    await transport.async_start()

    task = asyncio.ensure_future(transport.async_query())
    await asyncio.sleep(0)
    transport.datagram_received(status_frame(device_id=OTHER_DEV), ("10.10.1.126", 5001))

    with pytest.raises(TimeoutError):
        await task


# --------------------------------------------------------------------------
# lifecycle
# --------------------------------------------------------------------------


async def test_async_start_hands_its_bound_socket_to_the_loop(fake_loop):
    transport, _ = make_transport(fake_loop)
    await transport.async_start()

    # The socket options themselves are covered by the _configure_socket test.
    assert fake_loop.socks == [fake_loop.sock]


def test_configure_socket_sets_reuse_and_broadcast_options():
    """SO_REUSEADDR + SO_REUSEPORT together bind alongside either peer style."""
    sock = transport_module._configure_socket(FakeSocket(), BROADCAST_PORT)

    options = {(level, name) for level, name, _ in sock.options}
    assert (socket.SOL_SOCKET, socket.SO_REUSEADDR) in options
    assert (socket.SOL_SOCKET, socket.SO_REUSEPORT) in options
    assert (socket.SOL_SOCKET, socket.SO_BROADCAST) in options
    assert sock.bound == ("0.0.0.0", BROADCAST_PORT)
    assert sock.blocking is False


async def test_async_start_joins_the_configured_multicast_group(fake_loop):
    transport, _ = make_transport(fake_loop)
    await transport.async_start()

    joined = [
        value
        for level, name, value in fake_loop.sock.options
        if (level, name) == (socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP)
    ]
    assert joined == [socket.inet_aton(DEFAULT_MULTICAST) + socket.inet_aton("0.0.0.0")]


async def test_async_start_is_idempotent(fake_loop):
    transport, _ = make_transport(fake_loop)
    await transport.async_start()
    await transport.async_start()

    assert len(fake_loop.protocols) == 1


async def test_async_stop_closes_transport_and_fails_pending_query(fake_loop):
    transport, _ = make_transport(fake_loop)
    await transport.async_start()
    task = asyncio.ensure_future(transport.async_query())
    await asyncio.sleep(0)

    await transport.async_stop()

    assert fake_loop.transport.closed is True
    with pytest.raises((TimeoutError, asyncio.CancelledError)):
        await task


async def test_async_send_after_stop_raises(fake_loop):
    transport, _ = make_transport(fake_loop)
    await transport.async_start()
    await transport.async_stop()

    with pytest.raises(RuntimeError):
        await transport.async_send({"ps": "on"})


async def test_async_stop_is_safe_before_start(fake_loop):
    transport, _ = make_transport(fake_loop)
    await transport.async_stop()  # must not raise


# --------------------------------------------------------------------------
# the socket options themselves (real socket, ephemeral port)
# --------------------------------------------------------------------------


def test_make_socket_sets_reuse_and_broadcast_options(socket_enabled):
    """A real socket really does accept both reuse options and bind."""
    sock = transport_module._make_socket(0)
    try:
        assert sock.getsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR) != 0
        assert sock.getsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT) != 0
        assert sock.getsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST) != 0
        assert sock.getsockname()[1] != 0  # actually bound
    finally:
        sock.close()


# --------------------------------------------------------------------------
# discovery (used by the config flow to find the AC's IP)
# --------------------------------------------------------------------------


async def test_async_discover_returns_reply_source_ip(fake_loop):
    task = asyncio.ensure_future(
        async_discover("b0:81:84:4e:a3:a1", DEV, loop=fake_loop, timeout=0.5)
    )
    await asyncio.sleep(0)

    request, addr = fake_loop.transport.sent[0]
    assert request == b"DISC_REQ_B081844EA3A1" + b"\x00" * 11
    # The verified-working probe hits both ports; the reply is intermittent.
    assert addr == (BROADCAST_ADDRESS, DEFAULT_PORT)
    assert {a for _, a in fake_loop.transport.sent} == {
        (BROADCAST_ADDRESS, DEFAULT_PORT),
        (BROADCAST_ADDRESS, BROADCAST_PORT),
    }

    fake_loop.protocols[0].datagram_received(
        build_frame(KEY, IV, DEV, "DISC_RSP_B081844EA3A1", direction=DIR_DEV_TO_APP),
        AC,
    )

    assert await task == HOST


async def test_async_discover_returns_none_on_timeout(fake_loop):
    assert await async_discover("b0:81:84:4e:a3:a1", DEV, loop=fake_loop, timeout=0.01) is None


async def test_async_discover_ignores_other_devices(fake_loop):
    task = asyncio.ensure_future(
        async_discover("b0:81:84:4e:a3:a1", DEV, loop=fake_loop, timeout=0.2)
    )
    await asyncio.sleep(0)

    fake_loop.protocols[0].datagram_received(
        build_frame(KEY, IV, OTHER_DEV, "DISC_RSP_OTHER", direction=DIR_DEV_TO_APP),
        ("10.10.1.126", 5001),
    )
    fake_loop.protocols[0].datagram_received(b"nonsense", ("10.10.1.126", 5001))

    assert await task is None


async def test_async_discover_closes_its_socket(fake_loop):
    await async_discover("b0:81:84:4e:a3:a1", DEV, loop=fake_loop, timeout=0.01)

    assert fake_loop.transport.closed is True


async def test_discover_request_matches_the_verified_discovery_datagram():
    """The exact 32-byte datagram that the physical unit answers."""
    from custom_components.miraie_local.protocol import build_disc_req

    assert build_disc_req("b0:81:84:4e:a3:a1") == b"DISC_REQ_B081844EA3A1" + b"\x00" * 11
    assert len(build_disc_req("b0:81:84:4e:a3:a1")) == 32
    assert BROADCAST_PORT == 5002
