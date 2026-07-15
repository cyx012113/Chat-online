from __future__ import annotations

import unittest
from unittest.mock import patch

from chat_online.app import build_parser, main


class AppCliTests(unittest.TestCase):
    def test_parser_accepts_ephemeral_server_port(self) -> None:
        args = build_parser().parse_args(["--mode", "server", "--headless", "--port", "0"])
        self.assertEqual(args.port, 0)

    @patch("chat_online.app.run_headless_server", return_value=0)
    @patch("chat_online.app.AppStorage")
    def test_main_preserves_zero_port(self, _storage, run_headless) -> None:
        self.assertEqual(main(["--mode", "server", "--headless", "--port", "0"]), 0)
        self.assertEqual(run_headless.call_args.args[1], 0)


if __name__ == "__main__":
    unittest.main()

