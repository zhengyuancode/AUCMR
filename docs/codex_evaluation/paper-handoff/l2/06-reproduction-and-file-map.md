# 离线复算、数据字典与文件地图

## 最小可携带交付

上传整个 `docs/codex_evaluation/paper-handoff/l2/` 即可独立核查计数与复算表格。默认输出原冻结输入的论文主表（646项完成、89个共同用例）；649/91仅为明确选择的补充视图。包内数据不依赖原服务器绝对路径、不需要密钥、不调用模型、不生成新评价。

从AUCMR仓库根目录执行（每条命令均为单行）：

```sh
python -B docs/codex_evaluation/paper-handoff/l2/tools/verify_and_summarize.py
```

输出主实验复算表到终端：

```sh
python -B docs/codex_evaluation/paper-handoff/l2/tools/verify_and_summarize.py --json
```

单独输出历史649/91补充表（禁止用作主表或与原输入L1/L4分数直接配对）：

```sh
python -B docs/codex_evaluation/paper-handoff/l2/tools/verify_and_summarize.py --scope supplemental --json
```

`--scope both --json`按 `main`、`supplemental` 两个键分别输出。所有模式均核验主／补充隔离、五项无内容的null状态、输入哈希、响应链接及两套统计表，脚本始终只读。

如果原始材料也全部在仓库中，可额外核对原始文件SHA-256：

```sh
python -B docs/codex_evaluation/paper-handoff/l2/tools/verify_and_summarize.py --source-root .
```

最后一条需要 `data/source_files.json` 中所有引用文件都存在；缺失或变化时会明确报错。未上传完整原始存档不影响第一条仅依赖包内数据的复算。脚本使用Python标准库，推荐Python3.10及以上；本次核验环境为Conda `aucmr`、Python3.10.20。脚本不写文件。

本包不提供一键重新请求模型的命令。统计复现与重新采集模型响应是两件事：后者会产生费用和随机变化，并且需保留既定输入绑定。

## 包内文件与字段

| 文件 | 主要字段／用途 |
|---|---|
| `manifest.json` | 包内文件路径、字节大小及SHA-256；不对自己递归计算哈希 |
| `data/selection.json` | 主／补充政策、原冻结绑定和已修复结果清单的路径及哈希 |
| `data/summary.json` | 主实验93/651/646/89规模、模型、历史费用口径、封装时HEAD与版本分布 |
| `data/protocol.json` | 英文规则全文、依赖槽位、分组、模型与请求参数、原配置哈希；文档快照，不用于直接启动评价 |
| `data/cases.json` | 93个目标身份，分别标明是否进入主实验89例及补充91例共同集合 |
| `data/inputs.json` | 651项原冻结输入；`assertions`保留slot、original_index、text、可用step_number；input_hash指向原生成JSON字节 |
| `data/final_records.json` | 原输入的646项完成及5项无内容；保留评价器修复、规则计数、违规条目和来源 |
| `data/accepted_responses.json` | 主结果1231份采用响应的实际载荷、原正文、接受正文、usage与费用估计；不含密钥或授权头 |
| `data/prompt_examples.json` | 当前最终提示构造的具体例子；历史已发送提示以accepted_responses中的request_payload为准 |
| `data/tables.json` | 89例主表、原输入覆盖表及646项全部可用辅助表；明确标为PAPER_PRIMARY |
| `data/exclusions.json` | 五项主实验生成覆盖缺失，违规counts保持null |
| `data/input_substitutions.json` | 三项补充输入的原／新路径与哈希；明确禁止用于主表及原输入L1/L4直接配对 |
| `data/supplemental/inputs.json`、`final_records.json`、`accepted_responses.json` | 三份新输入、三份评价及六份响应；原判断不变 |
| `data/supplemental/tables.json`、`summary.json` | 历史649项完成、91共同例视图，明确标为SUPPLEMENTAL且paper_primary=false |
| `data/supplemental/examples.json` | 原交接中来自重生成的实例，保留为补充实例 |
| `data/source_documents.json` | 六个来源项目的PDF、页图目录与目标数量 |
| `data/examples.json` | 说明性违规记录；不作为人工金标准 |
| `data/source_files.json` | 源文件路径、字节数、SHA-256和用途；均相对仓库根目录 |

`artifact_id`连接输入、记录、请求和实例；`target_id`（输入里名为`task_id`）连接同一目标的七种方法。主／补充视图中三项artifact_id相同但input_hash不同，不能只按artifact_id拼接跨层结果。历史649视图由651项主记录加三项补充替换重建；主表始终只读取原输入记录。`request_id`连接分组与响应。源记录的签名哈希和源文件的字节哈希分别保存。

主表只读取 `status=COMPLETE` 的共同目标。`counts=null`表示无可评价／失败，不能按0参与均值。`rule_resolution.state=NOT_APPLICABLE_EMPTY_INPUT`表示特定规则不适用；`SATISFIED_LITERAL_ACTOR_PRESENCE`表示R4有输入中的字面充分证据；`MODEL_CHECK`表示曾交模型判断，不是违规标志。

## 原仓库关键路径

以下均从AUCMR仓库根目录解析，不使用本机绝对路径。

| 用途 | 相对路径 |
|---|---|
| 论文主结果选择入口，含已完成评价器修复 | `outputs/agent_l2_rules/final-repair-20261030/result-manifest.json` |
| 原冻结输入主结果汇总表 | `outputs/agent_l2_rules/final-repair-20261030/tables.json` |
| 原输入完成状态与修复审查 | `outputs/agent_l2_rules/final-repair-20261030/final.json`、`classification-review.json` |
| 补充结果选择与649/91汇总 | `outputs/agent_l2_rules/regeneration-supplement-20261030/result-manifest.json`、`tables.json` |
| 补充完成与保护核验 | `outputs/agent_l2_rules/regeneration-supplement-20261030/final.json`、`verification.json` |
| 初始输入绑定 | `outputs/evaluation_preparation/l2-rules-v2-20261029/artifacts.json` |
| 补充输入绑定 | `outputs/evaluation_preparation/l2-closeout-20261030/artifacts.json` |
| 最终L2配置 | `outputs/evaluation_preparation/l2-rules-v5-20261029/config.json` |
| 最终规则执行模块 | `eval/l2_rules_batch.py` |
| 原规则描述与输入格式 | `eval/l2_judge.py` |
| 规则名映射 | `eval/compute_metrics.py` 的 `CONSISTENCY_RULES` |
| HTTP、用量和缓存 | `eval/evaluation_runtime.py` |
| 外层JSON围栏兼容 | `eval/evaluation_response_format.py` |
| 费用估计与共同账本实现 | `eval/evaluation_cost_policy.py`、`eval/agent_evaluation/budget.py` |
| 共同账本 | `outputs/evaluation_campaign/budget.sqlite`（持续更新，不以包制作时的全库哈希冻结） |
| 首批生成材料 | `outputs/junxi_unified93_deepseek_flash_20261022/` |
| 限定范围的补充生成材料 | `outputs/regeneration/five-empty-20261030/` |
| 目标批次定义 | `config/evaluation_batches/unified93-v1/batch.json` |
| 相关离线测试 | `tests/test_l2_rules_batch.py` |

原配置的 `source_manifests` 与实际记录覆盖多个目录；纳入哪些记录分别以主／补充选择清单为准，不按“目录最新”“文件名版本最大”或“挑选较低违规值”合并。恢复原输入口径保留已完成评价器修复，不撤销纯格式处理或已保存的有效补评。历史来源目录的存在不意味着必须阅读全部调试日志。

## 版本与上传注意

封装时HEAD见 `data/summary.json`。相关评价文件包含未提交内容，因此HEAD本身不足以标识本次实现；应同时引用 `data/source_files.json` 中的文件哈希及包清单。若论文需要完整执行级复现，应同步保存对应代码和源材料，而不只记录一个提交号。

`outputs/evaluation_preparation/` 中部分文件被Git忽略，普通推送未必携带。为避免写作者必须访问这些准备目录，本包已包含最终参数、输入、请求与结果投影；原路径和哈希用于完整追溯。原始共享费用账本、密钥文件、无关层运行记录不应为了写作而复制进本目录。

本次只修订交接包的选择口径、输入投影和统计展示；原输入、原结果、失败响应、费用记录及L1/L4不变。提交时应携带README中的统一主实验输入说明和主／补充数据，不将649/91补充表重新标成主表。提交、推送及论文正文替换由维护者统一执行。
