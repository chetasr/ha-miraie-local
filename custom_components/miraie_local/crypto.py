"""Pure AES-256-CBC helpers for the MirAIe local UDP protocol.

The device firmware encrypts its JSON payloads with AES-256-CBC and pads the
plaintext with ZERO bytes up to a 16-byte boundary. It does NOT use PKCS#7:
when the plaintext already ends on a block boundary it still appends a full
16-byte block of zeros (the one exception is an empty plaintext, which stays
empty), and the receiver simply strips trailing NULs. These functions mirror
that behaviour exactly so the frames we emit are accepted by the AC and the
frames it broadcasts can be decoded.

This CBC is UNAUTHENTICATED (there is no MAC, and the IV is a fixed per-home
value), so protocol/transport code must treat decrypted bytes as untrusted
input and validate the JSON shape rather than assume integrity.

This module is intentionally pure - bytes in, bytes out, no I/O and no module
state - so it can be unit-tested in isolation.
"""

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

BLOCK_SIZE = 16


def pad_zero(data: bytes) -> bytes:
    """Zero-pad ``data`` to a multiple of 16 bytes.

    An empty input stays empty. Input that is already a multiple of the block
    size gets a FULL extra block of zeros, matching the device firmware.
    """
    if not data:
        return b""
    remainder = len(data) % BLOCK_SIZE
    padding = BLOCK_SIZE if remainder == 0 else BLOCK_SIZE - remainder
    return data + b"\x00" * padding


def _validate(key: bytes, iv: bytes) -> None:
    """Reject wrong key/IV sizes loudly instead of silently using AES-128/192."""
    if len(key) != 32:
        raise ValueError(f"AES-256 requires a 32-byte key, got {len(key)}")
    if len(iv) != 16:
        raise ValueError(f"AES-CBC requires a 16-byte IV, got {len(iv)}")


def aes_cbc_encrypt(key: bytes, iv: bytes, data: bytes) -> bytes:
    """AES-256-CBC encrypt ``data`` after zero-padding it to a block boundary."""
    _validate(key, iv)
    encryptor = Cipher(algorithms.AES(key), modes.CBC(iv)).encryptor()
    return encryptor.update(pad_zero(data)) + encryptor.finalize()


def aes_cbc_decrypt(key: bytes, iv: bytes, ct: bytes) -> bytes:
    """AES-256-CBC decrypt ``ct``.

    The result keeps the trailing zero padding; callers strip it with
    ``.rstrip(b"\\x00")`` as needed. Raises ``ValueError`` when ``ct`` is empty
    or is not a multiple of 16 bytes.
    """
    _validate(key, iv)
    if not ct:
        raise ValueError("empty ciphertext")
    if len(ct) % BLOCK_SIZE != 0:
        raise ValueError(f"ciphertext length {len(ct)} is not a multiple of {BLOCK_SIZE}")
    decryptor = Cipher(algorithms.AES(key), modes.CBC(iv)).decryptor()
    return decryptor.update(ct) + decryptor.finalize()
