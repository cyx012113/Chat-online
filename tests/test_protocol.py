from __future__ import annotations

import json
import unittest
from unittest.mock import patch

from chat_online.protocol import PacketReader, ProtocolError, encode_packet


class EncodePacketTests(unittest.TestCase):
    def test_encodes_compact_utf8_jsonl(self) -> None:
        encoded = encode_packet({"type": "message", "text": "你好", "items": [1, True]})

        self.assertTrue(encoded.endswith(b"\n"))
        self.assertNotIn(b"\\u4f60", encoded)
        self.assertEqual(
            json.loads(encoded.decode("utf-8")),
            {"type": "message", "text": "你好", "items": [1, True]},
        )

    def test_rejects_non_object_and_unsupported_values(self) -> None:
        with self.assertRaises(ProtocolError):
            encode_packet(["not", "an", "object"])  # type: ignore[arg-type]
        with self.assertRaises(ProtocolError):
            encode_packet({"data": b"raw bytes"})
        with self.assertRaises(ProtocolError):
            encode_packet({"value": float("nan")})
        with self.assertRaises(ProtocolError):
            encode_packet({1: "non-string key"})  # type: ignore[dict-item]

    def test_rejects_oversized_encoded_frame(self) -> None:
        with patch("chat_online.protocol.MAX_FRAME_BYTES", 20):
            with self.assertRaisesRegex(ProtocolError, "maximum"):
                encode_packet({"value": "x" * 30})


class PacketReaderTests(unittest.TestCase):
    def test_supports_split_packets(self) -> None:
        expected = {"type": "message", "text": "split 你好"}
        wire = encode_packet(expected)
        reader = PacketReader()
        packets: list[dict[str, object]] = []

        for byte in wire:
            packets.extend(reader.feed(bytes([byte])))

        self.assertEqual(packets, [expected])
        self.assertEqual(reader.buffered_bytes, 0)

    def test_supports_coalesced_packets_blank_lines_and_crlf(self) -> None:
        first = {"sequence": 1}
        second = {"sequence": 2, "text": "ok"}
        wire = encode_packet(first).replace(b"\n", b"\r\n")
        wire += b"\n   \r\n"
        wire += encode_packet(second)

        packets = PacketReader().feed(memoryview(wire))

        self.assertEqual(packets, [first, second])

    def test_keeps_incomplete_trailing_frame(self) -> None:
        reader = PacketReader()

        self.assertEqual(reader.feed(b'{"part":'), [])
        self.assertGreater(reader.buffered_bytes, 0)
        self.assertEqual(reader.feed(b'1}\n'), [{"part": 1}])

    def test_rejects_invalid_json_utf8_and_top_level_types(self) -> None:
        invalid_frames = (
            b'{"missing":}\n',
            b'{"bad":"\xff"}\n',
            b'[1,2,3]\n',
            b'{"not_standard":NaN}\n',
        )
        for frame in invalid_frames:
            with self.subTest(frame=frame):
                with self.assertRaises(ProtocolError):
                    PacketReader().feed(frame)

    def test_enforces_limit_with_and_without_delimiter(self) -> None:
        with self.assertRaisesRegex(ProtocolError, "unterminated"):
            PacketReader(max_frame_bytes=8).feed(b"x" * 9)
        with self.assertRaisesRegex(ProtocolError, "frame is"):
            PacketReader(max_frame_bytes=8).feed(b"x" * 9 + b"\n")
        with self.assertRaisesRegex(ProtocolError, "frame is"):
            PacketReader(max_frame_bytes=8).feed(b" " * 9 + b"\n")

    def test_rejects_non_bytes_input(self) -> None:
        with self.assertRaises(ProtocolError):
            PacketReader().feed("not bytes")  # type: ignore[arg-type]


if __name__ == "__main__":
    unittest.main()
