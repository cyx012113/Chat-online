from __future__ import annotations

import time
import unittest

from chat_online.cli import (
    CommandError,
    ParsedCommand,
    execute_command,
    parse_command,
)


class FakeStorage:
    def __init__(self) -> None:
        self.files = [
            {
                "file_id": "file-1",
                "original_name": "notes.txt",
                "size": 1536,
                "room_id": "main",
            },
            {
                "file_id": "file-2",
                "original_name": "photo.png",
                "size": 2048,
                "room_id": "room-2",
            },
        ]
        self.requested_rooms: list[str | None] = []

    def list_files(self, room_id: str | None = None) -> list[dict[str, object]]:
        self.requested_rooms.append(room_id)
        if room_id is None:
            return list(self.files)
        return [item for item in self.files if item["room_id"] == room_id]


class FakeEngine:
    def __init__(self) -> None:
        self.storage = FakeStorage()
        self.snapshot_value = {
            "running": True,
            "host": "127.0.0.1",
            "port": 8888,
            "started_at": time.time() - 3661,
            "users": [
                {
                    "id": "user-1",
                    "username": "Alice Smith",
                    "ip": "192.0.2.10",
                    "port": 5000,
                    "current_room": "room-2",
                    "rooms": ["main", "room-2"],
                },
                {
                    "id": "user-2",
                    "username": "Bob",
                    "ip": "192.0.2.11",
                    "port": 5001,
                    "current_room": "main",
                    "rooms": ["main"],
                },
            ],
            "rooms": [
                {"id": "main", "name": "Main Lounge", "member_count": 2},
                {"id": "room-2", "name": "Side Room", "member_count": 1},
            ],
            "banned": ["198.51.100.0/24"],
            "file_count": 2,
        }
        self.broadcasts: list[str] = []
        self.kicks: list[str] = []
        self.mutes: list[tuple[str, str, bool]] = []
        self.bans: list[str] = []
        self.unbans: list[str] = []
        self.stopped = False

    def snapshot(self) -> dict[str, object]:
        return self.snapshot_value

    def broadcast_system(self, text: str) -> bool:
        self.broadcasts.append(text)
        return bool(text)

    def kick_user(self, session_id: str) -> bool:
        self.kicks.append(session_id)
        return session_id in {"user-1", "user-2"}

    def mute_user(self, session_id: str, room_id: str, muted: bool = True) -> bool:
        self.mutes.append((session_id, room_id, muted))
        return session_id in {"user-1", "user-2"} and room_id in {"main", "room-2"}

    def ban_address(self, value: str) -> tuple[bool, str]:
        self.bans.append(value)
        if value == "invalid":
            return False, "not an address"
        return True, "203.0.113.0/24"

    def unban_address(self, value: str) -> bool:
        self.unbans.append(value)
        return value == "198.51.100.0/24"

    def stop(self) -> None:
        self.stopped = True


class ParseCommandTests(unittest.TestCase):
    def test_parses_quoted_shell_arguments(self) -> None:
        self.assertEqual(
            parse_command('MuTe "Alice Smith" "Side Room"'),
            ParsedCommand("mute", ("Alice Smith", "Side Room")),
        )
        self.assertEqual(
            parse_command('say "hello world"'),
            ParsedCommand("say", ("hello world",)),
        )

    def test_empty_input_is_ignored(self) -> None:
        self.assertIsNone(parse_command("   \t"))

    def test_reports_unclosed_quotes(self) -> None:
        with self.assertRaisesRegex(CommandError, "Could not parse"):
            parse_command('say "unfinished')


class ExecuteCommandTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = FakeEngine()

    def test_help_and_unknown_command(self) -> None:
        help_result = execute_command(self.engine, "help mute")
        self.assertTrue(help_result.ok)
        self.assertIn("mute <username-or-id> [room]", help_result.message)

        unknown = execute_command(self.engine, "explode")
        self.assertFalse(unknown.ok)
        self.assertIn("Unknown command", unknown.message)
        self.assertIn("help", unknown.message)

    def test_status_users_and_rooms(self) -> None:
        status = execute_command(self.engine, "status")
        self.assertTrue(status.ok)
        self.assertIn("Status: running", status.message)
        self.assertIn("Users: 2", status.message)
        self.assertIn("Rooms: 2", status.message)
        self.assertIn("Uptime: 1h 1m", status.message)

        users = execute_command(self.engine, "users")
        self.assertTrue(users.ok)
        self.assertIn("Alice Smith [user-1]", users.message)
        self.assertIn("192.0.2.10:5000", users.message)

        rooms = execute_command(self.engine, "rooms")
        self.assertTrue(rooms.ok)
        self.assertIn("Main Lounge [main]", rooms.message)
        self.assertIn("Side Room [room-2]", rooms.message)

    def test_files_supports_all_files_or_room_filter(self) -> None:
        all_files = execute_command(self.engine, "files")
        self.assertTrue(all_files.ok)
        self.assertIn("notes.txt [file-1]", all_files.message)
        self.assertIn("1.5 KiB", all_files.message)

        room_files = execute_command(self.engine, 'files "Side Room"')
        self.assertTrue(room_files.ok)
        self.assertIn("photo.png [file-2]", room_files.message)
        self.assertNotIn("notes.txt", room_files.message)
        self.assertEqual(self.engine.storage.requested_rooms, [None, "room-2"])

    def test_say_and_kick_resolve_shell_text_and_username(self) -> None:
        say = execute_command(self.engine, 'say "Maintenance in five minutes"')
        self.assertTrue(say.ok)
        self.assertEqual(self.engine.broadcasts, ["Maintenance in five minutes"])

        kick = execute_command(self.engine, 'kick "alice smith"')
        self.assertTrue(kick.ok)
        self.assertEqual(self.engine.kicks, ["user-1"])

        missing = execute_command(self.engine, "kick Nobody")
        self.assertFalse(missing.ok)
        self.assertIn("not online", missing.message)

    def test_mute_defaults_to_current_room_and_accepts_explicit_room(self) -> None:
        muted = execute_command(self.engine, 'mute "Alice Smith"')
        unmuted = execute_command(self.engine, 'unmute user-1 "Main Lounge"')

        self.assertTrue(muted.ok)
        self.assertTrue(unmuted.ok)
        self.assertEqual(
            self.engine.mutes,
            [("user-1", "room-2", True), ("user-1", "main", False)],
        )

    def test_ban_and_unban_report_validation_errors(self) -> None:
        banned = execute_command(self.engine, "ban 203.0.113.7/24")
        self.assertTrue(banned.ok)
        self.assertIn("203.0.113.0/24", banned.message)

        bad_ban = execute_command(self.engine, "ban invalid")
        self.assertFalse(bad_ban.ok)
        self.assertIn("not an address", bad_ban.message)

        unbanned = execute_command(self.engine, "unban 198.51.100.0/24")
        self.assertTrue(unbanned.ok)
        missing = execute_command(self.engine, "unban 192.0.2.0/24")
        self.assertFalse(missing.ok)
        self.assertIn("not currently blocked", missing.message)

    def test_argument_errors_are_clear(self) -> None:
        result = execute_command(self.engine, "kick")
        self.assertFalse(result.ok)
        self.assertIn("Usage: kick <username-or-id>", result.message)

        malformed = execute_command(self.engine, 'say "unfinished')
        self.assertFalse(malformed.ok)
        self.assertIn("Could not parse", malformed.message)

    def test_stop_and_alias_request_event_loop_exit(self) -> None:
        result = execute_command(self.engine, "exit")

        self.assertTrue(result.ok)
        self.assertTrue(result.should_stop)
        self.assertTrue(self.engine.stopped)

    def test_blank_command_is_a_successful_noop(self) -> None:
        self.assertEqual(execute_command(self.engine, "" ).message, "")


if __name__ == "__main__":
    unittest.main()

