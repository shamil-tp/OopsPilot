"""GitHub webhook signature verification (HMAC SHA-256 over the raw request body).

GitHub signs the exact bytes it sends and puts `sha256=<hex digest>` in `X-Hub-Signature-256`.
The digest is computed over the raw body, never over re-serialized JSON, and compared in constant
time. Error messages never contain the secret or either digest.
"""

import hashlib
import hmac
import re

SIGNATURE_HEADER = "X-Hub-Signature-256"
_SIGNATURE = re.compile(r"sha256=[0-9a-f]{64}")


class SignatureError(Exception):
    """The delivery is not authentic. Messages are safe to return to the sender."""


def sign(secret: str, body: bytes) -> str:
    """The `X-Hub-Signature-256` value GitHub would send for `body` (used by tests and the local
    webhook script)."""
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def verify_signature(secret: str, body: bytes, header: str | None) -> None:
    if not header:
        raise SignatureError(f"Missing {SIGNATURE_HEADER} header")
    if not _SIGNATURE.fullmatch(header.strip()):
        raise SignatureError(f"Malformed {SIGNATURE_HEADER} header")
    if not hmac.compare_digest(sign(secret, body), header.strip()):
        raise SignatureError("Invalid webhook signature")
