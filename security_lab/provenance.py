"""应用级输出溯源示例。

这不是模型水印：它只证明应用记录的内容摘要没有被篡改。
"""

from __future__ import annotations

import hashlib
import hmac
import json
from datetime import datetime, timezone
from typing import Any


ALGORITHM = "HMAC-SHA256"


def _canonical_json(value: dict[str, Any]) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def _sign(payload: dict[str, Any], signing_secret: str) -> str:
    if not signing_secret:
        raise ValueError("签名密钥不能为空")
    message = _canonical_json(payload).encode("utf-8")
    return hmac.new(
        signing_secret.encode("utf-8"),
        message,
        hashlib.sha256,
    ).hexdigest()


def create_provenance_record(
    answer: str,
    model: str,
    request_id: str,
    signing_secret: str,
    created_at: str | None = None,
) -> dict[str, Any]:
    """创建可验证的应用级输出摘要。"""
    if not answer:
        raise ValueError("answer 不能为空")
    if not model:
        raise ValueError("model 不能为空")
    if not request_id:
        raise ValueError("request_id 不能为空")

    payload = {
        "version": 1,
        "request_id": request_id,
        "model": model,
        "answer_sha256": hashlib.sha256(answer.encode("utf-8")).hexdigest(),
        "created_at": created_at or datetime.now(timezone.utc).isoformat(),
    }
    return {
        "algorithm": ALGORITHM,
        "payload": payload,
        "signature": _sign(payload, signing_secret),
    }


def verify_provenance_record(
    record: dict[str, Any],
    signing_secret: str,
) -> bool:
    """验证应用级记录是否仍然完整且由相同密钥签名。"""
    if record.get("algorithm") != ALGORITHM:
        return False
    payload = record.get("payload")
    signature = record.get("signature")
    if not isinstance(payload, dict) or not isinstance(signature, str):
        return False
    expected_signature = _sign(payload, signing_secret)
    return hmac.compare_digest(signature, expected_signature)

