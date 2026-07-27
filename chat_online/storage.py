"""Thread-safe local persistence for chat history and shared files."""

from __future__ import annotations

from collections import deque
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import tempfile
import threading
from typing import Any
import unicodedata

from .protocol import MAX_FILE_BYTES


_INDEX_VERSION = 1
_MAX_IDENTIFIER_BYTES = 4096
_STORAGE_NAME_RE = re.compile(r"^[0-9a-f]{64}-[0-9a-f]{64}\.blob$")
_WINDOWS_RESERVED_NAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{number}" for number in range(1, 10)),
    *(f"LPT{number}" for number in range(1, 10)),
}

_LOCKS_GUARD = threading.Lock()
_LOCKS: dict[str, threading.RLock] = {}


class StorageError(RuntimeError):
    """Raised when persisted application data is invalid or inaccessible."""


def _default_data_root() -> Path:
    configured = os.environ.get("CHAT_ONLINE_DATA_DIR", "").strip()
    if configured:
        return Path(configured).expanduser()
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
        if base:
            return Path(base) / "Chat Online"
        return Path.home() / "AppData" / "Local" / "Chat Online"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "Chat Online"
    xdg_data_home = os.environ.get("XDG_DATA_HOME", "").strip()
    base = Path(xdg_data_home).expanduser() if xdg_data_home else Path.home() / ".local" / "share"
    return base / "chat-online"


def _lock_for(root: Path) -> threading.RLock:
    key = os.path.normcase(str(root))
    with _LOCKS_GUARD:
        return _LOCKS.setdefault(key, threading.RLock())


def _validate_identifier(value: str, label: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{label} must be a string")
    if not value.strip():
        raise ValueError(f"{label} cannot be empty")
    if "\x00" in value:
        raise ValueError(f"{label} cannot contain a NUL character")
    if len(value.encode("utf-8")) > _MAX_IDENTIFIER_BYTES:
        raise ValueError(f"{label} is too long")
    return value


def _validate_json_object(value: Any, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise TypeError(f"{label} must be a dictionary")

    def check(item: Any, path: str) -> None:
        if item is None or isinstance(item, (str, bool, int)):
            return
        if isinstance(item, float):
            if item != item or item in (float("inf"), float("-inf")):
                raise ValueError(f"{path} contains a non-finite number")
            return
        if isinstance(item, list):
            for index, child in enumerate(item):
                check(child, f"{path}[{index}]")
            return
        if isinstance(item, dict):
            for key, child in item.items():
                if not isinstance(key, str):
                    raise TypeError(f"{path} contains a non-string key")
                check(child, f"{path}.{key}")
            return
        raise TypeError(f"{path} contains unsupported type {type(item).__name__}")

    check(value, label)
    return deepcopy(value)


def _sanitize_filename(name: str) -> str:
    if not isinstance(name, str):
        raise TypeError("original_name must be a string")
    normalized = unicodedata.normalize("NFKC", name).replace("\\", "/")
    normalized = normalized.rsplit("/", 1)[-1]
    normalized = re.sub(r"[<>:\"/\\|?*\x00-\x1f]", "_", normalized)
    normalized = re.sub(r"\s+", " ", normalized).strip(" .")
    if not normalized:
        normalized = "file"

    stem = normalized.rsplit(".", 1)[0].upper()
    if stem in _WINDOWS_RESERVED_NAMES:
        normalized = f"_{normalized}"
    if len(normalized) > 180:
        suffix = Path(normalized).suffix[:20]
        keep = max(1, 180 - len(suffix))
        normalized = normalized[:keep].rstrip(" .") + suffix
    return normalized or "file"


def _atomic_write_bytes(path: Path, data: bytes) -> None:
    temporary = tempfile.NamedTemporaryFile(
        mode="wb",
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
        delete=False,
    )
    temporary_path = Path(temporary.name)
    try:
        with temporary:
            temporary.write(data)
            temporary.flush()
            os.fsync(temporary.fileno())
        os.replace(temporary_path, path)
    finally:
        try:
            temporary_path.unlink(missing_ok=True)
        except OSError:
            pass


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class AppStorage:
    """Persist messages and file attachments below one isolated data root."""

    def __init__(self, root: Path | str | None = None) -> None:
        selected_root = _default_data_root() if root is None else Path(root).expanduser()
        self.root = selected_root.resolve(strict=False)
        self.messages_dir = self.root / "messages"
        self.files_dir = self.root / "files"
        self.index_path = self.root / "files-index.json"
        self.root.mkdir(parents=True, exist_ok=True)
        self.messages_dir.mkdir(parents=True, exist_ok=True)
        self.files_dir.mkdir(parents=True, exist_ok=True)
        self._lock = _lock_for(self.root)
        with self._lock:
            if not self.index_path.exists():
                self._write_index(self._empty_index())
            else:
                self._read_index()

    @staticmethod
    def _empty_index() -> dict[str, Any]:
        return {"version": _INDEX_VERSION, "files": {}}

    def _history_path(self, conversation_id: str) -> Path:
        identifier = _validate_identifier(conversation_id, "conversation_id")
        digest = hashlib.sha256(identifier.encode("utf-8")).hexdigest()
        return self.messages_dir / f"{digest}.jsonl"

    def _file_path(self, storage_name: str) -> Path:
        if not isinstance(storage_name, str) or not _STORAGE_NAME_RE.fullmatch(storage_name):
            raise StorageError("file index contains an invalid storage name")
        candidate = (self.files_dir / storage_name).resolve(strict=False)
        try:
            candidate.relative_to(self.files_dir.resolve(strict=False))
        except ValueError as exc:
            raise StorageError("file index attempts to escape the storage directory") from exc
        return candidate

    def _read_index(self) -> dict[str, Any]:
        try:
            with self.index_path.open("r", encoding="utf-8") as handle:
                index = json.load(handle)
        except (OSError, json.JSONDecodeError) as exc:
            raise StorageError(f"cannot read file index: {exc}") from exc
        if not isinstance(index, dict) or index.get("version") != _INDEX_VERSION:
            raise StorageError("file index has an unsupported format")
        if not isinstance(index.get("files"), dict):
            raise StorageError("file index does not contain a file mapping")
        return index

    def _write_index(self, index: dict[str, Any]) -> None:
        try:
            payload = json.dumps(
                index,
                ensure_ascii=False,
                allow_nan=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
            _atomic_write_bytes(self.index_path, payload)
        except (OSError, TypeError, ValueError) as exc:
            raise StorageError(f"cannot write file index: {exc}") from exc

    @staticmethod
    def _public_metadata(record: dict[str, Any]) -> dict[str, Any]:
        return deepcopy(
            {key: value for key, value in record.items() if not key.startswith("_")}
        )

    def append_message(self, conversation_id: str, message: dict[str, Any]) -> None:
        """Append one JSON object to a conversation's hashed history file."""

        path = self._history_path(conversation_id)
        safe_message = _validate_json_object(message, "message")
        try:
            encoded = json.dumps(
                safe_message,
                ensure_ascii=False,
                allow_nan=False,
                separators=(",", ":"),
            )
        except (TypeError, ValueError, OverflowError) as exc:
            raise ValueError(f"message cannot be encoded as JSON: {exc}") from exc
        with self._lock:
            try:
                with path.open("a", encoding="utf-8", newline="\n") as handle:
                    handle.write(encoded)
                    handle.write("\n")
                    handle.flush()
            except OSError as exc:
                raise StorageError(f"cannot append chat history: {exc}") from exc

    def load_messages(
        self, conversation_id: str, limit: int = 200
    ) -> list[dict[str, Any]]:
        """Return the newest messages in original chronological order."""

        path = self._history_path(conversation_id)
        if isinstance(limit, bool) or not isinstance(limit, int) or limit < 0:
            raise ValueError("limit must be a non-negative integer")
        if limit == 0 or not path.exists():
            return []
        messages: deque[dict[str, Any]] = deque(maxlen=limit)
        with self._lock:
            try:
                with path.open("r", encoding="utf-8") as handle:
                    for line_number, line in enumerate(handle, start=1):
                        if not line.strip():
                            continue
                        try:
                            message = json.loads(line)
                        except json.JSONDecodeError as exc:
                            raise StorageError(
                                f"chat history is corrupt at line {line_number}: {exc}"
                            ) from exc
                        if not isinstance(message, dict):
                            raise StorageError(
                                f"chat history line {line_number} is not an object"
                            )
                        messages.append(message)
            except OSError as exc:
                raise StorageError(f"cannot read chat history: {exc}") from exc
        return list(messages)

    def save_file(
        self,
        file_id: str,
        original_name: str,
        data: bytes,
        metadata: dict[str, Any] | None = None,
        *,
        max_bytes: int | None = None,
    ) -> dict[str, Any]:
        """Atomically save a bounded attachment and return public metadata."""

        identifier = _validate_identifier(file_id, "file_id")
        safe_name = _sanitize_filename(original_name)
        selected_limit = MAX_FILE_BYTES if max_bytes is None else max_bytes
        if (
            isinstance(selected_limit, bool)
            or not isinstance(selected_limit, int)
            or selected_limit < 1
        ):
            raise ValueError("max_bytes must be a positive integer")
        if not isinstance(data, (bytes, bytearray, memoryview)):
            raise TypeError("data must be bytes-like")
        payload = bytes(data)
        if len(payload) > selected_limit:
            raise ValueError(
                f"file is {len(payload)} bytes; maximum is {selected_limit}"
            )
        custom = (
            {} if metadata is None else _validate_json_object(metadata, "metadata")
        )
        content_hash = hashlib.sha256(payload).hexdigest()
        identifier_hash = hashlib.sha256(identifier.encode("utf-8")).hexdigest()
        storage_name = f"{identifier_hash}-{content_hash}.blob"
        storage_path = self._file_path(storage_name)

        with self._lock:
            index = self._read_index()
            previous = index["files"].get(identifier)
            now = _utc_now()
            record = custom
            record.update(
                {
                    "file_id": identifier,
                    "original_name": safe_name,
                    "size": len(payload),
                    "sha256": content_hash,
                    "created_at": (
                        previous.get("created_at", now)
                        if isinstance(previous, dict)
                        else now
                    ),
                    "updated_at": now,
                    "_storage_name": storage_name,
                }
            )

            _atomic_write_bytes(storage_path, payload)
            index["files"][identifier] = record
            try:
                self._write_index(index)
            except Exception:
                previous_name = (
                    previous.get("_storage_name")
                    if isinstance(previous, dict)
                    else None
                )
                if previous_name != storage_name:
                    storage_path.unlink(missing_ok=True)
                raise

            if isinstance(previous, dict):
                previous_name = previous.get("_storage_name")
                if previous_name and previous_name != storage_name:
                    self._file_path(previous_name).unlink(missing_ok=True)
            return self._public_metadata(record)

    def read_file(self, file_id: str) -> tuple[dict[str, Any], bytes]:
        """Read an attachment and verify it against the persisted metadata."""

        identifier = _validate_identifier(file_id, "file_id")
        with self._lock:
            index = self._read_index()
            record = index["files"].get(identifier)
            if not isinstance(record, dict):
                raise FileNotFoundError(identifier)
            storage_path = self._file_path(record.get("_storage_name"))
            try:
                payload = storage_path.read_bytes()
            except FileNotFoundError as exc:
                raise StorageError(f"stored file is missing for {identifier!r}") from exc
            except OSError as exc:
                raise StorageError(f"cannot read stored file: {exc}") from exc

            expected_size = record.get("size")
            expected_hash = record.get("sha256")
            actual_hash = hashlib.sha256(payload).hexdigest()
            if expected_size != len(payload) or expected_hash != actual_hash:
                raise StorageError(f"stored file failed integrity check for {identifier!r}")
            return self._public_metadata(record), payload

    def delete_file(self, file_id: str) -> bool:
        """Delete an attachment, returning whether it previously existed."""

        identifier = _validate_identifier(file_id, "file_id")
        with self._lock:
            index = self._read_index()
            record = index["files"].pop(identifier, None)
            if record is None:
                return False
            if not isinstance(record, dict):
                raise StorageError("file index contains an invalid record")
            storage_path = self._file_path(record.get("_storage_name"))
            self._write_index(index)
            try:
                storage_path.unlink(missing_ok=True)
            except OSError as exc:
                raise StorageError(f"cannot delete stored file: {exc}") from exc
            return True

    def list_files(self, room_id: str | None = None) -> list[dict[str, Any]]:
        """List public attachment metadata, optionally filtered by room ID."""

        if room_id is not None:
            _validate_identifier(room_id, "room_id")
        with self._lock:
            index = self._read_index()
            records: list[dict[str, Any]] = []
            for record in index["files"].values():
                if not isinstance(record, dict):
                    raise StorageError("file index contains an invalid record")
                if room_id is not None and record.get("room_id") != room_id:
                    continue
                records.append(self._public_metadata(record))
        records.sort(key=lambda item: item.get("updated_at", ""), reverse=True)
        return records
