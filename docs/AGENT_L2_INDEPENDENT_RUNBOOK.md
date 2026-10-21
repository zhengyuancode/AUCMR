# L2 独立整体一致性检查交付说明

代码仓库为 `D:\Research\02_Code\AUCMR\AUCMR`。本模块只依赖 Python 标准库，不修改根入口、共享依赖或已有评价模块。

## 完成状态

- 独立入口、输入盘点、完整用例提示、客户端注入接口、响应校验、记录保存、JSON/CSV 汇总已实现。
- 本轮执行的是结构盘点和固定样例的 mock 流程。真实模型调用次数为 **0**。
- 真实语义评价和正式实验均为 **NOT_RUN**。
- mock 通过不证明模型能识别矛盾，不证明 AUCMR 效果或裁判有效性。样例和模拟结果都标记为 FIXTURE、MOCK_ONLY、NOT_RESEARCH_DATA。
- CLI 已增加 run、--resume、真实 export 和 prepare-inputs。默认模型配置仍为 UNBOUND，live_authorized=false。
- 当前 Python 3.14.2 上完成离线验证，同时用 Python 3.10 语法模式解析新增 Python 文件。服务器 Python 3.10/ROCm 环境中的运行尚未验证。

## 新增文件

| 文件 | 用途 |
| --- | --- |
| eval/l2_independent/__init__.py | 模块版本，无导入副作用 |
| eval/l2_independent/__main__.py | 独立命令入口 |
| eval/l2_independent/common.py | 哈希、版本、路径保护和独立运行目录 |
| eval/l2_independent/inputs.py | 明确文件绑定、最终槽位适配、匿名化、结构状态 |
| eval/l2_independent/protocol.py | 完整用例关系检查提示 |
| eval/l2_independent/evaluation.py | 客户端协议、授权保护、响应定位校验和独立 L2 记录 |
| eval/l2_independent/offline.py | 阻断网络和旧评价/模型模块导入 |
| eval/l2_independent/fixtures.py | 固定构造材料和 mock 响应 |
| eval/l2_independent/integration.py | 与 L3 输入清单的只读兼容检查 |
| eval/l2_independent/reporting.py | 独立分母及 JSON/CSV 导出 |
| eval/l2_independent/runner.py | 盘点、mock 运行和重新导出 |
| config/agent_evaluation_l2_independent.json | 隔离的 L2 配置及冻结输入绑定 |
| tests/agent_l2_independent/test_inputs.py | 输入、匿名化与状态测试 |
| tests/agent_l2_independent/test_evaluation.py | 定位、去重、授权、缓存与隔离测试 |
| tests/agent_l2_independent/test_workflow.py | 分母、跨方法共同集合、导出和 L3 对接测试 |
| scripts/verify_agent_l2_independent_offline.py | 专用离线验收入口 |
| docs/AGENT_L2_INDEPENDENT_RUNBOOK.md | 本说明 |

没有修改 eval/l2_judge.py、eval/compute_metrics.py、eval/agent_evaluation/ 或 config/agent_evaluation_l3.json。没有改动 L0、L1、L4、生成算法、论文、原始 PDF、旧生成结果和旧评价结果。没有提交或推送其他人的修改。

## 检查目的与隔离

新检查看完整 Detail-UC 中的角色、对象、条件、动作、状态变化和结果能否同时构成一致的行为过程。确认冲突必须说明共同适用的条件或路径，并定位到原断言。

它不按原 R1–R7 逐条评分，不导入或调用原评价，不接收其提示、分数或 DRA 修复记录。不检查来源真实性或全部需求是否覆盖，这些属于 L3。不同合法分支、时间上的正常状态变化、同义改写、自然省略和空槽位不能自动判为冲突。

两种 L2 检查可能发现同一问题。独立性来自判据、输入材料和每份制品的新会话，不宣称指标在统计上独立。程序不把冲突数与 R1–R7 相加，也不产生加权总分、排名或显著性结论。

提示将被评文字明确声明为不可信数据。mock 测试确认命令式文字不会改变程序控制流程和请求构造，但没有验证真实模型抵抗提示注入的能力。

## 与已完成 L3 的连接

配置只读引用以下三个明确文件，并记录各自 SHA256。

1. config/agent_evaluation_l3.json
2. eval/agent_evaluation/design/cohort.json
3. outputs/agent_evaluation/l3-offline-20261021/input_manifest.json

不按文件时间选择输出，不调用 L3 的评价器，不读取 L3 语义分数。L2 配置复制当前已登记的目标、方法和输入绑定，再用 L3 冻结清单核对 SHA256。

当前登记范围为 18 个目标、7 种方法、126 个目标和方法组合。其中 MAIN 为 14 个目标、98 份制品，DIAGNOSTIC 为 4 个目标、28 份制品。这个范围来自既有 cohort，本模块不依据 AUCMR 是否接受目标重新筛选。

L2 的 task_id 与 L3 的 target_id 相同。L2 的 source_project 与 L3 的 project_id 相同。记录和表格保留这两组别名。跨层关联使用 project_id、target_id、method、input_hash，不使用两个模块各自的匿名 artifact_id 直接连接。

盘点导出 l3_join_map.csv，包含两个模块的制品 ID 和对应键。该表仅供本地结果连接，不进入模型请求。MAIN 和 DIAGNOSTIC 在 per_document_method_group 表中分别保留，不把诊断组偷偷计入 MAIN。

这只证明当前文件绑定与 L3 冻结清单一致。历史生成批次、历史模型和服务商来源没有被独立证实，配置对此保留未核实状态。

如果 L3 的输入绑定、cohort 或冻结清单发生变化，盘点会报告 DRIFT。需要显式审查并更新 L2 的独立配置，然后创建新 run；程序不会静默刷新清单或重用旧结果。

## 入口命令

在正式仓库根目录运行。使用 -B 避免产生 __pycache__。

真实输入只读盘点和 L3 兼容检查：

```text
python -B -m eval.l2_independent inventory --config config/agent_evaluation_l2_independent.json
```

固定构造样例的 mock 流程：

```text
python -B -m eval.l2_independent mock
```

每条命令返回本次唯一 run_dir。重新导出时明确指定该目录，不使用“最新目录”：

```text
python -B -m eval.l2_independent export --run-dir outputs/agent_l2_independent/本次mock目录名
```

专用总验收：

```text
python -B scripts/verify_agent_l2_independent_offline.py
```

单独运行相关测试：

```text
python -B -m unittest discover -s tests/agent_l2_independent -v
```

Windows 本次实际使用的解释器为 C:\Users\JunxiHu\AppData\Local\Python\pythoncore-3.14-64\python.exe。例如：

```powershell
& 'C:\Users\JunxiHu\AppData\Local\Python\pythoncore-3.14-64\python.exe' -B scripts/verify_agent_l2_independent_offline.py
```

盘点的 PASS 表示绑定校验通过，不表示所有制品可评价。结构损坏仍会写入清单。绑定漂移、完整性校验失败和配置错误返回非零退出码。完整 mock 流程若状态不符合固定预期也返回非零退出码。

## 输入示例与适配规则

每个 input_bindings 项明确提供 task_id、method、相对路径、sha256。目标名和主要参与者来自独立 targets 配置。不要用输入文件自述的身份覆盖任务配置。

最终输出的结构示例：

```json
{
  "use_case_name": "Save a record",
  "actor": "User",
  "slots": {
    "goal": {"claims": [{"content": "Save record R."}]},
    "preconditions": {"claims": []},
    "trigger": {"claims": []},
    "main_flow": {"claims": [{"content": "1. Save R.", "step_number": 1}]},
    "alternative_flow": {"claims": []},
    "exception_flow": {"claims": []},
    "postconditions": {"claims": [{"content": "R exists."}]}
  }
}
```

只适配七个最终 slots。每个 claims 项对应一个断言，不拆分或合并。内容、空白、数组顺序、原索引和 step_number 原样保留。稳定定位例如 main_flow:0，索引从 0 开始。不使用 pre_constraint_slots，也不从 raw_text 补造 claims。raw_text 非空但 claims 不存在或为空时记结构错误，避免隐藏有内容的输出。

请求只含匿名制品 ID、外部任务名称/参与者和七槽位断言。每次调用只构造一条固定系统消息和一条制品数据消息，不带历史对话。方法名、路径、分组、质量标记、引用元数据、旧分数和生成过程不进入请求。原文中的引用标识不作为一致性证据。若发现生成文字暴露方法身份，阻止送审，不能为了匿名化擅自润色原文。

明确拒答由生产者已有的 unresolvable_use_case 标记识别，不通过模型猜测。只有空内容的提前拒答允许存在省略槽位。若带该标记但仍有完整非空最终内容，不静默丢弃断言。其余缺槽、缺 claims、错误类型均保留结构错误。

## 状态与记录

| 处理状态 | 含义 |
| --- | --- |
| COMPLETE_WITH_CONFLICTS | 完成检查并检出具体冲突 |
| COMPLETE_NO_CONFLICTS | 完成检查而未检出冲突，不代表一致性满分 |
| PARTIAL_UNRESOLVED | 有未决、仅部分可判断，或只校验通过部分问题 |
| NO_EVALUABLE_CONTENT | 有效空输出或明确空拒答 |
| TECHNICAL_FAILURE | 文件缺失、读取/解析/结构失败、调用失败或响应完全无效 |

input_status 进一步区分 PRESENT、VALID_EMPTY、EXPLICIT_REFUSAL、MISSING_FILE、READ_FAILED、PARSE_FAILED 和 STRUCTURE_ERROR。

每条独立 L2 记录包含制品 ID、任务和项目 ID、输入哈希、批次、协议/提示/模型配置哈希、run_id、处理状态、断言定位表、冲突列表、未决列表、原始响应、解析状态和校验错误。

冲突记录包含本地 issue_id、refs、condition_or_path、incompatible_content、rationale。未决记录包含 refs、condition_or_path、question、reason。只要求简短理由，不索取长篇思维过程。

不存在的断言 ID、错误槽位和越界索引不能成为已确认问题。若部分问题有效，保留它们并标记不完整。若全部问题无效，记技术失败。原始响应始终可追溯。调用异常仅保存异常类型，避免把可能含密钥的异常文本写入日志。

精确相同的问题去重。仅涉及相同断言对但内容或理由不同的问题仍分别保留。程序没有语义去重 Agent。

没有可评价内容和技术失败的 confirmed_conflict_count 为 null，在 CSV 中为空，不写为 0。部分结果的已确认冲突单独统计，不进入完整检查分母。空槽数只统计确实存在且 claims 为空的槽位，缺失槽位另外记录。损坏制品的断言数和空槽数仅为已观察到的数量，并带有完整性标记。

## 输出结构

全部输出位于 outputs/agent_l2_independent/ 下，每次创建含 UTC 时间和随机后缀的新目录。保存操作拒绝覆盖旧文件。

```text
<inventory-run>/
  config.snapshot.json
  input_inventory.json
  input_inventory.csv
  structural_summary.json
  structural_summary.csv
  l3_alignment.json
  l3_join_map.csv
  run_manifest.json

<mock-run>/
  config.snapshot.json
  fixtures/
    artifacts.json
    fixture_checks.json
    records/<匿名制品ID>.json
    export/
      summary.json
      per_artifact.json
      per_artifact.csv
      per_document_method.json
      per_document_method.csv
      per_document_method_group.json
      per_document_method_group.csv
  run_manifest.json

<verify-run>/
  report.json
  tests.log
  protected_hashes.json
  code_manifest.json
```

汇总保存每份制品的已确认冲突数、未决数、断言数、空槽数，以及每文档和方法的预期目标数、实际完整检查数、部分结果数、失败数、空输出/拒答数。另列所有方法都已完成的共同目标集合及其分母。部分结果不能当作完整结果参加比较。

运行清单保存文件哈希。缓存身份含输入哈希、断言材料、任务输入、批次、协议、提示内容及版本、适配版本和模型配置。重新导出会验证保存文件、当前协议/提示、固定样例和模型配置。不存在隐式恢复。run --resume 明确选择原目录，修改输入或协议后必须创建新 run。

## 原离线交付的验收

执行过 inventory、mock、专用 unittest 和 verify_agent_l2_independent_offline.py；总验收还实际执行了明确指定源 run 的重新导出。没有运行全仓库测试。

总验收结果为 26 项测试通过、0 失败、0 错误、0 跳过。覆盖匿名化、最终槽位、旧评价隔离、合法与非法定位、各类空/错/未决状态、精确去重、缓存变化、模型和批次隔离、共同分母、JSON/CSV 一致性以及网络阻断下的端到端流程。

网络保护会直接阻断 socket 连接、DNS 和发送操作。专门的网络自检在发起连接前被拦截。离线运行不依赖“没有 API Key”。专用代码扫描没有发现对旧评价器或生成模型模块的导入。共享运行器仅复用 L3 的 budget/common 基础设施，不访问 L3 标签或结果。

总验收比较了 166 个受保护文件的前后哈希，均未变化。其中包含现有 L2/L3 代码及 126 份真实输入。

当前真实盘点结果为 110 份 PRESENT、10 份 VALID_EMPTY、2 份 EXPLICIT_REFUSAL、4 份 STRUCTURE_ERROR。4 份结构错误对应 JTV-08、JTV-09 的 AUCMR 和 CREST w/o cross-slot check 制品，缺少部分 claims 字段，全部保留且未修补。输入哈希漂移数为 0，L3 对接检查为 PASS。

固定材料包含明确矛盾、正常时间顺序、合法不同分支和信息不足，也覆盖空输出、拒答、损坏、缺失、错误响应、无效定位、部分冲突和调用失败，共 13 份。它们的 mock 标签只检查程序是否按指定响应处理，不代表模型已经识别这些情形。

交付时请查看明确指定的 verify run 中的 report.json 和 tests.log。代码哈希在 code_manifest.json 中，便于核对报告对应的代码。

## 服务器运行入口

本次已补齐共享 HTTP 客户端接入、run、显式恢复、真实记录导出、逐制品保存和共同费用账本。默认配置仍为 UNBOUND、live_authorized=false。本轮只验证构造输入和断网假传输，真实语义评价与正式实验仍为 NOT_RUN。

先在服务器本地建立输入清单。它只读取冻结的相对路径和内容哈希，不需要 L3 语义评价，也不使用 Windows 上的绝对路径。原 l3_alignment 保留为历史对照。

```text
python -B -m eval.l2_independent prepare-inputs --config config/agent_evaluation_l2_independent.json
```

返回明确的 config.bound.json 和 input_manifest.json 路径。若已有服务器清单，可以加 --manifest 路径。导入只接受相同的目标、方法、相对路径和内容哈希；当前固定为旧工业批次 18 个候选目标，其中 MAIN 14 个，不加入其他新用例。内容改变或文件缺失不会被静默改绑。

随后在返回的派生配置中填写模型绑定。需要 provider、model_id、model_revision、HTTPS base_url、api_key_env、parameters 中的正整数 max_tokens 或 max_completion_tokens。密钥只从指定环境变量读取。还需填写 billing 和 per_request_upper_bound，字段遵循 eval/evaluation_runtime.py。价格与每请求上限必须经核实，不能用占位值代替。L2 只发送文本，不需要图像能力。

真实调用必须同时满足派生配置 live_authorized=true 和命令 --authorize-live。三层使用同一个 campaign 账本，500 元是全轮上限。

```text
python -B -m eval.l2_independent inventory --config 返回的config.bound.json
python -B -m eval.l2_independent run --config 返回的config.bound.json --authorize-live --campaign-ledger outputs/evaluation_campaign/budget.sqlite --budget-cap-cny 500 --max-attempts 2
```

每次返回独立 run_dir。恢复必须明确指定原目录，配置、输入、提示、账本、上限与尝试政策保持一致。

```text
python -B -m eval.l2_independent run --config 原config.bound.json --authorize-live --campaign-ledger outputs/evaluation_campaign/budget.sqlite --budget-cap-cny 500 --max-attempts 2 --resume 原run_dir
python -B -m eval.l2_independent export --run-dir 原run_dir
```

真实 export 只读保存的结果，不请求模型，并输出新的 tables 目录。旧 mock run 的 export 保持原来的 fixtures 隔离。

每份制品保存在 attempts/匿名制品ID/尝试编号/ 下。started.json 在请求前保存，record.json 保存结果、原始响应、用量、费用、请求 ID 和运行器证据位置。runtime/runtime_requests/ 保留实际请求和返回。记录带内容哈希，export 和恢复拒绝损坏或不匹配的记录。

恢复复用完整记录，并保留未决、空内容和已确认技术失败。已开始但尚未写 record 的尝试沿用原编号，通过共享运行器恢复已有响应，不重复付费。--max-attempts 只允许 1 至 3，默认 2。只有运行器明确声明可安全重试的错误才自动尝试；已收费但格式错误的响应不重发，未决结果不为获得不同结论而重试。未发出请求的配置、凭证或预算阻止可在修复外部条件后显式恢复，且仍受尝试上限约束。

若计费未知，立即停止后续请求并保留已有记录。未开始项导出为 TECHNICAL_FAILURE，error_code=NOT_STARTED，冲突数为 null。部分结果不进入完整检查分母。费用总额以共享账本按请求 ID 结算，不能重复相加缓存恢复中的费用凭据。

## 本次补接的离线验证

专用测试现包含 35 项。新增测试使用构造输入和 mode=mock 的假传输，使用独立的 fixtures 账本，且网络保护全程开启。覆盖 CLI、本地清单严格绑定、真实输入禁用假传输、完整记录复用、已收费响应写记录前中断恢复、格式失败保留、未知计费停止、导出分母和 READ_FAILED 处理。共享运行器单独验证由其专用测试负责。

```text
python -B scripts/verify_agent_l2_independent_offline.py
```

验收会先按本地冻结输入建立派生清单，不要求本机存在历史 L3 run。报告在命令明确返回的 verify run 中。code_manifest.json 记录本轮代码、配置、测试、文档和共享运行器哈希。旧 26 项测试的历史报告仍保留，不能把旧报告当作新增运行功能的验收。服务器 Linux 实际运行、真实传输和裁判有效性仍需在服务器完成对应检查；本轮没有这类证据。
