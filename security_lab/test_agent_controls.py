import json
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


def _content_chunk(text: str) -> SimpleNamespace:
    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                delta=SimpleNamespace(content=text, tool_calls=None),
            )
        ]
    )


def _tool_chunk(index: int, call_id: str, name: str, arguments: str) -> SimpleNamespace:
    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                delta=SimpleNamespace(
                    content=None,
                    tool_calls=[
                        SimpleNamespace(
                            index=index,
                            id=call_id,
                            function=SimpleNamespace(name=name, arguments=arguments),
                        )
                    ],
                ),
            )
        ]
    )


class ScriptedStreamResponses:
    def __init__(self, rounds: list[list[SimpleNamespace]]) -> None:
        self._rounds = [list(round_chunks) for round_chunks in rounds]
        self.requests: list[dict] = []

    def create(self, **parameters: object) -> list[SimpleNamespace]:
        self.requests.append(parameters)
        return self._rounds.pop(0)


class ScriptedStreamClient:
    def __init__(self, rounds: list[list[SimpleNamespace]]) -> None:
        self.responses = ScriptedStreamResponses(rounds)
        self.chat = SimpleNamespace(completions=self.responses)


class StreamingTests(unittest.TestCase):
    def test_stream_yields_content_chunks(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            audit_path = Path(temporary_directory) / "audit.jsonl"
            logger = AuditLogger(audit_path)
            client = ScriptedStreamClient([[_content_chunk("你好"), _content_chunk("，世界")]])
            agent = Agent(
                client=cast(OpenAI, client),
                model="test-model",
                audit_logger=logger,
            )

            chunks = list(agent.stream([{"role": "user", "content": "打个招呼"}]))

            self.assertEqual(chunks, ["你好", "，世界"])
            self.assertTrue(client.responses.requests[0]["stream"])
            records = [
                json.loads(line) for line in audit_path.read_text("utf-8").splitlines()
            ]
            self.assertIn("agent_completed", [record["event"] for record in records])

    def test_stream_runs_tool_then_continues_generation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            audit_path = Path(temporary_directory) / "audit.jsonl"
            logger = AuditLogger(audit_path)
            client = ScriptedStreamClient(
                [
                    [_tool_chunk(0, "call-mul", "calculate", '{"expression": "6*7"}')],
                    [_content_chunk("结果是 42。")],
                ]
            )
            agent = Agent(
                client=cast(OpenAI, client),
                model="test-model",
                audit_logger=logger,
            )

            chunks = list(agent.stream([{"role": "user", "content": "6*7 等于多少"}]))

            self.assertEqual(chunks, ["结果是 42。"])
            second_round_messages = client.responses.requests[1]["messages"]
            tool_messages = [
                message for message in second_round_messages if message.get("role") == "tool"
            ]
            self.assertEqual(tool_messages[0]["content"], "42")
            records = [
                json.loads(line) for line in audit_path.read_text("utf-8").splitlines()
            ]
            tool_events = [
                record for record in records if record["event"] == "tool_call"
            ]
            self.assertEqual(tool_events[0]["details"]["tool_name"], "calculate")


if __name__ == "__main__":
    unittest.main()
