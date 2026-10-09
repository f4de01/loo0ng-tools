"""定位一个能跑本仓 shell 脚本的 bash，供脚本层测试 import。

为什么单放：跑 shell 脚本的测试都要起一个 bash，各写一份就各自漂：只从 `git` 旁边猜一层
目录的写法，PATH 上的 git 换成 `mingw64/bin/git.exe` 时就猜空，整包用例一起
FileNotFoundError。形状只在这里改。

怎么用（测试文件里三行）：

    sys.path.insert(0, str(REPO / "tests" / "共用"))
    from bash import find_bash
    BASH, BASH_PATH_DIRS = find_bash()
"""
import pathlib
import shutil


def find_bash():
    """返回 (bash 路径, 要前置进 PATH 的目录列表)；找不到时回 (None, [])。

    Windows 上不能直接信 `shutil.which("bash")`：PATH 上往往是 System32 的 WSL 垫片，
    没装发行版时它只回一段 UTF-16 的抱怨。从 git 所在目录往上找 Git for Windows 的
    bash，并把 usr/bin 前置进 PATH（od、head、xargs、sed 都在那儿）。
    """
    git = shutil.which("git")
    if git:
        here = pathlib.Path(git).resolve()
        for root in here.parents:
            for rel in ("bin/bash.exe", "usr/bin/bash.exe", "bin/bash"):
                cand = root / rel
                if cand.exists():
                    extra = [str(root / "usr" / "bin")] if (root / "usr" / "bin").is_dir() else []
                    return str(cand), extra
    found = shutil.which("bash")
    if found and "system32" not in found.lower():
        return found, []
    return None, []
