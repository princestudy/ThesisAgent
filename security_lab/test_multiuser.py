"""多用户相关的离线测试：账号、会话隔离、资料隔离、审计脱敏。"""

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import auth
import memory
from auth import (
    AuthError,
    authenticate,
    change_password,
    list_users,
    register_user,
    set_disabled,
)
from knowledge_base import search_knowledge_base
from scope import (
    ToolScope,
    delete_scope_file,
    iter_scope_files,
    read_scope_file,
    upload_directory,
)
from security_controls import (
    AuditLogger,
    ToolArgumentError,
    contains_api_key,
    mask_api_keys,
    resolve_data_file,
)


def build_scope(root: Path, user_id: str) -> ToolScope:
    """用临时目录拼出一个和 for_user 等价的作用域。"""
    shared = root / "knowledge" / "shared"
    return ToolScope(
        user_id=user_id,
        data_dir=root / "data" / "users" / user_id,
        knowledge_dirs=(root / "knowledge" / "users" / user_id, shared),
        shared_dir=shared,
    )


class AccountTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.database = Path(self.temporary_directory.name) / "memory.db"
        patcher = patch("memory.DB_PATH", self.database)
        patcher.start()
        self.addCleanup(patcher.stop)
        memory.initialize_database()
        auth.initialize_auth_database()

    def test_password_is_hashed_and_verified(self) -> None:
        register_user("alice", "alicepass123")
        user, message = authenticate("alice", "alicepass123")
        self.assertIsNotNone(user)
        self.assertEqual(message, "登录成功。")

        stored = self.database.read_bytes()
        self.assertNotIn(b"alicepass123", stored)

        failed_user, failed_message = authenticate("alice", "wrongpass123")
        self.assertIsNone(failed_user)
        self.assertEqual(failed_message, "用户名或密码错误。")

    def test_first_registrant_becomes_the_only_admin(self) -> None:
        first = register_user("alice", "alicepass123")
        self.assertTrue(first["is_admin"])
        second = register_user("bob", "bobpass12345")
        self.assertFalse(second["is_admin"])
        third = register_user("carol", "carolpass123")
        self.assertFalse(third["is_admin"])

        admins = [user for user in list_users() if user["is_admin"]]
        self.assertEqual([user["username"] for user in admins], ["alice"])
        self.assertTrue(auth.has_admin())
        self.assertEqual(auth.count_users(), 3)

    def test_database_rejects_a_second_admin(self) -> None:
        register_user("alice", "alicepass123")
        connection = sqlite3.connect(self.database)
        try:
            with self.assertRaises(sqlite3.IntegrityError):
                connection.execute(
                    "INSERT INTO users (username, password_salt, password_hash, is_admin) "
                    "VALUES ('backdoor', 'salt', 'hash', 1)"
                )
        finally:
            connection.close()

    def test_registration_rejects_bad_credentials(self) -> None:
        with self.assertRaises(AuthError):
            register_user("alice", "short")
        with self.assertRaises(AuthError):
            register_user("ab", "alicepass123")
        with self.assertRaises(AuthError):
            register_user("alice", "password123")
        with self.assertRaises(AuthError):
            register_user("alice001", "alice001")
        register_user("alice", "alicepass123")
        with self.assertRaises(AuthError):
            register_user("alice", "otherpass123")
        self.assertEqual(auth.count_users(), 1)

    def test_unknown_user_and_wrong_password_share_message(self) -> None:
        register_user("bob", "bobpass12345")
        _, missing = authenticate("nobody", "whatever123")
        _, wrong = authenticate("bob", "wrongpass123")
        self.assertEqual(missing, wrong)

    def test_account_locks_after_five_failures(self) -> None:
        register_user("carol", "carolpass123")
        for _ in range(auth.MAX_FAILED_ATTEMPTS):
            authenticate("carol", "wrongpass123")
        user, message = authenticate("carol", "carolpass123")
        self.assertIsNone(user)
        self.assertTrue(message.startswith("登录失败次数过多"))

    def test_admin_cannot_be_disabled(self) -> None:
        admin = register_user("alice", "alicepass123")
        with self.assertRaises(AuthError):
            set_disabled(admin["id"], True)

    def test_disabled_account_cannot_login(self) -> None:
        register_user("alice", "alicepass123")
        account = register_user("dave", "davepass1234")
        set_disabled(account["id"], True)
        user, message = authenticate("dave", "davepass1234")
        self.assertIsNone(user)
        self.assertIn("禁用", message)

    def test_change_password_requires_current_password(self) -> None:
        account = register_user("erin", "erinpass1234")
        ok, _ = change_password(account["id"], "wrongpass1234", "newpass12345")
        self.assertFalse(ok)
        ok, _ = change_password(account["id"], "erinpass1234", "newpass12345")
        self.assertTrue(ok)
        self.assertIsNotNone(authenticate("erin", "newpass12345")[0])


class IsolationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.root = Path(self.temporary_directory.name)
        self.scope_a = build_scope(self.root, "1")
        self.scope_b = build_scope(self.root, "2")
        for scope in (self.scope_a, self.scope_b):
            scope.ensure_directories()

    def write(self, scope: ToolScope, name: str, content: str) -> None:
        target = scope.knowledge_dirs[0] / name
        target.write_text(content, encoding="utf-8")

    def test_private_file_is_invisible_to_other_user(self) -> None:
        self.write(self.scope_a, "私有笔记.md", "这是用户 A 的私有研究笔记，内容关于量子纠错码。")
        self.write(self.scope_b, "私有笔记.md", "这是用户 B 的私有研究笔记，内容关于图神经网络。")

        answer_a = search_knowledge_base("量子纠错码", scope=self.scope_a)
        self.assertIn("量子纠错码", answer_a)
        self.assertNotIn("图神经网络", answer_a)

        answer_b = search_knowledge_base("图神经网络", scope=self.scope_b)
        self.assertIn("图神经网络", answer_b)
        self.assertNotIn("量子纠错码", answer_b)

        labels_a = {label for label, _ in iter_scope_files(self.scope_a)}
        self.assertEqual(labels_a, {"private"})

    def test_shared_file_is_visible_to_every_user(self) -> None:
        shared = self.root / "knowledge" / "shared" / "管理员导读.md"
        shared.write_text("管理员共享的课程导读，主题是 Agent 工具调用的风险。", encoding="utf-8")

        for scope in (self.scope_a, self.scope_b):
            answer = search_knowledge_base("Agent 工具调用的风险", scope=scope)
            self.assertIn("shared/管理员导读.md", answer)

    def test_cross_user_traversal_is_rejected(self) -> None:
        other_data = self.root / "data" / "users" / "2"
        other_data.mkdir(parents=True, exist_ok=True)
        (other_data / "secret.txt").write_text("用户 B 的私有数据", encoding="utf-8")

        with self.assertRaises(ToolArgumentError):
            resolve_data_file(self.scope_a.data_dir, "../2/secret.txt")

    def test_upload_directory_routing(self) -> None:
        admin_target = upload_directory(self.scope_a, is_admin=True)
        user_target = upload_directory(self.scope_a, is_admin=False)
        self.assertEqual(admin_target.name, "shared")
        self.assertEqual(user_target, self.scope_a.knowledge_dirs[0])

    def test_symlink_to_outside_scope_is_ignored(self) -> None:
        outside = self.root / "outside.md"
        outside.write_text("作用域外的资料", encoding="utf-8")
        link = self.scope_a.knowledge_dirs[0] / "链接.md"
        try:
            link.symlink_to(outside)
        except (OSError, NotImplementedError):
            self.skipTest("当前系统不允许创建符号链接")
        names = {path.name for _, path in iter_scope_files(self.scope_a)}
        self.assertNotIn("链接.md", names)


class DeleteFileTests(unittest.TestCase):
    """删除资料时同样只能碰到自己作用域里的文件。"""

    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.root = Path(self.temporary_directory.name)
        self.scope_a = build_scope(self.root, "1")
        self.scope_b = build_scope(self.root, "2")
        for scope in (self.scope_a, self.scope_b):
            scope.ensure_directories()
        self.private_dir = self.scope_a.knowledge_dirs[0]
        self.shared_dir = self.root / "knowledge" / "shared"

    def test_delete_own_file(self) -> None:
        target = self.private_dir / "笔记.md"
        target.write_text("待删除", encoding="utf-8")
        delete_scope_file(target, self.private_dir)
        self.assertFalse(target.exists())

    def test_delete_shared_file_for_admin(self) -> None:
        target = self.shared_dir / "共享.md"
        target.write_text("共享内容", encoding="utf-8")
        delete_scope_file(target, self.shared_dir)
        self.assertFalse(target.exists())

    def test_cannot_delete_file_from_other_directory(self) -> None:
        target = self.private_dir / "笔记.md"
        target.write_text("待删除", encoding="utf-8")
        with self.assertRaises(ValueError):
            delete_scope_file(target, self.shared_dir)
        self.assertTrue(target.exists())

    def test_cannot_delete_outside_scope(self) -> None:
        outside = self.root / "outside.md"
        outside.write_text("作用域外", encoding="utf-8")
        with self.assertRaises(ValueError):
            delete_scope_file(outside, self.private_dir)
        self.assertTrue(outside.exists())

    def test_cannot_delete_traversal_path(self) -> None:
        outside = self.root / "outside.md"
        outside.write_text("作用域外", encoding="utf-8")
        with self.assertRaises(ValueError):
            delete_scope_file(self.private_dir / ".." / ".." / "outside.md", self.private_dir)
        self.assertTrue(outside.exists())

    def test_cannot_delete_directory(self) -> None:
        with self.assertRaises(ValueError):
            delete_scope_file(self.private_dir, self.private_dir)
        self.assertTrue(self.private_dir.exists())


class ReadFileTests(unittest.TestCase):
    """查看/下载资料的读取边界：只能读自己作用域里的白名单文件。"""

    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.root = Path(self.temporary_directory.name)
        self.scope_a = build_scope(self.root, "1")
        self.scope_a.ensure_directories()
        self.private_dir = self.scope_a.knowledge_dirs[0]
        self.shared_dir = self.root / "knowledge" / "shared"
        self.shared_dir.mkdir(parents=True, exist_ok=True)

    def test_read_own_file(self) -> None:
        target = self.private_dir / "笔记.md"
        target.write_bytes("# 标题\n正文".encode("utf-8"))
        self.assertEqual(read_scope_file(target, self.private_dir), "# 标题\n正文".encode())

    def test_read_shared_file(self) -> None:
        target = self.shared_dir / "共享.md"
        target.write_text("共享内容", encoding="utf-8")
        self.assertEqual(read_scope_file(target, self.shared_dir), "共享内容".encode())

    def test_cannot_read_from_other_directory(self) -> None:
        target = self.private_dir / "笔记.md"
        target.write_text("私有", encoding="utf-8")
        with self.assertRaises(ValueError):
            read_scope_file(target, self.shared_dir)

    def test_cannot_read_outside_scope(self) -> None:
        outside = self.root / "outside.md"
        outside.write_text("作用域外", encoding="utf-8")
        with self.assertRaises(ValueError):
            read_scope_file(outside, self.private_dir)

    def test_cannot_read_traversal_path(self) -> None:
        outside = self.root / "outside.md"
        outside.write_text("作用域外", encoding="utf-8")
        with self.assertRaises(ValueError):
            read_scope_file(self.private_dir / ".." / ".." / "outside.md", self.private_dir)

    def test_rejects_directory_and_unlisted_suffix(self) -> None:
        with self.assertRaises(ValueError):
            read_scope_file(self.private_dir, self.private_dir)
        secret = self.private_dir / "secret.exe"
        secret.write_bytes(b"MZ")
        with self.assertRaises(ValueError):
            read_scope_file(secret, self.private_dir)

    def test_rejects_oversized_file(self) -> None:
        big = self.private_dir / "big.txt"
        big.write_text("x" * 32, encoding="utf-8")
        with self.assertRaises(ValueError):
            read_scope_file(big, self.private_dir, max_bytes=16)

class AuditRedactionTests(unittest.TestCase):
    def test_api_key_detection_and_masking(self) -> None:
        key = "sk-1234567890abcdefghij"
        self.assertTrue(contains_api_key(f"我的 key 是 {key}"))
        self.assertFalse(contains_api_key("sk- 只是普通文字"))
        masked = mask_api_keys(f"回答里不能出现 {key}")
        self.assertNotIn(key, masked)
        self.assertIn("已隐藏", masked)

    def test_api_key_never_reaches_audit_log(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            log_path = Path(temporary_directory) / "audit.jsonl"
            logger = AuditLogger(log_path)
            logger.record(
                "tool_call",
                arguments={"api_key": "sk-1234567890abcdefghij"},
                result="使用密钥 sk-1234567890abcdefghij 调用成功",
            )
            text = log_path.read_text(encoding="utf-8")
            self.assertIn("[REDACTED_SECRET]", text)
            self.assertNotIn("sk-1234567890abcdefghij", text)
            # 脱敏后每一行仍然是合法 JSON，审计日志本身不能因为脱敏而损坏。
            for line in text.splitlines():
                json.loads(line)


class ConversationTests(unittest.TestCase):
    """多对话：默认会话、上限、按用户隔离、删除时连带清理消息。"""

    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.database = Path(self.temporary_directory.name) / "memory.db"
        patcher = patch("memory.DB_PATH", self.database)
        patcher.start()
        self.addCleanup(patcher.stop)
        memory.initialize_database()

    def test_default_conversation_is_created_once(self) -> None:
        first = memory.ensure_default_conversation("1")
        second = memory.ensure_default_conversation("1")
        self.assertEqual(first, second)
        self.assertEqual(len(memory.list_conversations("1")), 1)

    def test_conversation_limit(self) -> None:
        memory.ensure_default_conversation("1")
        while memory.count_conversations("1") < memory.MAX_CONVERSATIONS:
            memory.create_conversation("1")
        with self.assertRaises(memory.ConversationLimitError):
            memory.create_conversation("1")
        self.assertEqual(
            memory.count_conversations("1"), memory.MAX_CONVERSATIONS
        )

    def test_conversations_are_isolated_between_users(self) -> None:
        memory.create_conversation("1", "alice 的对话")
        memory.create_conversation("2", "bob 的对话")
        self.assertEqual(
            [item["title"] for item in memory.list_conversations("1")],
            ["alice 的对话"],
        )
        self.assertEqual(
            [item["title"] for item in memory.list_conversations("2")],
            ["bob 的对话"],
        )
        # 拿别人的会话 id 读不到内容
        other = memory.list_conversations("2")[0]["id"]
        self.assertIsNone(memory.get_conversation(other, "1"))

    def test_delete_conversation_removes_its_messages(self) -> None:
        conversation_id = memory.ensure_default_conversation("1")
        memory.save_message("user", "你好", "1", conversation_id)
        memory.touch_conversation(conversation_id, "1")
        memory.delete_conversation(conversation_id, "1")
        self.assertIsNone(memory.get_conversation(conversation_id, "1"))
        self.assertEqual(memory.load_recent_messages(10, "1", conversation_id), [])

    def test_legacy_web_session_is_adopted(self) -> None:
        memory.save_message("user", "旧版历史", "1", memory.LEGACY_CONVERSATION_ID)
        adopted = memory.ensure_default_conversation("1")
        self.assertEqual(adopted, memory.LEGACY_CONVERSATION_ID)
        self.assertEqual(
            memory.list_conversations("1")[0]["title"],
            memory.LEGACY_CONVERSATION_TITLE,
        )
        self.assertEqual(
            len(memory.load_recent_messages(10, "1", adopted)), 1
        )


class AgentKeyTests(unittest.TestCase):
    def test_agent_requires_explicit_key(self) -> None:
        """项目文件里不再有 Key，所以不传 key 必须直接报错。"""
        import agent

        with patch.dict("os.environ", {"OPENAI_API_KEY": ""}):
            with self.assertRaises(RuntimeError) as context:
                agent.Agent()
        self.assertIn("API Key", str(context.exception))

    def test_agent_accepts_explicit_key(self) -> None:
        import agent

        instance = agent.Agent(api_key="sk-test-key-1234567890")
        self.assertIsNotNone(instance.client)


if __name__ == "__main__":
    unittest.main()
