"""Smoke-test a packaged Windows release outside the source interpreter."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
from queue import Empty, Queue
import signal
import subprocess
import tempfile
import threading
import time


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
    if "Chat Online 6.0.0" not in version_output:
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
                    if "Server listening on 127.0.0.1:" in line:
                        break
                elif headless_process.poll() is not None:
                    break
            else:
                raise RuntimeError("Timed out waiting for packaged headless server")
            if not any("Server listening on 127.0.0.1:" in line for line in output_lines):
                raise RuntimeError("Packaged headless server did not start: " + "".join(output_lines))
            headless_process.send_signal(signal.CTRL_C_EVENT)
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

