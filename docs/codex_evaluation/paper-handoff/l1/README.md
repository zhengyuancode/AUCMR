# L1 论文写作交接

本目录交付原冻结批次的最终 L1 结果：**93个目标、6种适用方法、558份材料、10,067条审计断言**。其中516份评价结果直接保留，42份在原生成材料上补齐评价。另行重生成的五份输出不纳入本包。整理日期：2026-11-04。

## 阅读顺序

1. [L1_PAPER_HANDOFF.md](L1_PAPER_HANDOFF.md)：正式口径、方法设计、全部指标定义、结果、实验材料、复现与论文表述边界。
2. [方法汇总](results/method-summary.csv)：论文主表的原始计数和比例。
3. [槽位汇总](results/slot-summary.csv)、[项目汇总](results/project-summary.csv)：结果分析及附录。
4. [558项明细](results/case-method-results.csv)、[93项目标名单](results/target-list.csv)：匹配用例、方法和来源。
5. [material-index.json](material-index.json)：逐份材料的仓库相对路径、原文件哈希、纳入来源和打包数据哈希。

## 数据与复算

| 文件 | 内容 |
| --- | --- |
| `data/final-records.jsonl` | 558份最终观察：完整审计断言、保存的逐条模型判断与理由、最终七槽内容、输入与评价出处。每行一份材料 |
| `data/cohort.json` | 93个正式目标、源PDF、纳入依据及选择限制 |
| `data/protocol.json` | 实际系统提示、请求模板、补齐阶段采用的格式说明、模型端点与聚合口径 |
| `data/provenance.json` | 原运行清单中的实现指纹与模型配置、354条补齐响应的usage及未定价状态 |
| `scripts/summarize_l1.py` | 仅用Python标准库离线复算，不导入评价器、不读密钥、不调用API |
| `results/integrity-checks.json` | 断言计数、材料覆盖及导出表核验结果，不能当作语义正确性证明 |

在 **AUCMR仓库根目录**执行以下单行命令即可验证包内数据和五张结果表，不需要原服务器或API密钥，也不写文件：

```bash
python -B docs/codex_evaluation/paper-handoff/l1/scripts/summarize_l1.py --verify-only
```

复算并更新本目录中的CSV与算术核验文件：

```bash
python -B docs/codex_evaluation/paper-handoff/l1/scripts/summarize_l1.py
```

只有在本机原始归档齐备时，才使用下面的只读原文件核验：

```bash
python -B docs/codex_evaluation/paper-handoff/l1/scripts/summarize_l1.py --verify-only --check-source
```

`--from-source`供重新打包人员使用，会重建本交接目录的数据快照，不适用于日常论文写作。它仅读取既定三个记录目录，不选择最好结果，不启动补评。

## 提交范围

本目录数据可随仓库提交，足以离线复算表格。原PDF、图片、完整API原始响应和服务器运行归档没有复制进来；其路径与哈希见索引及主文档。复算不等于重新裁判，复查模型证据仍需原归档。`outputs/evaluation_preparation/`通常被Git忽略，不能以为提交本目录就同时上传了那些文件。

本次不提交、不推送、不修改论文，也不改变任何评价层或历史结果。仓库推送由用户统一执行。
