"""Smoke-test a packaged Windows release outside the source interpreter."""

# ruff: noqa: E402 - direct script execution needs the project root first

from __future__ import annotations

import argparse
import os
from pathlib import Path
from queue import Empty, Queue
import re
import signal
import socket
import ssl
import subprocess
import sys
import tempfile
import threading
import time

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from chat_online.protocol import PacketReader, encode_packet
from chat_online.secure_protocol import (
    PROTOCOL_VERSION,
    SECURITY_VERSION,
    identity_fields,
    message_envelope,
)
from chat_online.security import (
    TLS_ALPN_PROTOCOL,
    Identity,
    decrypt_message_text,
)
from chat_online.server import MAIN_ROOM_ID
from chat_online.version import __version__


def run_checked(arguments: list[str], timeout: float = 20) -> str:
    result = subprocess.run(
        arguments,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"Command failed ({result.returncode}): {arguments!r}\n{result.stdout}\n{result.stderr}"
        )
    return result.stdout + result.stderr


def secure_roundtrip(port: int) -> None:
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    context.minimum_version = ssl.TLSVersion.TLSv1_3
    context.maximum_version = ssl.TLSVersion.TLSv1_3
    context.set_alpn_protocols([TLS_ALPN_PROTOCOL])
    identity = Identity.generate()
    raw_socket = socket.create_connection(("127.0.0.1", port), timeout=5)
    with context.wrap_socket(raw_socket, server_hostname="127.0.0.1") as connection:
        connection.settimeout(5)
        if connection.version() != "TLSv1.3":
            raise RuntimeError("Packaged server did not negotiate TLS 1.3")
        if connection.selected_alpn_protocol() != TLS_ALPN_PROTOCOL:
            raise RuntimeError("Packaged server did not negotiate Chat Online ALPN")
        connection.sendall(
            encode_packet(
                {
                    "type": "hello",
                    "username": "ReleaseSmoke",
                    "protocol_version": PROTOCOL_VERSION,
                    "security_version": SECURITY_VERSION,
                    **identity_fields(identity),
                }
            )
        )
        reader = PacketReader()
        hello: dict | None = None
        while hello is None:
            for packet in reader.feed(connection.recv(65536)):
                if packet.get("type") == "hello_ok":
                    hello = packet
                    break
        if hello.get("e2ee_required") is not True:
            raise RuntimeError("Packaged server did not require E2EE")

        envelope = message_envelope(
            identity,
            [identity.public_key],
            "packaged secure smoke",
            scope="room",
            room_id=MAIN_ROOM_ID,
        )
        message_id = envelope["metadata"]["id"]
        connection.sendall(
            encode_packet(
                {
                    "type": "message",
                    "room_id": MAIN_ROOM_ID,
                    "envelope": envelope,
                }
            )
        )
        echoed: dict | None = None
        while echoed is None:
            for packet in reader.feed(connection.recv(65536)):
                if packet.get("type") == "message" and packet.get("id") == message_id:
                    echoed = packet
                    break
        if "text" in echoed:
            raise RuntimeError("Packaged server exposed plaintext message content")
        plaintext = decrypt_message_text(
            echoed["envelope"],
            identity.private_key,
            identity.public_key,
        )
        if plaintext != "packaged secure smoke":
            raise RuntimeError("Packaged E2EE message roundtrip failed")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("release_dir", type=Path)
    args = parser.parse_args()
    release_dir = args.release_dir.resolve()
    gui = release_dir / "ChatOnline.exe"
    cli = release_dir / "ChatOnline-CLI.exe"
    if not gui.is_file() or not cli.is_file():
        raise FileNotFoundError("Packaged GUI or CLI executable is missing")

    version_output = run_checked([str(cli), "--version"])
    if f"Chat Online {__version__}" not in version_output:
        raise RuntimeError(f"Unexpected version output: {version_output!r}")
    help_output = run_checked([str(cli), "--help"])
    if "--headless" not in help_output:
        raise RuntimeError("Packaged CLI help is incomplete")

    platform_plugins = list(release_dir.rglob("qwindows.dll"))
    if not platform_plugins:
        raise FileNotFoundError("Qt platform plugin qwindows.dll was not packaged")

    with tempfile.TemporaryDirectory(prefix="chat-online-smoke-") as temporary:
        environment = dict(os.environ)
        environment["QT_QPA_PLATFORM"] = "offscreen"
        gui_process = subprocess.Popen(
            [str(gui), "--mode", "server", "--no-auto-start", "--data-dir", temporary],
            cwd=release_dir,
            env=environment,
        )
        try:
            time.sleep(3)
            if gui_process.poll() is not None:
                raise RuntimeError(f"Packaged GUI exited early with {gui_process.returncode}")
        finally:
            gui_process.terminate()
            gui_process.wait(timeout=10)

        headless_process = subprocess.Popen(
            [
                str(cli),
                "--mode",
                "server",
                "--headless",
                "--host",
                "127.0.0.1",
                "--port",
                "0",
                "--data-dir",
                temporary,
            ],
            cwd=release_dir,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
        )
        try:
            deadline = time.monotonic() + 15
            output_lines: list[str] = []
            output_queue: Queue[str] = Queue()
            listening_port: int | None = None

            def read_output() -> None:
                if headless_process.stdout is None:
                    return
                for output_line in headless_process.stdout:
                    output_queue.put(output_line)

            threading.Thread(target=read_output, daemon=True).start()
            while time.monotonic() < deadline:
                try:
                    line = output_queue.get(timeout=0.2)
                except Empty:
                    line = ""
                if line:
                    output_lines.append(line)
                    match = re.search(r"Server listening on 127\.0\.0\.1:(\d+)", line)
                    if match:
                        listening_port = int(match.group(1))
                        break
                elif headless_process.poll() is not None:
                    break
            else:
                raise RuntimeError("Timed out waiting for packaged headless server")
            if listening_port is None:
                raise RuntimeError("Packaged headless server did not start: " + "".join(output_lines))
            secure_roundtrip(listening_port)
            shutdown_signal = getattr(signal, "CTRL_BREAK_EVENT", signal.SIGINT)
            headless_process.send_signal(shutdown_signal)
            headless_process.wait(timeout=10)
            if headless_process.returncode != 0:
                raise RuntimeError(f"Headless server exited with {headless_process.returncode}")
        finally:
            if headless_process.poll() is None:
                headless_process.kill()
                headless_process.wait(timeout=5)

    print(f"Release smoke tests passed: {release_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
