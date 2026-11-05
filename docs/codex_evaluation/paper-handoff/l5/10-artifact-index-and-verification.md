# 文件索引、迁移与离线验证

## 路径约定

本目录为 `docs/codex_evaluation/paper-handoff/l5/`。JSON/CSV中的实验路径全部是 **AUCMR仓库根目录相对路径**，不是相对当前Markdown；本目录内文档链接是普通文件相对链接。原始日志可能仍有执行机路径字符串，交接派生文件已转为仓库相对路径，不改写原日志。

包内 `materials/inputs`、`generated`、`controls`、`references`、`ratings` 可独立阅读。`controls/*.raw.txt`是原始提取，`*.txt`是原展示稿；二者语义处理区别见03。参照与评分JSON是从最终有效记录中提取的写作快照，附原记录路径和哈希；它们不是重新调用模型得到的结果。`materials/record_index.json`逐份连接174份判断与其实际来源。

## 权威实验文件

| 内容 | 仓库相对路径 |
|---|---|
| 当前结果指针 | `experiments/l5/pure_human_20261101/agent_evaluation_v1/CURRENT_RESULT.json` |
| 官方PDF和来源版本 | `experiments/l5/pure_human_20261101/evidence/pure/`；`experiments/l5/pure_human_20261101/evidence/source_manifest.json` |
| 全部候选与边界 | `experiments/l5/pure_human_20261101/screening/candidates.json`；`experiments/l5/pure_human_20261101/screening/source_boundaries.json` |
| 冻结生成输入 | `experiments/l5/pure_human_20261101/inputs/`；`experiments/l5/pure_human_20261101/protocol/INPUT_AND_CONTROL_FREEZE.json` |
| 正式A、结构化输出和日志 | `experiments/l5/pure_human_20261101/runs/formal-v2-credential-recovery/<case>/` |
| 既有人工B | `experiments/l5/pure_human_20261101/private_controls/<case>.raw.txt` 与 `experiments/l5/pure_human_20261101/private_controls/<case>.txt` |
| 生成实现/提示 | `experiments/l5/pure_human_20261101/runtime/`；`experiments/l5/pure_human_20261101/prompts/`；`experiments/l5/pure_human_20261101/config/run.json` |
| 原始匿名展示材料 | `experiments/l5/pure_human_20261101/review/stage_R_requirements/` 与 `experiments/l5/pure_human_20261101/review/stage_S_products/` |
| 最终来源参照 | `experiments/l5/pure_human_20261101/agent_evaluation_v1/scope_adjudication_v1/continuation_v2/merged/references/<task>.json` |
| 最终首判、裁决与对齐 | `experiments/l5/pure_human_20261101/agent_evaluation_v1/scope_adjudication_v1/continuation_v2/merged/records/` |
| 最终结果JSON | `experiments/l5/pure_human_20261101/agent_evaluation_v1/scope_adjudication_v1/continuation_v2/merged/results/` |
| 最终范围解释与冻结 | `experiments/l5/pure_human_20261101/agent_evaluation_v1/scope_adjudication_v1/continuation_v2/reference_changes.json`；`experiments/l5/pure_human_20261101/agent_evaluation_v1/scope_adjudication_v1/continuation_v2/reference_freeze.json` |
| 最终分量来源 | `experiments/l5/pure_human_20261101/agent_evaluation_v1/scope_adjudication_v1/continuation_v2/composition_provenance.json` |
| 核心计算 | `experiments/l5/pure_human_20261101/agent_evaluation_v1/revision_v5/metrics.py` |
| 最终分量合并/聚合 | `experiments/l5/pure_human_20261101/agent_evaluation_v1/scope_adjudication_v1/summarize.py` |

逐文件路径和哈希以 `artifact_manifest.json` 为准。目录名中的版本或技术命名只作文件定位，不需写入论文叙述。

## 模型调用与分量来源

`materials/call_provenance_index.json`给被最终采用记录引用的请求、实际响应内容及响应元数据路径；`tables/model_and_role_config.csv`列角色/分量的实际参数与模型标识。请求指纹约束原任务，技术格式修复可能增加消息；实际采用的语义判断以最终记录为准。

对T003/T009的四份产物，C来自最终冻结范围后的覆盖重评；K/R沿用源和语义单元未变时的原首判/裁决。`agent_for_C`和`agent_for_K_R`分别标识来源，不能把合并记录说成全部维度都由一次新调用产生。其余目标同样保留各自真实响应标识。原始评分记录仍在仓库，通过索引查验，不必在论文讲述中间流程。

## 离线复核

从AUCMR仓库根目录执行：

```bash
python -B docs/codex_evaluation/paper-handoff/l5/scripts/verify_handoff.py
```

该脚本不联网、不读取凭据、不启动生成/评分。它核对来源清单及包内内容哈希，验证29目标/10项目配对和输入/B隔离记录，基于174份逐项判断复算C/K/R、项目聚合、配对差和10000次bootstrap，核对一致性与稳定性，并检查路径迁移、原文引句和表格数值。JSON验证记录位于 `verification.json`。

如需从同一冻结结果重新导出派生文件，按以下顺序执行；最后的参数明确表示为主动重建的本包更新哈希封存：

```bash
python -B docs/codex_evaluation/paper-handoff/l5/scripts/build_data.py
python -B docs/codex_evaluation/paper-handoff/l5/scripts/write_docs.py
python -B docs/codex_evaluation/paper-handoff/l5/scripts/verify_handoff.py --refresh-manifest
```

日常核验不加`--refresh-manifest`，以便发现包内文件变化。构建脚本要求当前结果仍指向本交接选定版本；不自动换成其他结果。重建会更新本交接包，不能改写原实验记录。评分快照的`final_boundary_decisions`保留已采用的边界说明；复算脚本按最终裁决记录区分适用义务的明确评分和被排除的来源未决，不依据旧字段名称自行令整例缺失。

## 上传仓库和共享范围

`artifact_manifest.json`索引原实验依赖及其SHA-256；`package_manifest.json`索引本交接文件。不能只上传README而遗漏CSV/JSON/材料和所引用的原实验文件。原PDF和模型日志未复制进本包；若仓库不存放这些大文件，应保留带哈希的公开来源与归档位置，并在数据可用性声明中明确。

本包包含解盲的A/B编码和逐方法结果，适合论文作者/组织者，不适合作为盲评发放包。未复制API密钥；不要将个人环境文件、账户凭据或私有组织者映射另混入公开附件。原需求文档的再分发按官方来源许可处理，不据本包推断拥有额外授权。

## 交付边界

交接证明最终实验记录可定位、统计可复算，并提供论文所需方法与限制；不证明模型语义判断无误、不替代真人复核、不表示整篇论文其他层级已同步，也不包含对论文正文或PDF排版的修改。
