"""Web Push encryption (RFC 8291) and VAPID (RFC 8292), checked on the RFC's own
worked example (RFC 8291, Appendix A) and by decrypting as a browser would."""

import json

import pytest

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from aegis import webpush
from aegis.webpush import b64d

AS_PRIVATE = "yfWPiYE-n46HLnH0KqZOF1fJJU3MYrct3AELtAQ-oRw"
AS_PUBLIC = "BP4z9KsN6nGRTbVYI_c7VJSPQTBtkgcy27mlmlMoZIIgDll6e3vCYLocInmYWAmS6TlzAC8wEqKK6PBru3jl7A8"
UA_PUBLIC = "BCVxsr7N_eNgVRqvHtD0zTZsEc6-VV-JvLexhqUzORcxaOzi6-AYWXvTBHm4bjyPjs7Vd8pZGH6SRpkNtoIAiw4"
UA_PRIVATE = "q1dXpw3UpT5VOmu_cf_v6ih07Aems3njxI-JWgLcM94"
AUTH = "BTBZMqHH6r4Tts7J_aSIgg"
SALT = "DGv6ra1nlYgDCS1FRnbzlw"
PLAIN = "V2hlbiBJIGdyb3cgdXAsIEkgd2FudCB0byBiZSBhIHdhdGVybWVsb24"
HEADER = "DGv6ra1nlYgDCS1FRnbzlwAAEABBBP4z9KsN6nGRTbVYI_c7VJSPQTBtkgcy27mlmlMoZIIgDll6e3vCYLocInmYWAmS6TlzAC8wEqKK6PBru3jl7A8"
CIPHER = (
    "8pfeW0KbunFT06SuDKoJH9Ql87S1QUrdirN6GcG7sFz1y1sqLgVi1VhjVkHsUoEsbI_0LpXMuGvnzQ"
)


def private(b64: str) -> ec.EllipticCurvePrivateKey:
    return ec.derive_private_key(int.from_bytes(b64d(b64), "big"), ec.SECP256R1())


def test_the_rfcs_example_encrypts_byte_for_byte():
    out = webpush.encrypt(
        b64d(PLAIN),
        b64d(UA_PUBLIC),
        b64d(AUTH),
        as_private=private(AS_PRIVATE),
        salt=b64d(SALT),
    )
    assert webpush.raw_public(private(AS_PRIVATE)) == b64d(AS_PUBLIC)
    assert out[:86] == b64d(HEADER) and out[86:] == b64d(CIPHER)


def decrypt(body: bytes, ua: ec.EllipticCurvePrivateKey, auth: bytes) -> bytes:
    """What the browser does with a push body (RFC 8291 section 3.4)."""
    salt, idlen = body[:16], body[20]
    as_public = body[21 : 21 + idlen]
    ua_public = webpush.raw_public(ua)
    ecdh = ua.exchange(
        ec.ECDH(),
        ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), as_public),
    )
    kdf = lambda salt, ikm, info, n: HKDF(hashes.SHA256(), n, salt, info).derive(ikm)  # noqa: E731
    ikm = kdf(auth, ecdh, b"WebPush: info\x00" + ua_public + as_public, 32)
    cek = kdf(salt, ikm, b"Content-Encoding: aes128gcm\x00", 16)
    nonce = kdf(salt, ikm, b"Content-Encoding: nonce\x00", 12)
    padded = AESGCM(cek).decrypt(nonce, body[21 + idlen :], None)
    assert padded.endswith(b"\x02")
    return padded[:-1]


def test_a_payload_round_trips_through_a_fresh_key():
    ua = ec.generate_private_key(ec.SECP256R1())
    body = webpush.encrypt(
        b'{"title": "x"}', webpush.raw_public(ua), b"0123456789abcdef"
    )
    assert decrypt(body, ua, b"0123456789abcdef") == b'{"title": "x"}'


def test_the_vapid_jwt_verifies_with_the_public_key():
    key = ec.generate_private_key(ec.SECP256R1())
    jwt = webpush.vapid_jwt(
        key, "https://fcm.googleapis.com", "mailto:aegis@localhost", now=1000
    )
    head, body, sig = jwt.split(".")
    assert json.loads(b64d(head)) == {"typ": "JWT", "alg": "ES256"}
    claims = json.loads(b64d(body))
    assert claims == {
        "aud": "https://fcm.googleapis.com",
        "exp": 1000 + 12 * 3600,
        "sub": "mailto:aegis@localhost",
    }
    raw = b64d(sig)
    der = encode_dss_signature(
        int.from_bytes(raw[:32], "big"), int.from_bytes(raw[32:], "big")
    )
    key.public_key().verify(der, f"{head}.{body}".encode(), ec.ECDSA(hashes.SHA256()))


def test_the_key_is_made_once_and_kept_private(tmp_path):
    path = tmp_path / "push" / "vapid.pem"
    a = webpush.load_key(path)
    b = webpush.load_key(path)
    assert webpush.raw_public(a) == webpush.raw_public(b)
    assert path.stat().st_mode & 0o777 == 0o600


@pytest.mark.slow
def test_the_server_loads_cryptography_only_when_it_pushes():
    # cryptography's bindings cost the server about 7 MB of resident memory
    # (#345), and a server nobody subscribed to never signs or encrypts.
    import subprocess
    import sys

    code = (
        "import sys, aegis.app\n"
        "print(sorted(m for m in sys.modules if m.startswith('cryptography')))"
    )
    out = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=True
    ).stdout
    assert out.strip() == "[]"
