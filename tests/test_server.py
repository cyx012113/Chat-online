from __future__ import annotations

import base64
import socket
import tempfile
import time
import unittest
from pathlib import Path
from typing import Callable

from chat_online.protocol import PacketReader, encode_packet
from chat_online.server import MAIN_ROOM_ID, ChatServerEngine
from chat_online.storage import AppStorage


class Peer:
    def __init__(self, host: str, port: int, username: str) -> None:
        self.socket = socket.create_connection((host, port), timeout=2)
        self.socket.settimeout(0.2)
        self.reader = PacketReader()
        self.pending: list[dict] = []
        self.send({"type": "hello", "username": username})
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

    def close(self) -> None:
        try:
            self.socket.close()
        except OSError:
            pass


class ServerIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.storage = AppStorage(Path(self.temporary.name))
        self.engine = ChatServerEngine(self.storage)
        self.assertTrue(self.engine.start("127.0.0.1", 0))
        self.port = self.engine.port
        self.peers: list[Peer] = []

    def tearDown(self) -> None:
        for peer in self.peers:
            peer.close()
        self.engine.stop()
        self.temporary.cleanup()

    def connect(self, username: str) -> Peer:
        peer = Peer("127.0.0.1", self.port, username)
        self.peers.append(peer)
        return peer

    def test_end_to_end_chat_rooms_private_moderation_and_files(self) -> None:
        alice = self.connect("Alice")
        bob = self.connect("Bob")
        self.assertEqual(alice.hello["username"], "Alice")
        self.assertEqual(bob.hello["username"], "Bob")

        alice.send({"type": "message", "room_id": MAIN_ROOM_ID, "text": "hello room"})
        alice_message = alice.receive(
            lambda packet: packet.get("type") == "message" and packet.get("text") == "hello room"
        )
        bob_message = bob.receive(
            lambda packet: packet.get("type") == "message" and packet.get("text") == "hello room"
        )
        self.assertEqual(alice_message["id"], bob_message["id"])

        alice.send({"type": "create_room", "name": "Project Team"})
        created = alice.receive_type("room_joined")
        room_id = created["room"]["id"]
        invite_code = created["invite_code"]
        bob.send({"type": "join_room", "invite_code": invite_code})
        joined = bob.receive_type("room_joined")
        self.assertEqual(joined["room"]["id"], room_id)

        bob.send({"type": "message", "room_id": room_id, "text": "group update"})
        alice.receive(lambda packet: packet.get("text") == "group update")
        bob.receive(lambda packet: packet.get("text") == "group update")

        alice.send({"type": "private_message", "to": "Bob", "text": "private hello"})
        alice.receive(lambda packet: packet.get("scope") == "private" and packet.get("text") == "private hello")
        bob.receive(lambda packet: packet.get("scope") == "private" and packet.get("text") == "private hello")
        bob.send({"type": "private_history", "with": "Alice"})
        history = bob.receive(
            lambda packet: packet.get("type") == "history" and packet.get("scope") == "private"
        )
        self.assertEqual(history["messages"][-1]["text"], "private hello")

        bob_id = bob.hello["session_id"]
        self.assertTrue(self.engine.mute_user(bob_id, room_id, True))
        bob.receive(lambda packet: packet.get("type") == "moderation" and packet.get("action") == "muted")
        bob.send({"type": "message", "room_id": room_id, "text": "blocked message"})
        error = bob.receive(lambda packet: packet.get("type") == "error" and packet.get("code") == "muted")
        self.assertIn("muted", error["message"].lower())
        self.assertTrue(self.engine.mute_user(bob_id, room_id, False))

        payload = b"chat-online-file"
        alice.send(
            {
                "type": "upload_file",
                "room_id": room_id,
                "name": "notes.txt",
                "size": len(payload),
                "data": base64.b64encode(payload).decode("ascii"),
            }
        )
        shared = bob.receive(lambda packet: packet.get("type") == "file_shared")
        self.assertEqual(shared["file"]["name"], "notes.txt")
        file_id = shared["file"]["id"]
        bob.send({"type": "download_file", "file_id": file_id})
        downloaded = bob.receive_type("file_data")
        self.assertEqual(base64.b64decode(downloaded["data"]), payload)

        history_on_disk = self.storage.load_messages(room_id)
        self.assertTrue(any(item.get("text") == "group update" for item in history_on_disk))
        self.assertTrue(any(item.get("type") == "file_shared" for item in history_on_disk))

    def test_duplicate_username_is_rejected(self) -> None:
        self.connect("Alice")
        duplicate_socket = socket.create_connection(("127.0.0.1", self.port), timeout=2)
        duplicate_socket.settimeout(2)
        try:
            duplicate_socket.sendall(encode_packet({"type": "hello", "username": "alice"}))
            packets = PacketReader().feed(duplicate_socket.recv(65536))
            self.assertEqual(packets[0]["type"], "error")
            self.assertEqual(packets[0]["code"], "duplicate_username")
        finally:
            duplicate_socket.close()

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

