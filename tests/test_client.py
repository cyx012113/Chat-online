from __future__ import annotations

import socket
import threading
import time
from collections.abc import Callable
from typing import Any

from PyQt6.QtCore import Qt

from chat_online.client import ChatClientConnection
from chat_online.protocol import PacketReader, encode_packet


DIRECT = Qt.ConnectionType.DirectConnection


def _wait_until(predicate: Callable[[], bool], timeout: float = 3.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


def _receive_packet(conn: socket.socket) -> dict[str, Any]:
    reader = PacketReader()
    while True:
        data = conn.recv(65536)
        if not data:
            raise AssertionError("client disconnected before sending a packet")
        packets = reader.feed(data)
        if packets:
            return packets[0]


def _start_server(
    handler: Callable[[socket.socket], None],
) -> tuple[int, threading.Thread, list[BaseException]]:
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    listener.settimeout(3.0)
    errors: list[BaseException] = []

    def run() -> None:
        try:
            conn, _ = listener.accept()
            with conn:
                conn.settimeout(3.0)
                handler(conn)
        except BaseException as exc:
            errors.append(exc)
        finally:
            listener.close()

    thread = threading.Thread(target=run, name="test-chat-server", daemon=True)
    thread.start()
    return listener.getsockname()[1], thread, errors


def _connect_direct(signal: Any, callback: Callable[..., None]) -> None:
    signal.connect(callback, DIRECT)


def test_connect_sends_hello_and_waits_for_hello_ok() -> None:
    hello_seen = threading.Event()
    allow_handshake = threading.Event()
    release_server = threading.Event()
    hello_packets: list[dict[str, Any]] = []

    def handler(conn: socket.socket) -> None:
        hello_packets.append(_receive_packet(conn))
        hello_seen.set()
        assert allow_handshake.wait(3.0)
        conn.sendall(
            encode_packet(
                {
                    "type": "hello_ok",
                    "session_id": "session-1",
                    "username": "Alice",
                }
            )
        )
        release_server.wait(3.0)

    port, server_thread, server_errors = _start_server(handler)
    client = ChatClientConnection(connect_timeout=1.0)
    connected_packets: list[dict[str, Any]] = []
    _connect_direct(client.signals.connected, connected_packets.append)

    started_at = time.monotonic()
    assert client.connect_to("127.0.0.1", port, "  Alice  ")
    assert time.monotonic() - started_at < 0.25
    assert hello_seen.wait(2.0)
    assert hello_packets == [{"type": "hello", "username": "Alice"}]
    assert not client.is_connected
    assert connected_packets == []

    allow_handshake.set()
    assert _wait_until(lambda: client.is_connected)
    assert connected_packets[0]["type"] == "hello_ok"
    assert client.host == "127.0.0.1"
    assert client.port == port
    assert client.username == "Alice"

    client.disconnect()
    release_server.set()
    server_thread.join(3.0)
    assert not server_thread.is_alive()
    assert server_errors == []


def test_fragmented_and_coalesced_packets_are_all_emitted() -> None:
    release_server = threading.Event()

    hello_ok = {"type": "hello_ok", "session_id": "session-2", "username": "Bob"}
    message = {"type": "message", "sender": "Ann", "text": "hello"}
    snapshot = {"type": "users_snapshot", "users": [{"username": "Ann"}]}

    def handler(conn: socket.socket) -> None:
        assert _receive_packet(conn) == {"type": "hello", "username": "Bob"}
        frames = encode_packet(hello_ok) + encode_packet(message) + encode_packet(snapshot)
        conn.sendall(frames[:5])
        time.sleep(0.02)
        conn.sendall(frames[5:])
        release_server.wait(3.0)

    port, server_thread, server_errors = _start_server(handler)
    client = ChatClientConnection(connect_timeout=1.0)
    received: list[dict[str, Any]] = []
    _connect_direct(client.signals.packet_received, received.append)

    assert client.connect_to("127.0.0.1", port, "Bob")
    assert _wait_until(lambda: len(received) == 3)
    assert [packet["type"] for packet in received] == [
        "hello_ok",
        "message",
        "users_snapshot",
    ]
    assert client.is_connected

    client.disconnect()
    release_server.set()
    server_thread.join(3.0)
    assert not server_thread.is_alive()
    assert server_errors == []


def test_active_disconnect_is_expected_and_closes_socket() -> None:
    server_saw_eof = threading.Event()

    def handler(conn: socket.socket) -> None:
        assert _receive_packet(conn) == {"type": "hello", "username": "Cara"}
        conn.sendall(
            encode_packet(
                {"type": "hello_ok", "session_id": "session-3", "username": "Cara"}
            )
        )
        while True:
            try:
                data = conn.recv(65536)
            except socket.timeout:
                continue
            if not data:
                server_saw_eof.set()
                return

    port, server_thread, server_errors = _start_server(handler)
    client = ChatClientConnection(connect_timeout=1.0)
    disconnects: list[tuple[str, bool]] = []
    errors: list[str] = []
    _connect_direct(
        client.signals.disconnected,
        lambda reason, expected: disconnects.append((reason, expected)),
    )
    _connect_direct(client.signals.error, errors.append)

    assert client.connect_to("127.0.0.1", port, "Cara")
    assert _wait_until(lambda: client.is_connected)

    client.disconnect()
    assert not client.is_connected
    assert disconnects[-1] == ("Disconnected by user.", True)
    assert server_saw_eof.wait(2.0)
    server_thread.join(3.0)
    assert not server_thread.is_alive()
    assert errors == []
    assert server_errors == []


def test_reconnect_ignores_callbacks_from_superseded_worker() -> None:
    old_hello_seen = threading.Event()
    old_socket_closed = threading.Event()
    release_new_server = threading.Event()

    def old_handler(conn: socket.socket) -> None:
        assert _receive_packet(conn) == {"type": "hello", "username": "Dana"}
        old_hello_seen.set()
        while conn.recv(65536):
            pass
        old_socket_closed.set()

    old_port, old_thread, old_errors = _start_server(old_handler)

    def new_handler(conn: socket.socket) -> None:
        assert _receive_packet(conn) == {"type": "hello", "username": "Dana"}
        conn.sendall(
            encode_packet(
                {"type": "hello_ok", "session_id": "new-session", "username": "Dana"}
            )
        )
        release_new_server.wait(3.0)

    new_port, new_thread, new_errors = _start_server(new_handler)
    client = ChatClientConnection(connect_timeout=1.0)
    disconnects: list[tuple[str, bool]] = []
    errors: list[str] = []
    _connect_direct(
        client.signals.disconnected,
        lambda reason, expected: disconnects.append((reason, expected)),
    )
    _connect_direct(client.signals.error, errors.append)

    assert client.connect_to("127.0.0.1", old_port, "Dana")
    assert old_hello_seen.wait(2.0)
    assert not client.is_connected

    assert client.connect_to("127.0.0.1", new_port, "Dana")
    assert _wait_until(lambda: client.is_connected)
    assert client.port == new_port
    assert old_socket_closed.wait(2.0)
    time.sleep(0.6)
    assert client.is_connected
    assert disconnects == [("Superseded by a new connection.", True)]
    assert errors == []

    client.disconnect()
    release_new_server.set()
    old_thread.join(3.0)
    new_thread.join(3.0)
    assert not old_thread.is_alive()
    assert not new_thread.is_alive()
    assert old_errors == []
    assert new_errors == []
