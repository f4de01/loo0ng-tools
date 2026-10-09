# 律师工具箱（loo0ng-tools）

不碰案件图的律师实用工具，以 skill 形态交付，Codex 为主、Claude Code 同样可用。读写案件图的工作归律师工作台 [loo0ng-skills](https://github.com/f4de01/loo0ng-skills)。

## 工具

| skill | 做什么 |
| --- | --- |
| [preservation-reminder](skills/preservation-reminder/SKILL.md) | 续保提醒：读保全告知书、裁定书等 PDF，认出每项被冻结、查封、扣押的财产，记进同目录的保全底账 `保全期限.md`，按申请截止日（届满日前七日）倒推提醒时点，给出一段导入文本，到 iPhone 上由快捷指令建进提醒事项 |

## 安装

走 skills.sh，安装源须是公开仓库：

```bash
npx skills@latest add f4de01/loo0ng-tools -a codex
```

用 Claude Code 就把 `-a codex` 换成 `-a claude-code`。机器上要有 Python 3.9 以上，不用另装任何包。

**续保提醒还要在 iPhone 上装一次快捷指令「续保导入」**：安装链接尚未发布，搭建步骤见 [docs/快捷指令搭建.md](docs/快捷指令搭建.md)。每次怎么导入见 [导入手机.md](skills/preservation-reminder/导入手机.md)。

## 开发

设计起点见 [docs/handoff.md](docs/handoff.md)，词汇以 [CONTEXT.md](CONTEXT.md) 为准，决策记在 [docs/adr/](docs/adr/)，给 agent 的规矩在 [AGENTS.md](AGENTS.md)。

克隆后先装隐私钩子（扫暂存改动与 commit message，拦案号、案件目录路径、手机号、身份证号、统一社会信用代码）：

```bash
git config core.hooksPath .githooks
python scripts/privacy-check.py --all     # 全仓基线
```

改了文本文件、提交之前：

```bash
bash scripts/check-text.sh .
python -m unittest discover -s tests/preservation-reminder -p "test_*.py"
python -m unittest discover -s tests/skills -p "test_*.py"
python -m unittest discover -s tests/privacy-check -p "test_*.py"
python -m unittest discover -s tests/check-text -p "test_*.py"
```

测试只用合成件。`tests/preservation-reminder/fixtures/` 里的两份假告知书（文字版、扫描版）由同目录的 `make_fixtures.py` 生成，那个脚本要 PyMuPDF，只在开发机上跑；测试本身只用标准库。
