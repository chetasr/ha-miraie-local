"""Pure frame codec and control-command map for the MirAIe local UDP protocol.

Wire frame (18-byte header + AES-256-CBC ciphertext)::

    offset 0   1 byte    always 0x00
    offset 1   2 bytes   deviceId length, BIG-ENDIAN (0x000c = 12)
    offset 3   12 bytes  deviceId as 12 ASCII hex chars, e.g. "8c1419857fd4"
    offset 15  1 byte    direction: 0x01 = app->device, 0x02 = device->app
    offset 16  2 bytes   ciphertext length, BIG-ENDIAN
    offset 18  n bytes   AES-256-CBC ciphertext (zero-padded plaintext)

Three details that cost real debugging time and must not be "tidied up":

1. The direction byte must be ``0x01`` for app->device. ``0x02`` is the
   device->app value; sending it makes the device silently ignore the frame
   ("udp ctrl pkt invalid") with no error on the wire.
2. The two bytes after the direction byte are a BIG-ENDIAN ciphertext LENGTH, not a
   sequence counter. A wrong value yields a frame the device drops.
3. ``build_disc_req`` must be ZERO-padded to 32 bytes; space padding gets no
   reply from the AC.

The plaintext is the same JSON the cloud control/status topics use, encrypted by
:mod:`crypto` with AES-256-CBC and zero padding (NOT PKCS#7). This module is
intentionally pure - bytes in, bytes out, no I/O and no module state - so it can be
unit-tested in isolation.
"""

import math

from .crypto import aes_cbc_decrypt, aes_cbc_encrypt

DIR_APP_TO_DEVICE = 0x01
DIR_DEV_TO_APP = 0x02

_HEADER_LEN = 18
_DEVICE_ID_LEN = 12
_DISC_PAYLOAD_LEN = 32

FAN_MODES = ("auto", "quiet", "low", "medium", "high")
MODES = ("auto", "cool", "dry", "fan")  # firmware "acmd"; the unit has NO heat mode
# Alias kept for the plan's enum-table name. These are firmware "acmd" values;
# HA's HVACMode spells fan mode "fan_only", not "fan".
HVAC_MODES = MODES


def build_frame(
    key: bytes,
    iv: bytes,
    device_id: str,
    payload: str | bytes,
    direction: int = DIR_APP_TO_DEVICE,
) -> bytes:
    """Encrypt ``payload`` and wrap it in an 18-byte MirAIe wire header.

    ``payload`` is the JSON control/status body. The ciphertext length field is
    written BIG-ENDIAN at offset 16.
    """
    plaintext = payload.encode("ascii") if isinstance(payload, str) else payload
    device_bytes = device_id.encode("ascii")
    if len(device_bytes) != _DEVICE_ID_LEN:
        raise ValueError(
            f"deviceId must be {_DEVICE_ID_LEN} ASCII bytes, got {len(device_bytes)}"
        )
    if direction not in (DIR_APP_TO_DEVICE, DIR_DEV_TO_APP):
        raise ValueError(
            f"direction must be 0x{DIR_APP_TO_DEVICE:02x} or 0x{DIR_DEV_TO_APP:02x}, "
            f"got 0x{direction:02x}"
        )
    ciphertext = aes_cbc_encrypt(key, iv, plaintext)
    header = (
        b"\x00"
        + len(device_bytes).to_bytes(2, "big")
        + device_bytes
        + bytes([direction])
        + len(ciphertext).to_bytes(2, "big")
    )
    return header + ciphertext


def parse_frame(key: bytes, iv: bytes, raw: bytes) -> tuple[int, bytes]:
    """Decode ``raw`` into ``(direction, zero-padded plaintext)``.

    Raises ``ValueError`` on any malformed frame: too short, wrong magic bytes,
    a deviceId length other than 12, a declared ciphertext length that does not
    match the actual payload, or a ciphertext that is not a whole number of
    16-byte blocks. Malformed frames are never silently accepted.

    The deviceId at ``raw[3:15]`` is NOT validated against a configured device;
    the caller MUST compare it itself.
    """
    if len(raw) < _HEADER_LEN:
        raise ValueError(f"frame shorter than {_HEADER_LEN} bytes: {len(raw)}")
    if raw[0:2] != b"\x00\x00":
        raise ValueError(f"unexpected frame magic {raw[0:2]!r}, expected b'\\x00\\x00'")
    if raw[2] != _DEVICE_ID_LEN:
        raise ValueError(f"unexpected deviceId length {raw[2]}, expected {_DEVICE_ID_LEN}")
    declared = int.from_bytes(raw[16:18], "big")
    actual = len(raw) - _HEADER_LEN
    if declared != actual:
        raise ValueError(f"declared ciphertext length {declared} != actual {actual}")
    return raw[15], aes_cbc_decrypt(key, iv, raw[_HEADER_LEN:])


def _disc_payload(prefix: str, ap_mac: str) -> bytes:
    """Return ``<prefix><MAC>`` zero-padded to 32 bytes (space padding gets no reply)."""
    mac = ap_mac.replace(":", "").upper()
    if len(mac) != 12:
        raise ValueError(f"AP MAC must be 12 hex chars, got {ap_mac!r}")
    return f"{prefix}{mac}".encode("ascii").ljust(_DISC_PAYLOAD_LEN, b"\x00")


def build_disc_req(ap_mac: str) -> bytes:
    """Return the zero-padded ``DISC_REQ_<MAC>`` discovery request payload."""
    return _disc_payload("DISC_REQ_", ap_mac)


def build_disc_rsp(ap_mac: str) -> bytes:
    """Return the zero-padded ``DISC_RSP_<MAC>`` discovery response payload."""
    return _disc_payload("DISC_RSP_", ap_mac)


def command(kind: str, value: object = None) -> dict:
    """Build the JSON control body for ``kind``.

    Temperature, mode and fan commands also carry ``ps: "on"`` in the SAME frame
    (decision D12) so that changing a value while the AC is off turns it on; the
    device reports such frames as ``lcmd: "group_cmd"``. The ``off`` command
    deliberately omits ``ps: "on"``. An unknown ``kind`` or ``value`` raises
    ``ValueError`` rather than sending a frame the device would ignore.
    """
    if kind == "off":
        return {"ki": 1, "ps": "off"}
    if kind == "on":
        return {"ki": 1, "ps": "on"}
    if kind == "temp":
        try:
            temp = float(value)
        except (TypeError, ValueError) as err:
            raise ValueError(f"temp requires a numeric value, got {value!r}") from err
        if not math.isfinite(temp):
            raise ValueError(f"temp must be finite, got {value!r}")
        return {"ki": 2, "actmp": f"{temp:.1f}", "ps": "on"}
    if kind == "mode":
        if value not in MODES:
            raise ValueError(f"unknown mode {value!r}, expected one of {MODES}")
        return {"ki": 3, "acmd": value, "ps": "on"}
    if kind == "fan":
        if value not in FAN_MODES:
            raise ValueError(f"unknown fan mode {value!r}, expected one of {FAN_MODES}")
        return {"ki": 4, "acfs": value, "ps": "on"}
    raise ValueError(f"unknown command kind {kind!r}")
