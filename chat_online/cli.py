"""Headless Qt event-loop runner and interactive server console."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import shlex
import signal
import sys
import threading
import time
from typing import Any, TextIO

from PyQt6.QtCore import QCoreApplication, QObject, QTimer, Qt, pyqtSignal, pyqtSlot

from .server import MAIN_ROOM_ID, ChatServerEngine
from .storage import AppStorage


_COMMAND_USAGE = {
    "help": "help [command]",
    "status": "status",
    "users": "users",
    "rooms": "rooms",
    "files": "files [room]",
    "say": "say <text>",
    "kick": "kick <username-or-id>",
    "mute": "mute <username-or-id> [room]",
    "unmute": "unmute <username-or-id> [room]",
    "ban": "ban <ip-or-cidr>",
    "unban": "unban <ip-or-cidr>",
    "stop": "stop",
}

_COMMAND_DESCRIPTIONS = {
    "help": "Show all commands or usage for one command.",
    "status": "Show listener state, uptime, and resource counts.",
    "users": "List connected users.",
    "rooms": "List rooms.",
    "files": "List shared files, optionally limited to one room.",
    "say": "Broadcast a server message to the main room.",
    "kick": "Disconnect a user.",
    "mute": "Mute a user in a room (their current room by default).",
    "unmute": "Remove a room mute.",
    "ban": "Block an IP address or CIDR network.",
    "unban": "Remove an IP/CIDR block.",
    "stop": "Stop the server cleanly.",
}


class CommandError(ValueError):
    """Raised when a console command cannot be parsed or resolved."""


@dataclass(frozen=True)
class ParsedCommand:
    name: str
    arguments: tuple[str, ...] = ()


@dataclass(frozen=True)
class CommandResult:
    ok: bool
    message: str = ""
    should_stop: bool = False


def parse_command(line: str) -> ParsedCommand | None:
    """Parse one shell-like command line without executing it."""

    if not isinstance(line, str):
        raise CommandError("Command input must be text.")
    if not line.strip():
        return None
    try:
        tokens = shlex.split(line, comments=False, posix=True)
    except ValueError as exc:
        raise CommandError(f"Could not parse command: {exc}.") from exc
    if not tokens:
        return None
    return ParsedCommand(tokens[0].casefold(), tuple(tokens[1:]))


def _require_arguments(
    command: ParsedCommand, minimum: int, maximum: int | None = None
) -> None:
    maximum = minimum if maximum is None else maximum
    count = len(command.arguments)
    if minimum <= count <= maximum:
        return
    usage = _COMMAND_USAGE.get(command.name, command.name)
    if minimum == maximum:
        expected = f"exactly {minimum} argument{'s' if minimum != 1 else ''}"
    else:
        expected = f"between {minimum} and {maximum} arguments"
    raise CommandError(f"Usage: {usage} (expected {expected}).")


def _snapshot(engine: Any) -> dict[str, Any]:
    snapshot = engine.snapshot()
    if not isinstance(snapshot, dict):
        raise CommandError("Server returned an invalid status snapshot.")
    return snapshot


def _users(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    users = snapshot.get("users", [])
    return [item for item in users if isinstance(item, dict)] if isinstance(users, list) else []


def _rooms(snapshot: dict[str, Any]) -> list[dict[str, Any]]:
    rooms = snapshot.get("rooms", [])
    return [item for item in rooms if isinstance(item, dict)] if isinstance(rooms, list) else []


def _resolve_user(snapshot: dict[str, Any], value: str) -> dict[str, Any]:
    target = value.strip()
    users = _users(snapshot)
    by_id = [item for item in users if str(item.get("id", "")) == target]
    if len(by_id) == 1:
        return by_id[0]
    folded = target.casefold()
    by_name = [
        item for item in users if str(item.get("username", "")).casefold() == folded
    ]
    if len(by_name) == 1:
        return by_name[0]
    if len(by_name) > 1:
        raise CommandError(f"More than one user is named {value!r}; use a user ID.")
    raise CommandError(f"User {value!r} is not online.")


def _resolve_room(snapshot: dict[str, Any], value: str) -> dict[str, Any]:
    target = value.strip()
    rooms = _rooms(snapshot)
    by_id = [item for item in rooms if str(item.get("id", "")) == target]
    if len(by_id) == 1:
        return by_id[0]
    folded = target.casefold()
    by_name = [
        item for item in rooms if str(item.get("name", "")).casefold() == folded
    ]
    if len(by_name) == 1:
        return by_name[0]
    if len(by_name) > 1:
        raise CommandError(f"More than one room is named {value!r}; use a room ID.")
    raise CommandError(f"Room {value!r} was not found.")


def _format_duration(seconds: float) -> str:
    total = max(0, int(seconds))
    days, remainder = divmod(total, 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes, seconds = divmod(remainder, 60)
    parts = []
    if days:
        parts.append(f"{days}d")
    if hours or days:
        parts.append(f"{hours}h")
    if minutes or hours or days:
        parts.append(f"{minutes}m")
    parts.append(f"{seconds}s")
    return " ".join(parts)


def _format_size(value: Any) -> str:
    try:
        size = max(0, int(value))
    except (TypeError, ValueError):
        return "unknown size"
    units = ("B", "KiB", "MiB", "GiB")
    amount = float(size)
    for unit in units:
        if amount < 1024 or unit == units[-1]:
            return f"{int(amount)} {unit}" if unit == "B" else f"{amount:.1f} {unit}"
        amount /= 1024
    return f"{size} B"


def _help(arguments: tuple[str, ...]) -> CommandResult:
    if len(arguments) > 1:
        raise CommandError(f"Usage: {_COMMAND_USAGE['help']}.")
    if arguments:
        name = arguments[0].casefold()
        if name not in _COMMAND_USAGE:
            raise CommandError(f"Unknown command {arguments[0]!r}.")
        return CommandResult(
            True, f"{_COMMAND_USAGE[name]} - {_COMMAND_DESCRIPTIONS[name]}"
        )
    width = max(len(usage) for usage in _COMMAND_USAGE.values())
    lines = ["Available commands:"]
    lines.extend(
        f"  {usage:<{width}}  {_COMMAND_DESCRIPTIONS[name]}"
        for name, usage in _COMMAND_USAGE.items()
    )
    return CommandResult(True, "\n".join(lines))


def _execute_parsed(engine: Any, command: ParsedCommand) -> CommandResult:
    name = command.name
    arguments = command.arguments
    if name in {"quit", "exit"}:
        name = "stop"
        command = ParsedCommand(name, arguments)
    if name == "help":
        return _help(arguments)
    if name not in _COMMAND_USAGE:
        raise CommandError(f"Unknown command {command.name!r}. Use 'help' for a list.")

    if name == "status":
        _require_arguments(command, 0)
        snapshot = _snapshot(engine)
        started_at = snapshot.get("started_at")
        uptime = (
            _format_duration(time.time() - float(started_at))
            if isinstance(started_at, (int, float))
            else "not started"
        )
        banned = snapshot.get("banned", [])
        banned_count = len(banned) if isinstance(banned, list) else 0
        state = "running" if snapshot.get("running") else "stopped"
        return CommandResult(
            True,
            "\n".join(
                (
                    f"Status: {state}",
                    f"Listen: {snapshot.get('host', '?')}:{snapshot.get('port', '?')}",
                    f"Uptime: {uptime}",
                    f"Users: {len(_users(snapshot))}",
                    f"Rooms: {len(_rooms(snapshot))}",
                    f"Files: {snapshot.get('file_count', 0)}",
                    f"Blocked networks: {banned_count}",
                )
            ),
        )

    if name == "users":
        _require_arguments(command, 0)
        users = sorted(
            _users(_snapshot(engine)),
            key=lambda item: str(item.get("username", "")).casefold(),
        )
        if not users:
            return CommandResult(True, "Users (0): none connected.")
        lines = [f"Users ({len(users)}):"]
        for user in users:
            address = str(user.get("ip", "?"))
            if user.get("port") is not None:
                address += f":{user['port']}"
            lines.append(
                f"  {user.get('username', '?')} [{user.get('id', '?')}] "
                f"{address} room={user.get('current_room', '?')}"
            )
        return CommandResult(True, "\n".join(lines))

    if name == "rooms":
        _require_arguments(command, 0)
        rooms = sorted(
            _rooms(_snapshot(engine)),
            key=lambda item: (str(item.get("id")) != MAIN_ROOM_ID, str(item.get("name", "")).casefold()),
        )
        if not rooms:
            return CommandResult(True, "Rooms (0): none.")
        lines = [f"Rooms ({len(rooms)}):"]
        for room in rooms:
            lines.append(
                f"  {room.get('name', '?')} [{room.get('id', '?')}] "
                f"members={room.get('member_count', 0)}"
            )
        return CommandResult(True, "\n".join(lines))

    if name == "files":
        _require_arguments(command, 0, 1)
        room_id = None
        if arguments:
            room_id = str(_resolve_room(_snapshot(engine), arguments[0]).get("id"))
        files = engine.storage.list_files(room_id)
        if not isinstance(files, list):
            raise CommandError("Storage returned an invalid file list.")
        if not files:
            suffix = f" in {room_id}" if room_id else ""
            return CommandResult(True, f"Files (0){suffix}: none.")
        lines = [f"Files ({len(files)}):"]
        for metadata in files:
            if not isinstance(metadata, dict):
                continue
            filename = metadata.get("original_name", metadata.get("name", "file"))
            lines.append(
                f"  {filename} [{metadata.get('file_id', '?')}] "
                f"{_format_size(metadata.get('size'))} room={metadata.get('room_id', '?')}"
            )
        return CommandResult(True, "\n".join(lines))

    if name == "say":
        if not arguments:
            raise CommandError(f"Usage: {_COMMAND_USAGE[name]} (message cannot be empty).")
        text = " ".join(arguments).strip()
        if not text or not engine.broadcast_system(text):
            raise CommandError("The server message was empty or could not be delivered.")
        return CommandResult(True, "Server message sent to the main room.")

    if name == "kick":
        _require_arguments(command, 1)
        user = _resolve_user(_snapshot(engine), arguments[0])
        if not engine.kick_user(str(user.get("id", ""))):
            raise CommandError(f"Could not kick {user.get('username', arguments[0])!r}.")
        return CommandResult(True, f"Kicked {user.get('username', arguments[0])}.")

    if name in {"mute", "unmute"}:
        _require_arguments(command, 1, 2)
        snapshot = _snapshot(engine)
        user = _resolve_user(snapshot, arguments[0])
        if len(arguments) == 2:
            room_id = str(_resolve_room(snapshot, arguments[1]).get("id", ""))
        else:
            room_id = str(user.get("current_room") or MAIN_ROOM_ID)
        muted = name == "mute"
        if not engine.mute_user(str(user.get("id", "")), room_id, muted=muted):
            verb = "mute" if muted else "unmute"
            raise CommandError(
                f"Could not {verb} {user.get('username', arguments[0])!r} in room {room_id!r}."
            )
        action = "Muted" if muted else "Unmuted"
        return CommandResult(
            True, f"{action} {user.get('username', arguments[0])} in {room_id}."
        )

    if name == "ban":
        _require_arguments(command, 1)
        succeeded, detail = engine.ban_address(arguments[0])
        if not succeeded:
            raise CommandError(f"Invalid IP address or CIDR: {detail}")
        return CommandResult(True, f"Blocked {detail}.")

    if name == "unban":
        _require_arguments(command, 1)
        if not engine.unban_address(arguments[0]):
            raise CommandError(
                f"Network {arguments[0]!r} is invalid or is not currently blocked."
            )
        return CommandResult(True, f"Unblocked {arguments[0]}.")

    if name == "stop":
        _require_arguments(command, 0)
        engine.stop()
        return CommandResult(True, "Server stopped.", should_stop=True)

    raise CommandError(f"Command {name!r} is not implemented.")


def execute_command(
    engine: Any, command: str | ParsedCommand | None
) -> CommandResult:
    """Execute a command and convert user-facing failures to a result object."""

    try:
        parsed = parse_command(command) if isinstance(command, str) else command
        if parsed is None:
            return CommandResult(True)
        if not isinstance(parsed, ParsedCommand):
            raise CommandError("Command must be text or a ParsedCommand instance.")
        return _execute_parsed(engine, parsed)
    except CommandError as exc:
        return CommandResult(False, f"Error: {exc}")
    except Exception as exc:
        return CommandResult(False, f"Command failed: {exc}")


def _timestamp() -> str:
    return datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S%z")


def _write_event(stream: TextIO | None, level: str, message: str) -> None:
    if stream is None:
        return
    lines = str(message).splitlines() or [""]
    for line in lines:
        stream.write(f"[{_timestamp()}] [{level.upper()}] {line}\n")
    stream.flush()


class _ConsoleBridge(QObject):
    command_received = pyqtSignal(str)
    shutdown_requested = pyqtSignal(str)


class _HeadlessRuntime(QObject):
    def __init__(
        self,
        app: QCoreApplication,
        engine: ChatServerEngine,
        stdout: TextIO | None,
        stderr: TextIO | None,
    ) -> None:
        super().__init__()
        self.app = app
        self.engine = engine
        self.stdout = stdout
        self.stderr = stderr
        self.stopping = False

    @pyqtSlot(str)
    def execute_line(self, line: str) -> None:
        result = execute_command(self.engine, line)
        if result.message:
            _write_event(
                self.stdout if result.ok else self.stderr,
                "console" if result.ok else "error",
                result.message,
            )
        if result.should_stop:
            self.stopping = True
            self.app.quit()

    @pyqtSlot(str)
    def shutdown(self, reason: str = "Shutdown requested.") -> None:
        if self.stopping:
            return
        self.stopping = True
        _write_event(self.stdout, "info", reason)
        self.engine.stop()
        self.app.quit()


def _stdin_is_tty() -> bool:
    try:
        return bool(sys.stdin.isatty())
    except (AttributeError, OSError):
        return False


def _console_input_loop(bridge: _ConsoleBridge, runtime: _HeadlessRuntime) -> None:
    while not runtime.stopping:
        try:
            line = input("chat-online> ")
        except EOFError:
            bridge.shutdown_requested.emit("Console input closed; stopping server.")
            return
        except KeyboardInterrupt:
            bridge.shutdown_requested.emit("Console interrupted; stopping server.")
            return
        bridge.command_received.emit(line)


def run_headless_server(
    host: str, port: int, storage: AppStorage | None = None
) -> int:
    """Run the server on a ``QCoreApplication`` without creating widgets."""

    app = QCoreApplication.instance()
    if app is None:
        app = QCoreApplication([sys.argv[0] if sys.argv else "chat-online"])
    app.setApplicationName("Chat Online Server")

    engine = ChatServerEngine(storage=storage)
    runtime = _HeadlessRuntime(app, engine, sys.stdout, sys.stderr)
    bridge = _ConsoleBridge()
    bridge.command_received.connect(
        runtime.execute_line, Qt.ConnectionType.QueuedConnection
    )
    bridge.shutdown_requested.connect(
        runtime.shutdown, Qt.ConnectionType.QueuedConnection
    )
    engine.signals.log.connect(
        lambda level, message: _write_event(sys.stdout, level, message)
    )
    engine.signals.error.connect(
        lambda message: _write_event(sys.stderr, "error", message)
    )

    if not engine.start(host, port):
        return 1

    timer = QTimer()
    timer.setInterval(250)
    timer.timeout.connect(lambda: None)
    timer.start()

    previous_handlers: dict[int, Any] = {}
    if threading.current_thread() is threading.main_thread():
        handled_signals = [signal.SIGINT]
        if hasattr(signal, "SIGTERM"):
            handled_signals.append(signal.SIGTERM)

        def request_shutdown(signum: int, _frame: Any) -> None:
            try:
                label = signal.Signals(signum).name
            except ValueError:
                label = str(signum)
            bridge.shutdown_requested.emit(f"Received {label}; stopping server.")

        for handled_signal in handled_signals:
            previous_handlers[int(handled_signal)] = signal.getsignal(handled_signal)
            signal.signal(handled_signal, request_shutdown)

    if _stdin_is_tty():
        threading.Thread(
            target=_console_input_loop,
            args=(bridge, runtime),
            name="chat-server-console",
            daemon=True,
        ).start()
    else:
        _write_event(
            sys.stdout,
            "info",
            "Standard input is not a TTY; interactive console disabled.",
        )

    try:
        return int(app.exec())
    finally:
        timer.stop()
        engine.stop()
        for signum, previous_handler in previous_handlers.items():
            signal.signal(signum, previous_handler)


__all__ = [
    "CommandError",
    "CommandResult",
    "ParsedCommand",
    "execute_command",
    "parse_command",
    "run_headless_server",
]
