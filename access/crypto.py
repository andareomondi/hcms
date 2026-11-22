"""
Signature checks for the browser key. The browser signs a server challenge with its private key,
which never leaves the browser. The server checks the signature with the public key stored at approval.
"""
import base64
import json

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature, encode_dss_signature


class KeyError_(ValueError):
    """The public key was not a valid P-256 key."""


def _b64url_decode(text):
    if not isinstance(text, str) or not text:
        raise KeyError_("missing value")
    padded = text + "=" * (-len(text) % 4)
    try:
        return base64.urlsafe_b64decode(padded.encode())
    except (ValueError, TypeError):
        raise KeyError_("not base64")


def normalise_public_key(raw):
    """Accepts the JWK the browser sends and returns it as clean JSON text, or raises KeyError_."""
    try:
        jwk = json.loads(raw or "")
    except (ValueError, TypeError):
        raise KeyError_("The browser key is not valid. Use a current browser.")
    if not isinstance(jwk, dict) or jwk.get("kty") != "EC" or jwk.get("crv") != "P-256":
        raise KeyError_("The browser key must be an EC P-256 key.")
    x, y = _b64url_decode(jwk.get("x")), _b64url_decode(jwk.get("y"))
    if len(x) != 32 or len(y) != 32:
        raise KeyError_("The browser key has the wrong size.")
    public_key_from_parts(x, y)  # raises if the point is not on the curve
    return json.dumps({"kty": "EC", "crv": "P-256",
                       "x": jwk["x"], "y": jwk["y"]}, separators=(",", ":"))


def public_key_from_parts(x_bytes, y_bytes):
    try:
        return ec.EllipticCurvePublicNumbers(int.from_bytes(x_bytes, "big"), int.from_bytes(y_bytes, "big"),
                                             ec.SECP256R1()).public_key()
    except ValueError:
        raise KeyError_("The browser key is not a valid point on the curve.")


def public_key_from_stored(stored_jwk):
    jwk = json.loads(stored_jwk)
    return public_key_from_parts(_b64url_decode(jwk["x"]), _b64url_decode(jwk["y"]))


def verify_signature(public_key, message, signature_text):
    """WebCrypto signs with raw r||s (64 bytes). Converts to DER and checks it."""
    try:
        raw = _b64url_decode(signature_text)
    except KeyError_:
        return False
    if len(raw) != 64:
        return False
    r, s = int.from_bytes(raw[:32], "big"), int.from_bytes(raw[32:], "big")
    try:
        public_key.verify(encode_dss_signature(r, s), message, ec.ECDSA(hashes.SHA256()))
    except InvalidSignature:
        return False
    return True


def sign_raw(private_key, message):
    """Used by the test suite to act as a browser. Returns the signature in WebCrypto's format."""
    r, s = decode_dss_signature(private_key.sign(message, ec.ECDSA(hashes.SHA256())))
    raw = r.to_bytes(32, "big") + s.to_bytes(32, "big")
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")
