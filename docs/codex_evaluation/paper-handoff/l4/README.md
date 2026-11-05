# L4 论文交接包

此目录是L4论文写作交接入口，内容按最终采用设计整理，不含弃用方案的叙事。整理日期：2026-11-04。用户自行提交和推送，本轮不执行Git写操作或模型调用。

**先读 [完整交接总稿](L4_PAPER_HANDOFF.md)。** 原冻结批次已完成279/279次评阅：1938份有效评分、15份无正文记录、最终技术失败0。研究主体为42个模拟从业者agent、3个底层模型，不能称42名真人专家。数据范围为6份PDF、93个用例、7种生成方法。

## 文档导航

| 论文用途 | 文档 |
| --- | --- |
| 方法设计、角色、模型、分配、实际版本 | [设计与协议](DESIGN_AND_PROTOCOL.md) |
| PCS/PRC量表、空内容处理、平均与分母 | [评分与汇总](RUBRICS_AND_AGGREGATION.md) |
| PDF、用例、生成文本、分配表、材料范围 | [材料与分配](MATERIALS_AND_ASSIGNMENT.md) |
| 实际提示、MCP、检索与图片、访问统计 | [Agent与来源访问](AGENT_AND_SOURCE_ACCESS.md) |
| 方法主表、共同支持集、模型/槽位分解、费用 | [结果与分析](RESULTS_AND_ANALYSIS.md) |
| 数据字典、路径、复算、文件校验、提交范围 | [可复现性](REPRODUCIBILITY.md) |
| 可用段落、必要局限、不可宣称的结论 | [写作说明](LIMITATIONS_AND_WRITING_NOTES.md) |

## 直接使用的数据入口

- [原子评分CSV](data/ratings.csv)、[最终结果JSON](data/export.json)、[方法结果表](data/method_summary.csv)。
- [原用例–agent分配Excel](data/L4-93cases-42agents.xlsx)、[实际任务与匿名顺序CSV](data/assignment.csv)。Excel页首分配版本是4.0.0，映射已核对一致，不代表评分实现版本。
- [用例–方法三模型均值](data/case_method_means.csv)、[共同89例结果](data/common_support_summary.csv)。
- [输入目录与哈希](data/catalog.json)、[实际评价正文](data/evaluated_inputs.jsonl)、[PDF索引](data/sources.json)。
- [逐项结果来源](data/provenance.json)、[实际提示](data/observed_system_prompts.json)、[费用摘要](data/cost_summary.json)。

**总控需先确定的唯一材料口径问题：** 本包是原冻结输出的完整结果，尚未采用后来4份非空重生成输出。若统一改用新输出，最小更新范围是3例×3agent=9次评阅（同时重评各例七方法，共63条评分），不是本包已完成事项。详情见[材料范围表](data/regeneration_scope.csv)。

以下命令在AUCMR仓库根目录执行，均为只读离线检查，不需要API密钥：

```bash
python -B docs/codex_evaluation/paper-handoff/l4/scripts/analyze.py
```

```bash
python -B docs/codex_evaluation/paper-handoff/l4/scripts/verify_package.py
```

本包包含写作所需核心副本，原始完整运行归档仍按provenance相对路径保留。没有删除失败记录、改写历史费用或把未知费用当作0。源码与配置的未提交修改由用户统一审阅提交；本包不会自行推送。
