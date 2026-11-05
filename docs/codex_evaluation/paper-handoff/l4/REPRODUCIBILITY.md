# 复核、数据字典与仓库移交

## 路径规则

本包根目录为 `docs/codex_evaluation/paper-handoff/l4/`。文档中的 `data/...` 和 `scripts/...` 除明确标为“仓库”外均相对本包；原始运行路径、目录catalog中的path及provenance的path相对AUCMR仓库根目录。文件内容不依赖服务器home路径，原运行证据中绝对路径在提取副本中规范化为仓库相对路径。原记录哈希始终对原文件计算，不对规范化副本冒充原哈希。

## 最小读取顺序

1. `README.md`和`L4_PAPER_HANDOFF.md`确定实际完成范围。
2. `data/export.json`或`data/ratings.csv`读取评分与理由。
3. `data/assignment.csv`连接task_id、agent_id、model_key、A–G方法映射。
4. `data/catalog.json`和`data/evaluated_inputs.jsonl`定位原生成材料；`data/sources.json`定位源PDF。
5. `data/provenance.json`连接每个evaluation_id的原始完整记录路径、SHA-256及实现版本。
6. 需要审计响应时查看`data/accepted_responses.jsonl`，需要完整历史时按其中相对路径读取原runtime/attempt文件。

## 主要数据文件

| 文件 | 单位与内容 |
| --- | --- |
| export.json | 279项完成摘要、1953条方法记录、651个用例–方法三模型均值 |
| ratings.csv | 1953行，保留各槽整数、PCS平均、PRC、双理由、状态；BOM UTF-8 |
| final_records.jsonl | 279行提取记录，包含模型身份、版本、完整评分、尝试引用；不重复源正文 |
| accepted_responses.jsonl | 279条最终评分响应，含原始JSON文本、实际model、finish_reason、raw_usage与原文件哈希 |
| case_method_means.csv | 651行三模型均值，新增项目与各槽三模型均值便于复算 |
| method_summary.csv | 按方法/指标的n、mean、median、样本sd、min、max |
| common_support_summary.csv | 共同89例的相同描述统计 |
| model_method_summary.csv / project_method_summary.csv | 模型或项目拆分后的方法指标 |
| slot_summary.csv | 各方法七槽统计，空槽0保留 |
| paired_comparison.csv | AUCMR与对照逐用例配对差、胜平负、有效n |
| model_agreement.csv | 模型对相同材料评分的绝对差、方向差与完全一致占比 |
| catalog.json / evaluated_inputs.jsonl | 原始材料绑定651份；实际供评价的规范化槽位内容 |
| cases.csv / sources.json / sources.csv | 93个任务和6个PDF的名称、身份、路径、页数、哈希 |
| allocation.json / assignment.csv / agents.csv | 42个agent与279次评阅任务映射及角色 |
| L4-93cases-42agents.xlsx | 原分配工作簿原字节副本；版本解释见材料文档 |
| method_positions.csv | A–G位置频数 |
| no_content.csv | 5份全空生成输出与其原文件路径 |
| source_access.jsonl | 279个访问审计提取，源正文以长度及哈希代替 |
| source_access_summary.csv / tool_requests.json | 每评阅读取数量、实际可解析工具请求及参数 |
| observed_system_prompts.json | 实际请求使用的3种system文本、SHA-256、关联评阅与版本 |
| observed_rating_contracts.json | 从实际请求提取的评分协议及关联评阅；非46套量表 |
| protocol_snapshot.json / execution_settings.json | 当前量表、提示、工具及执行参数的留存摘录；不是可直接启动实验的配置 |
| request_accounting.jsonl / cost_summary.json | 492个唯一request_id的用量、估算、状态与总计 |
| provenance.json / source_artifacts.json | 原最终记录及输入导出件的路径和哈希 |
| implementation_manifest.json | 当前源码哈希与最终配置的实现哈希，不替代逐运行版本 |
| regeneration_scope.json / regeneration_scope.csv | 5份重生成材料的状态、哈希和未纳入范围 |
| workbook_verification.json | Excel93+42行与分配JSON的一致性结果 |
| package_manifest.json | 本交接文件的大小及SHA-256；不包含自身 |

`null`与CSV空值表示缺失，不是零。`NO_EVALUABLE_CONTENT`的PCS=0是确定性约定，PRC仍缺失。CSV理由文本已采用原导出的防公式转义；逐字模型原文以JSON为准。`raw_usage`随供应商原样保存；统一usage只作为现有计费程序的规范化结果，不猜测缺失缓存或推理字段。

## 无模型调用的复算

在AUCMR仓库根目录执行下列单行命令。只读默认校验不写文件，不加载密钥、不访问网络、不导入评价runtime：

```bash
python -B docs/codex_evaluation/paper-handoff/l4/scripts/analyze.py
```

该脚本只用Python标准库。复算并校验1953个原子评分、七槽平均、651组三模型覆盖、分母、全部派生CSV与摘要。`--write`仅在明确需要重新生成本包派生汇总时使用，不能改变原评分。交接包文件校验：

```bash
python -B docs/codex_evaluation/paper-handoff/l4/scripts/verify_package.py
```

如本机保留完整原运行归档，可额外核查原始文件和当前源码：

```bash
python -B docs/codex_evaluation/paper-handoff/l4/scripts/verify_package.py --with-repository
```

`collect_materials.py`是从原材料重新提取附件的离线整理脚本，需要本仓库及含PyMuPDF/现有评价依赖的aucmr环境；它不发送请求，但会写本包附件，通常无需重跑。独立复算不依赖它。不要为了验证文档启动live入口。

## 完整执行的复现范围

代码位置：分配`eval/l4_agent/assignment.py`；输入`inputs.py`；量表与哈希`protocol.py`；提示/JSON协议`contract.py`；来源与检索`sources.py`、`retrieval.py`；MCP`mcp_tools.py`、`mcp_server.py`、`mcp_client.py`；执行`runner.py`；导出`export.py`。共享请求、用量和费用由`eval/evaluation_runtime.py`及计费组件处理。当前相关测试位于`tests/agent_l4/`。 本次读取的Git HEAD为`ef9d8988c79cc99530dae005218e33d1964dfda8`；工作区含既有未提交修改，HEAD不是完整实验实现快照。`implementation_manifest.json`所列23个当前源码哈希与最终4.1.4配置记录一致。

正式输入catalog：`config/l4_panel/unified93-v41/catalog.json`；最终合并源：`outputs/evaluation_preparation/l4-final-v414-20261030/combined-results/`；共同账本：`outputs/evaluation_campaign/budget.sqlite`。各采用原记录的运行根目录由provenance明确列出，不以单个latest目录替代。当前配置记录了更细的实现指纹，不能仅凭Git HEAD重现含未提交更改的执行版本。

复现历史精确响应应读取保存的响应，不是重新请求供应商。即使保持模型名和参数，在线服务更新及随机性也可能产生不同评分。角色身份隔离不等于独立模型随机样本。

## Git移交建议

本轮不提交或推送。新增文档与数据全部在本包，不移动或删改源记录。`outputs/evaluation_preparation/`、`outputs/agent_l4_panel/`与账本通常被.gitignore排除，因此已把写作所需核心附件放入可提交的文档目录。源PDF已受版本控制。用户统一提交时还应纳入希望发布的对应L4源码/配置变更，避免文档描述与仓库代码不一致；其他人的L0–L3和生成器修改由其负责人决定。

本包未复制API密钥、environment.env、完整环境变量、HTTP鉴权头、账本数据库或原图片base64。完整原失败历史和费用记录在原位置保留，论文正文不需要复述调试过程。没有删除未知费用或把失败变成低分。

## 本次交接验收

2026-11-04完成无模型调用的离线核对：1953条评分与全部派生表复算通过；279份最终响应与保存评分一致；492个请求ID唯一；Excel的93行用例和42行agent映射一致；1744个原文件（含原输入、PDF、结果、请求、接受响应、配置与源码）哈希匹配。包内相对链接均可定位。未修改原记录或账本，未启动评价或重生成。
