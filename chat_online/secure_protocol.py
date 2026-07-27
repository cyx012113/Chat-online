"""Versioned E2EE wire-policy helpers.

The cryptographic primitives live in :mod:`chat_online.security`.  This module
binds their signed envelopes to Chat Online routing fields so the server can
validate and relay ciphertext without learning message text or file names.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import math
import re
import time
import uuid
from collections.abc import Iterable, Mapping
from typing import Any

from cryptography.hazmat.primitives.asymmetric import rsa

from .protocol import MAX_FILE_BYTES
from .security import (
    ENVELOPE_VERSION,
    FILE_ALGORITHM,
    MESSAGE_ALGORITHM,
    Identity,
    PeerPublicKeyPinStore,
    PinResult,
    encrypt_message,
    load_public_key,
    public_key_fingerprint,
    verify_envelope_signature,
)


PROTOCOL_VERSION = 7
SECURITY_VERSION = 1
MAX_MESSAGE_CHARS = 4000
MAX_ENCRYPTED_MESSAGE_BYTES = MAX_MESSAGE_CHARS * 4 + 16
MAX_ENCRYPTED_FILE_BYTES = MAX_FILE_BYTES + 16

IDENTITY_KEY_FIELD = "identity_key"
IDENTITY_FINGERPRINT_FIELD = "identity_fingerprint"
SENDER_KEY_FIELD = "sender_identity_key"
SENDER_FINGERPRINT_FIELD = "sender_identity_fingerprint"

_HEX_ID_RE = re.compile(r"^[0-9a-f]{32}$")
_FINGERPRINT_RE = re.compile(r"^[0-9a-f]{64}$")


class SecureProtocolError(ValueError):
    """A security-version or authenticated routing rule was violated."""


def identity_fields(identity: Identity, *, sender: bool = False) -> dict[str, str]:
    """Return JSON-ready public identity fields for a hello or sender packet."""

    key_field = SENDER_KEY_FIELD if sender else IDENTITY_KEY_FIELD
    fingerprint_field = (
        SENDER_FINGERPRINT_FIELD if sender else IDENTITY_FINGERPRINT_FIELD
    )
    return {
        key_field: identity.public_pem.decode("ascii"),
        fingerprint_field: identity.fingerprint,
    }


def load_identity_fields(
    packet: Mapping[str, Any], *, sender: bool = False
) -> tuple[rsa.RSAPublicKey, str]:
    """Load an RSA-3072 public key and verify its declared fingerprint."""

    key_field = SENDER_KEY_FIELD if sender else IDENTITY_KEY_FIELD
    fingerprint_field = (
        SENDER_FINGERPRINT_FIELD if sender else IDENTITY_FINGERPRINT_FIELD
    )
    encoded_key = packet.get(key_field)
    declared = packet.get(fingerprint_field)
    if not isinstance(encoded_key, str) or not encoded_key.strip():
        raise SecureProtocolError(f"missing {key_field}")
    if not isinstance(declared, str) or not _FINGERPRINT_RE.fullmatch(
        declared.lower()
    ):
        raise SecureProtocolError(f"invalid {fingerprint_field}")
    public_key = load_public_key(encoded_key)
    actual = public_key_fingerprint(public_key)
    if not hmac.compare_digest(actual, declared.lower()):
        raise SecureProtocolError("identity key fingerprint does not match")
    return public_key, actual


def pin_peer_identity(
    pin_store: PeerPublicKeyPinStore,
    peer_id: str,
    packet: Mapping[str, Any],
    *,
    sender: bool = False,
) -> tuple[rsa.RSAPublicKey, PinResult]:
    """Load and TOFU-pin a public identity under a stable username."""

    public_key, _fingerprint = load_identity_fields(packet, sender=sender)
    return public_key, pin_store.verify(peer_id, public_key)


def message_envelope(
    identity: Identity,
    recipients: Iterable[rsa.RSAPublicKey],
    text: str,
    *,
    scope: str,
    room_id: str | None = None,
    target: str | None = None,
    message_id: str | None = None,
    timestamp: float | None = None,
) -> dict[str, Any]:
    """Encrypt text and bind it to its room or private routing destination."""

    plaintext = str(text)
    if not plaintext or len(plaintext) > MAX_MESSAGE_CHARS:
        raise SecureProtocolError(
            f"message must contain 1-{MAX_MESSAGE_CHARS} characters"
        )
    normalized_id = (message_id or uuid.uuid4().hex).lower()
    if not _HEX_ID_RE.fullmatch(normalized_id):
        raise SecureProtocolError("message id must be 32 lowercase hex characters")
    created_at = time.time() if timestamp is None else float(timestamp)
    if not math.isfinite(created_at):
        raise SecureProtocolError("message timestamp must be finite")
    metadata: dict[str, Any] = {
        "id": normalized_id,
        "scope": scope,
        "timestamp": created_at,
    }
    if scope == "room":
        normalized_room = str(room_id or "").strip()
        if not normalized_room:
            raise SecureProtocolError("room messages require a room id")
        metadata["room_id"] = normalized_room
    elif scope == "private":
        normalized_target = str(target or "").strip().casefold()
        if not normalized_target:
            raise SecureProtocolError("private messages require a target")
        metadata["to"] = normalized_target
    else:
        raise SecureProtocolError("message scope must be room or private")
    return encrypt_message(
        plaintext,
        identity.private_key,
        recipients,
        metadata=metadata,
    )


def validate_message_envelope(
    envelope: Mapping[str, Any],
    sender_public_key: rsa.RSAPublicKey,
    *,
    scope: str,
    room_id: str | None = None,
    target: str | None = None,
) -> dict[str, Any]:
    """Verify a signed message and return its authenticated routing metadata."""

    unsigned = verify_envelope_signature(envelope, sender_public_key)
    if (
        unsigned.get("version") != ENVELOPE_VERSION
        or unsigned.get("kind") != "message"
        or unsigned.get("algorithm") != MESSAGE_ALGORITHM
        or unsigned.get("content_type") != "text/utf-8"
    ):
        raise SecureProtocolError("unsupported encrypted message envelope")
    metadata = unsigned.get("metadata")
    if not isinstance(metadata, dict):
        raise SecureProtocolError("message routing metadata is missing")
    message_id = metadata.get("id")
    if not isinstance(message_id, str) or not _HEX_ID_RE.fullmatch(message_id):
        raise SecureProtocolError("invalid encrypted message id")
    created_at = metadata.get("timestamp")
    if (
        isinstance(created_at, bool)
        or not isinstance(created_at, (int, float))
        or not math.isfinite(float(created_at))
    ):
        raise SecureProtocolError("invalid encrypted message timestamp")
    if metadata.get("scope") != scope:
        raise SecureProtocolError("encrypted message scope does not match routing")
    if scope == "room":
        if metadata.get("room_id") != str(room_id or ""):
            raise SecureProtocolError("encrypted message room does not match routing")
    elif scope == "private":
        expected_target = str(target or "").strip().casefold()
        actual_target = metadata.get("to")
        if not isinstance(actual_target, str) or actual_target.casefold() != expected_target:
            raise SecureProtocolError("encrypted message target does not match routing")
    else:
        raise SecureProtocolError("message scope must be room or private")
    ciphertext = _decode_base64(unsigned.get("ciphertext"), "message ciphertext")
    if not 16 <= len(ciphertext) <= MAX_ENCRYPTED_MESSAGE_BYTES:
        raise SecureProtocolError("encrypted message exceeds the size limit")
    return dict(metadata)


def validate_file_envelope(
    envelope: Mapping[str, Any],
    sender_public_key: rsa.RSAPublicKey,
    *,
    room_id: str,
    ciphertext: bytes | None = None,
) -> dict[str, Any]:
    """Verify a file envelope and its authenticated room binding."""

    unsigned = verify_envelope_signature(envelope, sender_public_key)
    if (
        unsigned.get("version") != ENVELOPE_VERSION
        or unsigned.get("kind") != "file"
        or unsigned.get("algorithm") != FILE_ALGORITHM
    ):
        raise SecureProtocolError("unsupported encrypted file envelope")
    file_id = unsigned.get("file_id")
    if not isinstance(file_id, str) or not _HEX_ID_RE.fullmatch(file_id):
        raise SecureProtocolError("invalid encrypted file id")
    metadata = unsigned.get("metadata")
    if not isinstance(metadata, dict) or metadata.get("room_id") != room_id:
        raise SecureProtocolError("encrypted file room does not match routing")
    created_at = metadata.get("timestamp")
    if (
        isinstance(created_at, bool)
        or not isinstance(created_at, (int, float))
        or not math.isfinite(float(created_at))
    ):
        raise SecureProtocolError("invalid encrypted file timestamp")
    encrypted_size = unsigned.get("ciphertext_size")
    if (
        isinstance(encrypted_size, bool)
        or not isinstance(encrypted_size, int)
        or not 16 <= encrypted_size <= MAX_ENCRYPTED_FILE_BYTES
    ):
        raise SecureProtocolError("encrypted file exceeds the size limit")
    digest = unsigned.get("ciphertext_sha256")
    if not isinstance(digest, str) or not _FINGERPRINT_RE.fullmatch(digest.lower()):
        raise SecureProtocolError("encrypted file digest is invalid")
    if ciphertext is not None:
        payload = bytes(ciphertext)
        if len(payload) != encrypted_size:
            raise SecureProtocolError("encrypted file size does not match its envelope")
        actual = hashlib.sha256(payload).hexdigest()
        if not hmac.compare_digest(actual, digest.lower()):
            raise SecureProtocolError("encrypted file digest does not match its envelope")
    return dict(unsigned)


def recipient_fingerprints(envelope: Mapping[str, Any]) -> set[str]:
    """Return validated recipient fingerprints from a signed envelope."""

    recipients = envelope.get("recipients")
    if not isinstance(recipients, dict) or not recipients:
        raise SecureProtocolError("encrypted envelope has no recipients")
    result: set[str] = set()
    for fingerprint, wrapped_key in recipients.items():
        if (
            not isinstance(fingerprint, str)
            or not _FINGERPRINT_RE.fullmatch(fingerprint.lower())
            or not isinstance(wrapped_key, str)
        ):
            raise SecureProtocolError("encrypted envelope has invalid recipients")
        decoded = _decode_base64(wrapped_key, "wrapped recipient key")
        if len(decoded) < 256:
            raise SecureProtocolError("wrapped recipient key is too short")
        result.add(fingerprint.lower())
    return result


def require_exact_recipients(
    envelope: Mapping[str, Any], expected_fingerprints: Iterable[str]
) -> None:
    """Reject stale, missing, or additional recipients for a routed envelope."""

    expected = {str(value).lower() for value in expected_fingerprints}
    actual = recipient_fingerprints(envelope)
    if actual != expected:
        raise SecureProtocolError(
            "encrypted recipients are stale; refresh members and try again"
        )


def encode_file_ciphertext(ciphertext: bytes) -> str:
    payload = bytes(ciphertext)
    if not 16 <= len(payload) <= MAX_ENCRYPTED_FILE_BYTES:
        raise SecureProtocolError("encrypted file exceeds the size limit")
    return base64.b64encode(payload).decode("ascii")


def decode_file_ciphertext(value: Any) -> bytes:
    payload = _decode_base64(value, "encrypted file data")
    if not 16 <= len(payload) <= MAX_ENCRYPTED_FILE_BYTES:
        raise SecureProtocolError("encrypted file exceeds the size limit")
    return payload


def _decode_base64(value: Any, label: str) -> bytes:
    if not isinstance(value, str):
        raise SecureProtocolError(f"{label} must be base64 text")
    try:
        return base64.b64decode(value.encode("ascii"), validate=True)
    except (UnicodeEncodeError, ValueError, binascii.Error) as exc:
        raise SecureProtocolError(f"{label} is invalid base64") from exc


__all__ = [
    "IDENTITY_FINGERPRINT_FIELD",
    "IDENTITY_KEY_FIELD",
    "MAX_ENCRYPTED_FILE_BYTES",
    "MAX_ENCRYPTED_MESSAGE_BYTES",
    "MAX_MESSAGE_CHARS",
    "PROTOCOL_VERSION",
    "SECURITY_VERSION",
    "SENDER_FINGERPRINT_FIELD",
    "SENDER_KEY_FIELD",
    "SecureProtocolError",
    "decode_file_ciphertext",
    "encode_file_ciphertext",
    "identity_fields",
    "load_identity_fields",
    "message_envelope",
    "pin_peer_identity",
    "recipient_fingerprints",
    "require_exact_recipients",
    "validate_file_envelope",
    "validate_message_envelope",
]
