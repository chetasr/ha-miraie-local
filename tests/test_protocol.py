"""Tests for the pure MirAIe frame codec and control command map.

The key/IV used here are SYNTHETIC (``bytes(range(32))`` / ``bytes(range(16))``)
and must never be replaced with the real ``udpControlKey``/``udpControlIV``.
The deviceId and the 18-byte frame header are broadcast in cleartext on the LAN,
so pinning the header against a real observed packet is safe and is exactly what
proves the wire format.
"""

import pytest

from custom_components.miraie_local.protocol import (
    DIR_APP_TO_DEVICE,
    DIR_DEV_TO_APP,
    FAN_MODES,
    MODES,
    build_disc_req,
    build_disc_rsp,
    build_frame,
    command,
    parse_frame,
)

KEY = bytes(range(32))   # synthetic
IV = bytes(range(16))    # synthetic
DEV = "8c1419857fd4"     # deviceId is broadcast in cleartext, so not a secret

# Real 18-byte header observed on the wire from the physical unit:
#   00 00 | 0c | 8c1419857fd4 | 00 | 0020   (direction 0 = device->app, 32-byte ciphertext)
REAL_HEADER = bytes.fromhex("00000c386331343139383537666434020020")


def test_build_frame_layout_matches_real_wire_header():
    f = build_frame(KEY, IV, DEV, "DISC_RSP_B081844EA3A1", direction=DIR_DEV_TO_APP)
    assert f[:18] == REAL_HEADER                 # header layout pinned to a real packet
    assert int.from_bytes(f[16:18], "big") == len(f) - 18
    assert len(f[18:]) % 16 == 0              # ciphertext is block-aligned
    assert len(f) % 16 == 2                    # 18-byte header + n*16 ciphertext


def test_build_frame_direction_and_length():
    f = build_frame(KEY, IV, DEV, '{"ps":"on"}', direction=DIR_APP_TO_DEVICE)
    assert f[:3] == b"\x00\x00\x0c"
    assert f[3:15] == DEV.encode()
    assert f[15] == 0x01                          # app->device direction byte
    assert int.from_bytes(f[16:18], "big") == len(f) - 18


def test_roundtrip_parse_frame():
    payload = '{"sid":"3","cnt":"ios","ki":1,"ps":"on"}'
    raw = build_frame(KEY, IV, DEV, payload, direction=DIR_DEV_TO_APP)
    direction, pt = parse_frame(KEY, IV, raw)
    assert direction == DIR_DEV_TO_APP
    assert pt.rstrip(b"\x00").decode() == payload


def test_parse_frame_rejects_short_and_unaligned():
    with pytest.raises(ValueError):
        parse_frame(KEY, IV, REAL_HEADER[:10])
    with pytest.raises(ValueError):
        parse_frame(KEY, IV, REAL_HEADER + b"\x01" * 17)   # not a multiple of 16


def test_parse_frame_rejects_bad_magic_idlength_and_declared_length():
    with pytest.raises(ValueError):
        parse_frame(KEY, IV, b"\x01" + REAL_HEADER[1:])                     # magic != 0x00
    with pytest.raises(ValueError):
        parse_frame(KEY, IV, REAL_HEADER[:2] + b"\x0b" + REAL_HEADER[3:])   # deviceId len != 12
    bad_len = REAL_HEADER[:16] + (99).to_bytes(2, "big") + REAL_HEADER[18:]
    with pytest.raises(ValueError):
        parse_frame(KEY, IV, bad_len)                                       # declared != actual


def test_disc_req_is_zero_padded_to_32():
    assert build_disc_req("b0:81:84:4e:a3:a1") == b"DISC_REQ_B081844EA3A1".ljust(32, b"\x00")


def test_disc_rsp_is_zero_padded_to_32():
    assert build_disc_rsp("b0:81:84:4e:a3:a1") == b"DISC_RSP_B081844EA3A1".ljust(32, b"\x00")


def test_command_map():
    assert command("off") == {"ki": 1, "ps": "off"}
    assert command("on") == {"ki": 1, "ps": "on"}
    # D12: temp/mode/fan also carry ps="on" in the SAME frame so a value set
    # while the AC is off turns it on.
    assert command("temp", 25.0) == {"ki": 2, "actmp": "25.0", "ps": "on"}
    assert command("mode", "dry") == {"ki": 3, "acmd": "dry", "ps": "on"}
    assert command("fan", "low") == {"ki": 4, "acfs": "low", "ps": "on"}


def test_command_rejects_unknown_kind_and_value():
    with pytest.raises(ValueError):
        command("nope")
    with pytest.raises(ValueError):
        command("mode", "heat")      # the unit has no heat mode
    with pytest.raises(ValueError):
        command("fan", "turbo")
    with pytest.raises(ValueError):
        command("temp", "not-a-number")


def test_enum_tables_match_the_unit():
    assert FAN_MODES == ["auto", "quiet", "low", "medium", "high"]
    assert MODES == ["auto", "cool", "dry", "fan"]
    assert "heat" not in MODES
