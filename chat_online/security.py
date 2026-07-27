"""Cryptographic building blocks for Chat Online.

This module deliberately contains policy and serialization around established
``cryptography`` primitives.  It does not implement any cryptographic primitive
itself.  Versioned envelopes make the authenticated wire format explicit and
allow a future protocol migration without silently weakening existing traffic.
"""

from __future__ import annotations

import base64
import ctypes
import hashlib
import hmac
import ipaddress
import json
import os
import secrets
import ssl
import sys
import tempfile
import threading
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Literal

from cryptography import x509
from cryptography.exceptions import InvalidSignature, InvalidTag
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID


RSA_KEY_SIZE = 3072
AES_KEY_SIZE = 32
NONCE_SIZE = 12
ENVELOPE_VERSION = 1
MESSAGE_ALGORITHM = "RSA-OAEP-SHA256+AES-256-GCM+RSA-PSS-SHA256"
FILE_ALGORITHM = MESSAGE_ALGORITHM
TLS_ALPN_PROTOCOL = "chat-online/1"

_OAEP_LABEL = b"chat-online-key-wrap-v1"
_DPAPI_ENTROPY = b"Chat Online identity key v1"
_DPAPI_PREFIX = b"CHAT-ONLINE-DPAPI-V1\n"


class SecurityError(Exception):
    """Base class for security and serialization failures."""


class KeyValidationError(SecurityError):
    """A key is malformed, unsupported, or below the security policy."""


class KeyProtectionError(SecurityError):
    """A private key could not be protected or recovered."""


class EnvelopeError(SecurityError):
    """An encrypted envelope is malformed or cannot be decrypted."""


class VerificationError(EnvelopeError):
    """A signature or authenticated ciphertext failed verification."""


class RecipientError(EnvelopeError):
    """The supplied identity is not a recipient of an envelope."""


class PinStoreError(SecurityError):
    """A trust-on-first-use pin store is invalid or cannot be updated."""


class PinMismatchError(PinStoreError):
    """Previously trusted key or certificate material has changed."""

    def __init__(self, peer_id: str, expected: str, actual: str) -> None:
        self.peer_id = peer_id
        self.expected = expected
        self.actual = actual
        super().__init__(
            f"security pin for {peer_id!r} changed "
            f"(expected {expected}, received {actual})"
        )


def canonical_json(value: Any) -> bytes:
    """Return deterministic UTF-8 JSON used for signatures and AEAD AAD.

    NaN and Infinity are rejected because they are not JSON values.  Dictionary
    keys must be strings so independently implemented peers cannot disagree on
    key coercion.
    """

    def validate(item: Any) -> None:
        if isinstance(item, Mapping):
            for key, child in item.items():
                if not isinstance(key, str):
                    raise TypeError("canonical JSON object keys must be strings")
                validate(child)
        elif isinstance(item, (list, tuple)):
            for child in item:
                validate(child)

    validate(value)
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _json_object(value: Mapping[str, Any] | None) -> dict[str, Any]:
    if value is None:
        result: dict[str, Any] = {}
    elif isinstance(value, Mapping):
        result = dict(value)
    else:
        raise EnvelopeError("metadata must be a JSON object")
    try:
        encoded = canonical_json(result)
        decoded = json.loads(encoded)
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise EnvelopeError("metadata must be a valid JSON object") from exc
    if not isinstance(decoded, dict):
        raise EnvelopeError("metadata must be a JSON object")
    return decoded


def default_client_security_dir(*, create: bool = True) -> Path:
    """Return the platform data directory used for client keys and pins.

    ``CHAT_ONLINE_DATA_DIR`` follows the same override used by ``AppStorage``.
    Keeping this helper independent avoids creating message and attachment
    directories just to initialize transport security.
    """

    configured = os.environ.get("CHAT_ONLINE_DATA_DIR", "").strip()
    if configured:
        data_root = Path(configured).expanduser()
    elif sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
        data_root = (
            Path(base) / "Chat Online"
            if base
            else Path.home() / "AppData" / "Local" / "Chat Online"
        )
    elif sys.platform == "darwin":
        data_root = Path.home() / "Library" / "Application Support" / "Chat Online"
    else:
        xdg_data_home = os.environ.get("XDG_DATA_HOME", "").strip()
        base = (
            Path(xdg_data_home).expanduser()
            if xdg_data_home
            else Path.home() / ".local" / "share"
        )
        data_root = base / "chat-online"
    security_dir = data_root / "security"
    if create:
        try:
            security_dir.mkdir(parents=True, exist_ok=True)
            os.chmod(security_dir, 0o700)
        except OSError as exc:
            raise SecurityError(
                f"could not create client security directory {security_dir}"
            ) from exc
    return security_dir.resolve(strict=False)


def _b64encode(value: bytes) -> str:
    return base64.b64encode(value).decode("ascii")


def _b64decode(value: Any, field: str) -> bytes:
    if not isinstance(value, str):
        raise EnvelopeError(f"{field} must be base64 text")
    try:
        return base64.b64decode(value.encode("ascii"), validate=True)
    except (UnicodeEncodeError, ValueError) as exc:
        raise EnvelopeError(f"{field} is not valid base64") from exc


def _atomic_write(path: Path, data: bytes, mode: int = 0o600) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb", dir=path.parent, prefix=f".{path.name}.", delete=False
        ) as output:
            temporary_name = output.name
            output.write(data)
            output.flush()
            os.fsync(output.fileno())
        os.chmod(temporary_name, mode)
        os.replace(temporary_name, path)
        os.chmod(path, mode)
    except OSError as exc:
        if temporary_name:
            try:
                os.unlink(temporary_name)
            except OSError:
                pass
        raise SecurityError(f"could not write {path}") from exc


def validate_public_key(
    public_key: rsa.RSAPublicKey, *, min_key_size: int = RSA_KEY_SIZE
) -> rsa.RSAPublicKey:
    """Validate and return an RSA public key accepted by the identity policy."""

    if not isinstance(public_key, rsa.RSAPublicKey):
        raise KeyValidationError("an RSA public key is required")
    if public_key.key_size < min_key_size:
        raise KeyValidationError(
            f"RSA key must be at least {min_key_size} bits, got {public_key.key_size}"
        )
    if public_key.public_numbers().e != 65537:
        raise KeyValidationError("RSA public exponent must be 65537")
    return public_key


def validate_private_key(
    private_key: rsa.RSAPrivateKey, *, min_key_size: int = RSA_KEY_SIZE
) -> rsa.RSAPrivateKey:
    """Validate and return an RSA private key accepted by the identity policy."""

    if not isinstance(private_key, rsa.RSAPrivateKey):
        raise KeyValidationError("an RSA private key is required")
    validate_public_key(private_key.public_key(), min_key_size=min_key_size)
    return private_key


def generate_identity_private_key() -> rsa.RSAPrivateKey:
    """Generate a new RSA-3072 identity private key."""

    return rsa.generate_private_key(public_exponent=65537, key_size=RSA_KEY_SIZE)


def serialize_public_key(public_key: rsa.RSAPublicKey) -> bytes:
    public_key = validate_public_key(public_key)
    return public_key.public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )


def load_public_key(data: bytes | str) -> rsa.RSAPublicKey:
    """Load and validate a PEM or DER RSA identity public key."""

    raw = data.encode("utf-8") if isinstance(data, str) else bytes(data)
    try:
        if raw.lstrip().startswith(b"-----BEGIN"):
            key = serialization.load_pem_public_key(raw)
        else:
            key = serialization.load_der_public_key(raw)
    except (TypeError, ValueError) as exc:
        raise KeyValidationError("invalid public key encoding") from exc
    return validate_public_key(key)  # type: ignore[arg-type]


def public_key_fingerprint(public_key: rsa.RSAPublicKey | bytes | str) -> str:
    """Return the lowercase SHA-256 fingerprint of an identity public key."""

    key = load_public_key(public_key) if isinstance(public_key, (bytes, str)) else validate_public_key(public_key)
    der = key.public_bytes(
        serialization.Encoding.DER,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    return hashlib.sha256(der).hexdigest()


def format_fingerprint(fingerprint: str) -> str:
    """Format a 64-character SHA-256 fingerprint for human comparison."""

    normalized = _validate_fingerprint(fingerprint)
    return ":".join(normalized[index : index + 2] for index in range(0, 64, 2))


def sign_data(private_key: rsa.RSAPrivateKey, data: bytes) -> bytes:
    """Sign bytes with RSA-PSS and SHA-256."""

    key = validate_private_key(private_key)
    return key.sign(
        bytes(data),
        padding.PSS(
            mgf=padding.MGF1(hashes.SHA256()),
            salt_length=hashes.SHA256().digest_size,
        ),
        hashes.SHA256(),
    )


def verify_signature(
    public_key: rsa.RSAPublicKey, signature: bytes, data: bytes
) -> None:
    """Verify an RSA-PSS/SHA-256 signature or raise VerificationError."""

    key = validate_public_key(public_key)
    try:
        key.verify(
            bytes(signature),
            bytes(data),
            padding.PSS(
                mgf=padding.MGF1(hashes.SHA256()),
                salt_length=hashes.SHA256().digest_size,
            ),
            hashes.SHA256(),
        )
    except InvalidSignature as exc:
        raise VerificationError("signature verification failed") from exc


def _wrap_key(public_key: rsa.RSAPublicKey, key: bytes) -> bytes:
    return validate_public_key(public_key).encrypt(
        key,
        padding.OAEP(
            mgf=padding.MGF1(algorithm=hashes.SHA256()),
            algorithm=hashes.SHA256(),
            label=_OAEP_LABEL,
        ),
    )


def _unwrap_key(private_key: rsa.RSAPrivateKey, wrapped_key: bytes) -> bytes:
    try:
        key = validate_private_key(private_key).decrypt(
            wrapped_key,
            padding.OAEP(
                mgf=padding.MGF1(algorithm=hashes.SHA256()),
                algorithm=hashes.SHA256(),
                label=_OAEP_LABEL,
            ),
        )
    except ValueError as exc:
        raise RecipientError("recipient key could not unwrap the content key") from exc
    if len(key) != AES_KEY_SIZE:
        raise EnvelopeError("unwrapped content key has an invalid length")
    return key


def _recipient_map(
    recipient_public_keys: Iterable[rsa.RSAPublicKey] | rsa.RSAPublicKey,
    content_key: bytes,
) -> dict[str, str]:
    if isinstance(recipient_public_keys, rsa.RSAPublicKey):
        keys: Iterable[rsa.RSAPublicKey] = (recipient_public_keys,)
    else:
        keys = recipient_public_keys
    recipients: dict[str, str] = {}
    for public_key in keys:
        validated = validate_public_key(public_key)
        fingerprint = public_key_fingerprint(validated)
        recipients[fingerprint] = _b64encode(_wrap_key(validated, content_key))
    if not recipients:
        raise EnvelopeError("at least one recipient is required")
    return dict(sorted(recipients.items()))


def _recipient_content_key(
    envelope: Mapping[str, Any], private_key: rsa.RSAPrivateKey
) -> bytes:
    recipients = envelope.get("recipients")
    if not isinstance(recipients, dict):
        raise EnvelopeError("envelope recipients must be an object")
    recipient_id = public_key_fingerprint(validate_private_key(private_key).public_key())
    wrapped = recipients.get(recipient_id)
    if wrapped is None:
        raise RecipientError("this identity is not an envelope recipient")
    return _unwrap_key(private_key, _b64decode(wrapped, "wrapped recipient key"))


def _verify_envelope_signature(
    envelope: Mapping[str, Any], sender_public_key: rsa.RSAPublicKey
) -> dict[str, Any]:
    if not isinstance(envelope, Mapping):
        raise EnvelopeError("envelope must be an object")
    unsigned = dict(envelope)
    signature = _b64decode(unsigned.pop("signature", None), "signature")
    expected_sender = public_key_fingerprint(sender_public_key)
    actual_sender = unsigned.get("sender")
    if not isinstance(actual_sender, str) or not hmac.compare_digest(
        actual_sender, expected_sender
    ):
        raise VerificationError("envelope sender does not match the supplied public key")
    try:
        signed_data = canonical_json(unsigned)
    except (TypeError, ValueError) as exc:
        raise EnvelopeError("envelope is not canonical JSON data") from exc
    verify_signature(sender_public_key, signature, signed_data)
    return unsigned


def verify_envelope_signature(
    envelope: Mapping[str, Any], sender_public_key: rsa.RSAPublicKey
) -> dict[str, Any]:
    """Verify an envelope without decrypting it and return unsigned fields.

    This is intended for a relay server that must authenticate routing metadata
    while remaining unable to read end-to-end encrypted content.  Callers must
    still validate the returned envelope ``kind``, ``version`` and routing
    fields against their application policy.
    """

    return _verify_envelope_signature(envelope, sender_public_key)


def _signed_envelope(
    unsigned: Mapping[str, Any], sender_private_key: rsa.RSAPrivateKey
) -> dict[str, Any]:
    result = dict(unsigned)
    result["signature"] = _b64encode(
        sign_data(sender_private_key, canonical_json(result))
    )
    return result


def encrypt_message(
    plaintext: str | bytes,
    sender_private_key: rsa.RSAPrivateKey,
    recipient_public_keys: Iterable[rsa.RSAPublicKey] | rsa.RSAPublicKey,
    *,
    metadata: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Encrypt and sign a message for one or more RSA identity recipients.

    Include the sender's public key in ``recipient_public_keys`` when the sender
    must also be able to decrypt their own server-side history.
    """

    sender_key = validate_private_key(sender_private_key)
    if isinstance(plaintext, str):
        content = plaintext.encode("utf-8")
        content_type = "text/utf-8"
    else:
        content = bytes(plaintext)
        content_type = "application/octet-stream"
    content_key = AESGCM.generate_key(bit_length=256)
    nonce = os.urandom(NONCE_SIZE)
    header: dict[str, Any] = {
        "version": ENVELOPE_VERSION,
        "kind": "message",
        "algorithm": MESSAGE_ALGORITHM,
        "sender": public_key_fingerprint(sender_key.public_key()),
        "recipients": _recipient_map(recipient_public_keys, content_key),
        "nonce": _b64encode(nonce),
        "content_type": content_type,
        "metadata": _json_object(metadata),
    }
    ciphertext = AESGCM(content_key).encrypt(nonce, content, canonical_json(header))
    return _signed_envelope(
        {**header, "ciphertext": _b64encode(ciphertext)}, sender_key
    )


def decrypt_message(
    envelope: Mapping[str, Any],
    recipient_private_key: rsa.RSAPrivateKey,
    sender_public_key: rsa.RSAPublicKey,
) -> bytes:
    """Verify and decrypt a message envelope, returning plaintext bytes."""

    unsigned = _verify_envelope_signature(envelope, sender_public_key)
    if (
        unsigned.get("version") != ENVELOPE_VERSION
        or unsigned.get("kind") != "message"
        or unsigned.get("algorithm") != MESSAGE_ALGORITHM
    ):
        raise EnvelopeError("unsupported message envelope")
    content_key = _recipient_content_key(unsigned, recipient_private_key)
    nonce = _b64decode(unsigned.get("nonce"), "nonce")
    if len(nonce) != NONCE_SIZE:
        raise EnvelopeError("message nonce has an invalid length")
    ciphertext = _b64decode(unsigned.get("ciphertext"), "ciphertext")
    header = dict(unsigned)
    header.pop("ciphertext", None)
    try:
        return AESGCM(content_key).decrypt(nonce, ciphertext, canonical_json(header))
    except InvalidTag as exc:
        raise VerificationError("message authentication failed") from exc


def decrypt_message_text(
    envelope: Mapping[str, Any],
    recipient_private_key: rsa.RSAPrivateKey,
    sender_public_key: rsa.RSAPublicKey,
) -> str:
    """Verify and decrypt a UTF-8 message envelope."""

    if envelope.get("content_type") != "text/utf-8":
        raise EnvelopeError("message does not declare UTF-8 text content")
    plaintext = decrypt_message(envelope, recipient_private_key, sender_public_key)
    try:
        return plaintext.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise EnvelopeError("decrypted message is not valid UTF-8") from exc


def message_metadata(envelope: Mapping[str, Any]) -> dict[str, Any]:
    """Return a defensive copy of authenticated, but unencrypted, metadata."""

    value = envelope.get("metadata")
    if not isinstance(value, Mapping):
        raise EnvelopeError("message metadata must be an object")
    return _json_object(value)


def _safe_filename(filename: str) -> str:
    if not isinstance(filename, str):
        raise EnvelopeError("filename must be text")
    if (
        not filename
        or filename in {".", ".."}
        or "\x00" in filename
        or "/" in filename
        or "\\" in filename
        or len(filename) > 255
    ):
        raise EnvelopeError("filename must be a plain basename")
    return filename


@dataclass(frozen=True)
class EncryptedFile:
    """Wire-ready encrypted file bytes plus its signed envelope."""

    ciphertext: bytes
    envelope: dict[str, Any]


@dataclass(frozen=True)
class DecryptedFileManifest:
    """Verified private file metadata, available before downloading content."""

    filename: str
    media_type: str
    size: int
    sha256: str
    metadata: dict[str, Any]
    authenticated_metadata: dict[str, Any]


@dataclass(frozen=True)
class DecryptedFile:
    """A verified file and its formerly encrypted manifest fields."""

    data: bytes
    filename: str
    media_type: str
    metadata: dict[str, Any]
    authenticated_metadata: dict[str, Any]
    sha256: str


def encrypt_file(
    data: bytes,
    filename: str,
    sender_private_key: rsa.RSAPrivateKey,
    recipient_public_keys: Iterable[rsa.RSAPublicKey] | rsa.RSAPublicKey,
    *,
    media_type: str = "application/octet-stream",
    metadata: Mapping[str, Any] | None = None,
    authenticated_metadata: Mapping[str, Any] | None = None,
) -> EncryptedFile:
    """Encrypt file content and its private filename/manifest independently.

    ``metadata`` is encrypted with the filename.  ``authenticated_metadata`` is
    visible to the relay (for example room id and timestamp) but is covered by
    both the sender signature and AES-GCM associated data.
    """

    content = bytes(data)
    safe_filename = _safe_filename(filename)
    if not isinstance(media_type, str) or not media_type or len(media_type) > 255:
        raise EnvelopeError("media_type must be non-empty text")
    sender_key = validate_private_key(sender_private_key)
    content_key = AESGCM.generate_key(bit_length=256)
    data_nonce = os.urandom(NONCE_SIZE)
    manifest_nonce = os.urandom(NONCE_SIZE)
    header: dict[str, Any] = {
        "version": ENVELOPE_VERSION,
        "kind": "file",
        "algorithm": FILE_ALGORITHM,
        "sender": public_key_fingerprint(sender_key.public_key()),
        "recipients": _recipient_map(recipient_public_keys, content_key),
        "file_id": secrets.token_hex(16),
        "data_nonce": _b64encode(data_nonce),
        "manifest_nonce": _b64encode(manifest_nonce),
        "metadata": _json_object(authenticated_metadata),
    }
    manifest = {
        "filename": safe_filename,
        "media_type": media_type,
        "size": len(content),
        "sha256": hashlib.sha256(content).hexdigest(),
        "metadata": _json_object(metadata),
    }
    data_aad = canonical_json({"header": header, "component": "data"})
    manifest_aad = canonical_json({"header": header, "component": "manifest"})
    aes = AESGCM(content_key)
    ciphertext = aes.encrypt(data_nonce, content, data_aad)
    encrypted_manifest = aes.encrypt(
        manifest_nonce, canonical_json(manifest), manifest_aad
    )
    unsigned = {
        **header,
        "ciphertext_size": len(ciphertext),
        "ciphertext_sha256": hashlib.sha256(ciphertext).hexdigest(),
        "encrypted_manifest": _b64encode(encrypted_manifest),
    }
    return EncryptedFile(
        ciphertext=ciphertext,
        envelope=_signed_envelope(unsigned, sender_key),
    )


_FILE_HEADER_FIELDS = (
    "version",
    "kind",
    "algorithm",
    "sender",
    "recipients",
    "file_id",
    "data_nonce",
    "manifest_nonce",
    "metadata",
)


def _decrypt_file_manifest_and_key(
    envelope: Mapping[str, Any],
    recipient_private_key: rsa.RSAPrivateKey,
    sender_public_key: rsa.RSAPublicKey,
) -> tuple[dict[str, Any], bytes, dict[str, Any], DecryptedFileManifest]:
    unsigned = _verify_envelope_signature(envelope, sender_public_key)
    if (
        unsigned.get("version") != ENVELOPE_VERSION
        or unsigned.get("kind") != "file"
        or unsigned.get("algorithm") != FILE_ALGORITHM
    ):
        raise EnvelopeError("unsupported file envelope")
    file_id = unsigned.get("file_id")
    if (
        not isinstance(file_id, str)
        or len(file_id) != 32
        or any(character not in "0123456789abcdef" for character in file_id)
    ):
        raise EnvelopeError("file envelope id is invalid")
    ciphertext_size = unsigned.get("ciphertext_size")
    ciphertext_hash = unsigned.get("ciphertext_sha256")
    if (
        not isinstance(ciphertext_size, int)
        or isinstance(ciphertext_size, bool)
        or ciphertext_size < 16
        or not isinstance(ciphertext_hash, str)
        or len(ciphertext_hash) != 64
        or any(character not in "0123456789abcdef" for character in ciphertext_hash)
    ):
        raise EnvelopeError("file ciphertext properties are invalid")

    content_key = _recipient_content_key(unsigned, recipient_private_key)
    data_nonce = _b64decode(unsigned.get("data_nonce"), "data_nonce")
    manifest_nonce = _b64decode(unsigned.get("manifest_nonce"), "manifest_nonce")
    if len(data_nonce) != NONCE_SIZE or len(manifest_nonce) != NONCE_SIZE:
        raise EnvelopeError("file envelope nonce has an invalid length")
    header = {field: unsigned.get(field) for field in _FILE_HEADER_FIELDS}
    try:
        manifest_bytes = AESGCM(content_key).decrypt(
            manifest_nonce,
            _b64decode(unsigned.get("encrypted_manifest"), "encrypted_manifest"),
            canonical_json({"header": header, "component": "manifest"}),
        )
    except InvalidTag as exc:
        raise VerificationError("file manifest authentication failed") from exc
    try:
        manifest = json.loads(manifest_bytes)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise EnvelopeError("decrypted file manifest is invalid JSON") from exc
    if not isinstance(manifest, dict):
        raise EnvelopeError("decrypted file manifest must be an object")
    filename = _safe_filename(manifest.get("filename"))
    media_type = manifest.get("media_type")
    private_metadata = manifest.get("metadata")
    size = manifest.get("size")
    plaintext_hash = manifest.get("sha256")
    if (
        not isinstance(media_type, str)
        or not media_type
        or len(media_type) > 255
        or not isinstance(private_metadata, dict)
        or not isinstance(size, int)
        or isinstance(size, bool)
        or size < 0
        or not isinstance(plaintext_hash, str)
        or len(plaintext_hash) != 64
        or any(
            character not in "0123456789abcdef" for character in plaintext_hash
        )
    ):
        raise EnvelopeError("decrypted file manifest fields are invalid")
    decrypted_manifest = DecryptedFileManifest(
        filename=filename,
        media_type=media_type,
        size=size,
        sha256=plaintext_hash,
        metadata=_json_object(private_metadata),
        authenticated_metadata=_json_object(unsigned.get("metadata")),
    )
    return unsigned, content_key, header, decrypted_manifest


def decrypt_file_manifest(
    envelope: Mapping[str, Any],
    recipient_private_key: rsa.RSAPrivateKey,
    sender_public_key: rsa.RSAPublicKey,
) -> DecryptedFileManifest:
    """Verify and decrypt a file manifest without downloading file content."""

    return _decrypt_file_manifest_and_key(
        envelope, recipient_private_key, sender_public_key
    )[3]


def decrypt_file(
    ciphertext: bytes,
    envelope: Mapping[str, Any],
    recipient_private_key: rsa.RSAPrivateKey,
    sender_public_key: rsa.RSAPublicKey,
) -> DecryptedFile:
    """Verify and decrypt file bytes and their encrypted manifest."""

    encrypted_data = bytes(ciphertext)
    unsigned, content_key, header, manifest = _decrypt_file_manifest_and_key(
        envelope, recipient_private_key, sender_public_key
    )
    if unsigned.get("ciphertext_size") != len(encrypted_data):
        raise VerificationError("encrypted file size does not match its envelope")
    actual_ciphertext_hash = hashlib.sha256(encrypted_data).hexdigest()
    expected_ciphertext_hash = unsigned.get("ciphertext_sha256")
    if not isinstance(expected_ciphertext_hash, str) or not hmac.compare_digest(
        actual_ciphertext_hash, expected_ciphertext_hash
    ):
        raise VerificationError("encrypted file digest does not match its envelope")

    data_nonce = _b64decode(unsigned.get("data_nonce"), "data_nonce")
    try:
        plaintext = AESGCM(content_key).decrypt(
            data_nonce,
            encrypted_data,
            canonical_json({"header": header, "component": "data"}),
        )
    except InvalidTag as exc:
        raise VerificationError("file authentication failed") from exc
    if manifest.size != len(plaintext):
        raise VerificationError("decrypted file size does not match its manifest")
    actual_plaintext_hash = hashlib.sha256(plaintext).hexdigest()
    if not hmac.compare_digest(actual_plaintext_hash, manifest.sha256):
        raise VerificationError("decrypted file digest does not match its manifest")
    return DecryptedFile(
        data=plaintext,
        filename=manifest.filename,
        media_type=manifest.media_type,
        metadata=manifest.metadata,
        authenticated_metadata=manifest.authenticated_metadata,
        sha256=actual_plaintext_hash,
    )


def _protect_dpapi(data: bytes) -> bytes:
    if os.name != "nt":
        raise KeyProtectionError("Windows DPAPI is unavailable on this platform")
    from ctypes import wintypes

    class DataBlob(ctypes.Structure):
        _fields_ = [
            ("cbData", wintypes.DWORD),
            ("pbData", ctypes.POINTER(ctypes.c_ubyte)),
        ]

    def blob(value: bytes) -> tuple[DataBlob, ctypes.Array[Any]]:
        buffer = ctypes.create_string_buffer(value)
        return (
            DataBlob(len(value), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte))),
            buffer,
        )

    input_blob, input_buffer = blob(data)
    entropy_blob, entropy_buffer = blob(_DPAPI_ENTROPY)
    output_blob = DataBlob()
    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    crypt32.CryptProtectData.argtypes = [
        ctypes.POINTER(DataBlob),
        wintypes.LPCWSTR,
        ctypes.POINTER(DataBlob),
        ctypes.c_void_p,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(DataBlob),
    ]
    crypt32.CryptProtectData.restype = wintypes.BOOL
    if not crypt32.CryptProtectData(
        ctypes.byref(input_blob),
        "Chat Online identity",
        ctypes.byref(entropy_blob),
        None,
        None,
        0x1,
        ctypes.byref(output_blob),
    ):
        raise KeyProtectionError(f"DPAPI protection failed with error {ctypes.get_last_error()}")
    # Keep source buffers alive until CryptProtectData returns.
    _ = input_buffer, entropy_buffer
    try:
        return ctypes.string_at(output_blob.pbData, output_blob.cbData)
    finally:
        kernel32.LocalFree(output_blob.pbData)


def _unprotect_dpapi(data: bytes) -> bytes:
    if os.name != "nt":
        raise KeyProtectionError(
            "this identity is DPAPI-protected and can only be opened on Windows"
        )
    from ctypes import wintypes

    class DataBlob(ctypes.Structure):
        _fields_ = [
            ("cbData", wintypes.DWORD),
            ("pbData", ctypes.POINTER(ctypes.c_ubyte)),
        ]

    def blob(value: bytes) -> tuple[DataBlob, ctypes.Array[Any]]:
        buffer = ctypes.create_string_buffer(value)
        return (
            DataBlob(len(value), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte))),
            buffer,
        )

    input_blob, input_buffer = blob(data)
    entropy_blob, entropy_buffer = blob(_DPAPI_ENTROPY)
    output_blob = DataBlob()
    description = wintypes.LPWSTR()
    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    crypt32.CryptUnprotectData.argtypes = [
        ctypes.POINTER(DataBlob),
        ctypes.POINTER(wintypes.LPWSTR),
        ctypes.POINTER(DataBlob),
        ctypes.c_void_p,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(DataBlob),
    ]
    crypt32.CryptUnprotectData.restype = wintypes.BOOL
    if not crypt32.CryptUnprotectData(
        ctypes.byref(input_blob),
        ctypes.byref(description),
        ctypes.byref(entropy_blob),
        None,
        None,
        0x1,
        ctypes.byref(output_blob),
    ):
        raise KeyProtectionError(f"DPAPI recovery failed with error {ctypes.get_last_error()}")
    _ = input_buffer, entropy_buffer
    try:
        return ctypes.string_at(output_blob.pbData, output_blob.cbData)
    finally:
        if description:
            kernel32.LocalFree(description)
        kernel32.LocalFree(output_blob.pbData)


def save_private_key(
    private_key: rsa.RSAPrivateKey,
    path: str | os.PathLike[str],
    *,
    protection: Literal["auto", "dpapi", "pem"] = "auto",
    overwrite: bool = False,
) -> Path:
    """Persist an identity key using DPAPI on Windows or mode-0600 PEM.

    ``protection='pem'`` is intended for non-Windows fallback and test/export
    scenarios.  It stores unencrypted PKCS#8, so callers should prefer ``auto``.
    """

    key = validate_private_key(private_key)
    destination = Path(path)
    if destination.exists() and not overwrite:
        raise FileExistsError(destination)
    selected = "dpapi" if protection == "auto" and os.name == "nt" else protection
    if selected == "auto":
        selected = "pem"
    if selected == "dpapi":
        der = key.private_bytes(
            serialization.Encoding.DER,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
        encoded = _DPAPI_PREFIX + base64.b64encode(_protect_dpapi(der)) + b"\n"
    elif selected == "pem":
        encoded = key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    else:
        raise ValueError(f"unsupported private-key protection: {protection}")
    _atomic_write(destination, encoded, 0o600)
    return destination


def load_private_key(path: str | os.PathLike[str]) -> rsa.RSAPrivateKey:
    """Load a private identity saved by :func:`save_private_key`."""

    source = Path(path)
    try:
        encoded = source.read_bytes()
    except OSError as exc:
        raise KeyProtectionError(f"could not read identity key {source}") from exc
    try:
        if encoded.startswith(_DPAPI_PREFIX):
            protected = base64.b64decode(
                encoded[len(_DPAPI_PREFIX) :].strip(), validate=True
            )
            key = serialization.load_der_private_key(
                _unprotect_dpapi(protected), password=None
            )
        else:
            key = serialization.load_pem_private_key(encoded, password=None)
    except (TypeError, ValueError) as exc:
        raise KeyProtectionError("private identity key is invalid or unavailable") from exc
    return validate_private_key(key)  # type: ignore[arg-type]


@dataclass(frozen=True, repr=False)
class Identity:
    """Convenient owner for a validated RSA identity private key."""

    private_key: rsa.RSAPrivateKey

    def __post_init__(self) -> None:
        validate_private_key(self.private_key)

    @classmethod
    def generate(cls) -> "Identity":
        return cls(generate_identity_private_key())

    @classmethod
    def load(cls, path: str | os.PathLike[str]) -> "Identity":
        return cls(load_private_key(path))

    @classmethod
    def load_or_create(cls, path: str | os.PathLike[str]) -> "Identity":
        destination = Path(path)
        if destination.exists():
            return cls.load(destination)
        identity = cls.generate()
        identity.save(destination)
        return identity

    @property
    def public_key(self) -> rsa.RSAPublicKey:
        return self.private_key.public_key()

    @property
    def public_pem(self) -> bytes:
        return serialize_public_key(self.public_key)

    @property
    def fingerprint(self) -> str:
        return public_key_fingerprint(self.public_key)

    def save(
        self,
        path: str | os.PathLike[str],
        *,
        protection: Literal["auto", "dpapi", "pem"] = "auto",
        overwrite: bool = False,
    ) -> Path:
        return save_private_key(
            self.private_key, path, protection=protection, overwrite=overwrite
        )


def _validate_peer_id(peer_id: str) -> str:
    if not isinstance(peer_id, str) or not peer_id.strip() or len(peer_id) > 512:
        raise PinStoreError("peer id must be non-empty text")
    return peer_id.strip()


def _validate_fingerprint(value: str) -> str:
    normalized = value.lower().replace(":", "") if isinstance(value, str) else ""
    if len(normalized) != 64 or any(character not in "0123456789abcdef" for character in normalized):
        raise PinStoreError("pin must be a SHA-256 fingerprint")
    return normalized


@dataclass(frozen=True)
class PinResult:
    fingerprint: str
    first_seen: bool


class _PinStore:
    """Small fail-closed JSON pin store with atomic updates."""

    namespace = "material"

    def __init__(self, path: str | os.PathLike[str]) -> None:
        self.path = Path(path)
        self._lock = threading.RLock()

    def _load(self) -> dict[str, str]:
        if not self.path.exists():
            return {}
        try:
            document = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise PinStoreError(f"pin store {self.path} is unreadable or corrupt") from exc
        if (
            not isinstance(document, dict)
            or document.get("version") != 1
            or document.get("namespace") != self.namespace
            or not isinstance(document.get("pins"), dict)
        ):
            raise PinStoreError(f"pin store {self.path} has an unsupported format")
        result: dict[str, str] = {}
        for peer_id, fingerprint in document["pins"].items():
            result[_validate_peer_id(peer_id)] = _validate_fingerprint(fingerprint)
        return result

    def _save(self, pins: Mapping[str, str]) -> None:
        document = {
            "version": 1,
            "namespace": self.namespace,
            "pins": dict(sorted(pins.items())),
        }
        try:
            _atomic_write(self.path, canonical_json(document) + b"\n", 0o600)
        except SecurityError as exc:
            raise PinStoreError(f"could not update pin store {self.path}") from exc

    def _verify(self, peer_id: str, fingerprint: str) -> PinResult:
        identity = _validate_peer_id(peer_id)
        actual = _validate_fingerprint(fingerprint)
        with self._lock:
            pins = self._load()
            expected = pins.get(identity)
            if expected is None:
                pins[identity] = actual
                self._save(pins)
                return PinResult(actual, first_seen=True)
            if not hmac.compare_digest(expected, actual):
                raise PinMismatchError(identity, expected, actual)
            return PinResult(actual, first_seen=False)

    def get(self, peer_id: str) -> str | None:
        with self._lock:
            return self._load().get(_validate_peer_id(peer_id))

    def forget(self, peer_id: str) -> bool:
        identity = _validate_peer_id(peer_id)
        with self._lock:
            pins = self._load()
            existed = pins.pop(identity, None) is not None
            if existed:
                self._save(pins)
            return existed

    def all(self) -> dict[str, str]:
        with self._lock:
            return dict(self._load())


def certificate_fingerprint(certificate: bytes | x509.Certificate) -> str:
    """Return a SHA-256 fingerprint for a PEM or DER X.509 certificate."""

    cert = certificate
    if isinstance(certificate, bytes):
        try:
            if certificate.lstrip().startswith(b"-----BEGIN"):
                cert = x509.load_pem_x509_certificate(certificate)
            else:
                cert = x509.load_der_x509_certificate(certificate)
        except ValueError as exc:
            raise KeyValidationError("invalid X.509 certificate") from exc
    if not isinstance(cert, x509.Certificate):
        raise KeyValidationError("an X.509 certificate is required")
    return cert.fingerprint(hashes.SHA256()).hex()


class CertificatePinStore(_PinStore):
    """TOFU store for TLS peer certificates, indexed by host/endpoint."""

    namespace = "tls-certificates"

    def verify(self, peer_id: str, certificate: bytes | x509.Certificate) -> PinResult:
        return self._verify(peer_id, certificate_fingerprint(certificate))


class PeerPublicKeyPinStore(_PinStore):
    """TOFU store for end-to-end identity keys, indexed by stable user id."""

    namespace = "peer-public-keys"

    def verify(
        self, peer_id: str, public_key: rsa.RSAPublicKey | bytes | str
    ) -> PinResult:
        return self._verify(peer_id, public_key_fingerprint(public_key))


@dataclass(frozen=True)
class TLSCertificateInfo:
    certificate_path: Path
    private_key_path: Path
    fingerprint: str
    not_valid_after: datetime


def _certificate_info(
    certificate: x509.Certificate, certificate_path: Path, private_key_path: Path
) -> TLSCertificateInfo:
    if hasattr(certificate, "not_valid_after_utc"):
        expires = certificate.not_valid_after_utc
    else:  # pragma: no cover - compatibility with older cryptography releases
        expires = certificate.not_valid_after.replace(tzinfo=timezone.utc)
    return TLSCertificateInfo(
        certificate_path=certificate_path,
        private_key_path=private_key_path,
        fingerprint=certificate_fingerprint(certificate),
        not_valid_after=expires,
    )


def generate_self_signed_certificate(
    certificate_path: str | os.PathLike[str],
    private_key_path: str | os.PathLike[str],
    *,
    common_name: str = "Chat Online",
    hosts: Iterable[str] = ("localhost", "127.0.0.1", "::1"),
    valid_days: int = 825,
    password: bytes | None = None,
    overwrite: bool = False,
) -> TLSCertificateInfo:
    """Generate an RSA-3072 self-signed TLS server certificate and key."""

    cert_path = Path(certificate_path)
    key_path = Path(private_key_path)
    if not common_name or len(common_name) > 64:
        raise ValueError("common_name must contain 1 to 64 characters")
    if valid_days < 1:
        raise ValueError("valid_days must be positive")
    if not overwrite and (cert_path.exists() or key_path.exists()):
        raise FileExistsError("TLS certificate or private key already exists")
    key = generate_identity_private_key()
    now = datetime.now(timezone.utc)
    subject = issuer = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, common_name)])
    names: list[x509.GeneralName] = []
    for host in dict.fromkeys(hosts):
        candidate = str(host).strip()
        if not candidate:
            continue
        try:
            names.append(x509.IPAddress(ipaddress.ip_address(candidate)))
        except ValueError:
            try:
                names.append(x509.DNSName(candidate.encode("idna").decode("ascii")))
            except UnicodeError as exc:
                raise ValueError(f"invalid certificate host: {candidate}") from exc
    if not names:
        raise ValueError("at least one TLS certificate host is required")
    certificate = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=5))
        .not_valid_after(now + timedelta(days=valid_days))
        .add_extension(x509.SubjectAlternativeName(names), critical=False)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                content_commitment=False,
                key_encipherment=True,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=False,
                crl_sign=False,
                encipher_only=None,
                decipher_only=None,
            ),
            critical=True,
        )
        .add_extension(
            x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False
        )
        .sign(key, hashes.SHA256())
    )
    encryption = (
        serialization.BestAvailableEncryption(password)
        if password
        else serialization.NoEncryption()
    )
    key_bytes = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        encryption,
    )
    cert_bytes = certificate.public_bytes(serialization.Encoding.PEM)
    _atomic_write(key_path, key_bytes, 0o600)
    _atomic_write(cert_path, cert_bytes, 0o644)
    return _certificate_info(certificate, cert_path, key_path)


def ensure_self_signed_certificate(
    certificate_path: str | os.PathLike[str],
    private_key_path: str | os.PathLike[str],
    **generation_options: Any,
) -> TLSCertificateInfo:
    """Load an existing certificate/key pair or create it on first use."""

    cert_path = Path(certificate_path)
    key_path = Path(private_key_path)
    if not cert_path.exists() and not key_path.exists():
        return generate_self_signed_certificate(
            cert_path, key_path, **generation_options
        )
    if not cert_path.exists() or not key_path.exists():
        raise SecurityError("TLS certificate/key pair is incomplete")
    try:
        certificate = x509.load_pem_x509_certificate(cert_path.read_bytes())
    except (OSError, ValueError) as exc:
        raise SecurityError("existing TLS certificate is invalid") from exc
    return _certificate_info(certificate, cert_path, key_path)


def _require_tls13(context: ssl.SSLContext) -> ssl.SSLContext:
    if not getattr(ssl, "HAS_TLSv1_3", False):
        raise SecurityError("this Python/OpenSSL build does not support TLS 1.3")
    context.minimum_version = ssl.TLSVersion.TLSv1_3
    context.maximum_version = ssl.TLSVersion.TLSv1_3
    if hasattr(ssl, "OP_NO_COMPRESSION"):
        context.options |= ssl.OP_NO_COMPRESSION
    return context


def create_tls_server_context(
    certificate_path: str | os.PathLike[str],
    private_key_path: str | os.PathLike[str],
    *,
    password: str | bytes | None = None,
    alpn_protocols: Iterable[str] = (TLS_ALPN_PROTOCOL,),
) -> ssl.SSLContext:
    """Create a TLS-1.3-only server context."""

    context = _require_tls13(ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER))
    context.load_cert_chain(
        certfile=str(certificate_path), keyfile=str(private_key_path), password=password
    )
    protocols = list(alpn_protocols)
    if protocols:
        context.set_alpn_protocols(protocols)
    return context


def create_tls_client_context(
    *,
    cafile: str | os.PathLike[str] | None = None,
    tofu: bool = False,
    check_hostname: bool = True,
    alpn_protocols: Iterable[str] = (TLS_ALPN_PROTOCOL,),
) -> ssl.SSLContext:
    """Create a TLS-1.3-only client context.

    For a private CA or public certificate, pass ``cafile`` (or rely on system
    roots).  For a self-signed LAN server, set ``tofu=True`` and immediately call
    :func:`verify_tls_peer` after the handshake.  TOFU intentionally disables CA
    and hostname checks; the persisted certificate pin becomes the trust anchor.
    """

    if tofu:
        if cafile is not None:
            raise ValueError("cafile and tofu modes are mutually exclusive")
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
    else:
        context = ssl.create_default_context(
            ssl.Purpose.SERVER_AUTH,
            cafile=str(cafile) if cafile is not None else None,
        )
        context.check_hostname = check_hostname
        context.verify_mode = ssl.CERT_REQUIRED
    _require_tls13(context)
    protocols = list(alpn_protocols)
    if protocols:
        context.set_alpn_protocols(protocols)
    return context


def verify_tls_peer(
    tls_socket: ssl.SSLSocket,
    peer_id: str,
    pin_store: CertificatePinStore,
) -> PinResult:
    """Pin or verify the certificate presented by a connected TLS socket."""

    certificate = tls_socket.getpeercert(binary_form=True)
    if not certificate:
        raise VerificationError("TLS peer did not present a certificate")
    return pin_store.verify(peer_id, certificate)


__all__ = [
    "AES_KEY_SIZE",
    "CertificatePinStore",
    "DecryptedFile",
    "DecryptedFileManifest",
    "ENVELOPE_VERSION",
    "EncryptedFile",
    "EnvelopeError",
    "Identity",
    "KeyProtectionError",
    "KeyValidationError",
    "MESSAGE_ALGORITHM",
    "PinMismatchError",
    "PinResult",
    "PinStoreError",
    "PeerPublicKeyPinStore",
    "RSA_KEY_SIZE",
    "RecipientError",
    "SecurityError",
    "TLSCertificateInfo",
    "VerificationError",
    "canonical_json",
    "certificate_fingerprint",
    "create_tls_client_context",
    "create_tls_server_context",
    "default_client_security_dir",
    "decrypt_file",
    "decrypt_file_manifest",
    "decrypt_message",
    "decrypt_message_text",
    "encrypt_file",
    "encrypt_message",
    "ensure_self_signed_certificate",
    "format_fingerprint",
    "generate_identity_private_key",
    "generate_self_signed_certificate",
    "load_private_key",
    "load_public_key",
    "message_metadata",
    "public_key_fingerprint",
    "save_private_key",
    "serialize_public_key",
    "sign_data",
    "validate_private_key",
    "validate_public_key",
    "verify_signature",
    "verify_envelope_signature",
    "verify_tls_peer",
]
