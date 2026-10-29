# L4 修复交付与主控决策

本轮状态：**实现改进及离线回归完成，代表性真实验证 2/3 技术通过，未达到全部3项通过的预注册门槛。统一14项未启动（0/14），不得标记已稳定验收。** 已停止真实调用，没有增加纠正次数、换样本、换模型、改账本或新开目录重试失败。

## 已完成的改动

- 组织版本 `L4_SLOT_EVIDENCE_2.0.0`：非空槽分别核查全部断言并给一个整槽PCS；PRC单独看完整用例，不由PCS推导。每次保留完整用例上下文；所有方法同一规则。
- 空槽由程序按原规则处理；模型仅填写非空槽判断。原0–3/1–5量表、七槽分母和未决null处理未改。
- contract.py 提供 JSON Schema、字段/枚举、引用规则和机器校验。反馈有准确字段路径及缺失/多余/重复claim_id，不再只给笼统错误码。
- 实际已读页的连续原文片段产生稳定ID，只有模型主动选择的ID被映射回原access_id/source_id/页码/原文。不得拿搜索结果ID充当证据，不跨页模糊寻找引文。
- 每阶段最多4次请求、1次格式纠正；完成的组件不可重新评分，保存模型原文、来源链和实现身份。组件组合明确标识为程序组装，不冒充原始模型响应。
- 已付费但组件尚未保存的中断由原runtime缓存恢复；未修改共享runtime。导出新增请求组织及技术有效统计，仍区分未决和技术失败。

实际本轮修改/新增文件：

- `eval/l4_agent/contract.py`
- `eval/l4_agent/staged.py`
- `eval/l4_agent/client.py`
- `eval/l4_agent/engine.py`
- `eval/l4_agent/inputs.py`
- `eval/l4_agent/protocol.py`
- `eval/l4_agent/records.py`
- `eval/l4_agent/runner.py`
- `eval/l4_agent/export.py`
- `tests/agent_l4/test_staged.py`

`worktree-before.diff` 是本轮开始时已经存在的未提交差异；`working-diff-after.diff` 含累计未提交差异，不能将其中所有历史修改都算成本轮。两个新增模块与测试为未跟踪文件，实际代码及 implementation-freeze.json 是完整版本证据。没有修改 L0、L3、共享 runtime、冻结批次、原始用例或旧响应。

## 离线验收

| 检查 | 结果 |
|---|---|
| 旧14项原始响应重放 | 原1项通过、13项仍失败；不改历史结果 |
| 新组件原型测试 | 6项通过 |
| L4全组 | 增补测试后64项通过；第一次62项中1项因沙箱不允许创建socket失败，失败日志保留，未改断言绕过 |
| 共享runtime/外层围栏/平台费用策略 | 32项通过 |
| 最后统计字段修改后的必要回归 | 51项通过 |
| 三项真实记录离线再校验 | PASS，仅表示记录与组件、原始响应及映射一致，不表示三项均为有效评价 |
| diff空白检查 | PASS |
| 受保护原文件 | 21,112项哈希不变；共享runtime哈希不变 |

证据文件：`old-response-replay.json`、`l4-regression*.log`、`shared-regression.log`、`final-targeted-tests.log`、`representative-offline-validation.json`、`protected-after.json`。

## 有界真实验证：按失败机制预选，不按分数选样本

预注册计划：[l4-staged-20261026/representative-plan.json](/home/dzy/code/TOSEM2026/AUCMR/outputs/evaluation_preparation/l4-staged-20261026/representative-plan.json)。三个样本与完整14项使用相同配置；没有复制旧版本成功分数。未实际使用完整14项入口。

| 样本 | 完成阶段 | 结果 | 具体观察 |
|---|---:|---|---|
| JTV-01／LVLM-Direct | 5/5 | 技术通过，整体UNRESOLVED；PCS均值null，独立PRC=4 | 3个空槽没有向模型请求；12条主流程完整；未决/状态冲突一次反馈后合规 |
| S16-02／CREST w/o cross-slot check | 8/8 | 技术通过，整体UNRESOLVED；PCS均值null，独立PRC=5 | 14条异常流程全部检查，使用已访问片段ID，无漏项/未知页引用 |
| S16-02／VDocRAG-Elaborate | 1/7已完成；前置条件阶段失败 | TECHNICAL_FAILURE，整体PCS/PRC空值 | 首次用了搜索结果ID；纠正时重复两条断言，按上限停止 |

PRC与PCS独立，因此存在PCS未决而PRC仍给分的情况；这些是模型判断，不是已验证的研究结论。没有用PCS未决强行推导PRC，也未把PRC高分当作接口/语义通过证据。

实际19次调用，全部 `finish_reason=stop`，completion_tokens范围374–4828，全部原文可解析JSON。相较历史非法JSON/大量漏项已有改善，但样本小，不能推断全量可靠性。已尝试组件 15 个，其中 14 个合规、1 个失败。未完成的后续阶段没有补记录冒充成功。

## 最后一个失败的精确原因

组件：[representative-slot-v2-20261026/components/L4-8b09d1acc9e2694722c7c807/preconditions.json](/home/dzy/code/TOSEM2026/AUCMR/outputs/agent_l4/representative-slot-v2-20261026/components/L4-8b09d1acc9e2694722c7c807/preconditions.json)。其中保留两次完整请求/响应与反馈。

1. 输入明确要求 `preconditions:0`、`:1`、`:2` 三条检查。第一次确实返回三条，状态UNRESOLVED、score=null也正确。
2. 但`:1`和`:2`把 `L4-ACCESS-3`（source_search访问）填进 evidence_id。实际可用片段是 `L4-ACCESS-4:T*`、`L4-ACCESS-5:T*`，来自实际读取的PDF第23/25页。搜索命中提到第113页，但它未实际 source_page 读取，不能由程序补造第113页引用。
3. 精确反馈指出 `/checks/1/evidence/1/evidence_id` 和 `/checks/2/evidence/1/evidence_id` 非法。模型纠正后返回五条，ID顺序变为0、1、2、1、2，超过三条并重复两个ID。新增检查还把“搜索第113页的结论”挂在第25页片段上，因此不能简单删掉重复条目来放行。
4. 按一次纠正上限保留FAILED；没有自动去重、删除难断言、拿搜索片段代替页证据或调整分数。

这次剩余问题不应笼统写成“模型能力不足”：**搜索结果和可引用页证据在上下文中仍容易混淆，且列表形式的check要求模型自行维持唯一性。** Schema校验已经准确拦截，但防止模型反复犯错的输入组织还未充分验证。

下一次最小候选改动（本轮未实施、未付费验证）：

- 将搜索结果显式呈现为“仅用于发现页面、不可引用”，附准确的 source_page 下一步调用信息；可引用片段独立列出。不能把搜索hit自动变成已读页。
- 用输入决定的固定ID键结构或更小的固定断言批次减少重复身份输出；统一应用于全部方法，保留全部判断。即使两条重复判断看起来相同，也不得静默挑一条。
- 对这次保存的双响应先做离线契约测试；若主控决定再做真实验证，应使用新版本、新预注册验证目录，并保留本次FAIL。不要通过编辑本次报告、补一条成功记录或反复新目录试分来达到PASS。

## 费用和未知项

本轮19条请求均已在原共同账本留存，已知费用估算 **0.444988元**；新增未知费用0。全部为usage按价估算，不是平台实际账单。

共同账本累计SETTLED 131条，已知估算4.605662元；历史UNPRICED 4条、HELD_REJECTED 2条仍保留。后两条预留合计1元，actual仍NULL；没有删除或清零。PROVIDER_MANAGED策略未变，历史500/20元不是当前本地硬封顶。去重明细见 `ledger-and-validation.json`。

## 固定配置、恢复位置与下一步命令

配置：[l4-staged-20261026/config.json](/home/dzy/code/TOSEM2026/AUCMR/outputs/evaluation_preparation/l4-staged-20261026/config.json)。配置哈希 `0f84f3da4be23acee031bc9510f4798ece8b93740e21cac8b5d18e45f08a9814`，GLM-4.6V、max_tokens8192、thinking enabled、HTTP等待600秒；同一配置及实现用于本次所有代表项。参数选择依据是历史实际usage与正常stop，不是猜测截断。

实现冻结：[l4-staged-20261026/implementation-freeze.json](/home/dzy/code/TOSEM2026/AUCMR/outputs/evaluation_preparation/l4-staged-20261026/implementation-freeze.json)。当前代表性结果：[representative-slot-v2-20261026/representative-report.json](/home/dzy/code/TOSEM2026/AUCMR/outputs/agent_l4/representative-slot-v2-20261026/representative-report.json)，状态FAIL。`records/`与`components/`是恢复/审计位置；重新执行原脚本只会复用并保留这个失败，不会合法产生一个新的PASS。

**统一14项：NOT_RUN，0新增调用，0/14新结果。** 没有创建一个混入旧成功记录的“新批次”。本轮有界验证已经结束，下一步由主控决定剩余输入/契约修复及重新授权验证范围。

可直接执行的下一步离线回归命令（单行、无模型调用）：

`cd /home/dzy/code/TOSEM2026/AUCMR && /home/dzy/miniconda3/bin/conda run --no-capture-output -n aucmr python -B -m unittest tests.agent_l4.test_staged -v`

下述单行检查仅报告当前14项启动门槛，FAIL时退出非零，不调用模型：

`cd /home/dzy/code/TOSEM2026/AUCMR && /home/dzy/miniconda3/bin/conda run --no-capture-output -n aucmr python -B -c 'import json,sys; p=json.load(open("outputs/agent_l4/representative-slot-v2-20261026/representative-report.json")); print("representative validation:",p["status"]); sys.exit(0 if p["status"]=="PASS" else 2)'`

当前不提供绕过该FAIL直接启动14项的命令。没有修改论文、调用L0/L3或扩大到93项。

## 2026-10-26：2.2.0 两阶段纠错的真实验收

本次仅修改 L4 局部协议与测试：`contract.py`、`corrections.py`、`staged.py`、`client.py`、版本入口 `inputs.py`/`engine.py`，以及 `tests/agent_l4/test_corrections.py`。共享 runtime、L0、L3 未修改。

程序从被拒绝检查项的明确页码与其引用的已登记搜索结果交集确定读页；来源有歧义、没有明确页码或页码不在搜索结果中均不猜测。实际读取仍走原 SourceTools 的权限、页数、工具次数与审计。然后进入补丁专用阶段，禁止工具执行，只允许原有两个 claim_id 的替换及模型自己给出的整槽 assessment。不补判断、不补分、不修改量表。

70 项 L4 离线测试通过，无跳过。初次运行唯一错误是沙箱禁止创建 socket；在沙箱外原样执行全部 70 项通过，测试自身禁止网络。新增检查覆盖明确页码读取、来源歧义拒绝、补丁禁止工具执行、固定 ID 与恢复复用。验收驱动另经离线验证：无显式授权不创建 runtime；前阶段失败时代表性验证不创建 runtime。

真实请求只执行 1 次，目录 `outputs/agent_l4/correction-acceptance-v22-20261026`。本次实际读到第 113 页并登记 L4-ACCESS-6，模型只返回 preconditions:1、preconditions:2 两项替换，引用真实的 L4-ACCESS-6:T0；未再追加断言，也未假造工具返回。JSON 可解析、finish_reason=stop。原始 usage：prompt_tokens=9260，completion_tokens=1003，其中 reasoning_tokens=762；cached_tokens=298；total_tokens=10263。

**最终验收仍失败**：保留的 preconditions:0 是 UNRESOLVED，模型却给出 score=3、status=SCORED、unresolved=[]。合并后的严格规则正确拒绝该结果，未把未决改成正确。`correction_schema()` 只复制 assessment 的字段定义，未把完整 Schema 的条件规则与保留项的已知状态明确放入补丁 Schema；这是下一步最小修改位置。应从同一 PCS 规则生成补丁 assessment 的条件约束，并明确提供 retained_checks。若保留项中有 UNRESOLVED，要求模型自己返回 null/UNRESOLVED 和非空原因；程序仍不能替填分数。此后先离线拒绝本次错误响应，再以新版本进行一次有界验证，不重复调用本次失败记录。

本次费用估算 0.024538 元，依据返回用量和未缓存价格计算，不是平台账单；新增未知费用 0。共同账本累计 SETTLED 133 笔、已知估算 4.653116 元；历史 UNPRICED 4 笔与 HELD_REJECTED 2 笔（预留合计 1 元，实际费用未知）原样保留。失败原文、用量、读页与账本记录全部保留。未启动新代表性评价或统一 14 项；不混入旧成功记录。

保护复核覆盖既有清单 21,112 个文件，全部哈希不变。配置与日志位于 `outputs/evaluation_preparation/l4-live-v22-20261026`；详细原因位于本次运行目录的 `failure-analysis.json`。恢复点是补丁 assessment 约束修复及新版本验收，不能直接重跑本次失败目录或跳过门槛启动 14 项。
