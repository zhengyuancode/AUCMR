# L3 PURE 54：来源约束的 Agent 参照

协议：`AUCMR_L3_PURE_COVERAGE_3.0.0`。第一阶段运行入口已实现，两个小试的来源参照已生成并冻结；实际结果以 `coordinator/runtime-status.json` 和运行目录 `progress.json` 为准。覆盖评分尚未运行。构建与复核均由 Agent 承担；真实人工仅作另外记录的质量抽查。不能使用“人工构建并独立复核”或“PURE 官方 gold”的称谓。

## 本版入口

- [PROTOCOL.md](PROTOCOL.md)：第一阶段来源参照、第二阶段七配置覆盖率、固定分母与修订规则。
- [PROMPTS.md](PROMPTS.md)：A 构建、B 先盲读再对照、C 争议复核，以及覆盖率评分角色。
- [cohort.json](cohort.json)：qheadache 8、microcare 16、rlcs 19、peazip 11，共 54 个目标，七配置不变；另 39 个目标明确列为本 PURE 方案范围外。
- [sources/source-lock.json](sources/source-lock.json)：PURE 2.0 官方下载成员与本地冻结来源的版本核验；官方发布证据在 `coordinator/official-downloads.json`。
- [pilot-selection.json](pilot-selection.json)：两个新小试的选择依据与导航页；导航页不限制 Agent 继续读完整来源。
- [stage1-source-only.zip](stage1-source-only.zip)：**只供第一阶段使用**。不包含配置输出、得分、排名、方法映射或阶段二绑定。
- [record-templates.json](record-templates.json)：参照、逐条证据、独立复核和覆盖结果的空白记录结构。
- [execution-plan.json](execution-plan.json)：保留准备阶段的既定模型角色、输入包 hash 和历史未授权状态；本轮明确授权及当前执行状态见 `coordinator/runtime-status.json`。
- [human-audit-plan.json](human-audit-plan.json)：固定种子按项目抽 10% 目标（向上取整），共 7 个目标；尚无人工审查结果。
- [coordinator/verification.json](coordinator/verification.json)：实际离线核验记录。

## 两个新小试

| 目标 ID | 目标 | 来源选择理由 |
|---|---|---|
| UC-0b113737d3ff758c | Player / Open a Saved Game | qheadache；有官方 XML，单项文件操作的说明/输入/处理/输出较集中 |
| UC-47eb5a5e72f296eb | User / Extract an Archive | peazip；无官方 XML，过程、界面与条件/安全要求分散于多个位置 |

只依据来源和复杂度条件选例，没有读取这两个用例的七配置输出正文或评分来挑选。后续七配置比较输入仍为原冻结文件，378 份路径/hash 在私有 `coordinator/stage2-input-lock.json`；小试对应 14 份，暂未进入评分。

## 指标与当前状态

主指标是完整覆盖需求数除以该用例冻结参照需求总数。部分覆盖、未覆盖、矛盾、未决分别记录，技术失败独立记录；部分不默认计半分，未决和失败不缩分母。参照内仍未解决的保留条目单列，并对全部方法统一记未决。参照改版使该目标全部七配置旧评分失效，需要统一重评。

`tools/contract.py` 只实现离线记录检查、输入路径/hash 约束和固定分母计数，`tools/test_contract.py` 使用合成材料检查这些规则。它们不调用模型，不决定真实语义支持，也不自动冻结参照。正式运行器必须把来源 allowlist 接入受限工具，不能把整个仓库暴露给来源构建 Agent。

第一阶段新入口为 `python -m eval.l3_reference`，仅运行两项目标的 A 构建、B 盲读及对照、C 盲读及裁决。来源工具受 allowlist 约束，冻结检查绑定实际输入、读取日志和响应回执。已通过 30 项针对性离线检查。原 `eval.agent_evaluation` 仍是历史 2.0 流程；第二阶段七配置评分尚未适配运行。

本轮记录集中在 `outputs/agent_evaluation/l3-pure-reference-pilot-20261031-v1/`，复用已有共同账本；当前授权仅限两个用例的第一阶段，不包括覆盖评分和人工验收。

实际完成：qheadache 5 条（Agent 确认 5、未决 0），PeaZip 28 条（Agent 确认 27、未决 1）。共 35 次模型调用，含格式纠错和 1 次输出截断；35 笔均与共同账本匹配，用量计价估算合计 7.99128 元，并非平台账单。最终离线溯源检查见运行目录 `final-verification.json`。1280 个保护文件哈希未变，仅既有共同账本因本轮调用更新。未进行七配置评分或人工抽查。

## 清理与保护

用户明确要求删除旧 L3 准备包（含匿名候选包）及被替代的独立文档。实际逐文件删除清单/hash 在 `coordinator/cleanup.json`。不再另存旧包归档，也不把旧候选交给新来源 Agent。

保留原冻结配置、原始生成输出、旧模型响应/运行/费用记录，以及仍被旧运行器 `verify_design()` 引用的设计文件。历史记录中的旧路径可能已经失效，使用此目录作为当前入口。L1、L2、L4 未因本次 L3 改动而变化。

准备材料集中在本目录，运行代码集中在 `eval/l3_reference/`，模型请求、响应、读取与用量集中在上述专用运行目录。共同账本继续记录实际调用；不另建账本。
