import json
import unittest
from pathlib import Path

from agent import calculate, read_text_file
from security_lab.provenance import (
    create_provenance_record,
    verify_provenance_record,
)


CASES_PATH = Path(__file__).with_name("jailbreak_cases.jsonl")


class OfflineSecurityTests(unittest.TestCase):
    def test_calculator_rejects_python_code(self) -> None:
        with self.assertRaises(ValueError):
            calculate("__import__('os').getcwd()")

    def test_file_tool_rejects_path_traversal(self) -> None:
        with self.assertRaises(ValueError):
            read_text_file("..\\.env")

    def test_file_tool_rejects_unsupported_extension(self) -> None:
        with self.assertRaises(ValueError):
            read_text_file("not_allowed.py")

    def test_jailbreak_cases_have_required_fields(self) -> None:
        required_fields = {
            "id",
            "category",
            "prompt",
            "expected_behavior",
            "risk",
        }
        with CASES_PATH.open("r", encoding="utf-8") as cases_file:
            cases = [json.loads(line) for line in cases_file if line.strip()]

        self.assertGreaterEqual(len(cases), 5)
        self.assertTrue(all(required_fields <= set(case) for case in cases))

    def test_provenance_round_trip(self) -> None:
        record = create_provenance_record(
            answer="安全测试答案",
            model="test-model",
            request_id="request-001",
            signing_secret="test-secret",
            created_at="2026-01-01T00:00:00+00:00",
        )
        self.assertTrue(verify_provenance_record(record, "test-secret"))
        self.assertFalse(verify_provenance_record(record, "wrong-secret"))

    def test_provenance_detects_tampering(self) -> None:
        record = create_provenance_record(
            answer="原始答案",
            model="test-model",
            request_id="request-002",
            signing_secret="test-secret",
            created_at="2026-01-01T00:00:00+00:00",
        )
        record["payload"]["answer_sha256"] = "tampered"
        self.assertFalse(verify_provenance_record(record, "test-secret"))


if __name__ == "__main__":
    unittest.main()

