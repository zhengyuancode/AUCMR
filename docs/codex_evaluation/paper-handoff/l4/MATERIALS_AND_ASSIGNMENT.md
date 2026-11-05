# 实验材料与任务对应

## 数据冻结范围

本包结果只对应 `config/l4_panel/unified93-v41/catalog.json` 指定的651份生成输出及6份源PDF。原目录为 `outputs/junxi_unified93_deepseek_flash_20261022/`，包括AUCMR、消融与五个对照方法；不以当前同名重生成文件覆盖原材料。

| 来源项目 | 源 PDF（仓库相对路径） | PDF 物理页数 | 用例数 |
| --- | --- | --- | --- |
| jtvauctions | `data/jtvauctions.pdf` | 145 | 20 |
| milstd6016b_sec4 | `data/6016B-sec4-visual-excerpt.pdf` | 146 | 19 |
| qheadache | `data/qheadache.pdf` | 15 | 8 |
| microcare | `data/microcare.pdf` | 33 | 16 |
| rlcs | `data/rlcs.pdf` | 66 | 19 |
| peazip | `data/peazip.pdf` | 34 | 11 |

物理页码从1开始，不等于PDF正文印刷页码。MIL-STD使用仓库中的第4章视觉摘录PDF，不能说评阅了整部标准。来源哈希见 `data/sources.json`；93个具体用例的ID、项目、名称、参与者见 `data/cases.csv`；651份生成输入的原路径和SHA-256见 `data/catalog.json`。本次整理逐一复核这些哈希，全部匹配。

`data/evaluated_inputs.jsonl`逐行保存实际供评价的规范化槽位内容：`task_id`、`method`、原路径/哈希、七槽`claims`及`raw_text`（若有）、`empty_slots`、`no_content`和`explicit_refusal`。断言只保留内容与存在的步骤编号；不把生成器内部证据元数据、方法名称或历史评分作为评分提示。该附件足以重查评分对象文本；它不是原始生成JSON的字节级替代，原文件哈希仍指向仓库原材料。

全空输出如下，已计入PCS及PRC缺失分母：

| 用例 ID | 项目 | 方法 | 用例名称 |
| --- | --- | --- | --- |
| UC-edfa7d4431250d85 | jtvauctions | VDocRAG-Elaborate | Recover Forgotten Account Access |
| UC-5378f77e90aa8b68 | microcare | TextRAG-Elaborate | Maintain Voucher Service Provider Records |
| UC-5e1b75fbd45da207 | microcare | AUCMR | Review a Provider Claim Status |
| UC-5e1b75fbd45da207 | microcare | CREST w/o cross-slot check | Review a Provider Claim Status |
| UC-d9cc94863eb2c8c0 | peazip | VDocRAG-Elaborate | Securely Delete Files and Folders |

## 分配附件的使用

- `data/L4-93cases-42agents.xlsx`：沿用原分配表，两个工作表为“用例分配”和“Agent任务”。UC_1–UC_93为展示编号，原任务ID为稳定键。
- `data/assignment.csv`：279行，含任务、agent、底层模型、项目及A–G方法映射，是评分关联的直接入口。
- `data/agents.csv`：42行，含固定视角、任务数与任务ID列表。
- `data/allocation.json`：完整机器可读分配，含种子与算法标识。
- `data/method_positions.csv`：位置均衡计数。

Excel按原文件逐字节复制，并已核对93行用例对应关系、42行agent任务集合与当前allocation完全一致。其表头保留初始分配版本`L4_PANEL_4.0.0`，仅代表分配表的创建来源，不能当成结果的实现版本；结果逐项版本以provenance为准。不为改表头重写历史分配。

## 尚未并入的重生成材料

| 用例 ID | 方法 | 原正文断言数 | 重生成断言数 | 本次L4是否采用 |
| --- | --- | --- | --- | --- |
| UC-5378f77e90aa8b68 | TextRAG-Elaborate | 0 | 2 | 否 |
| UC-5e1b75fbd45da207 | AUCMR | 0 | 13 | 否 |
| UC-5e1b75fbd45da207 | CREST w/o cross-slot check | 0 | 13 | 否 |
| UC-edfa7d4431250d85 | VDocRAG-Elaborate | 0 | 2 | 否 |
| UC-d9cc94863eb2c8c0 | VDocRAG-Elaborate | 0 | 0 | 否 |

重生成目录为 `outputs/regeneration/five-empty-20261030/`。其中4份输出变为非空，涉及3个用例；PeaZip一份仍为空，其供L4使用的规范化内容与原内容一致。新的文本**没有进入本包评分**。`data/regeneration_scope.json/csv`保存替换路径、哈希、断言数及对应agent评阅ID，供写作总控选择材料口径。

如果论文采用原冻结批次，可以直接使用本包完整结果并披露空输出。如果统一采用重生成后的材料，不能把它们与原L4分数关联；最小一致更新范围是上述3个用例各3个原分配agent，共9次评阅，每次仍同时看7种方法，共刷新63条方法评分，保留另外90个用例。此为后续范围建议，本轮未执行、未授权自动调用。

本批来源访问记录没有成功读取`case_outputs`的记录，也没有解析出的该工具请求，未发现需要因跨用例阅读而扩大这一更新范围的证据。该结论只基于保存的访问历史。

## 可移交材料边界

源PDF在AUCMR仓库`data/`下已受Git跟踪，不重复复制到交接目录。生成正文已提供规范化副本；原生成输出路径和运行证据路径仍是仓库相对路径。运行目录通常被Git忽略，不能假设只推送代码就自动包含它们。本包复制了写作需要的评分、理由、输入内容、分配、提示、用量和审计摘要；如需要完整HTTP请求（含图片base64）与失败历史，应另行移交原运行归档，不把这些巨量材料混入论文叙述。
