"""Wire protocol helpers for the chat application.

Packets are UTF-8 encoded JSON objects delimited by a single newline.  The
incremental reader is intentionally transport agnostic and can be fed directly
from a TCP receive loop.
"""

from __future__ import annotations

import json
import math
from typing import Any


MAX_FILE_BYTES = 8 * 1024 * 1024
MAX_FRAME_BYTES = 12 * 1024 * 1024


class ProtocolError(ValueError):
    """Raised when a packet violates the wire protocol."""


def _validate_json_value(value: Any, path: str = "packet") -> None:
    if value is None or isinstance(value, (str, bool, int)):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ProtocolError(f"{path} contains a non-finite number")
        return
    if isinstance(value, list):
        for index, item in enumerate(value):
            _validate_json_value(item, f"{path}[{index}]")
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise ProtocolError(f"{path} contains a non-string object key")
            _validate_json_value(item, f"{path}.{key}")
        return
    raise ProtocolError(
        f"{path} contains unsupported value type {type(value).__name__}"
    )


def encode_packet(packet: dict[str, Any]) -> bytes:
    """Serialize one packet as a size-limited UTF-8 JSONL frame."""

    if not isinstance(packet, dict):
        raise ProtocolError("packet must be a JSON object")
    try:
        _validate_json_value(packet)
    except RecursionError as exc:
        raise ProtocolError("packet is nested too deeply") from exc
    try:
        payload = json.dumps(
            packet,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError, OverflowError, RecursionError) as exc:
        raise ProtocolError(f"packet cannot be encoded as JSON: {exc}") from exc
    if len(payload) > MAX_FRAME_BYTES:
        raise ProtocolError(
            f"packet is {len(payload)} bytes; maximum is {MAX_FRAME_BYTES}"
        )
    return payload + b"\n"


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"non-standard JSON constant {value}")


class PacketReader:
    """Incrementally split and decode JSONL packets from a byte stream."""

    def __init__(self, max_frame_bytes: int = MAX_FRAME_BYTES) -> None:
        if (
            isinstance(max_frame_bytes, bool)
            or not isinstance(max_frame_bytes, int)
            or max_frame_bytes <= 0
        ):
            raise ValueError("max_frame_bytes must be a positive integer")
        self._max_frame_bytes = max_frame_bytes
        self._buffer = bytearray()

    @property
    def buffered_bytes(self) -> int:
        """Number of bytes currently waiting for a terminating newline."""

        return len(self._buffer)

    def feed(self, data: bytes) -> list[dict[str, Any]]:
        """Consume a stream chunk and return every complete packet in it."""

        if not isinstance(data, (bytes, bytearray, memoryview)):
            raise ProtocolError("feed data must be bytes-like")
        self._buffer.extend(data)
        packets: list[dict[str, Any]] = []

        while True:
            newline = self._buffer.find(b"\n")
            if newline < 0:
                if len(self._buffer) > self._max_frame_bytes:
                    size = len(self._buffer)
                    self._buffer.clear()
                    raise ProtocolError(
                        f"unterminated frame is {size} bytes; maximum is "
                        f"{self._max_frame_bytes}"
                    )
                break

            raw_line = bytes(self._buffer[:newline])
            del self._buffer[: newline + 1]
            if raw_line.endswith(b"\r"):
                raw_line = raw_line[:-1]
            if len(raw_line) > self._max_frame_bytes:
                raise ProtocolError(
                    f"frame is {len(raw_line)} bytes; maximum is "
                    f"{self._max_frame_bytes}"
                )
            if not raw_line.strip():
                continue

            try:
                text = raw_line.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise ProtocolError(f"frame is not valid UTF-8: {exc}") from exc
            try:
                packet = json.loads(text, parse_constant=_reject_json_constant)
            except (json.JSONDecodeError, ValueError, RecursionError) as exc:
                raise ProtocolError(f"frame contains invalid JSON: {exc}") from exc
            if not isinstance(packet, dict):
                raise ProtocolError("JSON packet must be an object")
            try:
                _validate_json_value(packet)
            except RecursionError as exc:
                raise ProtocolError("JSON packet is nested too deeply") from exc
            packets.append(packet)

        return packets
