from __future__ import annotations

import errno
import socket
import ssl
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from PyQt6.QtCore import Qt

from chat_online.client import ChatClientConnection
from chat_online.protocol import PacketReader, encode_packet
from chat_online.secure_protocol import (
    PROTOCOL_VERSION,
    SECURITY_VERSION,
    load_identity_fields,
)
from chat_online.security import (
    CertificatePinStore,
    Identity,
    create_tls_client_context,
    create_tls_server_context,
    ensure_self_signed_certificate,
)


DIRECT = Qt.ConnectionType.DirectConnection


@dataclass
class TLSFixture:
    server_context: ssl.SSLContext
    client_context: ssl.SSLContext
    pin_store: CertificatePinStore
    certificate_path: Path
    security_dir: Path
    identity: Identity

    def client(self) -> ChatClientConnection:
        return ChatClientConnection(
            connect_timeout=1.0,
            ssl_context=self.client_context,
            pin_store=self.pin_store,
            security_dir=self.security_dir,
            identity=self.identity,
        )


@pytest.fixture
def tls_fixture(tmp_path: Path) -> TLSFixture:
    security_dir = tmp_path / "client-security"
    certificate = ensure_self_signed_certificate(
        tmp_path / "server-cert.pem",
        tmp_path / "server-key.pem",
    )
    return TLSFixture(
        server_context=create_tls_server_context(
            certificate.certificate_path,
            certificate.private_key_path,
        ),
        client_context=create_tls_client_context(tofu=True),
        pin_store=CertificatePinStore(tmp_path / "server-pins.json"),
        certificate_path=certificate.certificate_path,
        security_dir=security_dir,
        identity=Identity.load_or_create(security_dir / "fixture-identity.key"),
    )


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
    tls_context: ssl.SSLContext,
) -> tuple[int, threading.Thread, list[BaseException]]:
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    listener.settimeout(3.0)
    errors: list[BaseException] = []

    def run() -> None:
        try:
            raw_conn, _ = listener.accept()
            with tls_context.wrap_socket(raw_conn, server_side=True) as conn:
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


def _assert_only_peer_close_errors(errors: list[BaseException]) -> None:
    assert all(
        isinstance(exc, OSError)
        and exc.errno in {errno.ECONNABORTED, errno.ECONNRESET, 10053, 10054}
        for exc in errors
    )


def _assert_secure_hello(packet: dict[str, Any], username: str) -> None:
    assert packet["type"] == "hello"
    assert packet["username"] == username
    assert packet["protocol_version"] == PROTOCOL_VERSION
    assert packet["security_version"] == SECURITY_VERSION
    public_key, fingerprint = load_identity_fields(packet)
    assert public_key.key_size == 3072
    assert fingerprint == packet["identity_fingerprint"]


def test_connect_sends_hello_and_waits_for_hello_ok(tls_fixture: TLSFixture) -> None:
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

    port, server_thread, server_errors = _start_server(
        handler, tls_fixture.server_context
    )
    client = tls_fixture.client()
    connected_packets: list[dict[str, Any]] = []
    _connect_direct(client.signals.connected, connected_packets.append)

    started_at = time.monotonic()
    assert client.connect_to("127.0.0.1", port, "  Alice  ")
    assert time.monotonic() - started_at < 0.25
    assert hello_seen.wait(2.0)
    assert len(hello_packets) == 1
    _assert_secure_hello(hello_packets[0], "Alice")
    assert not client.is_connected
    assert connected_packets == []

    allow_handshake.set()
    assert _wait_until(lambda: client.is_connected)
    assert connected_packets[0]["type"] == "hello_ok"
    assert client.host == "127.0.0.1"
    assert client.port == port
    assert client.username == "Alice"
    assert client.tls_version == "TLSv1.3"
    assert len(client.tls_fingerprint) == 64
    assert connected_packets[0]["tls_version"] == "TLSv1.3"
    assert connected_packets[0]["tls_fingerprint"] == client.tls_fingerprint

    client.disconnect()
    release_server.set()
    server_thread.join(3.0)
    assert not server_thread.is_alive()
    assert server_errors == []


def test_fragmented_and_coalesced_packets_are_all_emitted(
    tls_fixture: TLSFixture,
) -> None:
    release_server = threading.Event()

    hello_ok = {"type": "hello_ok", "session_id": "session-2", "username": "Bob"}
    message = {"type": "message", "sender": "Ann", "text": "hello"}
    snapshot = {"type": "users_snapshot", "users": [{"username": "Ann"}]}

    def handler(conn: socket.socket) -> None:
        _assert_secure_hello(_receive_packet(conn), "Bob")
        frames = encode_packet(hello_ok) + encode_packet(message) + encode_packet(snapshot)
        conn.sendall(frames[:5])
        time.sleep(0.02)
        conn.sendall(frames[5:])
        release_server.wait(3.0)

    port, server_thread, server_errors = _start_server(
        handler, tls_fixture.server_context
    )
    client = tls_fixture.client()
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


def test_active_disconnect_is_expected_and_closes_socket(
    tls_fixture: TLSFixture,
) -> None:
    server_saw_eof = threading.Event()

    def handler(conn: socket.socket) -> None:
        _assert_secure_hello(_receive_packet(conn), "Cara")
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

    port, server_thread, server_errors = _start_server(
        handler, tls_fixture.server_context
    )
    client = tls_fixture.client()
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


def test_reconnect_ignores_callbacks_from_superseded_worker(
    tls_fixture: TLSFixture,
) -> None:
    old_hello_seen = threading.Event()
    old_socket_closed = threading.Event()
    release_new_server = threading.Event()

    def old_handler(conn: socket.socket) -> None:
        _assert_secure_hello(_receive_packet(conn), "Dana")
        old_hello_seen.set()
        try:
            while conn.recv(65536):
                pass
        except OSError:
            pass
        finally:
            old_socket_closed.set()

    old_port, old_thread, old_errors = _start_server(
        old_handler, tls_fixture.server_context
    )

    def new_handler(conn: socket.socket) -> None:
        _assert_secure_hello(_receive_packet(conn), "Dana")
        conn.sendall(
            encode_packet(
                {"type": "hello_ok", "session_id": "new-session", "username": "Dana"}
            )
        )
        release_new_server.wait(3.0)

    new_port, new_thread, new_errors = _start_server(
        new_handler, tls_fixture.server_context
    )
    client = tls_fixture.client()
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
    _assert_only_peer_close_errors(old_errors)
    _assert_only_peer_close_errors(new_errors)


def test_changed_server_certificate_is_rejected(tls_fixture: TLSFixture) -> None:
    def handler(conn: socket.socket) -> None:
        try:
            while conn.recv(65536):
                pass
        except OSError:
            pass

    other_certificate = ensure_self_signed_certificate(
        tls_fixture.certificate_path.parent / "other-cert.pem",
        tls_fixture.certificate_path.parent / "other-key.pem",
    )
    port, server_thread, server_errors = _start_server(
        handler, tls_fixture.server_context
    )
    tls_fixture.pin_store.verify(
        f"127.0.0.1:{port}",
        other_certificate.certificate_path.read_bytes(),
    )

    client = tls_fixture.client()
    errors: list[str] = []
    connected: list[dict[str, Any]] = []
    _connect_direct(client.signals.error, errors.append)
    _connect_direct(client.signals.connected, connected.append)

    assert client.connect_to("127.0.0.1", port, "PinnedUser")
    assert _wait_until(lambda: bool(errors))
    assert "Security error" in errors[-1]
    assert "changed" in errors[-1]
    assert connected == []
    assert not client.is_connected

    server_thread.join(3.0)
    assert not server_thread.is_alive()
    # Windows may report the intentional post-pin-check close as 10053/10054.
    _assert_only_peer_close_errors(server_errors)
