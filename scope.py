"""用户作用域。

模型永远不知道"当前用户是谁"，也不会在工具参数里指定用户。
身份由程序侧通过 ToolScope 注入，工具只能看到作用域内的目录，
这样即使模型被越狱，也只能在本用户的资料范围内活动。
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
KNOWLEDGE_ROOT = BASE_DIR / "knowledge"
USER_KNOWLEDGE_ROOT = KNOWLEDGE_ROOT / "users"
SHARED_KNOWLEDGE_DIR = KNOWLEDGE_ROOT / "shared"
DATA_ROOT = BASE_DIR / "data"
USER_DATA_ROOT = DATA_ROOT / "users"
LOCAL_USER_ID = "local-user"
LOCAL_DATA_DIR = DATA_ROOT
SUPPORTED_KNOWLEDGE_SUFFIXES = {".pdf", ".docx", ".txt", ".md", ".csv", ".json"}

# 扫描 knowledge 根目录时跳过这两个保留目录，避免把别人的资料卷进本地单用户模式。
RESERVED_DIRECTORY_NAMES = {"users", "shared"}


@dataclass(frozen=True)
class ToolScope:
    """一次请求能看到哪些目录。

    user_id 只用于标记归属和审计，不会传给模型。
    """

    user_id: str
    data_dir: Path
    knowledge_dirs: tuple[Path, ...]
    include_shared: bool = True
    shared_dir: Path | None = None

    @classmethod
    def local(cls) -> "ToolScope":
        """终端版和离线测试使用的单用户作用域，等价于改造前的行为。"""
        return cls(
            user_id=LOCAL_USER_ID,
            data_dir=LOCAL_DATA_DIR,
            knowledge_dirs=(KNOWLEDGE_ROOT,),
            include_shared=False,
        )

    @classmethod
    def for_user(
        cls,
        user_id: str | int,
        include_shared: bool = True,
    ) -> "ToolScope":
        """网页版用户的作用域：本人的私有目录 + （可选）管理员共享目录。"""
        key = str(user_id)
        directories = [USER_KNOWLEDGE_ROOT / key]
        if include_shared:
            directories.append(SHARED_KNOWLEDGE_DIR)
        return cls(
            user_id=key,
            data_dir=USER_DATA_ROOT / key,
            knowledge_dirs=tuple(directories),
            include_shared=include_shared,
            shared_dir=SHARED_KNOWLEDGE_DIR if include_shared else None,
        )

    def ensure_directories(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        for directory in self.knowledge_dirs:
            directory.mkdir(parents=True, exist_ok=True)


def directory_label(directory: Path, shared_dir: Path | None = None) -> str:
    """给检索结果标注来源：shared 表示管理员共享，private 表示本人资料。"""
    if shared_dir is None:
        return "private"
    try:
        if directory.resolve() == Path(shared_dir).resolve():
            return "shared"
    except OSError:
        pass
    return "private"


def iter_scope_files(scope: ToolScope) -> Iterator[tuple[str, Path]]:
    """按作用域列出可检索的文件，产出 (来源标签, 路径)。

    额外的边界检查：符号链接或挂载点不能把作用域外的文件带进来。
    """
    for directory in scope.knowledge_dirs:
        if not directory.is_dir():
            continue
        label = directory_label(directory, scope.shared_dir)
        root = directory.resolve()
        for path in sorted(directory.rglob("*")):
            if not path.is_file():
                continue
            relative = path.relative_to(directory)
            if relative.parts and relative.parts[0] in RESERVED_DIRECTORY_NAMES:
                continue
            if path.suffix.lower() not in SUPPORTED_KNOWLEDGE_SUFFIXES:
                continue
            try:
                resolved = path.resolve()
            except OSError:
                continue
            if root not in resolved.parents:
                continue
            yield label, path


def upload_directory(scope: ToolScope, *, is_admin: bool) -> Path:
    """上传目标目录：管理员进共享区，普通用户进自己的目录。"""
    if is_admin:
        return scope.shared_dir or SHARED_KNOWLEDGE_DIR
    return scope.knowledge_dirs[0]


def delete_scope_file(path: Path, directory: Path) -> None:
    """删除作用域目录里的一个资料文件。

    只删普通文件：拒绝符号链接、目录、不在目录内的路径和后缀不在白名单里的文件。
    所有检查都基于解析后的真实路径，避免 ``../`` 或链接把删除操作带出作用域。
    """
    original = Path(path)
    if original.is_symlink():
        raise ValueError("不能删除符号链接。")
    try:
        root = Path(directory).resolve()
        target = original.resolve()
    except OSError as error:
        raise ValueError("文件无法访问。") from error
    if target == root or root not in target.parents:
        raise ValueError("只能删除自己资料目录里的文件。")
    if not target.is_file():
        raise ValueError("只能删除文件。")
    if target.suffix.lower() not in SUPPORTED_KNOWLEDGE_SUFFIXES:
        raise ValueError("不支持删除这种类型的文件。")
    try:
        target.unlink()
    except OSError as error:
        raise ValueError(f"删除失败：{error}") from error


MAX_PREVIEW_BYTES = 8 * 1024 * 1024


def read_scope_file(
    path: Path,
    directory: Path,
    *,
    max_bytes: int = MAX_PREVIEW_BYTES,
) -> bytes:
    """读取作用域目录里的一个资料文件，用于预览和下载。

    和 delete_scope_file 一样的边界检查：必须是白名单后缀的普通文件、解析后的
    路径仍然落在给定目录里、且大小不超过 max_bytes。
    """
    original = Path(path)
    if original.is_symlink():
        raise ValueError("不能读取符号链接。")
    try:
        root = Path(directory).resolve()
        target = original.resolve()
    except OSError as error:
        raise ValueError("文件无法访问。") from error
    if target == root or root not in target.parents:
        raise ValueError("只能查看自己资料目录里的文件。")
    if not target.is_file():
        raise ValueError("只能查看文件。")
    if target.suffix.lower() not in SUPPORTED_KNOWLEDGE_SUFFIXES:
        raise ValueError("不支持查看这种类型的文件。")
    try:
        size = target.stat().st_size
        if size > max_bytes:
            raise ValueError(
                f"文件太大（{size / 1024 / 1024:.1f} MB），超过 "
                f"{max_bytes // 1024 // 1024} MB 的查看上限。"
            )
        return target.read_bytes()
    except OSError as error:
        raise ValueError(f"读取失败：{error}") from error
