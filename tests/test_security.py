from __future__ import annotations

import base64
import copy
import json
import math
import os
import ssl

import pytest
from cryptography import x509
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.x509.oid import ExtendedKeyUsageOID

from chat_online.security import (
    CertificatePinStore,
    EnvelopeError,
    Identity,
    KeyValidationError,
    PeerPublicKeyPinStore,
    PinMismatchError,
    RecipientError,
    VerificationError,
    canonical_json,
    certificate_fingerprint,
    create_tls_client_context,
    create_tls_server_context,
    default_client_security_dir,
    decrypt_file,
    decrypt_file_manifest,
    decrypt_message,
    decrypt_message_text,
    encrypt_file,
    encrypt_message,
    ensure_self_signed_certificate,
    format_fingerprint,
    generate_self_signed_certificate,
    load_private_key,
    load_public_key,
    message_metadata,
    public_key_fingerprint,
    save_private_key,
    serialize_public_key,
    sign_data,
    validate_public_key,
    verify_envelope_signature,
    verify_signature,
)


@pytest.fixture(scope="module")
def alice() -> Identity:
    return Identity.generate()


@pytest.fixture(scope="module")
def bob() -> Identity:
    return Identity.generate()


@pytest.fixture(scope="module")
def mallory() -> Identity:
    return Identity.generate()


def resign(envelope: dict, identity: Identity) -> dict:
    unsigned = copy.deepcopy(envelope)
    unsigned.pop("signature", None)
    unsigned["signature"] = base64.b64encode(
        sign_data(identity.private_key, canonical_json(unsigned))
    ).decode("ascii")
    return unsigned


def test_canonical_json_is_stable_utf8_and_rejects_non_json_numbers() -> None:
    left = {"z": [1, True], "a": "中文"}
    right = {"a": "中文", "z": [1, True]}
    assert canonical_json(left) == canonical_json(right)
    assert canonical_json(left) == b'{"a":"\xe4\xb8\xad\xe6\x96\x87","z":[1,true]}'

    with pytest.raises(ValueError):
        canonical_json({"bad": math.nan})
    with pytest.raises(TypeError):
        canonical_json({1: "non-string key"})


def test_public_key_round_trip_fingerprint_and_signature(
    alice: Identity, bob: Identity
) -> None:
    encoded = serialize_public_key(alice.public_key)
    loaded = load_public_key(encoded)
    assert public_key_fingerprint(loaded) == alice.fingerprint
    assert format_fingerprint(alice.fingerprint).count(":") == 31

    signature = sign_data(alice.private_key, b"authenticated data")
    verify_signature(loaded, signature, b"authenticated data")
    with pytest.raises(VerificationError):
        verify_signature(bob.public_key, signature, b"authenticated data")
    with pytest.raises(VerificationError):
        verify_signature(loaded, signature, b"altered data")


def test_public_key_policy_rejects_weak_rsa() -> None:
    weak = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    with pytest.raises(KeyValidationError):
        validate_public_key(weak.public_key())


def test_private_identity_pem_persistence_is_mode_600(
    tmp_path, alice: Identity
) -> None:
    key_path = tmp_path / "identity.pem"
    save_private_key(alice.private_key, key_path, protection="pem")
    loaded = load_private_key(key_path)
    assert public_key_fingerprint(loaded.public_key()) == alice.fingerprint
    assert key_path.read_bytes().startswith(b"-----BEGIN PRIVATE KEY-----")
    if os.name != "nt":
        assert key_path.stat().st_mode & 0o777 == 0o600
    with pytest.raises(FileExistsError):
        save_private_key(alice.private_key, key_path, protection="pem")


@pytest.mark.skipif(os.name != "nt", reason="Windows DPAPI is Windows-only")
def test_private_identity_uses_dpapi_on_windows(tmp_path, alice: Identity) -> None:
    key_path = tmp_path / "identity.key"
    alice.save(key_path)
    encoded = key_path.read_bytes()
    assert encoded.startswith(b"CHAT-ONLINE-DPAPI-V1\n")
    assert b"PRIVATE KEY" not in encoded
    assert Identity.load(key_path).fingerprint == alice.fingerprint


def test_identity_load_or_create_is_stable(tmp_path) -> None:
    key_path = tmp_path / "local-identity.pem"
    first = Identity.generate()
    first.save(key_path, protection="pem")
    second = Identity.load_or_create(key_path)
    assert second.fingerprint == first.fingerprint


def test_default_security_dir_honors_data_override(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("CHAT_ONLINE_DATA_DIR", str(tmp_path / "app-data"))
    result = default_client_security_dir()
    assert result == (tmp_path / "app-data" / "security").resolve()
    assert result.is_dir()
    assert not (tmp_path / "app-data" / "messages").exists()


def test_message_envelope_round_trip_and_authenticated_metadata(
    alice: Identity, bob: Identity
) -> None:
    envelope = encrypt_message(
        "你好，Bob",
        alice.private_key,
        [alice.public_key, bob.public_key],
        metadata={"conversation_id": "private:1", "sequence": 7},
    )

    assert decrypt_message_text(envelope, bob.private_key, alice.public_key) == "你好，Bob"
    assert decrypt_message(envelope, alice.private_key, alice.public_key) == "你好，Bob".encode()
    assert message_metadata(envelope) == {
        "conversation_id": "private:1",
        "sequence": 7,
    }
    assert envelope["sender"] == alice.fingerprint
    assert set(envelope["recipients"]) == {alice.fingerprint, bob.fingerprint}
    assert "你好" not in json.dumps(envelope, ensure_ascii=False)
    unsigned = verify_envelope_signature(envelope, alice.public_key)
    assert "signature" not in unsigned
    assert unsigned["metadata"]["sequence"] == 7


def test_message_rejects_wrong_recipient(
    alice: Identity, bob: Identity, mallory: Identity
) -> None:
    envelope = encrypt_message("private", alice.private_key, bob.public_key)
    with pytest.raises(RecipientError):
        decrypt_message(envelope, mallory.private_key, alice.public_key)


def test_message_rejects_wrong_sender_and_signature_tampering(
    alice: Identity, bob: Identity, mallory: Identity
) -> None:
    envelope = encrypt_message("signed", alice.private_key, bob.public_key)
    with pytest.raises(VerificationError):
        decrypt_message(envelope, bob.private_key, mallory.public_key)

    changed = copy.deepcopy(envelope)
    changed["metadata"]["admin"] = True
    with pytest.raises(VerificationError):
        decrypt_message(changed, bob.private_key, alice.public_key)

    changed = copy.deepcopy(envelope)
    signature = bytearray(base64.b64decode(changed["signature"]))
    signature[-1] ^= 1
    changed["signature"] = base64.b64encode(signature).decode("ascii")
    with pytest.raises(VerificationError):
        decrypt_message(changed, bob.private_key, alice.public_key)


def test_message_rejects_authenticated_ciphertext_tampering(
    alice: Identity, bob: Identity
) -> None:
    envelope = encrypt_message("authenticated", alice.private_key, bob.public_key)
    changed = copy.deepcopy(envelope)
    ciphertext = bytearray(base64.b64decode(changed["ciphertext"]))
    ciphertext[0] ^= 1
    changed["ciphertext"] = base64.b64encode(ciphertext).decode("ascii")
    # Re-sign to ensure this reaches AES-GCM authentication rather than only PSS.
    changed = resign(changed, alice)
    with pytest.raises(VerificationError, match="authentication"):
        decrypt_message(changed, bob.private_key, alice.public_key)


def test_binary_message_does_not_decode_as_text(
    alice: Identity, bob: Identity
) -> None:
    envelope = encrypt_message(b"\x00\xff", alice.private_key, bob.public_key)
    assert decrypt_message(envelope, bob.private_key, alice.public_key) == b"\x00\xff"
    with pytest.raises(EnvelopeError):
        decrypt_message_text(envelope, bob.private_key, alice.public_key)


def test_file_round_trip_encrypts_filename_and_manifest(
    alice: Identity, bob: Identity
) -> None:
    original = b"file body\x00" * 1024
    encrypted = encrypt_file(
        original,
        "quarterly-secret.txt",
        alice.private_key,
        [alice.public_key, bob.public_key],
        media_type="text/plain",
        metadata={"conversation_id": "room:42"},
        authenticated_metadata={"room_id": "42", "timestamp": 12345},
    )

    serialized_envelope = json.dumps(encrypted.envelope, ensure_ascii=False)
    assert b"quarterly-secret.txt" not in encrypted.ciphertext
    assert "quarterly-secret.txt" not in serialized_envelope
    assert "room:42" not in serialized_envelope
    assert encrypted.envelope["metadata"] == {"room_id": "42", "timestamp": 12345}
    manifest = decrypt_file_manifest(
        encrypted.envelope,
        bob.private_key,
        alice.public_key,
    )
    assert manifest.filename == "quarterly-secret.txt"
    assert manifest.media_type == "text/plain"
    assert manifest.size == len(original)
    assert manifest.metadata == {"conversation_id": "room:42"}
    assert manifest.authenticated_metadata == {
        "room_id": "42",
        "timestamp": 12345,
    }
    result = decrypt_file(
        encrypted.ciphertext,
        encrypted.envelope,
        bob.private_key,
        alice.public_key,
    )
    assert result.data == original
    assert result.filename == "quarterly-secret.txt"
    assert result.media_type == "text/plain"
    assert result.metadata == {"conversation_id": "room:42"}
    assert result.authenticated_metadata == {"room_id": "42", "timestamp": 12345}


def test_file_rejects_traversal_wrong_recipient_and_tampering(
    alice: Identity, bob: Identity, mallory: Identity
) -> None:
    with pytest.raises(EnvelopeError):
        encrypt_file(b"x", "../secret.txt", alice.private_key, bob.public_key)

    encrypted = encrypt_file(
        b"authenticated file", "safe.bin", alice.private_key, bob.public_key
    )
    with pytest.raises(RecipientError):
        decrypt_file(
            encrypted.ciphertext,
            encrypted.envelope,
            mallory.private_key,
            alice.public_key,
        )

    tampered = bytearray(encrypted.ciphertext)
    tampered[-1] ^= 1
    with pytest.raises(VerificationError, match="digest"):
        decrypt_file(
            bytes(tampered),
            encrypted.envelope,
            bob.private_key,
            alice.public_key,
        )

    manifest_changed = copy.deepcopy(encrypted.envelope)
    encrypted_manifest = bytearray(
        base64.b64decode(manifest_changed["encrypted_manifest"])
    )
    encrypted_manifest[0] ^= 1
    manifest_changed["encrypted_manifest"] = base64.b64encode(
        encrypted_manifest
    ).decode("ascii")
    manifest_changed = resign(manifest_changed, alice)
    with pytest.raises(VerificationError, match="authentication"):
        decrypt_file(
            encrypted.ciphertext,
            manifest_changed,
            bob.private_key,
            alice.public_key,
        )

    routing_changed = copy.deepcopy(encrypted.envelope)
    routing_changed["metadata"]["room_id"] = "other-room"
    routing_changed = resign(routing_changed, alice)
    with pytest.raises(VerificationError, match="authentication"):
        decrypt_file(
            encrypted.ciphertext,
            routing_changed,
            bob.private_key,
            alice.public_key,
        )


def test_peer_public_key_tofu_store(
    tmp_path, alice: Identity, bob: Identity
) -> None:
    store = PeerPublicKeyPinStore(tmp_path / "peer-pins.json")
    first = store.verify("alice-user-id", alice.public_key)
    assert first.first_seen is True
    assert first.fingerprint == alice.fingerprint
    assert store.verify("alice-user-id", serialize_public_key(alice.public_key)).first_seen is False
    assert store.get("alice-user-id") == alice.fingerprint
    with pytest.raises(PinMismatchError) as error:
        store.verify("alice-user-id", bob.public_key)
    assert error.value.expected == alice.fingerprint
    assert error.value.actual == bob.fingerprint
    assert store.forget("alice-user-id") is True
    assert store.get("alice-user-id") is None


@pytest.fixture()
def tls_certificate(tmp_path):
    cert_path = tmp_path / "server-cert.pem"
    key_path = tmp_path / "server-key.pem"
    info = generate_self_signed_certificate(
        cert_path,
        key_path,
        common_name="chat.test",
        hosts=["chat.test", "127.0.0.1", "::1"],
        valid_days=30,
    )
    return info, x509.load_pem_x509_certificate(cert_path.read_bytes())


def test_self_signed_certificate_has_expected_identity(tls_certificate) -> None:
    info, certificate = tls_certificate
    assert info.fingerprint == certificate_fingerprint(certificate)
    assert certificate.issuer == certificate.subject
    assert certificate.public_key().key_size == 3072
    san = certificate.extensions.get_extension_for_class(
        x509.SubjectAlternativeName
    ).value
    assert "chat.test" in san.get_values_for_type(x509.DNSName)
    assert {str(value) for value in san.get_values_for_type(x509.IPAddress)} == {
        "127.0.0.1",
        "::1",
    }
    usages = certificate.extensions.get_extension_for_class(
        x509.ExtendedKeyUsage
    ).value
    assert ExtendedKeyUsageOID.SERVER_AUTH in usages
    certificate.public_key().verify(
        certificate.signature,
        certificate.tbs_certificate_bytes,
        padding.PKCS1v15(),
        certificate.signature_hash_algorithm,
    )


def test_ensure_certificate_reuses_existing_pair(tmp_path) -> None:
    cert_path = tmp_path / "server.pem"
    key_path = tmp_path / "server.key"
    created = ensure_self_signed_certificate(cert_path, key_path)
    loaded = ensure_self_signed_certificate(cert_path, key_path)
    assert loaded.fingerprint == created.fingerprint


def test_certificate_tofu_store_detects_replacement(tmp_path) -> None:
    first_cert = tmp_path / "first.pem"
    first_key = tmp_path / "first.key"
    second_cert = tmp_path / "second.pem"
    second_key = tmp_path / "second.key"
    generate_self_signed_certificate(first_cert, first_key)
    generate_self_signed_certificate(second_cert, second_key)
    first_pem = first_cert.read_bytes()
    second_pem = second_cert.read_bytes()

    store = CertificatePinStore(tmp_path / "cert-pins.json")
    result = store.verify("chat.test:8888", first_pem)
    assert result.first_seen is True
    assert store.verify("chat.test:8888", first_cert.read_bytes()).first_seen is False
    with pytest.raises(PinMismatchError):
        store.verify("chat.test:8888", second_pem)


@pytest.mark.skipif(not ssl.HAS_TLSv1_3, reason="OpenSSL lacks TLS 1.3")
def test_tls_contexts_are_tls13_only(tls_certificate) -> None:
    info, _certificate = tls_certificate
    server = create_tls_server_context(
        info.certificate_path, info.private_key_path
    )
    tofu_client = create_tls_client_context(tofu=True)
    ca_client = create_tls_client_context(
        cafile=info.certificate_path, check_hostname=True
    )
    for context in (server, tofu_client, ca_client):
        assert context.minimum_version is ssl.TLSVersion.TLSv1_3
        assert context.maximum_version is ssl.TLSVersion.TLSv1_3
    assert tofu_client.verify_mode is ssl.CERT_NONE
    assert tofu_client.check_hostname is False
    assert ca_client.verify_mode is ssl.CERT_REQUIRED
    assert ca_client.check_hostname is True

    with pytest.raises(ValueError):
        create_tls_client_context(cafile=info.certificate_path, tofu=True)


def test_corrupt_pin_store_fails_closed(tmp_path, alice: Identity) -> None:
    path = tmp_path / "pins.json"
    path.write_text("not json", encoding="utf-8")
    store = PeerPublicKeyPinStore(path)
    with pytest.raises(Exception, match="corrupt"):
        store.verify("alice", alice.public_key)
