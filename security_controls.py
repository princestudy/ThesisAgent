"""ThesisAgent 的确定性安全控制。

模型输出只能提出动作，下面的控制负责在本地程序层限制动作的范围、大小和次数。
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

MAX_TOOL_ROUNDS = 6
MAX_TOOL_CALLS = 12
MAX_TOOL_OUTPUT_CHARS = 12000
MAX_FILE_BYTES = 2 * 1024 * 1024
MAX_QUERY_LENGTH = 1000
MAX_PATH_LENGTH = 400
MAX_EXPRESSION_LENGTH = 100
ALLOWED_DATA_SUFFIXES = {".txt", ".md", ".csv", ".json"}
BLOCKED_DATA_FILENAMES = {
    ".env",
    ".env.example",
    "memory.db",
    "secret_key",
}

_SECRET_PATTERNS = (
    re.compile(
        r'(?i)"?(OPENAI_API_KEY|API_KEY|ACCESS_TOKEN|SECRET_KEY)"?\s*(?:=|:)\s*"?[^\s,;}]+'
    ),
    re.compile(r"(?i)Bearer\s+[A-Za-z0-9._~+/=-]{12,}"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{12,}\b"),
)


class SecurityLimitExceeded(RuntimeError):
    """Agent 达到工具轮次或工具调用预算。"""


class ToolArgumentError(ValueError):
    """工具参数不符合本地安全策略。"""


def bounded_text(value: str, maximum_length: int, label: str) -> str:
    if not isinstance(value, str):
        raise ToolArgumentError(f"{label} 必须是字符串")
    if not value.strip():
        raise ToolArgumentError(f"{label} 不能为空")
    if len(value) > maximum_length:
        raise ToolArgumentError(f"{label} 超过 {maximum_length} 个字符")
    return value


def validate_tool_arguments(tool_name: str, arguments: object) -> dict[str, Any]:
    if not isinstance(arguments, dict):
        raise ToolArgumentError("工具参数必须是 JSON 对象")

    if tool_name == "calculate":
        allowed_keys = {"expression"}
        if set(arguments) != allowed_keys:
            raise ToolArgumentError("calculate 只接受 expression 参数")
        return {
            "expression": bounded_text(
                arguments["expression"],
                MAX_EXPRESSION_LENGTH,
                "expression",
            )
        }

    if tool_name == "get_current_time":
        if arguments:
            raise ToolArgumentError("get_current_time 不接受参数")
        return {}

    if tool_name == "read_text_file":
        allowed_keys = {"path"}
        if set(arguments) != allowed_keys:
            raise ToolArgumentError("read_text_file 只接受 path 参数")
        return {"path": bounded_text(arguments["path"], MAX_PATH_LENGTH, "path")}

    if tool_name == "search_knowledge_base":
        allowed_keys = {"query", "top_k"}
        if not set(arguments).issubset(allowed_keys) or "query" not in arguments:
            raise ToolArgumentError("search_knowledge_base 的参数不合法")
        top_k = arguments.get("top_k", 4)
        if isinstance(top_k, bool) or not isinstance(top_k, int):
            raise ToolArgumentError("top_k 必须是整数")
        if not 1 <= top_k <= 8:
            raise ToolArgumentError("top_k 必须在 1 到 8 之间")
        return {
            "query": bounded_text(arguments["query"], MAX_QUERY_LENGTH, "query"),
            "top_k": top_k,
        }

    raise ToolArgumentError(f"不支持的工具：{tool_name}")


def resolve_data_file(data_dir: Path, raw_path: str) -> Path:
    path = bounded_text(raw_path, MAX_PATH_LENGTH, "path")
    data_root = data_dir.resolve()
    target = (data_root / path).resolve()
    if target == data_root or data_root not in target.parents:
        raise ToolArgumentError("只能读取 data 文件夹内的文件")
    if target.name.lower() in BLOCKED_DATA_FILENAMES:
        raise ToolArgumentError("该文件属于受保护文件，禁止通过 Agent 读取")
    if target.suffix.lower() not in ALLOWED_DATA_SUFFIXES:
        raise ToolArgumentError("只支持 txt、md、csv 和 json 文件")
    if not target.is_file():
        raise ToolArgumentError(f"文件不存在：{raw_path}")
    if target.stat().st_size > MAX_FILE_BYTES:
        raise ToolArgumentError(
            f"文件超过 {MAX_FILE_BYTES // 1024 // 1024} MB 安全上限"
        )
    return target


def redact_sensitive_text(value: str) -> str:
    redacted_value = value
    for pattern in _SECRET_PATTERNS:
        redacted_value = pattern.sub("[REDACTED_SECRET]", redacted_value)
    return redacted_value


def limit_tool_output(value: str) -> str:
    safe_value = redact_sensitive_text(str(value))
    if len(safe_value) > MAX_TOOL_OUTPUT_CHARS:
        return safe_value[:MAX_TOOL_OUTPUT_CHARS] + "\n[工具输出已截断]"
    return safe_value


class AuditLogger:
    """将安全事件以脱敏 JSONL 形式写入本地，便于复盘实验。"""

    def __init__(self, log_path: Path | None = None) -> None:
        self.log_path = (
            log_path or Path(__file__).resolve().parent / "security_lab" / "audit.jsonl"
        )

    def record(self, event: str, **details: object) -> None:
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        safe_details = redact_sensitive_text(
            json.dumps(details, ensure_ascii=False, default=str)
        )
        record = {
            "timestamp": datetime.now(UTC).isoformat(),
            "event": event,
            "details": json.loads(safe_details),
        }
        with self.log_path.open("a", encoding="utf-8") as audit_file:
            audit_file.write(json.dumps(record, ensure_ascii=False) + "\n")
