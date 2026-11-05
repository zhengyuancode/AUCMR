# L2 论文写作交接

整理日期：2026-11-04；主／补充口径修订：2026-11-05。实验结果归属：2026-10-29—2026-10-30 已完成的 R1–R7 跨槽一致性评价及补充输入评价。

> **写作与提交口径：统一以原冻结输出作为论文主实验输入，三份重生成结果仅作为补充实验保留。** L1、L4 保持现状；L2 主结果保留已完成的评价器修复，采用 **646 项完成、0 项技术失败、5 项无可评价内容，七方法共同有效用例 N=89**。五项无内容计入生成覆盖率缺失，违规数为 `null`，不补零、不继续生成。三份重生成及其 L2 评价不参与主表，也不与原输入的 L1、L4 分数直接配对。原“649 项、91 例”表仅为补充口径。

## 本包的结论与使用口径

- 计划：93 个目标用例 × 7 种方法＝651 项“用例—方法”评价。
- 主结果：646 项完成、0 项技术失败、5 项无可评价内容、0 项未处理。
- 主比较：七种方法均完成的 89 个共同用例，共 623 项。所有方法的主表分母都是 89。
- 主输入的全部可用辅助表：646 项；AUCMR、消融配置、TextRAG 各 92 项，VDocRAG 91 项，其余各 93 项。不同分母不能替代共同用例主表。
- 五项无内容涉及四个目标，完整保留生成覆盖缺失状态。它们不产生有意义的违规数，不进入违规均值。
- 补充结果单列于 [07-supplemental-results.md](07-supplemental-results.md)：三份新输入及六份 L2 响应，以及历史合并视图的 649 项完成、91 个共同用例。
- **原冻结输入不变不等于评价器从未修复。** 已接受的评价器修复和有效历史响应仍保留；不同记录的执行版本分别可追溯，不能称作统一同版重跑。

这里的“完成”表示评价协议执行完成，不表示用例没有错误。“不适用”不表示规则已通过。所有均值的单位是“违规条目/用例”，不是通过率或正确率。

## 阅读顺序与论文用途

| 文件 | 支撑的论文内容 |
|---|---|
| [01-evaluation-design.md](01-evaluation-design.md) | 指标定义、R1–R7、判定与统计单位 |
| [02-implementation-and-execution.md](02-implementation-and-execution.md) | 模型、提示词、参数、结构化判定与执行控制 |
| [03-experimental-materials.md](03-experimental-materials.md) | 数据集构成、方法、输入选择和覆盖率 |
| [04-results-and-paper-tables.md](04-results-and-paper-tables.md) | 可直接引用的整数总数、均值、覆盖率及适用性表 |
| [05-findings-and-limitations.md](05-findings-and-limitations.md) | 结果讨论、实例、有效性边界 |
| [06-reproduction-and-file-map.md](06-reproduction-and-file-map.md) | 文件路径、数据字典、离线复算与版本追溯 |
| [07-supplemental-results.md](07-supplemental-results.md) | 三份重生成评价及原 649/91 补充视图；不用于主表 |
| [data/selection.json](data/selection.json) | 主／补充输入政策、原选择清单与哈希 |
| [data/summary.json](data/summary.json) | 主结果规模、历史调用与费用口径 |
| [data/final_records.json](data/final_records.json) | 原冻结输入对应的 651 项记录：646 完成、5 无内容 |
| [data/inputs.json](data/inputs.json) | 651 项原冻结输入的槽位正文、索引与哈希 |
| [data/accepted_responses.json](data/accepted_responses.json) | 主结果采用的 1231 份实际请求、响应与用量 |

## 写作时必须对齐的事实

本批次实际裁判为 **GLM-4.6V（API 标识 `glm-4.6v`）**。主结果 1231 份、补充输入 6 份选用响应的实际请求均未显式设置 `temperature` 或 `seed`，不能写成“温度 0 的确定性解码”。不可将源码中的旧注释或论文旧稿的模型名、14 用例样本量、旧数值带入本包结果。

L2 检查生成文本的内部一致性，不读取源 PDF 来核查事实，不输出 PCS/PRC，也不等同于真人专家评价。论文应称“模型辅助的结构化规则判定”。

本次修订仅重新选择已保存记录并离线导出表格。未修改论文、生成材料、评价器、L1/L4 交付包或费用账本；未删除历史材料，没有追加模型调用。

所有 `source_*`、`input_path`、`evidence_path` 等外部路径均相对于 **AUCMR 仓库根目录**。本目录内 Markdown 链接相对当前文档。整目录上传即可离线复算论文统计；完整源 PDF 和原始服务响应的存档位置另见文件清单。
