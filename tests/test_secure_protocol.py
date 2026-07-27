from __future__ import annotations

import base64
import copy

import pytest

from chat_online.secure_protocol import (
    IDENTITY_FINGERPRINT_FIELD,
    MAX_ENCRYPTED_FILE_BYTES,
    SecureProtocolError,
    identity_fields,
    load_identity_fields,
    message_envelope,
    recipient_fingerprints,
    require_exact_recipients,
    validate_file_envelope,
    validate_message_envelope,
)
from chat_online.security import (
    Identity,
    RecipientError,
    VerificationError,
    canonical_json,
    decrypt_file,
    decrypt_message_text,
    encrypt_file,
    sign_data,
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


def _resign(envelope: dict, identity: Identity) -> dict:
    unsigned = copy.deepcopy(envelope)
    unsigned.pop("signature", None)
    unsigned["signature"] = base64.b64encode(
        sign_data(identity.private_key, canonical_json(unsigned))
    ).decode("ascii")
    return unsigned


def _flip_base64_byte(value: str) -> str:
    decoded = bytearray(base64.b64decode(value))
    decoded[0] ^= 1
    return base64.b64encode(decoded).decode("ascii")


def test_hello_identity_rejects_declared_fingerprint_mismatch(
    alice: Identity, bob: Identity
) -> None:
    packet = identity_fields(alice)
    public_key, fingerprint = load_identity_fields(packet)
    assert public_key.key_size == 3072
    assert fingerprint == alice.fingerprint

    packet[IDENTITY_FINGERPRINT_FIELD] = bob.fingerprint
    with pytest.raises(SecureProtocolError, match="fingerprint does not match"):
        load_identity_fields(packet)


def test_room_and_private_messages_are_bound_to_the_routed_destination(
    alice: Identity, bob: Identity
) -> None:
    room = message_envelope(
        alice,
        [alice.public_key, bob.public_key],
        "room secret",
        scope="room",
        room_id="room-a",
        message_id="1" * 32,
        timestamp=1234.5,
    )
    assert validate_message_envelope(
        room,
        alice.public_key,
        scope="room",
        room_id="room-a",
    ) == {
        "id": "1" * 32,
        "scope": "room",
        "timestamp": 1234.5,
        "room_id": "room-a",
    }
    with pytest.raises(SecureProtocolError, match="room does not match routing"):
        validate_message_envelope(
            room,
            alice.public_key,
            scope="room",
            room_id="room-b",
        )
    with pytest.raises(SecureProtocolError, match="scope does not match routing"):
        validate_message_envelope(
            room,
            alice.public_key,
            scope="private",
            target="bob",
        )

    private = message_envelope(
        alice,
        bob.public_key,
        "private secret",
        scope="private",
        target="Bob",
        message_id="2" * 32,
        timestamp=2345.5,
    )
    metadata = validate_message_envelope(
        private,
        alice.public_key,
        scope="private",
        target="BOB",
    )
    assert metadata["to"] == "bob"
    with pytest.raises(SecureProtocolError, match="target does not match routing"):
        validate_message_envelope(
            private,
            alice.public_key,
            scope="private",
            target="mallory",
        )


def test_envelope_recipients_must_match_the_current_members_exactly(
    alice: Identity, bob: Identity, mallory: Identity
) -> None:
    envelope = message_envelope(
        alice,
        [alice.public_key, bob.public_key],
        "members only",
        scope="room",
        room_id="room-a",
    )
    expected = {alice.fingerprint, bob.fingerprint}
    assert recipient_fingerprints(envelope) == expected
    require_exact_recipients(envelope, (value.upper() for value in expected))

    with pytest.raises(SecureProtocolError, match="recipients are stale"):
        require_exact_recipients(envelope, [bob.fingerprint])
    with pytest.raises(SecureProtocolError, match="recipients are stale"):
        require_exact_recipients(
            envelope,
            [alice.fingerprint, bob.fingerprint, mallory.fingerprint],
        )


def test_message_rejects_tampered_signature_and_ciphertext(
    alice: Identity, bob: Identity
) -> None:
    envelope = message_envelope(
        alice,
        bob.public_key,
        "authenticated",
        scope="private",
        target="bob",
    )

    bad_signature = copy.deepcopy(envelope)
    bad_signature["signature"] = _flip_base64_byte(bad_signature["signature"])
    with pytest.raises(VerificationError):
        validate_message_envelope(
            bad_signature,
            alice.public_key,
            scope="private",
            target="bob",
        )

    bad_ciphertext = copy.deepcopy(envelope)
    bad_ciphertext["ciphertext"] = _flip_base64_byte(bad_ciphertext["ciphertext"])
    with pytest.raises(VerificationError):
        validate_message_envelope(
            bad_ciphertext,
            alice.public_key,
            scope="private",
            target="bob",
        )

    # A legitimately re-signed but corrupted body passes relay validation, then
    # fails AES-GCM authentication at the recipient.
    resigned_ciphertext = _resign(bad_ciphertext, alice)
    validate_message_envelope(
        resigned_ciphertext,
        alice.public_key,
        scope="private",
        target="bob",
    )
    with pytest.raises(VerificationError, match="authentication"):
        decrypt_message_text(
            resigned_ciphertext,
            bob.private_key,
            alice.public_key,
        )


def test_file_envelope_binds_room_size_and_ciphertext_hash(
    alice: Identity, bob: Identity
) -> None:
    encrypted = encrypt_file(
        b"encrypted file body",
        "secret.bin",
        alice.private_key,
        [alice.public_key, bob.public_key],
        authenticated_metadata={"room_id": "room-a", "timestamp": 3456.5},
    )
    validated = validate_file_envelope(
        encrypted.envelope,
        alice.public_key,
        room_id="room-a",
        ciphertext=encrypted.ciphertext,
    )
    assert validated["file_id"] == encrypted.envelope["file_id"]
    assert validated["ciphertext_size"] == len(encrypted.ciphertext)

    with pytest.raises(SecureProtocolError, match="room does not match routing"):
        validate_file_envelope(
            encrypted.envelope,
            alice.public_key,
            room_id="room-b",
            ciphertext=encrypted.ciphertext,
        )
    with pytest.raises(SecureProtocolError, match="size does not match"):
        validate_file_envelope(
            encrypted.envelope,
            alice.public_key,
            room_id="room-a",
            ciphertext=encrypted.ciphertext + b"x",
        )

    corrupted = bytearray(encrypted.ciphertext)
    corrupted[-1] ^= 1
    with pytest.raises(SecureProtocolError, match="digest does not match"):
        validate_file_envelope(
            encrypted.envelope,
            alice.public_key,
            room_id="room-a",
            ciphertext=bytes(corrupted),
        )

    oversized = copy.deepcopy(encrypted.envelope)
    oversized["ciphertext_size"] = MAX_ENCRYPTED_FILE_BYTES + 1
    oversized = _resign(oversized, alice)
    with pytest.raises(SecureProtocolError, match="exceeds the size limit"):
        validate_file_envelope(
            oversized,
            alice.public_key,
            room_id="room-a",
        )


def test_wrong_recipient_cannot_decrypt_messages_or_files(
    alice: Identity, bob: Identity, mallory: Identity
) -> None:
    message = message_envelope(
        alice,
        bob.public_key,
        "for bob",
        scope="private",
        target="bob",
    )
    require_exact_recipients(message, [bob.fingerprint])
    with pytest.raises(RecipientError):
        decrypt_message_text(message, mallory.private_key, alice.public_key)

    encrypted_file = encrypt_file(
        b"for bob",
        "private.bin",
        alice.private_key,
        bob.public_key,
        authenticated_metadata={"room_id": "room-a", "timestamp": 4567.5},
    )
    require_exact_recipients(encrypted_file.envelope, [bob.fingerprint])
    with pytest.raises(RecipientError):
        decrypt_file(
            encrypted_file.ciphertext,
            encrypted_file.envelope,
            mallory.private_key,
            alice.public_key,
        )
