from __future__ import annotations

import base64
import json
import socket
import ssl
import tempfile
import time
import unittest
from pathlib import Path
from typing import Callable

from chat_online.protocol import PacketReader, encode_packet
from chat_online.secure_protocol import (
    PROTOCOL_VERSION,
    SECURITY_VERSION,
    encode_file_ciphertext,
    identity_fields,
    message_envelope,
)
from chat_online.security import (
    TLS_ALPN_PROTOCOL,
    CertificatePinStore,
    Identity,
    create_tls_client_context,
    create_tls_server_context,
    decrypt_file,
    decrypt_file_manifest,
    decrypt_message_text,
    encrypt_file,
    ensure_self_signed_certificate,
    verify_tls_peer,
)
from chat_online.server import MAIN_ROOM_ID, ChatServerEngine
from chat_online.storage import AppStorage


class Peer:
    def __init__(
        self,
        host: str,
        port: int,
        username: str,
        tls_context: ssl.SSLContext,
        pin_store: CertificatePinStore,
    ) -> None:
        raw_socket = socket.create_connection((host, port), timeout=2)
        self.socket = tls_context.wrap_socket(raw_socket, server_hostname=host)
        verify_tls_peer(self.socket, f"{host}:{port}", pin_store)
        self.socket.settimeout(0.2)
        self.reader = PacketReader()
        self.pending: list[dict] = []
        self.username = username
        self.identity = Identity.generate()
        self.send(
            {
                "type": "hello",
                "username": username,
                "protocol_version": PROTOCOL_VERSION,
                "security_version": SECURITY_VERSION,
                **identity_fields(self.identity),
            }
        )
        self.hello = self.receive_type("hello_ok")

    def send(self, packet: dict) -> None:
        self.socket.sendall(encode_packet(packet))

    def receive(self, predicate: Callable[[dict], bool], timeout: float = 3) -> dict:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            for index, packet in enumerate(self.pending):
                if predicate(packet):
                    return self.pending.pop(index)
            try:
                data = self.socket.recv(65536)
            except socket.timeout:
                continue
            if not data:
                break
            self.pending.extend(self.reader.feed(data))
        raise AssertionError(f"packet not received; buffered packets: {self.pending!r}")

    def receive_type(self, packet_type: str, timeout: float = 3) -> dict:
        return self.receive(lambda packet: packet.get("type") == packet_type, timeout)

    def encrypted_message(
        self,
        text: str,
        recipients: list["Peer"],
        *,
        room_id: str | None = None,
        target: str | None = None,
    ) -> dict:
        keys = [peer.identity.public_key for peer in recipients]
        if target is not None:
            return {
                "type": "private_message",
                "to": target,
                "envelope": message_envelope(
                    self.identity,
                    keys,
                    text,
                    scope="private",
                    target=target,
                ),
            }
        return {
            "type": "message",
            "room_id": room_id,
            "envelope": message_envelope(
                self.identity,
                keys,
                text,
                scope="room",
                room_id=room_id,
            ),
        }

    def close(self) -> None:
        try:
            self.socket.close()
        except OSError:
            pass


class ServerIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.storage = AppStorage(Path(self.temporary.name))
        certificate = ensure_self_signed_certificate(
            self.storage.root / "security" / "tls-cert.pem",
            self.storage.root / "security" / "tls-key.pem",
        )
        self.server_tls_context = create_tls_server_context(
            certificate.certificate_path,
            certificate.private_key_path,
        )
        self.client_tls_context = create_tls_client_context(tofu=True)
        self.pin_store = CertificatePinStore(
            self.storage.root / "security" / "test-server-pins.json"
        )
        self.engine = ChatServerEngine(
            self.storage,
            ssl_context=self.server_tls_context,
        )
        self.assertTrue(self.engine.start("127.0.0.1", 0))
        self.port = self.engine.port
        self.peers: list[Peer] = []

    def tearDown(self) -> None:
        for peer in self.peers:
            peer.close()
        self.engine.stop()
        self.temporary.cleanup()

    def connect(self, username: str) -> Peer:
        peer = Peer(
            "127.0.0.1",
            self.port,
            username,
            self.client_tls_context,
            self.pin_store,
        )
        self.peers.append(peer)
        return peer

    def test_end_to_end_chat_rooms_private_moderation_and_files(self) -> None:
        alice = self.connect("Alice")
        bob = self.connect("Bob")
        self.assertEqual(alice.hello["username"], "Alice")
        self.assertEqual(bob.hello["username"], "Bob")
        self.assertEqual(alice.socket.version(), "TLSv1.3")
        self.assertEqual(alice.socket.selected_alpn_protocol(), TLS_ALPN_PROTOCOL)

        main_message = alice.encrypted_message(
            "hello room", [alice, bob], room_id=MAIN_ROOM_ID
        )
        alice.send(main_message)
        message_id = main_message["envelope"]["metadata"]["id"]
        alice_message = alice.receive(lambda packet: packet.get("id") == message_id)
        bob_message = bob.receive(lambda packet: packet.get("id") == message_id)
        self.assertEqual(alice_message["id"], bob_message["id"])
        self.assertNotIn("text", bob_message)
        self.assertEqual(
            decrypt_message_text(
                bob_message["envelope"],
                bob.identity.private_key,
                alice.identity.public_key,
            ),
            "hello room",
        )

        alice.send({"type": "create_room", "name": "Project Team"})
        created = alice.receive_type("room_joined")
        room_id = created["room"]["id"]
        invite_code = created["invite_code"]
        bob.send({"type": "join_room", "invite_code": invite_code})
        joined = bob.receive_type("room_joined")
        self.assertEqual(joined["room"]["id"], room_id)

        group_message = bob.encrypted_message(
            "group update", [alice, bob], room_id=room_id
        )
        bob.send(group_message)
        group_id = group_message["envelope"]["metadata"]["id"]
        alice_group = alice.receive(lambda packet: packet.get("id") == group_id)
        bob.receive(lambda packet: packet.get("id") == group_id)
        self.assertEqual(
            decrypt_message_text(
                alice_group["envelope"],
                alice.identity.private_key,
                bob.identity.public_key,
            ),
            "group update",
        )

        private_message = alice.encrypted_message(
            "private hello", [alice, bob], target="Bob"
        )
        alice.send(private_message)
        private_id = private_message["envelope"]["metadata"]["id"]
        alice.receive(lambda packet: packet.get("id") == private_id)
        bob_private = bob.receive(lambda packet: packet.get("id") == private_id)
        self.assertEqual(
            decrypt_message_text(
                bob_private["envelope"],
                bob.identity.private_key,
                alice.identity.public_key,
            ),
            "private hello",
        )
        bob.send({"type": "private_history", "with": "Alice"})
        history = bob.receive(
            lambda packet: packet.get("type") == "history" and packet.get("scope") == "private"
        )
        historical_private = history["messages"][-1]
        self.assertNotIn("text", historical_private)
        self.assertEqual(
            decrypt_message_text(
                historical_private["envelope"],
                bob.identity.private_key,
                alice.identity.public_key,
            ),
            "private hello",
        )

        bob_id = bob.hello["session_id"]
        self.assertTrue(self.engine.mute_user(bob_id, room_id, True))
        bob.receive(lambda packet: packet.get("type") == "moderation" and packet.get("action") == "muted")
        bob.send(
            bob.encrypted_message(
                "blocked message", [alice, bob], room_id=room_id
            )
        )
        error = bob.receive(lambda packet: packet.get("type") == "error" and packet.get("code") == "muted")
        self.assertIn("muted", error["message"].lower())
        self.assertTrue(self.engine.mute_user(bob_id, room_id, False))

        payload = b"chat-online-file"
        encrypted_file = encrypt_file(
            payload,
            "notes.txt",
            alice.identity.private_key,
            [alice.identity.public_key, bob.identity.public_key],
            media_type="text/plain",
            authenticated_metadata={"room_id": room_id, "timestamp": time.time()},
        )
        alice.send(
            {
                "type": "upload_file",
                "room_id": room_id,
                "envelope": encrypted_file.envelope,
                "data": encode_file_ciphertext(encrypted_file.ciphertext),
            }
        )
        shared = bob.receive(lambda packet: packet.get("type") == "file_shared")
        self.assertEqual(shared["file"]["name"], "Encrypted file")
        manifest = decrypt_file_manifest(
            shared["file"]["envelope"],
            bob.identity.private_key,
            alice.identity.public_key,
        )
        self.assertEqual(manifest.filename, "notes.txt")
        file_id = shared["file"]["id"]
        bob.send({"type": "download_file", "file_id": file_id})
        downloaded = bob.receive_type("file_data")
        ciphertext = base64.b64decode(downloaded["data"])
        self.assertNotEqual(ciphertext, payload)
        decrypted_file = decrypt_file(
            ciphertext,
            downloaded["file"]["envelope"],
            bob.identity.private_key,
            alice.identity.public_key,
        )
        self.assertEqual(decrypted_file.data, payload)
        self.assertEqual(decrypted_file.filename, "notes.txt")

        history_on_disk = self.storage.load_messages(room_id)
        self.assertTrue(any(item.get("type") == "file_shared" for item in history_on_disk))
        serialized_history = json.dumps(history_on_disk, ensure_ascii=False)
        self.assertNotIn("group update", serialized_history)
        self.assertNotIn("notes.txt", serialized_history)
        _stored_metadata, stored_ciphertext = self.storage.read_file(file_id)
        self.assertNotEqual(stored_ciphertext, payload)

        alice.send(main_message)
        replay_error = alice.receive(
            lambda packet: packet.get("type") == "error"
            and packet.get("code") == "replayed_envelope"
        )
        self.assertIn("already", replay_error["message"].lower())

    def test_duplicate_username_is_rejected(self) -> None:
        self.connect("Alice")
        duplicate_raw_socket = socket.create_connection(
            ("127.0.0.1", self.port), timeout=2
        )
        duplicate_socket = self.client_tls_context.wrap_socket(
            duplicate_raw_socket,
            server_hostname="127.0.0.1",
        )
        verify_tls_peer(
            duplicate_socket,
            f"127.0.0.1:{self.port}",
            self.pin_store,
        )
        duplicate_socket.settimeout(2)
        try:
            duplicate_identity = Identity.generate()
            duplicate_socket.sendall(
                encode_packet(
                    {
                        "type": "hello",
                        "username": "alice",
                        "protocol_version": PROTOCOL_VERSION,
                        "security_version": SECURITY_VERSION,
                        **identity_fields(duplicate_identity),
                    }
                )
            )
            packets = PacketReader().feed(duplicate_socket.recv(65536))
            self.assertEqual(packets[0]["type"], "error")
            self.assertEqual(packets[0]["code"], "duplicate_username")
        finally:
            duplicate_socket.close()

    def test_legacy_plaintext_history_and_files_are_not_relayed(self) -> None:
        self.storage.append_message(
            MAIN_ROOM_ID,
            {
                "type": "message",
                "scope": "room",
                "room_id": MAIN_ROOM_ID,
                "sender": "Legacy",
                "sender_id": "legacy-user",
                "text": "legacy plaintext secret",
                "kind": "chat",
                "timestamp": time.time(),
            },
        )
        self.storage.save_file(
            "legacy-file",
            "legacy-secret.txt",
            b"legacy plaintext bytes",
            {"room_id": MAIN_ROOM_ID},
        )

        peer = self.connect("SecureReader")
        history = peer.receive(
            lambda packet: packet.get("type") == "history"
            and packet.get("room_id") == MAIN_ROOM_ID
        )
        self.assertNotIn("legacy plaintext secret", json.dumps(history))

        peer.send({"type": "download_file", "file_id": "legacy-file"})
        error = peer.receive(
            lambda packet: packet.get("type") == "error"
            and packet.get("code") == "legacy_file_unsupported"
        )
        self.assertIn("not served", error["message"])

    def test_plaintext_client_is_not_accepted(self) -> None:
        plaintext_socket = socket.create_connection(("127.0.0.1", self.port), timeout=2)
        try:
            plaintext_socket.sendall(
                encode_packet({"type": "hello", "username": "Plaintext"})
            )
        finally:
            plaintext_socket.close()

        time.sleep(0.05)
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            with self.engine._lock:
                if not self.engine._pending_sockets:
                    break
            time.sleep(0.01)
        self.assertEqual(self.engine.snapshot()["users"], [])

        secure_peer = self.connect("Secure")
        self.assertEqual(secure_peer.hello["username"], "Secure")

    def test_stop_interrupts_a_tls_handshake_in_progress(self) -> None:
        pending_socket = socket.create_connection(("127.0.0.1", self.port), timeout=2)
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            with self.engine._lock:
                if self.engine._pending_sockets:
                    break
            time.sleep(0.01)
        with self.engine._lock:
            self.assertTrue(self.engine._pending_sockets)

        started_at = time.monotonic()
        self.engine.stop()
        self.assertLess(time.monotonic() - started_at, 1.0)
        pending_socket.settimeout(1)
        try:
            self.assertEqual(pending_socket.recv(1), b"")
        except (ConnectionResetError, OSError):
            pass
        finally:
            pending_socket.close()

    def test_stop_releases_listener(self) -> None:
        port = self.port
        self.engine.stop()
        probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            probe.bind(("127.0.0.1", port))
        finally:
            probe.close()


if __name__ == "__main__":
    unittest.main()
