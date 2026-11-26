# AUCMR 人工评价系统

本目录是论文《AUCMR：基于多模态检索增强生成的自动化用例精化》的**人工评价系统材料包**，覆盖从"源文档 + 生成用例"到"逐条评阅记录 + 汇总指标"的完整链路，供复核与复现参考。

## 一、目录结构（按评价流水线顺序）

```
human-evaluation/
├── 0_sources/          # 输入①：6 份源需求文档 PDF
├── 1_artifacts/        # 输入②：651 个生成用例（93 目标 × 7 配置）
├── 2_protocols/        # 规则：各层评阅判据/评分标准/协议快照
├── 3_scripts/          # 实现：指标计算与判定脚本（现有代码，非新写）
└── 4_review-records/   # 输出：各层人工评阅的逐条记录与汇总
```

| 目录 | 内容 |
|---|---|
| `0_sources/` | JTV Auctions、MIL-STD-6016B、QHeadache、Microcare、RLCS、PeaZip 六份 PDF；其中 QHeadache/Microcare/RLCS/PeaZip 为 PURE 2.0 公开数据（DOI: https://doi.org/10.5281/zenodo.7118517），JTV 与 MIL-STD 为自备文档。页面图片未随附，可从此处 PDF 渲染。 |
| `1_artifacts/` | `aucmr_full/`（AUCMR）、`baselines/`（TextRAG/VDocRAG/ViDoRAG/ReAct/LVLM-Direct 五种基线）、`crest_without_dra/`（关闭 DRA），共 651 个 JSON；每个含八槽用例 `slots`、`evidence_summary`、`citation_audit`（每条断言的 `evidence_refs`）、`quality_flags` 等。 |
| `2_protocols/` | L1 协议快照、L2 的 R1–R7 判据、L3 覆盖判据、L4 评分细则与聚合规则。 |
| `3_scripts/` | `calculate.py`（NLI-G/覆盖/消融指标计算）、`l3_judge.py`（覆盖判定）、`l2_judge.py`（R1–R7 判定）、`l4_analyze.py`（PCS/PRC 统计）、`recompute.py`（指标复算校验）。均为现有实现，本包只收录、不改写。 |
| `4_review-records/` | L1/L2/组件消融/L3/L4/机制对照/L5 七组评阅记录（逐条 + 汇总 + README）。 |

## 二、评价流程（如何执行，简述）

1. **准备输入**：从 `0_sources/` 取六份源 PDF；从 `1_artifacts/` 取七种配置在同一目标上的生成用例。
2. **按协议评阅**：依 `2_protocols/` 的判据，对每条断言（L1）、每条跨槽规则（L2）、每个参照需求项（L3）、每份用例（L4）给出人工判断；L3 的参照清单构建与覆盖判定均不接触生成结果。
3. **用脚本聚合**：以 `3_scripts/` 的脚本把逐条判断汇总为 NLI-G、覆盖宏/微平均、残余违规数、PCS/PRC 均值等指标。
4. **对照验收**：把重跑得到的指标与 `4_review-records/` 中已归档的评阅记录（及各层 README 的关键数值）逐项核对。

> 本包只提供材料、协议与现有脚本，**不包含可一键运行的端到端评价程序**；上述四步为流程说明，具体评阅由专家/评阅人执行。

## 三、各层评价一览

| 层 | 评价对象 | 指标 | 结果位置 |
|---|---|---|---|
| L0 | 检索 | Recall@5、下游问答准确率 | 论文 4.2 节（本包未单列） |
| L1 | 候选断言 | 证据锚定性 NLI-G | `4_review-records/L1_证据锚定性/` |
| L2 | 最终用例 | R1–R7 残余违规 | `4_review-records/L2_内部一致性/` |
| L3 | 最终用例 | 需求覆盖（宏/微平均） | `4_review-records/L3_需求覆盖/` |
| L4 | 用例质量 | PCS（0–3）、PRC（1–5） | `4_review-records/L4_人工评价/` |
| L5 | 与人工稿对照 | 完整性 C、来源约束正确性 K、冗余度 R | `4_review-records/L5_文本源适配/` |
| 组件消融 | 6 配置 | 后六槽支持率、主流程齐备且衔接、分支规则 | `4_review-records/组件消融/` |
| 机制对照 | 完整 / 固定邻页 / 简化 CREST | L1/L2/L3 + 消融指标 | `4_review-records/机制对照/` |

## 四、口径与注意事项

1. **本目录是新版 93 目标评价系统**，与仓库根目录的旧版 `eval/`、`outputs/`（2026-08-16 备份，仅 jtvauctions/milstd 两个文档）**不是同一批数据**，请勿混用。
2. 评价口径统一为**人工评阅**：L1/L2/L3/L5 由小专家团承担（两名独立 + 分歧裁决，或构建/复核/裁决），L4 由 42 名评阅人员承担（每目标 3 人，部分交叉）。
3. L2、组件消融、机制对照三处的违规数来自不同目标子集与批次，**不可直接比绝对值**，详见各子目录 README。
4. 本批为课程 mock 数据集，用于论文方法验证演示，不构成对真实产品性能的承诺。
