from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import re
import tempfile
import unittest
from unittest.mock import patch

from chat_online.storage import AppStorage, StorageError


class AppStorageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.base = Path(self.temporary_directory.name)
        self.root = self.base / "data"
        self.storage = AppStorage(self.root)

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def test_message_history_is_hashed_and_limited(self) -> None:
        conversation_id = "../../outside/会话"
        for number in range(5):
            self.storage.append_message(
                conversation_id, {"number": number, "text": f"message {number}"}
            )

        self.assertEqual(
            self.storage.load_messages(conversation_id, limit=2),
            [
                {"number": 3, "text": "message 3"},
                {"number": 4, "text": "message 4"},
            ],
        )
        history_files = list(self.storage.messages_dir.iterdir())
        self.assertEqual(len(history_files), 1)
        self.assertRegex(history_files[0].name, r"^[0-9a-f]{64}\.jsonl$")
        self.assertEqual(self.storage.load_messages("unknown"), [])
        self.assertEqual(self.storage.load_messages(conversation_id, limit=0), [])
        self.assertFalse((self.base / "outside").exists())

    def test_message_validation(self) -> None:
        with self.assertRaises(TypeError):
            self.storage.append_message("room", ["not an object"])  # type: ignore[arg-type]
        with self.assertRaises(ValueError):
            self.storage.load_messages("room", limit=-1)
        with self.assertRaises(ValueError):
            self.storage.append_message("", {"text": "empty id"})

    def test_concurrent_message_appends_are_complete(self) -> None:
        def append(number: int) -> None:
            self.storage.append_message("shared-room", {"number": number})

        with ThreadPoolExecutor(max_workers=8) as executor:
            list(executor.map(append, range(100)))

        messages = self.storage.load_messages("shared-room", limit=100)
        self.assertEqual(len(messages), 100)
        self.assertEqual({message["number"] for message in messages}, set(range(100)))

    def test_file_round_trip_sanitization_filter_and_delete(self) -> None:
        public = self.storage.save_file(
            "../../file-id",
            "../../CON?.txt",
            b"attachment bytes",
            {"room_id": "room-a", "sender": "alice", "size": -1},
        )

        self.assertEqual(public["file_id"], "../../file-id")
        self.assertEqual(public["size"], len(b"attachment bytes"))
        self.assertEqual(public["room_id"], "room-a")
        self.assertNotIn("_storage_name", public)
        self.assertNotRegex(public["original_name"], r"[\\/?*]")
        metadata, payload = self.storage.read_file("../../file-id")
        self.assertEqual(metadata, public)
        self.assertEqual(payload, b"attachment bytes")
        self.assertEqual(self.storage.list_files("room-a"), [public])
        self.assertEqual(self.storage.list_files("other-room"), [])

        stored_names = [path.name for path in self.storage.files_dir.iterdir()]
        self.assertEqual(len(stored_names), 1)
        self.assertTrue(
            re.fullmatch(r"[0-9a-f]{64}-[0-9a-f]{64}\.blob", stored_names[0])
        )
        self.assertFalse((self.base / "file-id").exists())

        self.assertTrue(self.storage.delete_file("../../file-id"))
        self.assertFalse(self.storage.delete_file("../../file-id"))
        with self.assertRaises(FileNotFoundError):
            self.storage.read_file("../../file-id")

    def test_overwriting_file_replaces_blob_and_preserves_created_at(self) -> None:
        first = self.storage.save_file("same", "first.txt", b"first")
        second = self.storage.save_file("same", "second.txt", b"second")

        self.assertEqual(first["created_at"], second["created_at"])
        self.assertEqual(second["original_name"], "second.txt")
        self.assertEqual(self.storage.read_file("same")[1], b"second")
        self.assertEqual(len(list(self.storage.files_dir.iterdir())), 1)

    def test_rejects_oversized_or_invalid_file_data(self) -> None:
        with patch("chat_online.storage.MAX_FILE_BYTES", 3):
            with self.assertRaisesRegex(ValueError, "maximum"):
                self.storage.save_file("large", "large.bin", b"1234")
        with self.assertRaises(TypeError):
            self.storage.save_file("bad", "bad.bin", "text")  # type: ignore[arg-type]
        with self.assertRaises(TypeError):
            self.storage.save_file("bad-meta", "bad.bin", b"x", {"value": b"x"})

    def test_detects_tampered_file(self) -> None:
        self.storage.save_file("file", "file.bin", b"original")
        blob = next(self.storage.files_dir.iterdir())
        blob.write_bytes(b"tampered")

        with self.assertRaisesRegex(StorageError, "integrity"):
            self.storage.read_file("file")

    def test_index_is_valid_and_atomically_replaced(self) -> None:
        self.storage.save_file("file", "file.txt", b"data")

        index = json.loads(self.storage.index_path.read_text(encoding="utf-8"))
        self.assertEqual(index["version"], 1)
        self.assertIn("file", index["files"])
        self.assertEqual(list(self.root.glob("*.tmp")), [])

    def test_environment_variable_selects_default_root(self) -> None:
        configured = self.base / "configured"
        with patch.dict(
            os.environ, {"CHAT_ONLINE_DATA_DIR": str(configured)}, clear=False
        ):
            storage = AppStorage()

        self.assertEqual(storage.root, configured.resolve())
        self.assertTrue(storage.index_path.exists())


if __name__ == "__main__":
    unittest.main()

