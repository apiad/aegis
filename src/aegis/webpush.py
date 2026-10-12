"""Web Push without a library: the message encryption of RFC 8291 (the
aes128gcm content coding of RFC 8188) and the VAPID token of RFC 8292, with
``cryptography``, which aegis already depends on. ``push.py`` uses them; nothing
here does I/O except ``load_key``.

A browser's subscription gives an endpoint at its vendor's push service, its
own P-256 public key (``p256dh``) and a 16-byte ``auth`` secret. ``encrypt``
derives a content key from an ECDH between a fresh key and ``p256dh`` and the
``auth`` secret, so the push service carries a body it cannot read. The VAPID
token, signed with this server's long-lived key, tells the push service which
server sends: a subscription made with that key's public half accepts nothing
signed by any other."""

from __future__ import annotations

import base64
import json
import os
import struct
import time
from pathlib import Path

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

RECORD_SIZE = 4096


def b64d(s: str) -> bytes:
    """base64url with or without padding, whitespace ignored (the RFCs wrap it)."""
    s = "".join(s.split())
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def b64e(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def raw_public(key: ec.EllipticCurvePrivateKey) -> bytes:
    """The public half as the 65-byte uncompressed point the browser expects."""
    return key.public_key().public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
    )


def _hkdf(salt: bytes, ikm: bytes, info: bytes, n: int) -> bytes:
    return HKDF(algorithm=hashes.SHA256(), length=n, salt=salt, info=info).derive(ikm)


def encrypt(
    plaintext: bytes,
    ua_public: bytes,
    auth_secret: bytes,
    *,
    as_private: ec.EllipticCurvePrivateKey | None = None,
    salt: bytes | None = None,
) -> bytes:
    """One aes128gcm record for the subscription's ``p256dh`` and ``auth``: the
    86-byte header (salt, record size, this message's public key), then the
    ciphertext of the plaintext and its 0x02 delimiter. ``as_private`` and
    ``salt`` are fresh for every message; tests pass the RFC's."""
    as_private = as_private or ec.generate_private_key(ec.SECP256R1())
    salt = salt or os.urandom(16)
    as_public = raw_public(as_private)
    ua_key = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), ua_public)
    ecdh = as_private.exchange(ec.ECDH(), ua_key)
    ikm = _hkdf(auth_secret, ecdh, b"WebPush: info\x00" + ua_public + as_public, 32)
    cek = _hkdf(salt, ikm, b"Content-Encoding: aes128gcm\x00", 16)
    nonce = _hkdf(salt, ikm, b"Content-Encoding: nonce\x00", 12)
    header = salt + struct.pack("!IB", RECORD_SIZE, len(as_public)) + as_public
    return header + AESGCM(cek).encrypt(nonce, plaintext + b"\x02", None)


def vapid_jwt(
    key: ec.EllipticCurvePrivateKey, aud: str, sub: str, now: float | None = None
) -> str:
    """The ES256 token for one push service (``aud`` is the endpoint's origin),
    valid 12 hours. ``sub`` is how the push service could reach the operator."""

    def part(d: dict) -> str:
        return b64e(json.dumps(d, separators=(",", ":")).encode())

    exp = int((now if now is not None else time.time()) + 12 * 3600)
    signing = f"{part({'typ': 'JWT', 'alg': 'ES256'})}.{part({'aud': aud, 'exp': exp, 'sub': sub})}"
    r, s = decode_dss_signature(key.sign(signing.encode(), ec.ECDSA(hashes.SHA256())))
    return f"{signing}.{b64e(r.to_bytes(32, 'big') + s.to_bytes(32, 'big'))}"


def load_key(path: Path) -> ec.EllipticCurvePrivateKey:
    """This server's VAPID key, made on first use and kept at ``path`` (0600).
    Every subscription is bound to its public half, so it must never change."""
    if path.exists():
        key = serialization.load_pem_private_key(path.read_bytes(), password=None)
        assert isinstance(key, ec.EllipticCurvePrivateKey)
        return key
    key = ec.generate_private_key(ec.SECP256R1())
    pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(pem)
    os.replace(tmp, path)
    return key
