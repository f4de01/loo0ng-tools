"""临时 git 仓库夹具：在临时目录里起一个只有一次提交的干净仓库，供扫全仓的脚本测试 import。

为什么共用：`check-text.sh` 与 `privacy-check.py --all` 的名单都是「已跟踪 + 未被忽略的
未跟踪」，两边的测试都得在一个真 git 仓库里摆三种文件（跟踪的、未跟踪的、被 .gitignore
忽略的）。这段夹具原先只长在 privacy-check 那一份测试里，第二份照抄过去当场就开始分叉
（抄漏了 `core.quotepath=false`），所以提到这里，形状只在这里改。

环境隔离：`HOME`、`GIT_CONFIG_NOSYSTEM`、`GIT_CONFIG_GLOBAL` 全指向临时目录，
开发者自己的 git 配置（钩子、autocrlf、excludesFile）碰不到测试。

怎么用（测试目录里两行）：

    sys.path.insert(0, str(REPO / "tests" / "共用"))
    from 临时仓库 import GitRepoMixin

    class 某某(GitRepoMixin, unittest.TestCase):
        def test_x(self):
            self.write("a.md", "内容\\n")
            self.git("add", "a.md")
"""
import os
import pathlib
import subprocess
import tempfile


class GitRepoMixin:
    """setUp 起仓库、tearDown 删掉。仓库里只有一个 README.md，已提交。

    子类拿 `self.root`（仓库根）与 `self.env`（隔离过的环境）去跑被测脚本。
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self._tmp.name) / "repo"
        self.root.mkdir()
        self.env = dict(os.environ)
        self.env["HOME"] = self._tmp.name
        self.env["GIT_CONFIG_NOSYSTEM"] = "1"
        self.env["GIT_CONFIG_GLOBAL"] = os.path.join(self._tmp.name, "no-global-gitconfig")
        self.git("init", "-q", "-b", "main")
        self.git("config", "user.name", "t")
        self.git("config", "user.email", "t@example.invalid")
        self.git("config", "core.autocrlf", "false")
        self.write("README.md", "起点\n")
        self.git("add", "README.md")
        self.git("commit", "-q", "-m", "init")

    def tearDown(self):
        self._tmp.cleanup()

    def git(self, *args):
        return subprocess.run(
            ["git", "-c", "core.quotepath=false", *args],
            cwd=self.root, env=self.env, check=True, capture_output=True,
        )

    def write(self, rel, text):
        p = self.root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8", newline="\n")
        return p
