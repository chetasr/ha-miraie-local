"""Pure AES-256-CBC helpers for the MirAIe local UDP protocol.

The device firmware encrypts its JSON payloads with AES-256-CBC and pads the
plaintext with ZERO bytes up to a 16-byte boundary. It does NOT use PKCS#7:
when the plaintext already ends on a block boundary it still appends a full
16-byte block of zeros, and the receiver simply strips trailing NULs. These
functions mirror that behaviour exactly so the frames we emit are accepted by
the AC and the frames it broadcasts can be decoded.

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


def aes_cbc_encrypt(key: bytes, iv: bytes, data: bytes) -> bytes:
    """AES-256-CBC encrypt ``data`` after zero-padding it to a block boundary."""
    encryptor = Cipher(algorithms.AES(key), modes.CBC(iv)).encryptor()
    return encryptor.update(pad_zero(data)) + encryptor.finalize()


def aes_cbc_decrypt(key: bytes, iv: bytes, ct: bytes) -> bytes:
    """AES-256-CBC decrypt ``ct``.

    The result keeps the trailing zero padding; callers strip it with
    ``.rstrip(b"\\x00")`` as needed. Raises ``ValueError`` when ``ct`` is not a
    multiple of 16 bytes.
    """
    if len(ct) % BLOCK_SIZE != 0:
        raise ValueError(f"ciphertext length {len(ct)} is not a multiple of {BLOCK_SIZE}")
    decryptor = Cipher(algorithms.AES(key), modes.CBC(iv)).decryptor()
    return decryptor.update(ct) + decryptor.finalize()
