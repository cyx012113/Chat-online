"""Threaded chat server with a Qt signal bridge.

The network layer deliberately has no widget dependencies. Worker threads only
emit signals, leaving every UI update on Qt's main thread.
"""

from __future__ import annotations

import base64
from collections import deque
import ipaddress
import secrets
import socket
import ssl
import string
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Iterable

from cryptography.hazmat.primitives.asymmetric import rsa
from PyQt6.QtCore import QObject, pyqtSignal

from .protocol import MAX_FILE_BYTES, PacketReader, ProtocolError, encode_packet
from .secure_protocol import (
    MAX_ENCRYPTED_FILE_BYTES,
    PROTOCOL_VERSION,
    SECURITY_VERSION,
    SecureProtocolError,
    decode_file_ciphertext,
    load_identity_fields,
    require_exact_recipients,
    validate_file_envelope,
    validate_message_envelope,
)
from .security import (
    TLS_ALPN_PROTOCOL,
    SecurityError,
    create_tls_server_context,
    ensure_self_signed_certificate,
)
from .storage import AppStorage, StorageError


MAIN_ROOM_ID = "main"
MAIN_ROOM_NAME = "Main Lounge"
MAX_MESSAGE_CHARS = 4000
MAX_USERNAME_CHARS = 24
MAX_ROOM_NAME_CHARS = 40


class ServerSignals(QObject):
    """Signals emitted by :class:`ChatServerEngine`."""

    log = pyqtSignal(str, str)
    running_changed = pyqtSignal(bool, str, int)
    snapshot_changed = pyqtSignal(object)
    error = pyqtSignal(str)


@dataclass(eq=False)
class ClientSession:
    socket: socket.socket
    address: tuple[str, int]
    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    username: str = ""
    rooms: set[str] = field(default_factory=lambda: {MAIN_ROOM_ID})
    current_room: str = MAIN_ROOM_ID
    send_lock: threading.Lock = field(default_factory=threading.Lock)
    closed: threading.Event = field(default_factory=threading.Event)
    identity_key: rsa.RSAPublicKey | None = None
    identity_key_pem: str = ""
    identity_fingerprint: str = ""

    @property
    def ready(self) -> bool:
        return (
            bool(self.username)
            and self.identity_key is not None
            and bool(self.identity_fingerprint)
            and not self.closed.is_set()
        )


@dataclass
class Room:
    id: str
    name: str
    invite_code: str | None
    owner_id: str | None
    members: set[str] = field(default_factory=set)
    admins: set[str] = field(default_factory=set)
    muted: set[str] = field(default_factory=set)
    created_at: float = field(default_factory=time.time)


class ChatServerEngine:
    """Owns server state and handles JSON-line protocol clients."""

    def __init__(
        self,
        storage: AppStorage | None = None,
        *,
        ssl_context: ssl.SSLContext | None = None,
        tls_handshake_timeout: float = 8.0,
    ) -> None:
        self.signals = ServerSignals()
        self.storage = storage or AppStorage()
        self._lock = threading.RLock()
        self._listener: socket.socket | None = None
        self._accept_thread: threading.Thread | None = None
        self._ssl_context = ssl_context
        self._tls_handshake_timeout = max(0.1, float(tls_handshake_timeout))
        self._tls_certificate_fingerprint = ""
        self._pending_sockets: set[socket.socket] = set()
        self._seen_envelope_ids: set[str] = set()
        self._seen_envelope_order: deque[str] = deque()
        self._stop_event = threading.Event()
        self._sessions: dict[str, ClientSession] = {}
        self._usernames: dict[str, str] = {}
        self._rooms: dict[str, Room] = {
            MAIN_ROOM_ID: Room(
                id=MAIN_ROOM_ID,
                name=MAIN_ROOM_NAME,
                invite_code=None,
                owner_id=None,
            )
        }
        self._banned_networks: set[ipaddress.IPv4Network | ipaddress.IPv6Network] = set()
        self._host = "0.0.0.0"
        self._port = 0
        self._started_at: float | None = None

    @property
    def running(self) -> bool:
        return self._listener is not None and not self._stop_event.is_set()

    @property
    def port(self) -> int:
        return self._port

    def start(self, host: str = "0.0.0.0", port: int = 8888) -> bool:
        """Start listening. Returns ``False`` after emitting a useful error."""
        if self.running:
            return True
        if not 0 <= int(port) <= 65535:
            self.signals.error.emit("Port must be between 0 and 65535.")
            return False

        if self._ssl_context is None:
            security_dir = self.storage.root / "security"
            certificate_path = security_dir / "tls-cert.pem"
            private_key_path = security_dir / "tls-key.pem"
            try:
                certificate = ensure_self_signed_certificate(
                    certificate_path,
                    private_key_path,
                )
                self._ssl_context = create_tls_server_context(
                    certificate.certificate_path,
                    certificate.private_key_path,
                )
                self._tls_certificate_fingerprint = certificate.fingerprint
            except (OSError, SecurityError, ssl.SSLError, ValueError) as exc:
                self.signals.error.emit(f"Could not initialize TLS: {exc}")
                return False

        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            listener.bind((host, int(port)))
            listener.listen(128)
            listener.settimeout(0.5)
        except OSError as exc:
            listener.close()
            self.signals.error.emit(f"Could not start server on {host}:{port}: {exc}")
            return False

        self._listener = listener
        self._host = host
        self._port = int(listener.getsockname()[1])
        self._started_at = time.time()
        self._stop_event.clear()
        self._accept_thread = threading.Thread(
            target=self._accept_loop,
            name="chat-server-accept",
            daemon=True,
        )
        self._accept_thread.start()
        self._emit_log("info", f"Server listening on {host}:{self._port}")
        self.signals.running_changed.emit(True, host, self._port)
        self._emit_snapshot()
        return True

    def stop(self) -> None:
        """Stop accepting connections and close every client session."""
        if not self._listener and self._stop_event.is_set():
            return
        self._stop_event.set()
        listener, self._listener = self._listener, None
        if listener:
            try:
                listener.close()
            except OSError:
                pass

        with self._lock:
            sessions = list(self._sessions.values())
            pending_sockets = list(self._pending_sockets)
            self._pending_sockets.clear()
        for pending_socket in pending_sockets:
            self._close_transport(pending_socket)
        for session in sessions:
            self._send(session, {"type": "server_shutdown", "message": "Server stopped."})
            self._close_socket(session)
        for session in sessions:
            self._disconnect(session, announce=False)

        self._emit_log("info", "Server stopped")
        self.signals.running_changed.emit(False, self._host, self._port)
        self._emit_snapshot()

    def broadcast_system(self, text: str, room_id: str = MAIN_ROOM_ID) -> bool:
        text = self._clean_text(text)
        if not text:
            return False
        with self._lock:
            if room_id not in self._rooms:
                return False
        packet = self._message_packet(
            room_id=room_id,
            sender="Server",
            sender_id="server",
            text=text,
            kind="system",
        )
        self.storage.append_message(room_id, packet)
        self._broadcast_room(room_id, packet)
        self._emit_log("message", f"[{self._rooms[room_id].name}] Server: {text}")
        return True

    def kick_user(self, session_id: str, reason: str = "Removed by server administrator.") -> bool:
        session = self._get_session(session_id)
        if not session:
            return False
        self._send(session, {"type": "kicked", "message": reason})
        self._close_socket(session)
        self._disconnect(session)
        return True

    def mute_user(self, session_id: str, room_id: str, muted: bool = True) -> bool:
        with self._lock:
            room = self._rooms.get(room_id)
            session = self._sessions.get(session_id)
            if not room or not session or session_id not in room.members:
                return False
            if muted:
                room.muted.add(session_id)
            else:
                room.muted.discard(session_id)
        self._send(
            session,
            {
                "type": "moderation",
                "action": "muted" if muted else "unmuted",
                "room_id": room_id,
            },
        )
        self._broadcast_snapshots()
        self._emit_snapshot()
        return True

    def ban_address(self, value: str) -> tuple[bool, str]:
        """Ban an IP or CIDR network and disconnect matching sessions."""
        try:
            network = ipaddress.ip_network(value.strip(), strict=False)
        except ValueError as exc:
            return False, str(exc)
        with self._lock:
            self._banned_networks.add(network)
            sessions = [
                session
                for session in self._sessions.values()
                if ipaddress.ip_address(session.address[0]) in network
            ]
        for session in sessions:
            self.kick_user(session.id, "Your address has been blocked by the server.")
        self._emit_log("warning", f"Blocked network {network}")
        self._emit_snapshot()
        return True, str(network)

    def unban_address(self, value: str) -> bool:
        try:
            network = ipaddress.ip_network(value.strip(), strict=False)
        except ValueError:
            return False
        with self._lock:
            existed = network in self._banned_networks
            self._banned_networks.discard(network)
        if existed:
            self._emit_log("info", f"Unblocked network {network}")
            self._emit_snapshot()
        return existed

    def delete_shared_file(self, file_id: str) -> bool:
        deleted = self.storage.delete_file(file_id)
        if deleted:
            packet = {"type": "file_deleted", "file_id": file_id}
            with self._lock:
                sessions = [session for session in self._sessions.values() if session.ready]
            for session in sessions:
                self._send(session, packet)
            self._broadcast_snapshots()
        return deleted

    def rename_room(self, room_id: str, name: str) -> tuple[bool, str]:
        """Rename a room from the server console."""
        clean_name = " ".join(str(name).split())
        if room_id == MAIN_ROOM_ID:
            return False, "The main room cannot be renamed."
        if not clean_name or len(clean_name) > MAX_ROOM_NAME_CHARS:
            return False, f"Room names may contain 1-{MAX_ROOM_NAME_CHARS} characters."
        with self._lock:
            room = self._rooms.get(room_id)
            if room is None:
                return False, "Room not found."
            if any(
                item.id != room_id and item.name.casefold() == clean_name.casefold()
                for item in self._rooms.values()
            ):
                return False, "A room with that name already exists."
            old_name = room.name
            room.name = clean_name
        self._broadcast_room(
            room_id,
            {
                "type": "room_renamed",
                "room_id": room_id,
                "name": clean_name,
                "old_name": old_name,
            },
        )
        self._broadcast_snapshots()
        self._emit_snapshot()
        self._emit_log("info", f"Room renamed: {old_name} -> {clean_name}")
        return True, clean_name

    def delete_room(self, room_id: str) -> bool:
        """Delete a room and move its connected members to the main room."""
        if room_id == MAIN_ROOM_ID:
            return False
        with self._lock:
            room = self._rooms.pop(room_id, None)
            if room is None:
                return False
            members = [self._sessions[item] for item in room.members if item in self._sessions]
            for session in members:
                session.rooms.discard(room_id)
                session.rooms.add(MAIN_ROOM_ID)
                session.current_room = MAIN_ROOM_ID
                self._rooms[MAIN_ROOM_ID].members.add(session.id)
        for session in members:
            self._send(
                session,
                {"type": "room_deleted", "room_id": room_id, "current_room": MAIN_ROOM_ID},
            )
            self._send_history(session, MAIN_ROOM_ID)
        self._broadcast_snapshots()
        self._emit_snapshot()
        self._emit_log("warning", f"Room deleted: {room.name}")
        return True

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            users = [self._session_public(session) for session in self._sessions.values() if session.ready]
            rooms = [self._room_public(room) for room in self._rooms.values()]
            banned = sorted(str(network) for network in self._banned_networks)
        return {
            "running": self.running,
            "host": self._host,
            "port": self._port,
            "started_at": self._started_at,
            "users": users,
            "rooms": rooms,
            "banned": banned,
            "file_count": len(self.storage.list_files()),
            "tls_version": "TLSv1.3",
            "tls_fingerprint": self._tls_certificate_fingerprint,
        }

    def _accept_loop(self) -> None:
        while not self._stop_event.is_set():
            listener = self._listener
            if listener is None:
                break
            try:
                client_socket, address = listener.accept()
            except socket.timeout:
                continue
            except OSError:
                break

            if self._is_banned(address[0]):
                self._close_transport(client_socket)
                continue

            client_socket.settimeout(self._tls_handshake_timeout)
            with self._lock:
                if self._stop_event.is_set():
                    self._close_transport(client_socket)
                    break
                self._pending_sockets.add(client_socket)
            thread = threading.Thread(
                target=self._secure_client_loop,
                args=(client_socket, address),
                name=f"chat-client-{address[0]}:{address[1]}",
                daemon=True,
            )
            thread.start()

    def _secure_client_loop(
        self, client_socket: socket.socket, address: tuple[str, int]
    ) -> None:
        tls_socket: ssl.SSLSocket | None = None
        session: ClientSession | None = None
        try:
            context = self._ssl_context
            if context is None:
                raise SecurityError("TLS server context is unavailable")
            tls_socket = context.wrap_socket(
                client_socket,
                server_side=True,
                do_handshake_on_connect=False,
            )
            with self._lock:
                self._pending_sockets.discard(client_socket)
                self._pending_sockets.add(tls_socket)
            tls_socket.do_handshake()
            if tls_socket.version() != "TLSv1.3":
                raise SecurityError("client did not negotiate TLS 1.3")
            if tls_socket.selected_alpn_protocol() != TLS_ALPN_PROTOCOL:
                raise SecurityError("client did not negotiate the Chat Online protocol")
            tls_socket.settimeout(0.5)
            with self._lock:
                self._pending_sockets.discard(tls_socket)
            if self._stop_event.is_set():
                return
            session = ClientSession(tls_socket, address)
            self._client_loop(session)
        except (OSError, SecurityError, ssl.SSLError) as exc:
            if not self._stop_event.is_set():
                self._emit_log(
                    "warning",
                    f"TLS connection error from {address[0]}:{address[1]}: {exc}",
                )
        finally:
            with self._lock:
                self._pending_sockets.discard(client_socket)
                if tls_socket is not None:
                    self._pending_sockets.discard(tls_socket)
            if session is None:
                self._close_transport(tls_socket or client_socket)

    def _client_loop(self, session: ClientSession) -> None:
        reader = PacketReader()
        connected_at = time.monotonic()
        try:
            while not self._stop_event.is_set() and not session.closed.is_set():
                if not session.username and time.monotonic() - connected_at > 15:
                    self._send_error(session, "hello_timeout", "Login timed out.")
                    break
                try:
                    data = session.socket.recv(65536)
                except socket.timeout:
                    continue
                if not data:
                    break
                for packet in reader.feed(data):
                    self._handle_packet(session, packet)
        except ProtocolError as exc:
            self._send_error(session, "protocol_error", str(exc))
        except OSError as exc:
            if not session.closed.is_set() and not self._stop_event.is_set():
                self._emit_log("warning", f"Connection error from {session.address[0]}: {exc}")
        finally:
            self._close_socket(session)
            self._disconnect(session)

    def _handle_packet(self, session: ClientSession, packet: dict[str, Any]) -> None:
        packet_type = packet.get("type")
        if not session.username:
            if packet_type != "hello":
                self._send_error(session, "hello_required", "Send a hello packet first.")
                return
            self._handle_hello(session, packet)
            return

        handlers = {
            "message": self._handle_message,
            "private_message": self._handle_private_message,
            "private_history": self._handle_private_history,
            "create_room": self._handle_create_room,
            "join_room": self._handle_join_room,
            "leave_room": self._handle_leave_room,
            "switch_room": self._handle_switch_room,
            "room_action": self._handle_room_action,
            "upload_file": self._handle_upload_file,
            "download_file": self._handle_download_file,
            "typing": self._handle_typing,
            "ping": self._handle_ping,
            "disconnect": self._handle_disconnect,
        }
        handler = handlers.get(str(packet_type))
        if handler is None:
            self._send_error(session, "unknown_packet", f"Unknown packet type: {packet_type!r}")
            return
        handler(session, packet)

    def _handle_hello(self, session: ClientSession, packet: dict[str, Any]) -> None:
        username = " ".join(str(packet.get("username", "")).split())
        if not username or len(username) > MAX_USERNAME_CHARS or any(ord(char) < 32 for char in username):
            self._send_error(
                session,
                "invalid_username",
                f"Username must contain 1-{MAX_USERNAME_CHARS} printable characters.",
            )
            return
        if packet.get("protocol_version") != PROTOCOL_VERSION:
            self._send_error(
                session,
                "incompatible_protocol",
                f"Encrypted protocol version {PROTOCOL_VERSION} is required.",
            )
            return
        if packet.get("security_version") != SECURITY_VERSION:
            self._send_error(
                session,
                "incompatible_security",
                f"Security protocol version {SECURITY_VERSION} is required.",
            )
            return
        try:
            identity_key, identity_fingerprint = load_identity_fields(packet)
        except (SecurityError, SecureProtocolError, TypeError, ValueError) as exc:
            self._send_error(session, "invalid_identity", str(exc))
            return
        folded = username.casefold()
        with self._lock:
            if folded in self._usernames:
                self._send_error(session, "duplicate_username", "That username is already online.")
                return
            session.username = username
            session.identity_key = identity_key
            session.identity_key_pem = str(packet.get("identity_key", ""))
            session.identity_fingerprint = identity_fingerprint
            self._sessions[session.id] = session
            self._usernames[folded] = session.id
            self._rooms[MAIN_ROOM_ID].members.add(session.id)

        self._send(
            session,
            {
                "type": "hello_ok",
                "session_id": session.id,
                "username": username,
                "current_room": MAIN_ROOM_ID,
                "max_file_bytes": MAX_FILE_BYTES,
                "protocol_version": PROTOCOL_VERSION,
                "security_version": SECURITY_VERSION,
                "e2ee_required": True,
            },
        )
        self._send_history(session, MAIN_ROOM_ID)
        self._send_snapshot(session)
        joined = self._message_packet(
            MAIN_ROOM_ID,
            "System",
            "system",
            f"{username} joined the server.",
            "system",
        )
        self._broadcast_room(MAIN_ROOM_ID, joined, exclude={session.id})
        self._emit_log("info", f"{username} connected from {session.address[0]}:{session.address[1]}")
        self._broadcast_snapshots()
        self._emit_snapshot()

    def _handle_message(self, session: ClientSession, packet: dict[str, Any]) -> None:
        room_id = str(packet.get("room_id") or session.current_room)
        envelope = packet.get("envelope")
        if not isinstance(envelope, dict) or session.identity_key is None:
            self._send_error(session, "encryption_required", "A signed encrypted message is required.")
            return
        with self._lock:
            room = self._rooms.get(room_id)
            allowed = room is not None and session.id in room.members
            muted = bool(room and session.id in room.muted)
            recipient_fingerprints = {
                self._sessions[member_id].identity_fingerprint
                for member_id in (room.members if room else set())
                if member_id in self._sessions and self._sessions[member_id].ready
            }
        if not allowed:
            self._send_error(session, "not_in_room", "You are not a member of that room.")
            return
        if muted:
            self._send_error(session, "muted", "You are muted in this room.")
            return
        try:
            metadata = validate_message_envelope(
                envelope,
                session.identity_key,
                scope="room",
                room_id=room_id,
            )
            require_exact_recipients(envelope, recipient_fingerprints)
        except (SecurityError, SecureProtocolError, TypeError, ValueError) as exc:
            self._send_error(session, "invalid_envelope", str(exc))
            return
        message_id = str(metadata["id"])
        if not self._register_envelope_id(message_id):
            self._send_error(session, "replayed_envelope", "This encrypted message was already received.")
            return
        message = self._encrypted_message_packet(
            session,
            envelope,
            message_id=message_id,
            scope="room",
            room_id=room_id,
            timestamp=float(metadata["timestamp"]),
        )
        self.storage.append_message(room_id, message)
        self._broadcast_room(room_id, message)
        self._emit_log("message", f"[{room.name}] {session.username}: [encrypted message]")

    def _handle_private_message(self, session: ClientSession, packet: dict[str, Any]) -> None:
        target_name = str(packet.get("to", "")).strip()
        envelope = packet.get("envelope")
        if not target_name:
            return
        if not isinstance(envelope, dict) or session.identity_key is None:
            self._send_error(session, "encryption_required", "A signed encrypted message is required.")
            return
        target = self._find_session_by_username(target_name)
        if target is None:
            self._send_error(session, "user_offline", f"{target_name} is not online.")
            return
        if target.identity_key is None:
            self._send_error(session, "invalid_identity", "The recipient has no valid identity key.")
            return
        try:
            metadata = validate_message_envelope(
                envelope,
                session.identity_key,
                scope="private",
                target=target.username,
            )
            require_exact_recipients(
                envelope,
                {session.identity_fingerprint, target.identity_fingerprint},
            )
        except (SecurityError, SecureProtocolError, TypeError, ValueError) as exc:
            self._send_error(session, "invalid_envelope", str(exc))
            return
        message_id = str(metadata["id"])
        if not self._register_envelope_id(message_id):
            self._send_error(session, "replayed_envelope", "This encrypted message was already received.")
            return
        conversation_id = self._private_conversation_id(session.username, target.username)
        message = self._encrypted_message_packet(
            session,
            envelope,
            message_id=message_id,
            scope="private",
            conversation_id=conversation_id,
            target=target.username,
            timestamp=float(metadata["timestamp"]),
        )
        self.storage.append_message(conversation_id, message)
        self._send(session, message)
        if target.id != session.id:
            self._send(target, message)
        self._emit_log(
            "message",
            f"[Private] {session.username} -> {target.username}: [encrypted message]",
        )

    def _handle_private_history(self, session: ClientSession, packet: dict[str, Any]) -> None:
        target_name = str(packet.get("with", "")).strip()
        target = self._find_session_by_username(target_name)
        canonical_name = target.username if target else target_name
        if not canonical_name:
            return
        conversation_id = self._private_conversation_id(session.username, canonical_name)
        self._send(
            session,
            {
                "type": "history",
                "scope": "private",
                "conversation_id": conversation_id,
                "with": canonical_name,
                "messages": self._load_secure_history(conversation_id),
            },
        )

    def _handle_create_room(self, session: ClientSession, packet: dict[str, Any]) -> None:
        name = " ".join(str(packet.get("name", "")).split())
        if not name or len(name) > MAX_ROOM_NAME_CHARS:
            self._send_error(session, "invalid_room_name", f"Room names may contain 1-{MAX_ROOM_NAME_CHARS} characters.")
            return
        with self._lock:
            if any(room.name.casefold() == name.casefold() for room in self._rooms.values()):
                self._send_error(session, "duplicate_room", "A room with that name already exists.")
                return
            room_id = f"room-{uuid.uuid4().hex[:10]}"
            invite_code = "".join(secrets.choice(string.ascii_uppercase + string.digits) for _ in range(8))
            room = Room(room_id, name, invite_code, session.id, {session.id})
            self._rooms[room_id] = room
            session.rooms.add(room_id)
            session.current_room = room_id
        self._send(
            session,
            {
                "type": "room_joined",
                "room": self._room_for_session(room, session),
                "invite_code": invite_code,
            },
        )
        self._send_history(session, room_id)
        self._broadcast_snapshots()
        self._emit_snapshot()
        self._emit_log("info", f"{session.username} created room {name}")

    def _handle_join_room(self, session: ClientSession, packet: dict[str, Any]) -> None:
        invite_code = str(packet.get("invite_code", "")).strip().upper()
        with self._lock:
            room = next((item for item in self._rooms.values() if item.invite_code == invite_code), None)
            if room is None:
                self._send_error(session, "invalid_invite", "Invite code not found.")
                return
            room.members.add(session.id)
            session.rooms.add(room.id)
            session.current_room = room.id
        self._send(session, {"type": "room_joined", "room": self._room_for_session(room, session)})
        self._send_history(session, room.id)
        self._broadcast_snapshots()
        self._emit_snapshot()

    def _handle_leave_room(self, session: ClientSession, packet: dict[str, Any]) -> None:
        room_id = str(packet.get("room_id", ""))
        if room_id == MAIN_ROOM_ID:
            self._send_error(session, "main_room", "The main room cannot be left.")
            return
        with self._lock:
            room = self._rooms.get(room_id)
            if room is None or session.id not in room.members:
                self._send_error(session, "not_in_room", "You are not a member of that room.")
                return
            self._remove_member_from_room(room, session)
            deleted = not room.members
            if deleted:
                self._rooms.pop(room_id, None)
            session.current_room = MAIN_ROOM_ID
        self._send(session, {"type": "room_left", "room_id": room_id, "current_room": MAIN_ROOM_ID})
        self._broadcast_snapshots()
        self._emit_snapshot()

    def _handle_switch_room(self, session: ClientSession, packet: dict[str, Any]) -> None:
        room_id = str(packet.get("room_id", ""))
        with self._lock:
            room = self._rooms.get(room_id)
            if room is None or session.id not in room.members:
                self._send_error(session, "not_in_room", "You are not a member of that room.")
                return
            session.current_room = room_id
        self._send(session, {"type": "room_switched", "room": self._room_for_session(room, session)})
        self._send_history(session, room_id)
        self._broadcast_snapshots()

    def _handle_room_action(self, session: ClientSession, packet: dict[str, Any]) -> None:
        room_id = str(packet.get("room_id", ""))
        action = str(packet.get("action", ""))
        target_id = str(packet.get("target_id", ""))
        with self._lock:
            room = self._rooms.get(room_id)
            target = self._sessions.get(target_id)
            if room is None or target is None or target_id not in room.members:
                self._send_error(session, "invalid_target", "Room or target user not found.")
                return
            is_owner = room.owner_id == session.id
            is_admin = session.id in room.admins
            if not (is_owner or is_admin):
                self._send_error(session, "permission_denied", "You cannot manage this room.")
                return
            if target_id == room.owner_id and action in {"kick", "mute", "demote"}:
                self._send_error(session, "permission_denied", "The room owner cannot be targeted.")
                return
            if action in {"promote", "demote"} and not is_owner:
                self._send_error(session, "permission_denied", "Only the room owner can manage administrators.")
                return

            if action == "mute":
                room.muted.add(target_id)
            elif action == "unmute":
                room.muted.discard(target_id)
            elif action == "promote":
                room.admins.add(target_id)
            elif action == "demote":
                room.admins.discard(target_id)
            elif action == "kick":
                self._remove_member_from_room(room, target)
                target.current_room = MAIN_ROOM_ID
            else:
                self._send_error(session, "invalid_action", f"Unsupported room action: {action}")
                return

        self._send(target, {"type": "moderation", "action": action, "room_id": room_id})
        self._broadcast_snapshots()
        self._emit_snapshot()

    def _handle_upload_file(self, session: ClientSession, packet: dict[str, Any]) -> None:
        room_id = str(packet.get("room_id") or session.current_room)
        with self._lock:
            room = self._rooms.get(room_id)
            allowed = room is not None and session.id in room.members
            muted = bool(room and session.id in room.muted)
            recipient_fingerprints = {
                self._sessions[member_id].identity_fingerprint
                for member_id in (room.members if room else set())
                if member_id in self._sessions and self._sessions[member_id].ready
            }
        if not allowed:
            self._send_error(session, "not_in_room", "You are not a member of that room.")
            return
        if muted:
            self._send_error(session, "muted", "You are muted in this room.")
            return
        envelope = packet.get("envelope")
        if not isinstance(envelope, dict) or session.identity_key is None:
            self._send_error(session, "encryption_required", "A signed encrypted file is required.")
            return
        try:
            raw = decode_file_ciphertext(packet.get("data"))
            unsigned = validate_file_envelope(
                envelope,
                session.identity_key,
                room_id=room_id,
                ciphertext=raw,
            )
            require_exact_recipients(envelope, recipient_fingerprints)
        except (SecurityError, SecureProtocolError, TypeError, ValueError) as exc:
            self._send_error(session, "invalid_file", str(exc))
            return
        file_id = str(unsigned["file_id"])
        if not self._register_envelope_id(file_id):
            self._send_error(session, "replayed_file", "This encrypted file was already received.")
            return
        uploaded_at = float(unsigned["metadata"]["timestamp"])
        try:
            stored_metadata = self.storage.save_file(
                file_id,
                "encrypted-file",
                raw,
                {
                    "room_id": room_id,
                    "sender": session.username,
                    "sender_id": session.id,
                    "sender_identity_key": session.identity_key_pem,
                    "sender_identity_fingerprint": session.identity_fingerprint,
                    "uploaded_at": uploaded_at,
                    "plaintext_size": len(raw) - 16,
                    "envelope": envelope,
                },
                max_bytes=MAX_ENCRYPTED_FILE_BYTES,
            )
        except (OSError, StorageError, ValueError) as exc:
            self._send_error(session, "file_storage_error", f"Could not store file: {exc}")
            return
        metadata = self._file_public(stored_metadata)
        event = {
            "type": "file_shared",
            "room_id": room_id,
            "file": metadata,
            "sender": session.username,
            "sender_id": session.id,
            "timestamp": uploaded_at,
        }
        self.storage.append_message(room_id, event)
        self._broadcast_room(room_id, event)
        self._emit_log(
            "info",
            f"{session.username} shared an encrypted file in {room.name}",
        )
        self._emit_snapshot()

    def _handle_download_file(self, session: ClientSession, packet: dict[str, Any]) -> None:
        file_id = str(packet.get("file_id", ""))
        try:
            stored_metadata, data = self.storage.read_file(file_id)
        except (FileNotFoundError, KeyError, StorageError):
            self._send_error(session, "file_not_found", "The requested file no longer exists.")
            return
        if not self._is_secure_file_metadata(stored_metadata):
            self._send_error(
                session,
                "legacy_file_unsupported",
                "Legacy plaintext files are not served by the encrypted protocol.",
            )
            return
        metadata = self._file_public(stored_metadata)
        room_id = str(metadata.get("room_id", ""))
        with self._lock:
            allowed = room_id in session.rooms
        if not allowed:
            self._send_error(session, "permission_denied", "You cannot download this file.")
            return
        self._send(
            session,
            {
                "type": "file_data",
                "file": metadata,
                "data": base64.b64encode(data).decode("ascii"),
            },
        )

    def _handle_typing(self, session: ClientSession, packet: dict[str, Any]) -> None:
        room_id = str(packet.get("room_id") or session.current_room)
        with self._lock:
            room = self._rooms.get(room_id)
            if room is None or session.id not in room.members:
                return
        self._broadcast_room(
            room_id,
            {
                "type": "typing",
                "room_id": room_id,
                "session_id": session.id,
                "username": session.username,
                "active": bool(packet.get("active")),
            },
            exclude={session.id},
        )

    def _handle_ping(self, session: ClientSession, packet: dict[str, Any]) -> None:
        self._send(session, {"type": "pong", "timestamp": packet.get("timestamp", time.time())})

    def _handle_disconnect(self, session: ClientSession, _packet: dict[str, Any]) -> None:
        self._close_socket(session)

    def _send_history(self, session: ClientSession, room_id: str) -> None:
        self._send(
            session,
            {
                "type": "history",
                "scope": "room",
                "room_id": room_id,
                "messages": self._load_secure_history(room_id),
            },
        )

    def _send_snapshot(self, session: ClientSession) -> None:
        with self._lock:
            rooms = [
                self._room_for_session(self._rooms[room_id], session)
                for room_id in session.rooms
                if room_id in self._rooms
            ]
            current_room = self._rooms.get(session.current_room, self._rooms[MAIN_ROOM_ID])
            users = [
                self._session_for_room(
                    self._sessions[user_id], current_room, viewer_id=session.id
                )
                for user_id in current_room.members
                if user_id in self._sessions and self._sessions[user_id].ready
            ]
            files = [
                self._file_public(item)
                for item in self.storage.list_files(current_room.id)
                if self._is_secure_file_metadata(item)
            ]
        self._send(
            session,
            {
                "type": "snapshot",
                "current_room": current_room.id,
                "rooms": sorted(rooms, key=lambda item: (item["id"] != MAIN_ROOM_ID, item["name"].casefold())),
                "users": sorted(users, key=lambda item: item["username"].casefold()),
                "files": files,
            },
        )

    def _broadcast_snapshots(self) -> None:
        with self._lock:
            sessions = [session for session in self._sessions.values() if session.ready]
        for session in sessions:
            self._send_snapshot(session)

    def _broadcast_room(
        self,
        room_id: str,
        packet: dict[str, Any],
        exclude: Iterable[str] = (),
    ) -> None:
        excluded = set(exclude)
        with self._lock:
            room = self._rooms.get(room_id)
            sessions = [
                self._sessions[session_id]
                for session_id in (room.members if room else set())
                if session_id in self._sessions and session_id not in excluded
            ]
        for session in sessions:
            self._send(session, packet)

    def _send(self, session: ClientSession, packet: dict[str, Any]) -> bool:
        if session.closed.is_set():
            return False
        try:
            encoded = encode_packet(packet)
            with session.send_lock:
                session.socket.sendall(encoded)
            return True
        except (OSError, ProtocolError):
            self._close_socket(session)
            return False

    def _send_error(self, session: ClientSession, code: str, message: str) -> None:
        self._send(session, {"type": "error", "code": code, "message": message})

    def _disconnect(self, session: ClientSession, announce: bool = True) -> None:
        with self._lock:
            existing = self._sessions.pop(session.id, None)
            if existing is None:
                return
            self._usernames.pop(session.username.casefold(), None)
            for room_id in list(session.rooms):
                room = self._rooms.get(room_id)
                if room:
                    self._remove_member_from_room(room, session)
                    if room.id != MAIN_ROOM_ID and not room.members:
                        self._rooms.pop(room.id, None)
        if announce and session.username and not self._stop_event.is_set():
            left = self._message_packet(
                MAIN_ROOM_ID,
                "System",
                "system",
                f"{session.username} left the server.",
                "system",
            )
            self._broadcast_room(MAIN_ROOM_ID, left)
            self._emit_log("info", f"{session.username} disconnected")
        self._broadcast_snapshots()
        self._emit_snapshot()

    @staticmethod
    def _close_socket(session: ClientSession) -> None:
        if session.closed.is_set():
            return
        session.closed.set()
        ChatServerEngine._close_transport(session.socket)

    @staticmethod
    def _close_transport(sock: socket.socket) -> None:
        try:
            sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        try:
            sock.close()
        except OSError:
            pass

    def _remove_member_from_room(self, room: Room, session: ClientSession) -> None:
        room.members.discard(session.id)
        room.admins.discard(session.id)
        room.muted.discard(session.id)
        session.rooms.discard(room.id)
        if room.owner_id == session.id:
            room.owner_id = next(iter(room.members), None)

    def _is_banned(self, address: str) -> bool:
        ip = ipaddress.ip_address(address)
        with self._lock:
            return any(ip in network for network in self._banned_networks)

    def _get_session(self, session_id: str) -> ClientSession | None:
        with self._lock:
            return self._sessions.get(session_id)

    def _find_session_by_username(self, username: str) -> ClientSession | None:
        with self._lock:
            session_id = self._usernames.get(username.casefold())
            return self._sessions.get(session_id) if session_id else None

    def _register_envelope_id(self, envelope_id: str) -> bool:
        """Remember a bounded set of IDs and reject live replay attempts."""

        with self._lock:
            if envelope_id in self._seen_envelope_ids:
                return False
            self._seen_envelope_ids.add(envelope_id)
            self._seen_envelope_order.append(envelope_id)
            while len(self._seen_envelope_order) > 20_000:
                expired = self._seen_envelope_order.popleft()
                self._seen_envelope_ids.discard(expired)
            return True

    @staticmethod
    def _private_conversation_id(first: str, second: str) -> str:
        names = sorted((first.casefold(), second.casefold()))
        return f"private:{names[0]}:{names[1]}"

    @staticmethod
    def _clean_text(value: Any) -> str:
        text = str(value or "").replace("\x00", "").strip()
        return text[:MAX_MESSAGE_CHARS]

    @staticmethod
    def _message_packet(
        room_id: str,
        sender: str,
        sender_id: str,
        text: str,
        kind: str,
    ) -> dict[str, Any]:
        return {
            "type": "message",
            "id": uuid.uuid4().hex,
            "scope": "room",
            "room_id": room_id,
            "sender": sender,
            "sender_id": sender_id,
            "text": text,
            "kind": kind,
            "timestamp": time.time(),
        }

    @staticmethod
    def _encrypted_message_packet(
        session: ClientSession,
        envelope: dict[str, Any],
        *,
        message_id: str,
        scope: str,
        timestamp: float,
        room_id: str | None = None,
        conversation_id: str | None = None,
        target: str | None = None,
    ) -> dict[str, Any]:
        packet: dict[str, Any] = {
            "type": "message",
            "id": message_id,
            "scope": scope,
            "sender": session.username,
            "sender_id": session.id,
            "sender_identity_key": session.identity_key_pem,
            "sender_identity_fingerprint": session.identity_fingerprint,
            "envelope": envelope,
            "kind": "chat",
            "timestamp": timestamp,
        }
        if room_id is not None:
            packet["room_id"] = room_id
        if conversation_id is not None:
            packet["conversation_id"] = conversation_id
        if target is not None:
            packet["to"] = target
        return packet

    def _session_public(self, session: ClientSession) -> dict[str, Any]:
        return {
            "id": session.id,
            "username": session.username,
            "ip": session.address[0],
            "port": session.address[1],
            "current_room": session.current_room,
            "rooms": sorted(session.rooms),
            "identity_key": session.identity_key_pem,
            "identity_fingerprint": session.identity_fingerprint,
        }

    def _session_for_room(
        self, session: ClientSession, room: Room, *, viewer_id: str = ""
    ) -> dict[str, Any]:
        role = "owner" if room.owner_id == session.id else "admin" if session.id in room.admins else "member"
        return {
            "id": session.id,
            "username": session.username,
            "role": role,
            "muted": session.id in room.muted,
            "self": session.id == viewer_id,
            "identity_key": session.identity_key_pem,
            "identity_fingerprint": session.identity_fingerprint,
        }

    def _room_public(self, room: Room) -> dict[str, Any]:
        return {
            "id": room.id,
            "name": room.name,
            "member_count": len(room.members),
            "owner_id": room.owner_id,
            "invite_code": room.invite_code,
            "created_at": room.created_at,
        }

    def _room_for_session(self, room: Room, session: ClientSession) -> dict[str, Any]:
        role = "owner" if room.owner_id == session.id else "admin" if session.id in room.admins else "member"
        return {
            "id": room.id,
            "name": room.name,
            "member_count": len(room.members),
            "role": role,
            "muted": session.id in room.muted,
            "invite_code": room.invite_code if role in {"owner", "admin"} else None,
        }

    def _load_secure_history(self, conversation_id: str) -> list[dict[str, Any]]:
        """Exclude legacy user plaintext instead of downgrading the protocol."""

        result: list[dict[str, Any]] = []
        for item in self.storage.load_messages(conversation_id):
            packet_type = str(item.get("type", "")).lower()
            is_system = (
                str(item.get("kind", "")).lower() == "system"
                or str(item.get("sender_id", "")).lower() in {"system", "server"}
            )
            if packet_type == "message" and is_system:
                result.append(item)
            elif (
                packet_type == "message"
                and isinstance(item.get("envelope"), dict)
                and "text" not in item
            ):
                result.append(item)
            elif packet_type == "file_shared":
                metadata = item.get("file")
                if isinstance(metadata, dict) and self._is_secure_file_metadata(metadata):
                    result.append(item)
        return result

    @staticmethod
    def _is_secure_file_metadata(metadata: dict[str, Any]) -> bool:
        envelope = metadata.get("envelope")
        return (
            isinstance(envelope, dict)
            and envelope.get("kind") == "file"
            and bool(metadata.get("sender_identity_key"))
            and bool(metadata.get("sender_identity_fingerprint"))
        )

    @staticmethod
    def _file_public(metadata: dict[str, Any]) -> dict[str, Any]:
        """Normalize storage metadata into stable wire-facing field names."""
        result = dict(metadata)
        result["id"] = str(metadata.get("file_id", metadata.get("id", "")))
        result["name"] = "Encrypted file"
        result["ciphertext_size"] = int(metadata.get("size", 0))
        result["size"] = int(
            metadata.get("plaintext_size", max(0, result["ciphertext_size"] - 16))
        )
        result.pop("file_id", None)
        result.pop("original_name", None)
        return result

    def _emit_snapshot(self) -> None:
        self.signals.snapshot_changed.emit(self.snapshot())

    def _emit_log(self, level: str, message: str) -> None:
        self.signals.log.emit(level, message)
