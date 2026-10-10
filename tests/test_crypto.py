"""Tests for the pure AES-256-CBC helpers."""

from custom_components.miraie_local.crypto import (
    aes_cbc_decrypt,
    aes_cbc_encrypt,
    pad_zero,
)

KEY = bytes(range(32))   # synthetic - never the real udpControlKey
IV = bytes(range(16))    # synthetic - never the real udpControlIV


def test_pad_zero_rounds_to_block():
    assert pad_zero(b"") == b""
    assert len(pad_zero(b"1234567890123456")) == 32   # exact block still gets a full block
    assert len(pad_zero(b"x")) == 16
    assert pad_zero(b"x").endswith(b"\x00" * 15)


def test_roundtrip_needs_zero_padding():
    pt = b'{"sid":"3","cnt":"ios","ki":1,"ps":"on"}'      # 36 bytes
    ct = aes_cbc_encrypt(KEY, IV, pt)
    assert len(ct) == 48
    assert len(ct) % 16 == 0
    assert aes_cbc_decrypt(KEY, IV, ct).rstrip(b"\x00") == pt


def test_aes_is_cbc_and_keyed():
    ct = aes_cbc_encrypt(KEY, IV, b"A" * 16)
    assert ct != aes_cbc_encrypt(KEY, bytes(16), b"A" * 16)          # IV matters
    assert ct != aes_cbc_encrypt(bytes(range(1, 33)), IV, b"A" * 16)   # key matters


def test_decrypt_rejects_unaligned():
    import pytest
    with pytest.raises(ValueError):
        aes_cbc_decrypt(KEY, IV, b"not-a-multiple-of-16")
