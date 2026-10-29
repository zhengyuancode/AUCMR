# L4 分槽请求与证据映射修复

本目录集中保存本轮根因、验收日志和交付说明。原始运行材料仍在 `outputs/agent_l4/`，配置与预注册计划在 `outputs/evaluation_preparation/l4-staged-20261026/`；没有改动 L0、L3、共享 runtime 或旧记录。

## 根因与对应证据

`old-response-replay.json` 逐项记录了 14 条历史记录、最后请求/响应路径及 SHA256、finish_reason、usage、原校验错误、要求的 claim_id 与实际漏项。离线重放保留 1 项通过、13 项失败；没有给旧结果换成新结果。

| 根因类别 | 实际证据与校验规则 | 本次处理 |
|---|---|---|
| 提示/校验不一致 | 较早请求的模板缺 EMPTY_SLOT；后续虽补枚举，仍要求模型重复输出程序已知空槽。JTV-01 LVLM/ReAct 返回状态或分数违反 `evaluate_response` 的 EMPTY_SLOT 条件 | 新模式只请求非空槽，空槽仍由既定 fixed PCS=0 规则处理；模型不能覆盖空槽 |
| 输入呈现不足 | 旧 artifact 的 slots 已含完整稳定 claim_id，但缺独立、明确的当前待核对 ID 清单；七槽与 PRC 混在一个响应。S16-02 AUCMR 8 条 main_flow 只返回3条，14条 exception_flow 只返回1条 | 每个非空槽一次独立请求，完整用例始终提供；required_claim_ids 与 JSON schema 按真实输入生成，明确缺失、重复、非法 ID；不删除断言，不平均子分数 |
| 定位字段手工拼接 | 原工具结果有实际 source_id/page/access_id，但模型需重复抄写定位及精确引文。S16-02 CREST 返回 UNACCESSED_PAGE，JTV-01 ViDoRAG 返回 QUOTE_NOT_ON_ACCESSED_PAGE | 只从实际 source_page 访问建立固定 500 字符连续原文片段 ID；模型显式选择 ID，程序映射回同一访问和原文。没有模糊匹配、跨页找引用或自动选证据 |
| 模型漏字段/未决冲突 | JTV-01 VDocRAG 的 UNRESOLVED check 缺 source_refs；LVLM 用 UNRESOLVED 同时给分；多个 PRC FACTUAL 用 slot 或 ARTIFACT/range 而不是单 claim_id | 新 wire schema、提示和校验共用 contract.py：所有 check 都必须 evidence 数组；非未决须非空，未决须 null 和非空原因字符串；FACTUAL 必须单个输入 ID及证据；实际分值/判断仍由模型给出 |
| 反馈不具体 | 旧反馈常只有 INVALID_CHECK_FIELDS、MISSING_OR_DUPLICATE_ASSERTION_CHECK；未指出缺哪个字段、哪条断言 | JSON Schema 错误路径及消息，附 missing/extra/duplicate_claim_ids；只有一次格式纠正，不增加纠正轮数 |
| 非法 JSON，而非已证实截断 | S16-02 的 LVLM/TextRAG/VDocRAG/ViDoRAG/ReAct 最后响应均 finish_reason=stop；completion_tokens 为 3661–5847，低于8192，reasoning用量实际可见；原 JSON 有多余对象/括号或分隔错误 | 降低每次输出范围，不猜补内层 JSON。保留严格整段围栏兼容。8192 和 thinking enabled 维持旧 L4 实测设置，不凭猜测放大上限/换模式 |
| 超时 | JTV-01 TextRAG 在120秒等待时无完整响应，账本费用未知 | 沿用已修复的 L4 显式600秒；不改共享 HTTP 实现，不重发旧超时请求 |
| 恢复/保存 | 旧纠正失败保留上次分值触发 NO_RESPONSE_CANNOT_HAVE_PRC；上一轮已修复 | 本次进一步分组件封存响应/来源/身份/实现哈希；已完成组件直接验证复用，未封存的付费请求由原 runtime 缓存恢复，不挑分、不换账本 |
| 真正的未决 | 某些响应声称来源未支持所有前后条件；原规则并不要求必须高分或必须可评分，但有些响应同时给确定分数 | 能以合法 UNRESOLVED/null/具体理由完成。模型没找到证据不等于整份原文确实没有；本轮不替模型裁定，也不把未决变正确 |

## 固定执行约定

请求组织版本 `L4_SLOT_EVIDENCE_2.0.0`，原 PCS 0–3、PRC 1–5、7槽分母、空槽及未决计分不变。每个非空槽根据全部断言核查给出一个整槽分数；PRC 是独立完整用例请求，不接收 PCS 分数或由程序推导。每次仍提供完整用例，不丢跨槽语境。

每阶段最多4次请求、最多1次纠正；所有方法相同。原全局工具限额 search_max/pages_max/tools_per_round 等保持不变。源页实际读取和失败仍留痕。组件输出包括模型原文、完整 transcript、usage/费用回执和 source_access。最终 raw_response 明确标为程序组装，不冒充供应商原始响应；原文可由 components 逐一追溯并复算。程序集成仅处理输入身份、固定空槽与模型主动选中的证据映射，不能补判断、分数或理由。

代表性验证在调用前固定3项：JTV-01/LVLM-Direct（空槽）、S16-02/CREST w/o cross-slot check（长槽与错误引用）、S16-02/VDocRAG-Elaborate（JSON及空槽）。共20个非空PCS/PRC阶段，理论请求次数上限80；不是费用上界，也不会为了用满名额追加调用。首次最终技术失败即停；未决合规是成功。只有三项全部技术通过，才另开新目录同版本运行全部14项，任何旧评分都不复制。

费用策略仍为 PROVIDER_MANAGED，共用原账本；历史500/20元不表述为本地硬封顶。所有新调用仍需明确 live 授权；日志仅输出密钥变量是否存在。

## 离线验证

- `old-response-replay.json`：14条原始结果重放；13条失败仍失败。
- `staged-tests.log`：最初6项新组织测试通过。
- `l4-regression.log`：62项中1项因沙箱禁止创建空socket失败；没有删除该测试。
- `l4-regression-rerun.log`：补充空槽/低分/授权测试后，64项L4回归原样在允许socket构造的环境通过，测试自身仍禁止网络；无模型调用。
- `shared-regression.log`：32项共享runtime、围栏解析、平台管理费用回归通过。
- `final-targeted-tests.log`：最后增加统计元数据后，51项分槽/流水线/runtime测试通过。
- 测试覆盖：完整上下文、独立PRC、确定性空槽、低分不抬高、未决不补零、精确证据ID、漏项/重复项、非法JSON不猜补、有限纠正、未授权不调用、组件恢复及付费未落盘请求不重发、组装结果篡改拒绝。

最终真实验证、小试状态、费用、保护核验和下一步命令见本目录 `DELIVERY.md`（执行结束后生成）。历史未提交差异另存 `worktree-before.diff`，本轮只增量修改 L4 相关文件。
