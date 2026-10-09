# 律师工具箱（loo0ng-tools）

不碰案件图的律师实用工具，以 skill 形态构建，Codex 为主、Claude Code 同样可用。碰案件图的工作归律师工作台 loo0ng-skills；一件工具一旦要读写图或成为某个节点出件的固定一步，就搬去那边。设计起点见 `docs/handoff.md`，词汇以 `CONTEXT.md` 为准，决策记在 `docs/adr/`。

## 硬边界

1. **案件材料永不入本仓库**：真实的告知书、裁定书、扫描件与由它们产出的底账只存在于律师的案件目录里，仓库内不存、测试只用合成件；案件敏感信息（当事人、案号、金额、账户）不得写入 issue、commit message、ADR。机械守门是隐私钩子（`scripts/privacy-check.py` 与 `.githooks/`）。

## 约定

1. **skill 布局**：每件 skill 在 `skills/<name>/`，`name` 只用小写字母、数字、连字符；`SKILL.md` 不带 BOM；根 `README.md` 列出每一件并链接到它的 `SKILL.md`。
2. **文本规矩**：改了任何文本文件就跑 `bash scripts/check-text.sh .`（禁破折号 U+2014、非 `.ps1` 不带 BOM、`.ps1` 必须带 BOM）。
3. **说明随包自足**：skill 目录内的说明（含脚本注释与回显）不出现 ADR 号、issue 号、版本号，不指向 skill 目录之外的仓库文件。
4. **脚本只用 Python 标准库**：律师机上只保证有 Python，不保证能 pip 装包。
5. **确定的交给脚本，判断留给模型**：日期推算、合并去重、拼导入文本由脚本做；读文书、认财产、判断到期日来源由模型做。
