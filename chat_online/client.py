"""Asynchronous socket transport for the PyQt6 chat client."""

from __future__ import annotations

import socket
import threading
from typing import Any

from PyQt6.QtCore import QObject, pyqtSignal

from .protocol import PacketReader, ProtocolError, encode_packet


class ClientSignals(QObject):
    """Signals emitted by :class:`ChatClientConnection`."""

    state_changed = pyqtSignal(str, str)
    packet_received = pyqtSignal(object)
    connected = pyqtSignal(object)
    disconnected = pyqtSignal(str, bool)
    error = pyqtSignal(str)


class _HandshakeRejected(ConnectionError):
    pass


class ChatClientConnection(QObject):
    """Own a reconnectable client socket without blocking the Qt event loop.

    Network work happens on a daemon thread.  A monotonically increasing
    generation number prevents a superseded worker from updating state or
    emitting signals after a reconnect.
    """

    def __init__(
        self,
        parent: QObject | None = None,
        *,
        connect_timeout: float = 8.0,
    ) -> None:
        super().__init__(parent)
        self.signals = ClientSignals(self)
        self._connect_timeout = max(0.1, float(connect_timeout))
        self._lock = threading.RLock()
        self._send_lock = threading.Lock()
        self._generation = 0
        self._stop_event: threading.Event | None = None
        self._socket: socket.socket | None = None
        self._thread: threading.Thread | None = None
        self._state = "disconnected"
        self._is_connected = False
        self._host = ""
        self._port: int | None = None
        self._username = ""

    @property
    def is_connected(self) -> bool:
        with self._lock:
            return self._is_connected

    @property
    def host(self) -> str:
        with self._lock:
            return self._host

    @property
    def port(self) -> int | None:
        with self._lock:
            return self._port

    @property
    def username(self) -> str:
        with self._lock:
            return self._username

    @property
    def state(self) -> str:
        with self._lock:
            return self._state

    def connect_to(self, host: str, port: int, username: str) -> bool:
        """Start connecting in the background and return immediately."""

        try:
            clean_host, clean_port, clean_username = self._validate_parameters(
                host, port, username
            )
        except (TypeError, ValueError) as exc:
            self.signals.error.emit(str(exc))
            return False

        stop_event = threading.Event()
        with self._lock:
            old_active = self._state != "disconnected" or self._thread is not None
            old_socket = self._socket
            if self._stop_event is not None:
                self._stop_event.set()

            self._generation += 1
            generation = self._generation
            self._socket = None
            self._is_connected = False
            self._host = clean_host
            self._port = clean_port
            self._username = clean_username
            self._stop_event = stop_event
            self._state = "connecting"
            thread = threading.Thread(
                target=self._connection_loop,
                args=(generation, stop_event, clean_host, clean_port, clean_username),
                name=f"chat-connection-{generation}",
                daemon=True,
            )
            self._thread = thread

            if old_active:
                self.signals.disconnected.emit(
                    "Superseded by a new connection.", True
                )
            self.signals.state_changed.emit(
                "connecting", f"Connecting to {clean_host}:{clean_port}..."
            )

        self._close_socket(old_socket)
        thread.start()
        return True

    def disconnect(self) -> None:
        """Stop the active connection and suppress stale worker callbacks."""

        with self._lock:
            active = (
                self._state != "disconnected"
                or self._thread is not None
                or self._socket is not None
            )
            if not active:
                return

            self._generation += 1
            if self._stop_event is not None:
                self._stop_event.set()
            sock = self._socket
            self._socket = None
            self._thread = None
            self._stop_event = None
            self._is_connected = False
            self._state = "disconnected"
            self._close_socket(sock)
            reason = "Disconnected by user."
            self.signals.state_changed.emit("disconnected", reason)
            self.signals.disconnected.emit(reason, True)

    def send(self, packet: dict[str, Any]) -> bool:
        """Send one packet when the hello handshake has completed."""

        try:
            frame = encode_packet(packet)
        except ProtocolError as exc:
            self.signals.error.emit(f"Protocol error: {exc}")
            return False

        with self._send_lock:
            with self._lock:
                if not self._is_connected or self._socket is None:
                    return False
                generation = self._generation
                sock = self._socket
            try:
                sock.sendall(frame)
            except OSError as exc:
                self._fail_send(generation, sock, exc)
                return False
            with self._lock:
                return (
                    generation == self._generation
                    and self._is_connected
                    and self._socket is sock
                )

    @staticmethod
    def _validate_parameters(
        host: str, port: int, username: str
    ) -> tuple[str, int, str]:
        clean_host = str(host).strip()
        if not clean_host:
            raise ValueError("Server address is required.")
        if isinstance(port, bool):
            raise ValueError("Port must be an integer from 1 to 65535.")
        try:
            clean_port = int(port)
        except (TypeError, ValueError) as exc:
            raise ValueError("Port must be an integer from 1 to 65535.") from exc
        if not 1 <= clean_port <= 65535:
            raise ValueError("Port must be an integer from 1 to 65535.")
        clean_username = " ".join(str(username).split())
        if not clean_username:
            raise ValueError("Username is required.")
        return clean_host, clean_port, clean_username

    def _connection_loop(
        self,
        generation: int,
        stop_event: threading.Event,
        host: str,
        port: int,
        username: str,
    ) -> None:
        sock: socket.socket | None = None
        reason = "Connection closed by the server."
        error_message: str | None = None
        try:
            sock = socket.create_connection(
                (host, port), timeout=self._connect_timeout
            )
            sock.settimeout(0.5)
            with self._lock:
                if generation != self._generation or stop_event.is_set():
                    return
                self._socket = sock
                self._state = "handshaking"
                self.signals.state_changed.emit(
                    "handshaking", "Waiting for the server handshake..."
                )

            hello = encode_packet({"type": "hello", "username": username})
            with self._send_lock:
                if not self._is_current(generation, stop_event):
                    return
                sock.sendall(hello)

            reader = PacketReader()
            handshake_complete = False
            while self._is_current(generation, stop_event):
                try:
                    data = sock.recv(65536)
                except socket.timeout:
                    continue
                if not data:
                    break

                for packet in reader.feed(data):
                    if not self._is_current(generation, stop_event):
                        return
                    if not handshake_complete:
                        packet_type = packet.get("type")
                        if packet_type == "hello_ok":
                            handshake_complete = True
                            self._complete_handshake(generation, packet)
                        elif packet_type == "error":
                            self._emit_packet(generation, packet)
                            message = str(
                                packet.get("message")
                                or packet.get("code")
                                or "The server rejected the login."
                            )
                            raise _HandshakeRejected(message)
                        else:
                            raise ProtocolError(
                                "expected hello_ok as the first server packet"
                            )
                    self._emit_packet(generation, packet)
        except _HandshakeRejected as exc:
            reason = f"Handshake rejected: {exc}"
            error_message = reason
        except ProtocolError as exc:
            reason = f"Protocol error: {exc}"
            error_message = reason
        except OSError as exc:
            reason = f"Connection error: {self._exception_text(exc)}"
            error_message = reason
        except Exception as exc:  # keep background failures visible to the UI
            reason = f"Client error: {self._exception_text(exc)}"
            error_message = reason
        finally:
            self._close_socket(sock)
            self._finish_worker(generation, sock, reason, error_message)

    def _complete_handshake(
        self, generation: int, packet: dict[str, Any]
    ) -> None:
        with self._lock:
            if generation != self._generation:
                return
            self._is_connected = True
            self._state = "connected"
            self.signals.state_changed.emit("connected", "Connected.")
            self.signals.connected.emit(packet)

    def _emit_packet(self, generation: int, packet: dict[str, Any]) -> None:
        with self._lock:
            if generation == self._generation:
                self.signals.packet_received.emit(packet)

    def _is_current(
        self, generation: int, stop_event: threading.Event
    ) -> bool:
        with self._lock:
            return generation == self._generation and not stop_event.is_set()

    def _finish_worker(
        self,
        generation: int,
        sock: socket.socket | None,
        reason: str,
        error_message: str | None,
    ) -> None:
        with self._lock:
            if generation != self._generation:
                return
            if self._socket is sock:
                self._socket = None
            self._thread = None
            self._stop_event = None
            self._is_connected = False
            self._state = "disconnected"
            if error_message:
                self.signals.error.emit(error_message)
            self.signals.state_changed.emit("disconnected", reason)
            self.signals.disconnected.emit(reason, False)

    def _fail_send(
        self, generation: int, sock: socket.socket, exc: OSError
    ) -> None:
        reason = f"Connection error: {self._exception_text(exc)}"
        with self._lock:
            if generation != self._generation or self._socket is not sock:
                return
            self._generation += 1
            if self._stop_event is not None:
                self._stop_event.set()
            self._socket = None
            self._thread = None
            self._stop_event = None
            self._is_connected = False
            self._state = "disconnected"
            self._close_socket(sock)
            self.signals.error.emit(reason)
            self.signals.state_changed.emit("disconnected", reason)
            self.signals.disconnected.emit(reason, False)

    @staticmethod
    def _close_socket(sock: socket.socket | None) -> None:
        if sock is None:
            return
        try:
            sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        try:
            sock.close()
        except OSError:
            pass

    @staticmethod
    def _exception_text(exc: BaseException) -> str:
        return str(exc).strip() or type(exc).__name__


__all__ = ["ChatClientConnection", "ClientSignals"]
