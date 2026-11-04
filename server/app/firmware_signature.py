"""Checks an uploaded image's signature block with the public signing key (Secure Boot V2 scheme, RSA-3072 PSS).

An extra guard at upload: it stops a wrong image early. The watch's own check (esp_ota_end) stays the authority.
Layout: esp-idf ets_secure_boot_signature_t, in the last 4 KB sector of a signed image.
"""

from __future__ import annotations

import hashlib
import struct
import zlib
from pathlib import Path

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa, utils

SECTOR = 4096
BLOCK = 1216
MAGIC, VERSION = 0xE7, 0x02
KEY_BYTES = 384


def load_public_key(setting: str) -> rsa.RSAPublicKey:
    """The setting holds a PEM public key, or the path of one."""
    pem = setting if "BEGIN" in setting else Path(setting).read_text(encoding="ascii")
    key = serialization.load_pem_public_key(pem.encode("ascii"))
    if not isinstance(key, rsa.RSAPublicKey) or key.key_size != KEY_BYTES * 8:
        raise ValueError("the signing key must be RSA-3072")
    return key


def verify(image: bytes, public_key: rsa.RSAPublicKey) -> tuple[bool, str]:
    """(valid, why not). Up to 3 signature blocks can share the sector: one by this key is enough."""
    if len(image) < SECTOR or len(image) % SECTOR:
        return False, "the image has no signature sector"
    body, sector = image[:-SECTOR], image[-SECTOR:]
    digest = hashlib.sha256(body).digest()
    want = public_key.public_numbers()
    found = False
    for i in range(3):
        block = sector[i * BLOCK : (i + 1) * BLOCK]
        if block[0] != MAGIC or block[1] != VERSION:
            continue
        found = True
        if struct.unpack("<I", block[1196:1200])[0] != zlib.crc32(block[:1196]) & 0xFFFFFFFF:
            return False, "the signature block is damaged"
        if block[4:36] != digest:
            return False, "the image was changed after signing"
        n = int.from_bytes(block[36 : 36 + KEY_BYTES], "little")
        e = struct.unpack("<I", block[36 + KEY_BYTES : 40 + KEY_BYTES])[0]
        if n != want.n or e != want.e:
            continue  # another key's block
        signature = block[812 : 812 + KEY_BYTES][::-1]
        try:
            public_key.verify(signature, digest, padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=32),
                              utils.Prehashed(hashes.SHA256()))
        except InvalidSignature:
            return False, "the signature is not valid"
        return True, ""
    return False, "signed with a different key" if found else "the image is not signed"
