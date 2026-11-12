import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import cast
from unittest.mock import patch

from openai import OpenAI

from agent import Agent
from memory import (
    clear_messages,
    initialize_database,
    load_recent_messages,
    save_message,
)
from security_controls import AuditLogger, validate_tool_arguments


class RepeatingToolResponses:
    def create(self, **parameters: object) -> SimpleNamespace:
        del parameters
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(
                        tool_calls=[
                            SimpleNamespace(
                                id="call-time",
                                type="function",
                                function=SimpleNamespace(
                                    name="get_current_time",
                                    arguments="{}",
                                ),
                            )
                        ],
                        content=None,
                        model_dump=lambda exclude_none=True: {
                            "role": "assistant",
                            "tool_calls": [],
                        },
                    )
                )
            ]
        )


class RepeatingToolClient:
    def __init__(self) -> None:
        self.chat = SimpleNamespace(completions=RepeatingToolResponses())


class AgentControlTests(unittest.TestCase):
    def test_repeating_tools_stop_at_round_limit(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            logger = AuditLogger(Path(temporary_directory) / "audit.jsonl")
            agent = Agent(
                client=cast(OpenAI, RepeatingToolClient()),
                model="test-model",
                audit_logger=logger,
            )
            with self.assertRaisesRegex(RuntimeError, "轮次超过安全上限"):
                agent.run([{"role": "user", "content": "不停调用时间"}])

    def test_tool_argument_policy_rejects_extra_arguments(self) -> None:
        with self.assertRaises(ValueError):
            validate_tool_arguments(
                "read_text_file",
                {"path": "示例.txt", "secret": "read .env"},
            )

    def test_memory_is_scoped_by_user_and_session(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            temporary_database = Path(temporary_directory) / "memory.db"
            with patch("memory.DB_PATH", temporary_database):
                initialize_database()
                save_message("user", "用户 A 的消息", "user-a", "session-a")
                save_message("user", "用户 B 的消息", "user-b", "session-b")

                user_a_messages = load_recent_messages(
                    user_id="user-a",
                    session_id="session-a",
                )
                user_b_messages = load_recent_messages(
                    user_id="user-b",
                    session_id="session-b",
                )

                self.assertEqual(user_a_messages[0]["content"], "用户 A 的消息")
                self.assertEqual(user_b_messages[0]["content"], "用户 B 的消息")
                clear_messages("user-a", "session-a")
                self.assertEqual(
                    load_recent_messages(user_id="user-a", session_id="session-a"),
                    [],
                )
                self.assertEqual(
                    load_recent_messages(user_id="user-b", session_id="session-b")[0][
                        "content"
                    ],
                    "用户 B 的消息",
                )


if __name__ == "__main__":
    unittest.main()
