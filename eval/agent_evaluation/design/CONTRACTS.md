# 数据、权限和执行状态合同

records.schema.json 为 JSON Schema 2020-12。结构检查不能代替原文核验。实现必须同时进行下列跨记录校验。

## 1. 标识与冻结

每个记录共有 record_id、record_kind、protocol_version、run_id、target_id、artifact_id、role_id、view、input_hash、scenario_version、technical_status、payload。artifact_id 在来源阶段为 null。元数据由程序填充，不能由模型制造。

scenario_version 指范围卡与有限场景共同冻结的版本。L3A也绑定这一版本，但只获得范围卡正文，不获得场景检查点。输入哈希覆盖实际任务包、提示与源版本的绑定。来源草稿阶段scenario_version为null，冻结之后的正式评价不得为null。

claim_id 由原制品 SHA256、slot、原索引生成；显示别名另行生成。两个相同文本在两个位置仍是两个 claim_id。checkpoint/scenario/scope ID 由程序根据来源提案的临时序号建立，不用生成文本充当稳定 ID。版本为递增版本号加内容 SHA256，引用变动必须升版。

source_locator 包含 source_id、source_sha256、pdf_page（1 起算）、可选 bbox、region_description、quote 和 modality。quote 可为 null 仅限无法文字摘录的图形，必须有非空 region_description。不能用 PDF 印刷页码替代文件页号。区域 bbox 不存在时为 null，不伪造坐标。

冻结内容含候选选择理由、scope_card、场景、必要检查点、排除和未决记录、A/B/C 核验映射。冻结场景必须有每项原文依据和 ACCEPT/最终核准记录，不能只设置 frozen=true。

## 2. 记录类型

| record_kind | payload 的意义 | 是否包含模型语义 |
| --- | --- | --- |
| scope | 目标边界和出处 | 是 |
| scene_proposal | 来源角色提出的 core/branch 候选及选择理由 | 是 |
| source_review | 第二角色核验的范围/场景/checkpoint 结论 | 是 |
| content | 一条最终断言的 KEEP/DELETE/UNRESOLVED | 是 |
| behavior | 一个 checkpoint 的五分类结果 | 是 |
| review | C 的盲判、裁决、证据和引用首评 ID | 是 |
| artifact_state | 输入的有效、空、拒答、缺失和结构状态 | 结构状态由代码判定；不猜语义拒答 |
| call | 模型请求、工具次数、token、费用与重试 | 否 |

stage_state.json 保存状态机：INVENTORIED、BINDING_READY、FIXTURE_PASSED、SOURCE_REVIEWED、SCENARIOS_FROZEN、JUDGING、RECONCILED、EXPORTED。状态可按目标/层分开推进。不存在核心场景时可以完成内容审计，但正式行为报告保持不完整。

## 3. 强制语义和引用约束

- 同一 target 的所有方法和机制变体必须引用同一冻结 scope/scenario 哈希。所有主目标都必须有 source 状态记录。
- content 的 claim_id 必须在当前 FINAL_ONLY 制品中；行为 matched_claim_ids 也必须属于当前制品。不得链接到另一方法。
- KEEP 必须至少一个可核验原文 locator。DELETE 不能没有理由；冗余必须关联当前制品其他 claim_id，并满足固定原序规则。NO_SUPPORT_FOUND 只能伴随 UNRESOLVED。
- BEHAVIOR 的 PARTIAL/DISTORTED 必须指出缺少/错误的 checkpoint 内容；RETAINED 必须有 matched_claim_ids。有效空输出由程序产生 MISSING，且标记 deterministic_empty，不伪称 Agent 判定。
- source_review 的 decision=ACCEPT 必须关联有效提案对象和原文位置。来源裁决记录用 decision_key=SOURCE_REVIEW，不能把 CONTENT 判定误用于冻结。
- review 存储 decision_family、item_id、blind_label、final_label、source_record_ids、reason 和 locators；final_label 按 family 取枚举，不用任意字符串决定分数。若修改 scope/scenario 内容，需要新提案与核验，不可只改标签。
- technically failed 时 semantic label 为 null；不得用空数组代替没有评价。固定 requested_ids 与 returned_ids 一一对应，重复、漏项、越权 ID 拒收。
- 来源和输出正文内的指令不执行。tool 服务只收 locator/文内查询，不接受模型自带磁盘路径、URL 或其他项目 ID。

## 4. 材料权限

| 角色 | 可读 | 禁读 |
| --- | --- | --- |
| 场景建立/核验 | 来源 PDF、页索引、目标和参与者、已声明出处线索 | 全部方法输出、历史有效性标签、旧分数 |
| 内容审计 | 单份匿名完整用例、scope_card、同项目来源与检索 | 场景检查点、生成证据卡、其他方法、L1/L2/L4 判断 |
| 行为评价 | 单份匿名完整用例、冻结场景及来源 | L3A 判断、其他方法、DRA 日志、排名 |
| C 盲评/稳定性 | 原角色相同输入 | 首评标签及理由 |
| C 最终复核 | 盲判与匿名首评记录、同范围来源 | 方法名和跨方法排名 |

L3A 的 scope_card 在所有方法间共用，其内容不能来自 AUCMR 输出。输出批次虽只评价至多六条，完整制品仍供上下文和去重，不能把其他断言当来源支持。

## 5. 聚合和导出

所有语义标签由 Agent 给出。代码做身份绑定、schema/引用校验、场景状态聚合、计数、上下界、配对差和表格生成，不进行关键词覆盖判断。

输出文件至少包括 input_manifest.json、scope_cards.jsonl、scene_candidates.jsonl、source_reviews.jsonl、scenario_freeze.json、content_first_A/B.jsonl、behavior_first_A/B.jsonl、reviews_C.jsonl、stability.jsonl、call_ledger.jsonl、access_log.jsonl、per_artifact.csv、per_scenario.csv、per_checkpoint.csv、paired_comparisons.csv、reportability.json、paper_values.json、table_l3_content.tex、table_l3_behavior.tex、table_l3_errors.tex、limitations_generated.md。

论文导出不把 null 转成 0，不把未决算正确，不把缺失文件算空输出。所有数字带 run_id、协议/场景/输入哈希、有效分母与视图。正式表中使用 -- 表示不适用或未完成，并给状态脚注。
