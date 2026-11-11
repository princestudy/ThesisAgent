from pathlib import Path

import streamlit as st

from agent import Agent, build_system_message
from knowledge_base import KNOWLEDGE_DIR

SUPPORTED_SUFFIXES = {".pdf", ".docx", ".txt", ".md", ".csv", ".json"}
MAX_UPLOAD_BYTES = 10 * 1024 * 1024
MAX_UPLOAD_FILES = 10
TOOL_LABELS = {
    "calculate": "计算器",
    "get_current_time": "当前时间",
    "read_text_file": "读取文件",
    "search_knowledge_base": "检索知识库",
}


st.set_page_config(
    page_title="论文知识库助手",
    page_icon=None,
    layout="wide",
)


@st.cache_resource
def get_agent() -> Agent:
    return Agent()


def list_knowledge_files() -> list[Path]:
    if not KNOWLEDGE_DIR.exists():
        return []
    return sorted(
        path
        for path in KNOWLEDGE_DIR.rglob("*")
        if path.is_file() and path.suffix.lower() in SUPPORTED_SUFFIXES
    )


def initialize_session() -> None:
    if "chat_messages" not in st.session_state:
        st.session_state.chat_messages = []
    if "agent_messages" not in st.session_state:
        st.session_state.agent_messages = [build_system_message()]


initialize_session()

try:
    agent = get_agent()
    startup_error = None
except (OSError, RuntimeError, ValueError) as error:
    agent = None
    startup_error = str(error)


with st.sidebar:
    st.header("知识库")
    uploads = st.file_uploader(
        "上传资料",
        type=["pdf", "docx", "txt", "md", "csv", "json"],
        accept_multiple_files=True,
        help="文件只会保存在本机的 knowledge 文件夹内。",
    )

    if st.button("保存到知识库", disabled=not uploads):
        if len(uploads) > MAX_UPLOAD_FILES:
            st.error(f"单次最多上传 {MAX_UPLOAD_FILES} 个文件。")
            st.stop()
        KNOWLEDGE_DIR.mkdir(parents=True, exist_ok=True)
        saved_names = []
        for uploaded_file in uploads:
            safe_name = Path(uploaded_file.name).name
            suffix = Path(safe_name).suffix.lower()
            if not safe_name or suffix not in SUPPORTED_SUFFIXES:
                st.warning(f"已跳过不支持的文件：{uploaded_file.name}")
                continue
            if uploaded_file.size > MAX_UPLOAD_BYTES:
                st.warning(
                    f"已跳过过大的文件：{safe_name}，上限为 {MAX_UPLOAD_BYTES // 1024 // 1024} MB。"
                )
                continue
            target = KNOWLEDGE_DIR / safe_name
            if target.exists():
                st.warning(f"已跳过同名文件，避免覆盖已有资料：{safe_name}")
                continue
            target.write_bytes(uploaded_file.getvalue())
            saved_names.append(safe_name)
        if saved_names:
            st.success(f"已保存：{', '.join(saved_names)}")

    knowledge_files = list_knowledge_files()
    st.caption(f"当前知识库文件：{len(knowledge_files)} 个")
    for knowledge_file in knowledge_files:
        size_kb = knowledge_file.stat().st_size / 1024
        st.write(f"{knowledge_file.name}  {size_kb:.1f} KB")

    st.divider()
    if st.button("清空当前对话"):
        st.session_state.chat_messages = []
        st.session_state.agent_messages = [build_system_message()]
        st.rerun()

    if agent:
        st.caption(f"模型：{agent.model}")


st.title("论文知识库助手")
st.caption("可以询问 knowledge 文件夹中的资料，也可以使用计算、时间和文件读取工具。")

if startup_error:
    st.error(startup_error)
    st.stop()

assert agent is not None

for message in st.session_state.chat_messages:
    with st.chat_message(message["role"]):
        st.markdown(message["content"])
        tools = message.get("tools", [])
        if tools:
            with st.expander("查看工具调用"):
                for tool in tools:
                    st.code(
                        f"{TOOL_LABELS.get(tool['name'], tool['name'])}："
                        f"{tool['arguments']}"
                    )
                    st.text(tool["result"][:1000])


prompt = st.chat_input("请输入关于资料、学习或计算的问题")

if prompt:
    st.session_state.chat_messages.append({"role": "user", "content": prompt})
    st.session_state.agent_messages.append({"role": "user", "content": prompt})

    with st.chat_message("user"):
        st.markdown(prompt)

    tool_events = []

    def record_tool(name: str, arguments: dict, result: str) -> None:
        tool_events.append(
            {
                "name": name,
                "arguments": arguments,
                "result": result,
            }
        )

    with st.chat_message("assistant"):
        with st.spinner("正在查阅资料并思考..."):
            try:
                answer = agent.run(
                    st.session_state.agent_messages,
                    on_tool=record_tool,
                )
            except (OSError, RuntimeError, ValueError) as error:
                answer = f"调用失败：{error}"
        st.markdown(answer)

        if tool_events:
            with st.expander("查看工具调用"):
                for tool in tool_events:
                    st.code(
                        f"{TOOL_LABELS.get(tool['name'], tool['name'])}："
                        f"{tool['arguments']}"
                    )
                    st.text(tool["result"][:1000])

    st.session_state.agent_messages.append({"role": "assistant", "content": answer})
    st.session_state.chat_messages.append(
        {
            "role": "assistant",
            "content": answer,
            "tools": tool_events,
        }
    )
