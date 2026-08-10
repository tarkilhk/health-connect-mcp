"""Pure auth helpers: HMAC signature and bearer token verification.

No I/O. Both functions use ``hmac.compare_digest`` for timing-safe comparison.
Signature verification is disabled (returns True) when ``secret`` is empty —
this lets dev environments run without a configured webhook secret.
"""
from __future__ import annotations

import hashlib
import hmac

_SIGNATURE_PREFIX = "sha256="
_BEARER_PREFIX = "Bearer "


def verify_signature(
    raw_body: bytes, signature_header: str | None, secret: str
) -> bool:
    """Verify ``X-Signature: sha256=<hex>`` HMAC-SHA256 of raw body.

    Returns True if ``secret`` is empty/None (verification disabled). Returns
    False if the header is missing, malformed, or does not match.
    """
    if not secret:
        return True
    if not signature_header:
        return False
    if not signature_header.startswith(_SIGNATURE_PREFIX):
        return False
    provided = signature_header[len(_SIGNATURE_PREFIX):].strip().lower()
    if not provided:
        return False
    expected = hmac.new(
        secret.encode("utf-8"), raw_body, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(provided, expected)


def verify_token(auth_header: str | None, expected_token: str) -> bool:
    """Verify ``Authorization: Bearer <token>`` against the expected token.

    Returns False if the header is missing, uses the wrong scheme, or the
    token does not match. An empty ``expected_token`` always rejects.
    """
    if not expected_token:
        return False
    if not auth_header or not auth_header.startswith(_BEARER_PREFIX):
        return False
    provided = auth_header[len(_BEARER_PREFIX):].strip()
    return hmac.compare_digest(provided, expected_token)
