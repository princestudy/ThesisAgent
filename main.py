from agent import Agent, build_system_message
from memory import (
    clear_messages,
    initialize_database,
    load_recent_messages,
    save_message,
)


def show_tool_call(name: str, arguments: dict, result: str) -> None:
    preview = result.replace("\n", " ")[:120]
    print(f"[工具调用] {name}({arguments}) -> {preview}")


def main() -> None:
    initialize_database()
    previous_messages = load_recent_messages()
    messages = [build_system_message(), *previous_messages]
    agent = Agent()

    print("第一个 Agent 已经启动。")
    print("输入问题并按回车；输入“退出”结束程序。")
    if previous_messages:
        print(f"已恢复 {len(previous_messages)} 条历史消息。")
    print()

    while True:
        user_input = input("你：").strip()

        if user_input.lower() in {"exit", "quit", "退出"}:
            print("\n程序已结束，再见。")
            break

        if user_input in {"清空记忆", "清除记忆"}:
            clear_messages()
            messages[:] = [build_system_message()]
            print("\n历史记忆已清空。\n")
            continue

        if not user_input:
            print("请输入内容后再按回车。")
            continue

        messages.append({"role": "user", "content": user_input})
        save_message("user", user_input)

        answer = agent.run(messages, on_tool=show_tool_call)

        messages.append({"role": "assistant", "content": answer})
        save_message("assistant", answer)
        print(f"\nAI：{answer}\n")


if __name__ == "__main__":
    main()
