"""Compatibility launcher for Chat Online.

Use ``python -m chat_online --help`` or the installed ``chat-online`` command
for the complete CLI.
"""

from chat_online.app import main


if __name__ == "__main__":
    raise SystemExit(main())

