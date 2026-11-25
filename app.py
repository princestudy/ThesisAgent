"""Streamlit 网页入口：注册 / 登录 → 填写自己的 API Key → 聊天。

几条硬性约定：
- 账号开放注册，系统里第一个注册的人自动成为唯一管理员；
- 每个用户只使用自己的 API Key，Key 只存在浏览器会话内存，不落库、不写日志；
- 资料目录按用户隔离，身份由程序侧的 ToolScope 决定，模型看不到别人的目录。
"""

from __future__ import annotations

import html
import io
import re
import time
from pathlib import Path

from docx import Document
from pypdf import PdfReader

import streamlit as st

import auth
import memory
from agent import Agent, build_system_message
from auth import AuthError, api_base_url, api_model, verify_api_key
from scope import (
    MAX_PREVIEW_BYTES,
    SUPPORTED_KNOWLEDGE_SUFFIXES,
    ToolScope,
    delete_scope_file,
    iter_scope_files,
    read_scope_file,
    upload_directory,
)
from security_controls import (
    contains_api_key,
    mask_api_keys,
    redact_sensitive_text,
)

MAX_UPLOAD_BYTES = 10 * 1024 * 1024
MAX_UPLOAD_FILES = 10
MAX_HISTORY_TURNS = 10
# 流式输出时刷新页面的最小间隔（秒）：太勤会让前端一直重排，太慢看起来又像卡住。
STREAM_FLUSH_INTERVAL = 0.06
THINKING_HINT = "正在思考…"
TOOL_LABELS = {
    "calculate": "计算器",
    "get_current_time": "当前时间",
    "read_text_file": "读取文件",
    "search_knowledge_base": "检索知识库",
}
UPLOAD_TYPES = [suffix.lstrip(".") for suffix in sorted(SUPPORTED_KNOWLEDGE_SUFFIXES)]
SUGGESTIONS = (
    "什么是智能体安全？",
    "帮我总结共享资料的重点",
    "计算 (128 + 72) * 3 / 5",
)
MAIN_CAPTION = "可以检索本人资料与管理员共享的资料，也可以使用计算、时间和文件读取工具。"
# 资料预览：纯文本直接显示，PDF/Word 先抽文字，超过上限只提示下载。
PREVIEW_CHAR_LIMIT = 8000
PDF_PREVIEW_PAGES = 20
PREVIEW_SUFFIXES = {".pdf", ".docx", ".txt", ".md", ".csv", ".json"}
PREVIEW_MIME = {
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".md": "text/markdown",
    ".txt": "text/plain",
    ".csv": "text/csv",
    ".json": "application/json",
}
# 左侧栏的“工具”入口：点击后打开居中弹窗，不占用对话区。
TOOL_DIALOGS = {
    "upload": ("上传资料", 680),
    "files": ("资料列表", 720),
    "admin": ("用户管理", 720),
    "password": ("修改密码", None),
}

st.set_page_config(
    page_title="论文知识库助手",
    page_icon="📚",
    layout="centered",
    initial_sidebar_state="collapsed",
)

memory.initialize_database()
auth.initialize_auth_database()


# ---------------------------------------------------------------- 界面样式


def inject_styles() -> None:
    st.markdown(
        """
        <style>
        .stApp {
            background:
                radial-gradient(1100px 520px at 50% -12%, #EDF1FF 0%, #FFFFFF 62%);
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI",
                "PingFang SC", "Microsoft YaHei", sans-serif;
        }
        .block-container {padding-top: 2.2rem; padding-bottom: 0; max-width: 1120px;}
        #MainMenu, footer, [data-testid="stToolbar"],
        [data-testid="stHeader"] {visibility: hidden;}
        .sidebar-brand {display: flex; align-items: center; gap: 10px; padding: 2px 0 2px 0;}
        .sidebar-brand .logo {
            width: 34px; height: 34px; border-radius: 11px;
            background: linear-gradient(135deg, #4D6BFE 0%, #7B5CFF 100%);
            color: #FFFFFF; font-size: 16px; font-weight: 700;
            display: flex; align-items: center; justify-content: center;
            box-shadow: 0 8px 18px rgba(77, 107, 254, 0.26);
        }
        .sidebar-title {font-size: 17px; font-weight: 700; color: #111827;}
        .nav-gap {height: 6px;}
        div[data-testid="stColumn"]:has(.sidebar-brand) {
            position: sticky; top: 3.2rem; align-self: flex-start;
            height: calc(100vh - 6.75rem) !important;
            z-index: 20;
        }
        /* 让侧栏卡片的边框一直画到底部，和输入框下沿对齐 */
        div[data-testid="stColumn"]:has(.sidebar-brand)
            > div[data-testid="stVerticalBlock"]
            > div[data-testid="stLayoutWrapper"] {
            height: 100% !important;
            flex: 1 1 auto !important;
        }
        div[data-testid="stColumn"]:has(.sidebar-brand)
            > div[data-testid="stVerticalBlock"]
            > div[data-testid="stLayoutWrapper"]
            > div[data-testid="stVerticalBlock"] {
            height: 100% !important;
        }
        div[data-testid="stColumn"]:has(.sidebar-brand)
            [data-testid="stVerticalBlockBorderWrapper"] {
            background: #FBFCFE;
        }
        /* 账号信息与退出登录贴到卡片底部，让卡片下沿和输入框下沿对齐 */
        div[data-testid="stColumn"]:has(.sidebar-brand) [data-testid="stDivider"] {
            margin-top: auto;
        }
        .brand {display: flex; align-items: center; gap: 14px; margin: 4px 0 2px 0;}
        .brand-logo {
            width: 46px; height: 46px; border-radius: 15px;
            background: linear-gradient(135deg, #4D6BFE 0%, #7B5CFF 100%);
            color: #FFFFFF; font-size: 22px; font-weight: 700;
            display: flex; align-items: center; justify-content: center;
            box-shadow: 0 10px 24px rgba(77, 107, 254, 0.28);
        }
        .brand-title {
            font-size: 30px; font-weight: 700; letter-spacing: 0.4px;
            margin: 0; line-height: 1.15; color: #111827;
        }
        .brand-sub {margin: 4px 0 0 0; color: #6B7280; font-size: 14px;}
        .hero {padding: 6px 0 18px 0;}
        .hero h2 {margin: 0 0 6px 0; font-size: 22px;}
        .hero p {margin: 0; color: #6B7280; font-size: 14px;}
        div[data-baseweb="input"] > div,
        div[data-baseweb="base-input"] {
            border-radius: 12px !important;
        }
        div[data-baseweb="input"] > div {border-color: #E6E9F2 !important;}
        div[data-baseweb="input"] > div:focus-within {
            border-color: #4D6BFE !important;
            box-shadow: 0 0 0 3px rgba(77, 107, 254, 0.12) !important;
        }
        .stButton > button, .stFormSubmitButton > button {
            border-radius: 12px; font-weight: 600;
        }
        .stFormSubmitButton > button[kind="primary"] {
            background: linear-gradient(135deg, #4D6BFE 0%, #6E8BFF 100%);
            border: none;
            box-shadow: 0 8px 20px rgba(77, 107, 254, 0.22);
        }
        [data-testid="stForm"] {
            border: 1px solid #EDF0F7; border-radius: 18px;
            background: #FFFFFF; padding: 20px 20px 8px 20px;
            box-shadow: 0 12px 32px rgba(17, 24, 39, 0.06);
        }
        [data-testid="stSidebar"] {
            background: #FBFCFE; border-right: 1px solid #EDF0F7;
        }
        [data-testid="stChatMessage"] {
            border-radius: 16px; padding: 6px 10px; background: #FFFFFF;
        }
        [data-testid="stChatInput"] textarea {border-radius: 16px;}
        .stTabs [data-baseweb="tab"] {font-weight: 600; font-size: 15px;}
        .stCaption, [data-testid="stCaptionContainer"] {color: #6B7280;}
        .doc-row {color: #374151; font-size: 14px; padding: 2px 0;}
    .doc-size {color: #9CA3AF; font-size: 12px; margin-left: 6px;}
        .who {display: flex; flex-direction: column; gap: 2px; padding: 4px 0 6px 2px;}
        .who-name {font-size: 15px; font-weight: 600; color: #111827;}
        .who-role {font-size: 12px; color: #6B7280;}
        .nav-label {
            font-size: 12px; font-weight: 600; color: #9CA3AF;
            letter-spacing: 0.08em; padding: 12px 0 4px 2px;
        }
        /* 左侧栏里的按钮做成扁平列表项，接近桌面客户端的观感 */
        div[data-testid="stColumn"]:has(.sidebar-brand) .stButton > button {
            background: transparent; border: 1px solid transparent;
            box-shadow: none; color: #374151; font-weight: 500;
            justify-content: flex-start; text-align: left;
            padding: 4px 10px; min-height: 34px;
        }
        div[data-testid="stColumn"]:has(.sidebar-brand)
            .stButton > button > div {
            justify-content: flex-start;
        }
        /* 对话行的“删除”小按钮：字号小一点，不抢视线 */
        div[class*="st-key-del-"] button {
            color: #9CA3AF; font-size: 12px; padding: 4px 2px; min-height: 34px;
            background: transparent; border-color: transparent; box-shadow: none;
        }
        div[class*="st-key-del-"] button:hover {color: #EF4444; background: #FEF2F2;}
        div[role="dialog"] [data-testid="stForm"] {
            border: none; box-shadow: none; padding: 0; background: transparent;
        }
        div[data-testid="stColumn"]:has(.sidebar-brand) .stButton > button:hover {
            background: #F3F5FB; color: #111827; border-color: #F3F5FB;
        }
        div[data-testid="stColumn"]:has(.sidebar-brand)
            .stButton > button[kind="primary"],
        div[data-testid="stColumn"]:has(.sidebar-brand)
            [data-testid="stBaseButton-primary"] {
            background: #EEF1FF; color: #4D6BFE; border-color: #E2E7FF;
            font-weight: 600;
        }
        .account {display: flex; align-items: center; gap: 10px; padding: 2px 0 8px 2px;}
        .account-avatar {
            width: 32px; height: 32px; border-radius: 50%;
            background: linear-gradient(135deg, #4D6BFE 0%, #7B5CFF 100%);
            color: #FFFFFF; font-size: 14px; font-weight: 700;
            display: flex; align-items: center; justify-content: center;
            flex-shrink: 0;
        }
        .account-text {display: flex; flex-direction: column; min-width: 0;}
        .chat-title {
            font-size: 18px; font-weight: 700; color: #111827;
            margin: 0 0 2px 0; line-height: 1.3;
        }
        /* 气泡：用户靠右，助手靠左 */
        [data-testid="stChatMessage"] {
            gap: 12px; background: transparent; border: none;
        }
        [data-testid="stChatMessage"]:has([data-testid="stChatMessageAvatarUser"]) {
            flex-direction: row-reverse;
            width: fit-content;
            margin-left: auto;
            max-width: 80%;
            background: #EEF1FF;
            border: 1px solid #E2E7FF;
            border-radius: 18px 18px 6px 18px;
            padding: 10px 16px;
        }
        [data-testid="stChatMessage"]:has([data-testid="stChatMessageAvatarUser"])
            [data-testid="stChatMessageContent"] {text-align: left;}
        [data-testid="stChatMessage"]:has([data-testid="stChatMessageAvatarAssistant"]) {
            padding: 6px 2px;
        }
        /* 参照豆包：不显示头像，只靠左右分边区分说话人 */
        [data-testid="stChatMessage"] [data-testid^="stChatMessageAvatar"] {
            display: none;
        }
        div[role="dialog"] {border-radius: 18px;}
        [data-testid="stBottomBlockContainer"] {
            max-width: 1120px;
            margin: 0 auto;
            padding-left: 0;
            padding-right: 0;
        }
        /* 把输入条从文档流里拿出来，内容区才能一直延伸到窗口底部 */
        [data-testid="stBottom"] {
            position: fixed; left: 0; right: 0; bottom: 0;
            background: linear-gradient(
                180deg, rgba(255, 255, 255, 0) 0%, #FFFFFF 45%
            );
            z-index: 10;
        }
        /* 消息区高度跟视口走：st.container(height=...) 生成的是 flex 子项，
           高度由 flex-basis 决定，只改 height 无效，必须一起改 flex。 */
        div[data-testid="stVerticalBlock"].st-key-chat_history {
            flex: 0 0 calc(100vh - 250px) !important;
            height: calc(100vh - 250px) !important;
        }
        /* 助手还在等第一个 token 时的等待提示 */
        .thinking {
            display: inline-flex; align-items: center; gap: 8px;
            color: #6B7280; font-size: 14px; padding: 2px 0;
        }
        .thinking::before {
            content: ""; width: 8px; height: 8px; border-radius: 50%;
            background: #4D6BFE;
            animation: thinking-pulse 1.1s ease-in-out infinite;
        }
        @keyframes thinking-pulse {
            0%, 100% {opacity: 0.25; transform: scale(0.75);}
            50% {opacity: 1; transform: scale(1);}
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def inject_bottom_bar_offset(sidebar_open: bool) -> None:
    """侧栏展开时，把底部的输入框右移到对话列下方。"""

    if not sidebar_open:
        return
    st.markdown(
        """
        <style>
        [data-testid="stBottomBlockContainer"] {
            max-width: none !important;
            width: 100%;
            padding-left: calc(max(0px, (100% - 1120px)) / 2 + 313px) !important;
            padding-right: calc(max(0px, (100% - 1120px)) / 2 + 16px) !important;
            box-sizing: border-box;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def render_brand(subtitle: str = "") -> None:
    subtitle_html = f'<p class="brand-sub">{subtitle}</p>' if subtitle else ""
    st.markdown(
        '<div class="brand"><div class="brand-logo">论</div>'
        f'<div><div class="brand-title">论文知识库助手</div>{subtitle_html}</div></div>',
        unsafe_allow_html=True,
    )


def flash(message: str, level: str = "success") -> None:
    st.session_state.flash = (level, message)


def show_flash() -> None:
    item = st.session_state.pop("flash", None)
    if not item:
        return
    level, message = item
    {"success": st.success, "error": st.error, "warning": st.warning}.get(
        level, st.info
    )(message)


# ---------------------------------------------------------------- 会话管理


def current_scope() -> ToolScope:
    return ToolScope.for_user(st.session_state.user["id"])


def clear_credentials() -> None:
    for key in (
        "user",
        "api_key",
        "agent",
        "conversation_id",
        "chat_messages",
        "agent_messages",
        "dialog",
        "confirm_delete",
        "sidebar_open",
        "awaiting_reply",
        "pending_prompt",
        "preview",
        "preview_return",
    ):
        st.session_state.pop(key, None)


def load_conversation(conversation_id: str) -> None:
    """把某个对话的历史读进 session_state。"""
    user = st.session_state.user
    scope = ToolScope.for_user(user["id"])
    history = memory.load_recent_messages(
        limit=20, user_id=str(user["id"]), session_id=conversation_id
    )
    st.session_state.conversation_id = conversation_id
    st.session_state.chat_messages = [
        {"role": item["role"], "content": item["content"]} for item in history
    ]
    st.session_state.agent_messages = [build_system_message(scope), *history]


def start_session(user: dict, message: str | None = None) -> None:
    scope = ToolScope.for_user(user["id"])
    scope.ensure_directories()
    st.session_state.user = user
    st.session_state.api_key = None
    st.session_state.agent = None
    st.session_state.dialog = None
    st.session_state.confirm_delete = None
    st.session_state.preview = None
    st.session_state.sidebar_open = True
    conversation_id = memory.ensure_default_conversation(str(user["id"]))
    load_conversation(conversation_id)
    if message:
        flash(message)
    st.rerun()


# ---------------------------------------------------------------- 登录与注册


def render_login_form() -> None:
    with st.form("login_form"):
        username = st.text_input("用户名", placeholder="请输入用户名")
        password = st.text_input("密码", type="password", placeholder="请输入密码")
        submitted = st.form_submit_button(
            "登录", use_container_width=True, type="primary"
        )

    if not submitted:
        return
    if not (username or "").strip() or not password:
        st.error("请输入用户名和密码。")
        return
    try:
        user, message = auth.authenticate(username, password)
    except Exception:  # noqa: BLE001 - 数据库异常不能把栈信息暴露给用户
        st.error("登录失败，请稍后重试。")
        return
    if user is None:
        st.error(message)
        return
    start_session(user, f"欢迎回来，{user['username']}。")


def render_register_form() -> None:
    if not auth.has_admin():
        if auth.ADMIN_USERNAME:
            st.info("管理员账号由部署者预先配置，这里注册的都是普通用户。")
        else:
            st.info("系统里还没有账号，第一个注册的账号会成为唯一管理员。")
    if auth.INVITE_CODE:
        st.caption("注册需要邀请码，请向管理员索取。")

    with st.form("register_form"):
        username = st.text_input("用户名", placeholder="3-32 位字母、数字、下划线、点或短横线")
        password = st.text_input("密码", type="password", placeholder="至少 8 位")
        confirm = st.text_input("确认密码", type="password", placeholder="再输入一次密码")
        invite = st.text_input(
            "邀请码",
            type="password",
            placeholder="请输入邀请码",
            help="由部署者在 Secrets 里配置；没有配置时可以留空。",
        ) if auth.INVITE_CODE else ""
        submitted = st.form_submit_button(
            "创建账号", use_container_width=True, type="primary"
        )

    if not submitted:
        return
    if password != confirm:
        st.error("两次输入的密码不一致。")
        return
    try:
        user = auth.register_user(username, password, invite)
    except AuthError as error:
        st.error(str(error))
        return
    except Exception:  # noqa: BLE001
        st.error("注册失败，请稍后重试。")
        return

    role = "管理员" if user["is_admin"] else "普通用户"
    start_session(user, f"注册成功，你已作为{role}登录。")


def render_auth_page() -> None:
    left, center, right = st.columns([1, 1.15, 1])
    with center:
        render_brand("多用户论文知识库 · 会话与资料互相隔离")
        st.write("")
        show_flash()
        login_tab, register_tab = st.tabs(["登录", "注册"])
        with login_tab:
            render_login_form()
        with register_tab:
            render_register_form()
        footer = "Key 只存内存 · 资料按账号隔离 · "
        footer += "管理员由部署者固定" if auth.ADMIN_USERNAME else "首个账号即管理员"
        st.caption(footer)


def render_api_key_step() -> None:
    left, center, right = st.columns([1, 1.15, 1])
    with center:
        render_brand("每个账号使用自己的 DeepSeek API Key")
        st.write("")
        show_flash()
        st.caption(f"当前登录：{st.session_state.user['username']}")
        st.markdown(
            "Key 只保存在本次浏览器会话的内存中：不写入数据库，不写入审计日志，"
            "管理员和其他用户都看不到；刷新页面后需要重新输入。接口地址由部署者统一配置。"
        )
        with st.form("api_key_form"):
            api_key = st.text_input(
                "DeepSeek API Key", type="password", placeholder="sk-..."
            )
            submitted = st.form_submit_button(
                "校验并开始使用", use_container_width=True, type="primary"
            )

        if st.button("退出登录", use_container_width=True):
            clear_credentials()
            st.rerun()

        if not submitted:
            return
        with st.spinner("正在校验 API Key…"):
            ok, message = verify_api_key(api_key, api_base_url())
        if not ok:
            st.error(message)
            return
        try:
            agent = Agent(
                api_key=api_key.strip(),
                model=api_model(),
                base_url=api_base_url(),
            )
        except RuntimeError as error:
            st.error(redact_sensitive_text(str(error)))
            return
        st.session_state.api_key = api_key.strip()
        st.session_state.agent = agent
        st.rerun()


# ---------------------------------------------------------------- 资料管理


def sanitize_filename(name: str) -> str:
    """上传文件名只保留最后一段，并去掉路径分隔符、控制字符和首尾的点。"""
    base = Path(str(name or "")).name
    base = re.sub(r'[\x00-\x1f<>:"/\\|?*]', "_", base).strip().strip(".")
    return base[:120] or "未命名文件"


def render_upload(scope: ToolScope) -> None:
    is_admin = bool(st.session_state.user["is_admin"])
    tip = "以管理员身份上传，资料进入共享库，所有用户都能检索到。" if is_admin else "上传的资料只对你本人可见。"
    st.caption(tip)
    uploads = st.file_uploader(
        "选择文件",
        type=UPLOAD_TYPES,
        accept_multiple_files=True,
        key="uploader",
        label_visibility="collapsed",
    )
    if not uploads:
        return
    if st.button("保存上传的文件", use_container_width=True):
        if len(uploads) > MAX_UPLOAD_FILES:
            st.error(f"单次最多上传 {MAX_UPLOAD_FILES} 个文件。")
            return
        target_dir = upload_directory(scope, is_admin=is_admin)
        target_dir.mkdir(parents=True, exist_ok=True)
        saved: list[str] = []
        skipped: list[str] = []
        for uploaded_file in uploads:
            safe_name = sanitize_filename(uploaded_file.name)
            suffix = Path(safe_name).suffix.lower()
            if suffix not in SUPPORTED_KNOWLEDGE_SUFFIXES:
                skipped.append(f"{safe_name}（格式不支持）")
                continue
            if uploaded_file.size > MAX_UPLOAD_BYTES:
                skipped.append(f"{safe_name}（超过 10 MB）")
                continue
            target = target_dir / safe_name
            if target.exists():
                skipped.append(f"{safe_name}（同名文件已存在）")
                continue
            target.write_bytes(uploaded_file.getvalue())
            saved.append(safe_name)
        if saved:
            flash(f"已保存 {len(saved)} 个文件。")
        if skipped:
            flash("已跳过：" + "、".join(skipped[:5]), "warning")
        st.rerun()


def format_size(size: int) -> str:
    if size < 1024:
        return f"{size} B"
    if size < 1024 * 1024:
        return f"{size / 1024:.1f} KB"
    return f"{size / 1024 / 1024:.1f} MB"


def extract_preview_text(name: str, data: bytes) -> tuple[str, bool]:
    """把资料转成可读文本；返回 (文本, 是否截断)。不支持的类型返回空串。"""
    suffix = Path(name).suffix.lower()
    text = ""
    if suffix in {".txt", ".md", ".csv", ".json"}:
        text = data.decode("utf-8", errors="replace")
    elif suffix == ".pdf":
        try:
            reader = PdfReader(io.BytesIO(data))
            pages = []
            for number, page in enumerate(reader.pages[:PDF_PREVIEW_PAGES], start=1):
                content = page.extract_text() or ""
                if content.strip():
                    pages.append(f"—— 第 {number} 页 ——\n{content.strip()}")
            text = "\n\n".join(pages)
        except Exception as error:  # noqa: BLE001 - 坏 PDF 不应让整页崩掉
            return f"这个 PDF 无法解析：{error}", False
    elif suffix == ".docx":
        try:
            document = Document(io.BytesIO(data))
            parts = [paragraph.text for paragraph in document.paragraphs if paragraph.text.strip()]
            for table in document.tables:
                for row in table.rows:
                    cells = [cell.text.strip() for cell in row.cells if cell.text.strip()]
                    if cells:
                        parts.append(" | ".join(cells))
            text = "\n".join(parts)
        except Exception as error:  # noqa: BLE001
            return f"这个 Word 文件无法解析：{error}", False
    else:
        return "", False
    truncated = len(text) > PREVIEW_CHAR_LIMIT
    return text[:PREVIEW_CHAR_LIMIT], truncated


def open_preview(path: Path, directory: Path) -> None:
    # Streamlit 同一时刻只允许一个弹窗：打开预览前先把资料列表收起来，
    # 记下它，关掉预览再自动回到列表。
    st.session_state.preview_return = st.session_state.get("dialog")
    st.session_state.dialog = None
    st.session_state.preview = {"path": str(path), "directory": str(directory)}
    st.rerun()


def close_preview() -> None:
    st.session_state.preview = None
    back_to = st.session_state.get("preview_return")
    st.session_state.preview_return = None
    if back_to:
        st.session_state.dialog = back_to
        st.rerun()


def render_preview_dialog() -> None:
    """资料预览弹窗：先看内容，再决定要不要下载。"""
    state = st.session_state.get("preview")
    if not state:
        return

    def dialog_body() -> None:
        path = Path(state["path"])
        directory = Path(state["directory"])
        try:
            data = read_scope_file(path, directory)
        except (ValueError, OSError) as error:
            st.error(str(error))
            if st.button("关闭", use_container_width=True):
                close_preview()
                st.rerun()
            return

        suffix = path.suffix.lower()
        st.caption(f"{format_size(len(data))} · 只读预览，下载后可用本地软件打开")
        text, truncated = extract_preview_text(path.name, data)
        if text:
            with st.container(height=420, border=False):
                if suffix == ".md":
                    st.markdown(text)
                else:
                    st.text(text)
            if truncated:
                st.caption(
                    f"内容较长，这里只显示前 {PREVIEW_CHAR_LIMIT} 个字符，完整内容请下载查看。"
                )
        else:
            st.info("这种格式没法在网页里预览，请直接下载。")
        st.download_button(
            "下载文件",
            data=data,
            file_name=path.name,
            mime=PREVIEW_MIME.get(suffix, "application/octet-stream"),
            use_container_width=True,
            key=f"download-{path.name}",
        )

    title = Path(state["path"]).name
    kwargs = {"on_dismiss": close_preview}
    try:
        opener = st.dialog(title, width=760, **kwargs)
    except TypeError:  # 老版本 Streamlit 没有 width / on_dismiss
        opener = st.dialog(title)
    opener(dialog_body)()


def render_file_section(
    title: str,
    files: list[Path],
    directory: Path,
    *,
    deletable: bool,
    key_prefix: str,
) -> None:
    """列出一组资料；每行可以查看内容，可删除时还带"删除 → 确认/取消"两步操作。"""
    st.caption(f"{title} · {len(files)} 个")
    pending = st.session_state.get("confirm_delete")
    for index, path in enumerate(files):
        marker = str(path)
        if deletable:
            columns = st.columns([3.1, 1.0, 1.1, 1.0])
        else:
            columns = st.columns([3.7, 1.05])
        try:
            size_text = format_size(path.stat().st_size)
        except OSError:
            size_text = ""
        columns[0].markdown(
            f'<div class="doc-row">· {html.escape(path.name)}'
            f'<span class="doc-size">{html.escape(size_text)}</span></div>',
            unsafe_allow_html=True,
        )
        if columns[1].button(
            "查看", key=f"view-{key_prefix}-{index}", use_container_width=True
        ):
            open_preview(path, directory)
        if not deletable:
            continue
        if pending == marker:
            if columns[2].button(
                "确认删除", key=f"confirm-{key_prefix}-{index}", use_container_width=True
            ):
                try:
                    delete_scope_file(path, directory)
                except (ValueError, OSError) as error:
                    flash(str(error), "error")
                else:
                    flash(f"已删除 {path.name}。")
                st.session_state.confirm_delete = None
                st.rerun()
            if columns[3].button(
                "取消", key=f"cancel-{key_prefix}-{index}", use_container_width=True
            ):
                st.session_state.confirm_delete = None
                st.rerun()
        elif columns[3].button(
            "删除", key=f"delete-{key_prefix}-{index}", use_container_width=True
        ):
            st.session_state.confirm_delete = marker
            st.rerun()


def render_file_lists(scope: ToolScope) -> None:
    files = list(iter_scope_files(scope))
    private_files = sorted(
        (path for label, path in files if label == "private"), key=lambda item: item.name
    )
    shared_files = sorted(
        (path for label, path in files if label == "shared"), key=lambda item: item.name
    )
    is_admin = bool(st.session_state.user and st.session_state.user.get("is_admin"))
    if private_files:
        render_file_section(
            "我的资料",
            private_files,
            scope.knowledge_dirs[0],
            deletable=True,
            key_prefix="private",
        )
    else:
        st.caption("我的资料：还没有上传。")
    if shared_files:
        shared_dir = scope.shared_dir or scope.knowledge_dirs[-1]
        render_file_section(
            "共享资料",
            shared_files,
            shared_dir,
            deletable=is_admin,
            key_prefix="shared",
        )
        if not is_admin:
            st.caption("共享资料由管理员维护，普通用户只能查看和使用。")


def render_change_password() -> None:
    st.caption("管理员也只能修改自己的密码。")
    with st.form("password_form"):
        current = st.text_input("当前密码", type="password")
        new_password = st.text_input("新密码", type="password")
        confirm = st.text_input("确认新密码", type="password")
        submitted = st.form_submit_button("保存新密码", use_container_width=True)
    if not submitted:
        return
    if new_password != confirm:
        st.error("两次输入的新密码不一致。")
        return
    try:
        ok, message = auth.change_password(
            st.session_state.user["id"], current, new_password
        )
    except AuthError as error:
        st.error(str(error))
        return
    (st.success if ok else st.error)(message)


# ---------------------------------------------------------------- 管理员面板


def render_admin_panel() -> None:
    st.caption("管理员有且只有一个；这里可以查看全部账号，并停用或恢复普通用户。")
    for account in auth.list_users():
        account_id = int(account["id"])
        is_self = account_id == int(st.session_state.user["id"])
        role = "管理员" if account["is_admin"] else "普通用户"
        status = " · 已停用" if account["is_disabled"] else ""
        left, right = st.columns([3, 2])
        left.markdown(f"**{account['username']}**  \n{role}{status}")
        if account["is_admin"]:
            right.caption("管理员不可停用")
        elif is_self:
            right.caption("当前账号")
        else:
            label = "恢复" if account["is_disabled"] else "停用"
            if right.button(label, key=f"toggle_{account_id}", use_container_width=True):
                try:
                    auth.set_disabled(account_id, not account["is_disabled"])
                except AuthError as error:
                    st.error(str(error))
                else:
                    st.rerun()
        st.divider()


# ---------------------------------------------------------------- 左侧栏与弹窗


def open_dialog(key: str) -> None:
    st.session_state.dialog = key
    st.rerun()


def close_dialog() -> None:
    st.session_state.dialog = None


def toggle_sidebar(open_state: bool) -> None:
    st.session_state.sidebar_open = open_state
    st.rerun()


def new_conversation() -> None:
    user = st.session_state.user
    try:
        conversation_id = memory.create_conversation(str(user["id"]))
    except memory.ConversationLimitError as error:
        flash(str(error), "warning")
        st.rerun()
        return
    st.session_state.awaiting_reply = False
    load_conversation(conversation_id)
    st.rerun()


def switch_conversation(conversation_id: str) -> None:
    if conversation_id == st.session_state.get("conversation_id"):
        return
    st.session_state.awaiting_reply = False
    load_conversation(conversation_id)
    st.rerun()


def remove_conversation(conversation_id: str) -> None:
    user = st.session_state.user
    user_id = str(user["id"])
    memory.delete_conversation(conversation_id, user_id)
    remaining = memory.list_conversations(user_id)
    if not remaining:
        remaining_id = memory.ensure_default_conversation(user_id)
    else:
        remaining_id = remaining[0]["id"]
    if conversation_id == st.session_state.get("conversation_id"):
        load_conversation(remaining_id)
    flash("对话已删除。")
    st.rerun()


def clear_current_conversation() -> None:
    user = st.session_state.user
    conversation_id = st.session_state.get("conversation_id")
    if not conversation_id:
        return
    memory.clear_messages(user_id=str(user["id"]), session_id=conversation_id)
    st.session_state.awaiting_reply = False
    load_conversation(conversation_id)
    flash("当前对话已清空。")
    st.rerun()


def conversation_label(conversation: dict) -> str:
    title = (conversation.get("title") or "新对话").strip()
    return title if len(title) <= 12 else title[:12] + "…"


def render_sidebar(scope: ToolScope) -> None:
    """常驻左栏：品牌、新对话、对话列表、工具入口、左下角账号区。"""
    user = st.session_state.user
    user_id = str(user["id"])
    role = "管理员" if user["is_admin"] else "普通用户"
    active_conversation = st.session_state.get("conversation_id")

    with st.container(border=True):
        st.markdown(
            '<div class="sidebar-brand">'
            '<span class="logo">论</span>'
            '<span class="sidebar-title">论文知识库助手</span>'
            "</div>",
            unsafe_allow_html=True,
        )
        if st.button(
            "＋ 新对话",
            key="new-conversation",
            use_container_width=True,
        ):
            new_conversation()

        st.markdown('<div class="nav-label">对话</div>', unsafe_allow_html=True)
        conversations = memory.list_conversations(user_id)
        for conversation in conversations:
            is_active = conversation["id"] == active_conversation
            title_column, action_column = st.columns([5.2, 1.4])
            if title_column.button(
                conversation_label(conversation),
                key=f"conv-{conversation['id']}",
                use_container_width=True,
                type="primary" if is_active else "secondary",
            ):
                switch_conversation(conversation["id"])
            if action_column.button(
                "删除", key=f"del-{conversation['id']}", help="删除这个对话"
            ):
                remove_conversation(conversation["id"])

        st.markdown('<div class="nav-gap"></div>', unsafe_allow_html=True)
        st.markdown('<div class="nav-label">工具</div>', unsafe_allow_html=True)
        entries = [("upload", "上传资料"), ("files", "资料列表")]
        if user["is_admin"]:
            entries.append(("admin", "用户管理"))
        for key, label in entries:
            if st.button(label, key=f"dialog-{key}", use_container_width=True):
                open_dialog(key)
        if conversations and st.button(
            "清空当前对话", key="clear-conversation", use_container_width=True
        ):
            clear_current_conversation()

        st.divider()
        st.markdown(
            '<div class="account">'
            '<span class="account-avatar">'
            f'{html.escape(str(user["username"])[:1].upper())}</span>'
            '<span class="account-text">'
            f'<span class="who-name">{html.escape(user["username"])}</span>'
            f'<span class="who-role">{role} · 已登录</span>'
            "</span>"
            "</div>",
            unsafe_allow_html=True,
        )
        password_column, logout_column = st.columns(2)
        if password_column.button("修改密码", key="open-password", use_container_width=True):
            open_dialog("password")
        if logout_column.button("退出登录", key="logout", use_container_width=True):
            clear_credentials()
            st.rerun()


def render_active_dialog(scope: ToolScope) -> None:
    """左侧栏的工具入口统一用居中弹窗展示，不占用对话区。"""
    if st.session_state.get("preview"):
        # 预览弹窗优先，避免同时开两个弹窗（Streamlit 不允许）。
        return
    key = st.session_state.get("dialog")
    if not key:
        return
    user = st.session_state.get("user") or {}
    if key == "admin" and not user.get("is_admin"):
        st.session_state.dialog = None
        return
    spec = TOOL_DIALOGS.get(key)
    if not spec:
        st.session_state.dialog = None
        return
    title, width = spec
    renderers = {
        "upload": lambda: render_upload(scope),
        "files": lambda: render_file_lists(scope),
        "admin": render_admin_panel,
        "password": render_change_password,
    }
    renderer = renderers[key]

    def dialog_body() -> None:
        renderer()

    kwargs = {"on_dismiss": close_dialog}
    if width:
        kwargs["width"] = width
    try:
        opener = st.dialog(title, **kwargs)
    except TypeError:  # 老版本 Streamlit 没有 width / on_dismiss
        opener = st.dialog(title)
    opener(dialog_body)()


# ---------------------------------------------------------------- 聊天


def render_tool_events(tools: list[dict]) -> None:
    if not tools:
        return
    with st.expander("查看工具调用"):
        for tool in tools:
            st.code(f"{TOOL_LABELS.get(tool['name'], tool['name'])}：{tool['arguments']}")
            st.text(tool["result"][:1000])


def trim_agent_messages() -> None:
    messages = st.session_state.agent_messages
    keep = MAX_HISTORY_TURNS * 2
    if len(messages) > keep + 1:
        st.session_state.agent_messages = [messages[0], *messages[-keep:]]


def render_chat_header() -> None:
    user = st.session_state.user
    conversation = memory.get_conversation(
        st.session_state.get("conversation_id") or "", str(user["id"])
    )
    title = (conversation or {}).get("title") or "新对话"
    st.markdown(
        f'<div class="chat-title">{html.escape(title)}</div>',
        unsafe_allow_html=True,
    )
    st.caption(MAIN_CAPTION)


def render_history(scope: ToolScope) -> None:
    with st.container(key="chat_history", height=520, border=False, autoscroll=True):
        for message in st.session_state.chat_messages:
            with st.chat_message(message["role"]):
                st.markdown(message["content"])
                render_tool_events(message.get("tools", []))

        if not st.session_state.chat_messages:
            st.caption("可以试着问我：")
            suggestion_columns = st.columns(len(SUGGESTIONS))
            for column, suggestion in zip(suggestion_columns, SUGGESTIONS):
                if column.button(suggestion, use_container_width=True):
                    st.session_state.pending_prompt = suggestion
                    st.rerun()

        if st.session_state.get("awaiting_reply"):
            render_streaming_reply(scope)


def render_streaming_reply(scope: ToolScope) -> None:
    """用户消息已经落库后，在这里等模型第一段输出，并把后续内容逐字贴出来。"""
    user = st.session_state.user
    user_id = str(user["id"])
    conversation_id = st.session_state.get("conversation_id")
    tool_events: list[dict] = []

    def record_tool(name: str, arguments: dict, result: str) -> None:
        tool_events.append({"name": name, "arguments": arguments, "result": result})

    chunks: list[str] = []
    with st.chat_message("assistant"):
        status = st.empty()
        body = st.empty()
        status.markdown(
            f'<div class="thinking">{THINKING_HINT}</div>', unsafe_allow_html=True
        )
        last_push = 0.0
        try:
            for chunk in st.session_state.agent.stream(
                st.session_state.agent_messages,
                on_tool=record_tool,
                request_id=f"web-{user['id']}",
                user_id=user_id,
                session_id=conversation_id,
                scope=scope,
            ):
                chunks.append(chunk)
                now = time.monotonic()
                if now - last_push >= STREAM_FLUSH_INTERVAL:
                    last_push = now
                    if status is not None:
                        status.empty()
                        status = None
                    body.markdown(mask_api_keys("".join(chunks)) + "▌")
        except Exception as error:  # noqa: BLE001 - 网络和鉴权错误都要兜住
            detail = redact_sensitive_text(str(error)).strip()
            chunks = ["这次回答没有成功：" + (detail[:300] if detail else "请稍后重试。")]

        if status is not None:
            status.empty()
        answer = mask_api_keys("".join(chunks).strip())
        if not answer:
            answer = "模型没有返回文字内容。"
        body.markdown(answer)
        render_tool_events(tool_events)

    st.session_state.agent_messages.append({"role": "assistant", "content": answer})
    st.session_state.chat_messages.append(
        {"role": "assistant", "content": answer, "tools": tool_events}
    )
    st.session_state.awaiting_reply = False
    trim_agent_messages()
    memory.save_message(
        "assistant", answer, user_id=user_id, session_id=conversation_id
    )
    memory.touch_conversation(conversation_id, user_id)
    # 重绘一次：让消息从「流式占位」变成正式的历史消息，同时恢复输入框。
    st.rerun()


def handle_prompt(scope: ToolScope) -> None:
    user = st.session_state.user
    user_id = str(user["id"])
    conversation_id = st.session_state.get("conversation_id")
    if not conversation_id:
        conversation_id = memory.ensure_default_conversation(user_id)
        load_conversation(conversation_id)

    prompt = st.chat_input(
        "给论文知识库助手发消息…",
        disabled=bool(st.session_state.get("awaiting_reply")),
    )
    if not prompt:
        prompt = st.session_state.pop("pending_prompt", None)
    if not prompt:
        return
    if contains_api_key(prompt):
        st.error("消息里似乎有 API Key，已拒绝发送。Key 请只在登录后的输入框里填写。")
        return

    conversation = memory.get_conversation(conversation_id, user_id)
    if conversation and conversation["title"] in ("新对话", ""):
        memory.rename_conversation(conversation_id, prompt[:16], user_id)

    # 用户消息先落库、再重绘，这样点完发送立刻就能在对话里看到自己的问题。
    st.session_state.chat_messages.append({"role": "user", "content": prompt})
    st.session_state.agent_messages.append({"role": "user", "content": prompt})
    memory.save_message("user", prompt, user_id=user_id, session_id=conversation_id)
    memory.touch_conversation(conversation_id, user_id)
    st.session_state.awaiting_reply = True
    st.rerun()


# ---------------------------------------------------------------- 入口


def main() -> None:
    inject_styles()

    try:
        admin_hint = auth.ensure_admin_account()
    except Exception:  # noqa: BLE001 - 初始化失败不应该让整页打不开
        admin_hint = "固定管理员账号初始化失败，请检查 Secrets 里的 ADMIN_USERNAME / ADMIN_PASSWORD。"
    if admin_hint:
        st.warning(admin_hint)

    user = st.session_state.get("user")
    if not user:
        render_auth_page()
        return

    if not st.session_state.get("agent"):
        render_api_key_step()
        return

    scope = current_scope()
    sidebar_open = st.session_state.get("sidebar_open", True)
    if sidebar_open:
        sidebar_column, main_column = st.columns([1, 3.1], gap="large")
        with sidebar_column:
            render_sidebar(scope)
        with main_column:
            render_chat_header()
            show_flash()
            render_history(scope)
    else:
        toggle_column, brand_column = st.columns([1, 11])
        with toggle_column:
            if st.button("»", key="sidebar-open", help="展开侧栏"):
                toggle_sidebar(True)
        with brand_column:
            render_brand()
        show_flash()
        render_history(scope)

    render_active_dialog(scope)
    render_preview_dialog()

    # 输入框放在页面顶层，Streamlit 会把它停靠到窗口底部。
    inject_bottom_bar_offset(sidebar_open)
    handle_prompt(scope)


main()
