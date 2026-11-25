"""统一的运行时配置读取：环境变量优先，其次 Streamlit secrets。

本地开发用 `.env` 或系统环境变量；部署到 Streamlit Community Cloud 时没有
`.env`，可以在 App 的 Secrets 里写同名配置（TOML 格式），这里会透明读到。
"""

from __future__ import annotations

import os
import sys

from dotenv import load_dotenv

# 本地开发时把项目目录下的 .env 读进环境变量；云端没有 .env，会自动跳过。
load_dotenv()

_MISSING = object()


def _secrets_lookup(name: str):
    """只有在 Streamlit 已经加载时才去读 secrets，避免终端版多 import 一次。"""
    if "streamlit" not in sys.modules:
        return _MISSING
    try:
        import streamlit as st

        secrets = st.secrets
        if name in secrets:
            return secrets[name]
    except Exception:
        return _MISSING
    return _MISSING


def get(name: str, default: str = "") -> str:
    """读取字符串配置：环境变量 > st.secrets > 默认值。"""
    value = os.getenv(name)
    if value not in (None, ""):
        return value
    secret = _secrets_lookup(name)
    if secret is _MISSING or secret is None:
        return default
    if isinstance(secret, bool):
        return "1" if secret else "0"
    return str(secret)


def get_int(name: str, default: int) -> int:
    """读取整数配置，解析失败时回退默认值。"""
    raw = get(name, "")
    try:
        return int(raw) if str(raw).strip() else default
    except (TypeError, ValueError):
        return default
