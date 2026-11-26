# L5 文本源适配（与既有人工稿对照）

## 实验内容

在常规文本需求（PURE 2.0 的 10 个项目、29 对用例稿）上，比较 AUCMR 生成稿（A）与同一目标的既有人工稿（B），在三项指标上的差异：

- **完整性 C**：用例对共同来源需求的行为覆盖程度（越高越好）；
- **来源约束正确性 K**：断言是否被其引用的来源支持（越高越好）；
- **冗余度 R**：不必要重复内容的比例（越低越好）。

每对用例稿由两名评阅人独立评分，分歧由第三名裁决；最终报告独立评分均值与裁决后均值，并给出 A−B 的 95% 置信区间（自助重采样）。

## 关键结果（与论文表 tab:l5-results 一致）

| 指标 | 独立评分 A / B / A−B | 最终裁决 A / B / A−B |
|---|---|---|
| 完整性 C | 0.6172 / 0.5121 / **0.1051** | 0.6122 / 0.5057 / 0.1065 |
| 来源约束正确性 K | 0.9639 / 0.6514 / **0.3125** | 0.9581 / 0.5895 / 0.3686 |
| 冗余度 R | 0.1098 / 0.0360 / **0.0738** | 0.1540 / 0.0699 / 0.0841 |

**评阅一致性**：线性加权 κ **0.7438**、完全一致率 **79.32%**（590 对覆盖判断）；名义尺度 κ **0.7178**（397 对一一等价断言）。报告口径为描述性一致，不等同于准确率或人工金标准。

## 材料文件

| 目录/文件 | 说明 |
|---|---|
| `results/overall.json` | 总体 C/K/R（独立评分 + 裁决，含 95% CI） |
| `results/per_project.json` | 逐项目结果（10 个项目） |
| `results/per_case.json` | 逐用例结果（29 对） |
| `results/agreement.json` | 评阅一致性（κ、完全一致率、逐产物差异） |
| `results/agreement_and_stability.json` | 一致性与重复评分稳定性 |
| `results/completion.json`、`diagnostics.json`、`generation_summary.json` | 完成度、诊断、生成汇总 |
| `tables/results_overall.csv`、`results_per_project.csv`、`results_per_case.csv` | 可读结果表 |
| `tables/projects.csv`、`reference_inventory.csv`、`candidates.csv`、`cases.csv` | 项目/参照/候选/用例清单 |
| `ratings/L5_PURE_ratings_score_primary.jsonl` | 主评逐条评分（1.9MB） |
| `ratings/L5_PURE_ratings_score_secondary.jsonl` | 副评逐条评分（2.3MB） |
| `ratings/L5_PURE_ratings_score_adjudicated.jsonl` | 裁决后逐条评分（2.2MB） |
| `ratings/L5_PURE_references.jsonl` | 参照需求项（389KB） |
| `method/01–10-*.md` | 十份方法说明（设计/材料/输入控制/生成/评价/指标/结果/限制/写作/索引） |
| `method/PROTOCOL.md`、`metric_rules.txt` | 评阅协议与指标规则 |
