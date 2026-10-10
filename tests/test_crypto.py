"""Tests for the pure AES-256-CBC helpers."""

import pytest

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


def test_nist_sp800_38a_cbc_aes256_known_answer():
    """NIST SP 800-38A F.2.5 CBC-AES256.Encrypt, first block (published vector)."""
    nist_key = bytes.fromhex(
        "603deb1015ca71be2b73aef0857d7781"
        "1f352c073b6108d72d9810a30914dff4"
    )
    nist_iv = bytes.fromhex("000102030405060708090a0b0c0d0e0f")
    block1 = bytes.fromhex("6bc1bee22e409f96e93d7e117393172a")
    expected_ct1 = bytes.fromhex("f58c4c04d6e5f1ba779eabfb5f7bfbd6")
    ct = aes_cbc_encrypt(nist_key, nist_iv, block1)
    assert ct[:16] == expected_ct1          # pins CBC mode + AES-256 exactly
    assert len(ct) == 32                    # 16-byte input still gets the extra zero block
    assert aes_cbc_decrypt(nist_key, nist_iv, ct)[:16] == block1


def test_aligned_plaintext_through_aes_gets_extra_block():
    ct = aes_cbc_encrypt(KEY, IV, b"A" * 16)
    assert len(ct) == 32
    assert aes_cbc_decrypt(KEY, IV, ct) == b"A" * 16 + b"\x00" * 16


def test_empty_plaintext_encrypts_to_empty():
    assert aes_cbc_encrypt(KEY, IV, b"") == b""


def test_rejects_wrong_key_or_iv_length():
    with pytest.raises(ValueError):
        aes_cbc_encrypt(bytes(16), IV, b"x")
    with pytest.raises(ValueError):
        aes_cbc_encrypt(KEY, bytes(8), b"x")


def test_decrypt_rejects_empty():
    with pytest.raises(ValueError):
        aes_cbc_decrypt(KEY, IV, b"")


def test_decrypt_rejects_unaligned():
    with pytest.raises(ValueError):
        aes_cbc_decrypt(KEY, IV, b"not-a-multiple-of-16")
