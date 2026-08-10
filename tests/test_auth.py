"""Pure unit tests for health_hub.api.auth — no async, no DB."""
import hashlib
import hmac

from health_hub.api.auth import verify_signature, verify_token


def _sign(body: bytes, secret: str) -> str:
    digest = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


# --- verify_signature -------------------------------------------------------


def test_signature_match():
    body = b'{"hello": "world"}'
    secret = "topsecret"
    assert verify_signature(body, _sign(body, secret), secret) is True


def test_signature_mismatch_wrong_body():
    body = b'{"hello": "world"}'
    secret = "topsecret"
    # Signature computed over a different body.
    assert verify_signature(body, _sign(b"other body", secret), secret) is False


def test_signature_mismatch_wrong_secret():
    body = b'{"hello": "world"}'
    assert verify_signature(body, _sign(body, "other-secret"), "topsecret") is False


def test_signature_disabled_when_secret_empty():
    body = b'{"hello": "world"}'
    assert verify_signature(body, None, "") is True
    assert verify_signature(body, "sha256=abc", "") is True
    assert verify_signature(body, "garbage", "") is True


def test_signature_missing_header():
    body = b'{"hello": "world"}'
    secret = "topsecret"
    assert verify_signature(body, None, secret) is False
    assert verify_signature(body, "", secret) is False


def test_signature_malformed_header():
    body = b'{"hello": "world"}'
    secret = "topsecret"
    # No sha256= prefix.
    assert verify_signature(body, "deadbeef", secret) is False
    # Wrong algorithm.
    assert verify_signature(body, "md5=abc", secret) is False
    # Empty hex after prefix.
    assert verify_signature(body, "sha256=", secret) is False
    # Totally unrelated.
    assert verify_signature(body, "not-a-signature", secret) is False


def test_signature_case_insensitive_hex():
    """Hex digest is lowercased before comparison, so both cases match."""
    body = b'{"hello": "world"}'
    secret = "topsecret"
    digest = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    assert verify_signature(body, f"sha256={digest}", secret) is True
    assert verify_signature(body, f"sha256={digest.upper()}", secret) is True
    assert verify_signature(body, f"sha256={digest[:31].upper()}{digest[31:]}", secret) is True


# --- verify_token -----------------------------------------------------------


def test_token_match():
    assert verify_token("Bearer abc123", "abc123") is True


def test_token_mismatch():
    assert verify_token("Bearer wrong", "abc123") is False


def test_token_missing_header():
    assert verify_token(None, "abc123") is False
    assert verify_token("", "abc123") is False


def test_token_wrong_scheme():
    assert verify_token("Basic abc123", "abc123") is False
    # No scheme at all.
    assert verify_token("abc123", "abc123") is False
    # Wrong scheme name.
    assert verify_token("Token abc123", "abc123") is False


def test_token_empty_expected_rejects():
    """An empty expected_token must never authenticate."""
    assert verify_token("Bearer anything", "") is False
    assert verify_token("Bearer ", "") is False


def test_token_bearer_prefix_only_rejects():
    """'Bearer ' with no token value must not match an empty expected."""
    assert verify_token("Bearer ", "abc123") is False


def test_token_timing_safe_comparison():
    """Full-length token comparison uses compare_digest (no early return)."""
    expected = "a" * 64
    assert verify_token(f"Bearer {expected}", expected) is True
    assert verify_token(f"Bearer {'a' * 63}b", expected) is False
