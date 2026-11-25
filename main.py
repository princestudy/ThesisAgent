import getpass
import os

from dotenv import load_dotenv

from agent import Agent, build_system_message
from memory import (
    clear_messages,
    initialize_database,
    load_recent_messages,
    save_message,
)
from scope import ToolScope


def show_tool_call(name: str, arguments: dict, result: str) -> None:
    preview = result.replace("\n", " ")[:120]
    print(f"[工具调用] {name}({arguments}) -> {preview}")


def ask_api_key() -> str:
    """终端版不再从 .env 读 Key：每次启动时向使用者要一次。"""
    key = os.getenv("THESISAGENT_TERMINAL_KEY", "").strip()
    if key:
        return key
    while True:
        try:
            value = getpass.getpass("请输入你的 DeepSeek API Key（输入时不显示）：").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n已取消。")
            raise SystemExit(1) from None
        if value:
            return value
        print("Key 不能为空，请重新输入。")


def main() -> None:
    initialize_database()
    load_dotenv()
    scope = ToolScope.local()
    scope.ensure_directories()

    api_key = ask_api_key()
    try:
        agent = Agent(api_key=api_key)
    except RuntimeError as error:
        print(f"启动失败：{error}")
        raise SystemExit(1) from None

    previous_messages = load_recent_messages()
    messages = [build_system_message(scope), *previous_messages]

    print("论文知识库助手（终端版）已启动。")
    print("输入问题并按回车；输入“退出”结束程序。")
    print("Key 只保存在本次进程内存里，退出即失效。")
    if previous_messages:
        print(f"已恢复 {len(previous_messages)} 条历史消息。")
    print()

    while True:
        try:
            user_input = input("你：").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n程序已结束，再见。")
            break

        if user_input.lower() in {"exit", "quit", "退出"}:
            print("\n程序已结束，再见。")
            break

        if user_input in {"清空记忆", "清除记忆"}:
            clear_messages()
            messages[:] = [build_system_message(scope)]
            print("\n历史记忆已清空。\n")
            continue

        if not user_input:
            print("请输入内容后再按回车。")
            continue

        messages.append({"role": "user", "content": user_input})
        save_message("user", user_input)

        answer = agent.run(messages, on_tool=show_tool_call, scope=scope)

        messages.append({"role": "assistant", "content": answer})
        save_message("assistant", answer)
        print(f"\nAI：{answer}\n")


if __name__ == "__main__":
    main()
