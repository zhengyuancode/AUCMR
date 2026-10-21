# AUCMR Agent L4 运行说明

本模块在正式仓库 `D:\Research\02_Code\AUCMR\AUCMR` 实现。协议为 `AUCMR_AGENT_L4_1.0.0`。只评价七个最终生成槽位的 PCS 和整份制品的 PRC。没有 PCmpS、覆盖率、共同需求清单、加权总分、从业者模拟或人工统计模型。

代码已实现独立入口、显式输入绑定、匿名材料、来源工具交互、真实 HTTP 客户端、图片消息转换、费用记录、恢复、严格记录校验和 JSON/CSV 汇总。默认仍禁止真实调用。当前验证使用断网构造测试和真实文件结构盘点，真实模型调用为 0，真实语义评价和正式实验均为 NOT_RUN。离线测试不能证明评分准确、人类一致性或 AUCMR 更实用。

## 文件与职责

| 新增文件 | 职责 |
| --- | --- |
| `eval/l4_agent/__init__.py` | 独立协议版本，导入无模型加载 |
| `eval/l4_agent/__main__.py` | inventory、mock、validate、export、run/--resume 命令 |
| `eval/l4_agent/protocol.py` | 提示、量表、版本、独立运行目录、网络阻断 |
| `eval/l4_agent/inputs.py` | 最终输入适配、明确绑定、结构盘点、哈希冻结 |
| `eval/l4_agent/sources.py` | 只读复用 L3 来源工具，增加 L4 状态和访问审计 |
| `eval/l4_agent/records.py` | PCS/PRC 响应及持久化记录校验 |
| `eval/l4_agent/engine.py` | 单主评执行、调用授权、逐制品保存和恢复 |
| `eval/l4_agent/client.py` | 把来源工具结果转为文字及原页 image_url 消息 |
| `eval/l4_agent/runner.py` | 真实运行入口、冻结配置、共享费用账本和导出 |
| `eval/l4_agent/export.py` | 独立分母、共同目标集合、JSON/CSV 导出 |
| `eval/l4_agent/fixtures.py` | 人工构造的固定示例和脚本化 mock 客户端 |
| `config/agent_evaluation_l4.json` | 独立 L4 配置，默认禁止真实调用 |
| `tests/agent_l4/test_pipeline.py` | 输入、记录、统计、授权和漂移测试 |
| `tests/agent_l4/test_sources.py` | 对实际 L3 SourceAccess 的本地文字和图像兼容测试 |
| `tests/agent_l4/test_runtime.py` | 实际运行器配合断网 fake transport，检查视觉消息、费用、失败和恢复 |
| `scripts/verify_agent_l4_offline.py` | 仅执行上述测试，明确阻断网络 |
| `docs/AGENT_L4_RUNBOOK.md` | 本说明 |

L4 产物写入 `outputs/agent_l4/`，新运行使用独立目录。真实请求的共享费用账本使用 `outputs/evaluation_campaign/budget.sqlite`。已有记录不可覆盖，`run --resume` 会校验并复用全部已保存制品，失败和未决也保留，只继续尚未保存的制品。中断制品的已留存请求由共享运行器复用，不能因分数方向重抽。构造测试的账本只放在该测试目录的 fixtures 内，不接触真实 campaign 账本。

## 与 L3、L2 的关系

只读复用 `eval.agent_evaluation.source_access.SourceAccess`、`common.py` 的 JSON 和哈希工具，并通过 `eval/evaluation_runtime.py` 使用已有预算工具。不会读取 L3 的判定标签。共享运行器与 L4 实现都记录哈希，依赖变化后旧运行不能直接通过当前版本验证。

L4 配置显式复制 L3 配置的 `input_bindings` 和 cohort 中的任务、方法、原始 PDF 绑定。保留预先登记的 MAIN 与 DIAGNOSTIC 分组。当前共 18 个目标、7 种方法、126 个绑定，其中 MAIN 为 14 个目标，DIAGNOSTIC 为 4 个目标。分组来自既有登记，不依据当前输出是否拒答或当前评分重新划分。配置没有动态挑选最新文件的逻辑。若 L3 以后更换绑定，兼容测试会要求人工核对 L4 的明确批次。

L4 不调用 L3 的评分执行器，不读取其内容标签、场景得分或结论，不要求 L3 已完成真实评价。L4 不导入或改动 L2、L0、L1、原 R1–R7。L4 的 `AGENT_L4_ARTIFACT` 记录与 L3 评价记录独立。`source_backend` 内是复用工具产生的访问日志，不是 L3 或 L4 评分。

现有根运行入口没有修改。今后使用本模块的独立 CLI，旧根入口的旧 L4 统计不会自动切换为新指标。

## 运行环境和命令

需要 Python 3.10 或更高版本，以及现有环境中的 PyMuPDF。HTTP 和 SQLite 使用标准库，没有新增 API SDK，不加载 torch、嵌入模型或生成模型。已有测试环境为 Windows Python 3.14.2 和 PyMuPDF 1.27.2.3；服务器 Linux/Python 3.10 的实际执行仍需在服务器验证。

在正式仓库根目录执行。以下命令均不调用模型。

```bash
python -B -m eval.l4_agent inventory --config config/agent_evaluation_l4.json
```

这个命令读取真实输入的结构与哈希。它不创建评分记录，不判断真实断言正确性。输出给出本次 `run_dir`。

```bash
python -B -m eval.l4_agent mock
```

这个命令在新目录内生成专用 PDF 和 22 份计划制品，运行脚本化 mock 的搜索、文字页和原图访问，然后校验和导出。模拟输入标记 FIXTURE，记录标记 MOCK_ONLY，均注明 NOT_RESEARCH_DATA。该命令不接受真实输出作为 mock 输入。

```bash
python -B scripts/verify_agent_l4_offline.py
```

仅发现 `tests/agent_l4/test_*.py`，不执行全仓库测试。socket 连接、DNS、UDP 发送均被显式阻断。不读取已有密钥，不进行连通性试调用。

把下面的 `MOCK_RUN_DIR` 替换为 `mock` 命令刚返回的目录。

```bash
python -B -m eval.l4_agent validate --run MOCK_RUN_DIR
python -B -m eval.l4_agent export --run MOCK_RUN_DIR
```

`validate` 重新核对输入、来源、配置、代码、提示和量表哈希，并检查逐份记录与合并文件一致。`export` 先做同样校验，然后在该 run 内创建新的导出子目录。不会覆盖既有导出。版本变化时，旧 mock 目录仍保留，但需要运行新一轮 mock 验证，不能绕过漂移检查。

## 配置示例

完整配置已经写入 `config/agent_evaluation_l4.json`。下面是只绑定一个现有任务的最小结构示例，仅用于说明字段。它不会授权真实评价。

```json
{
  "protocol_version": "AUCMR_AGENT_L4_1.0.0",
  "data_class": "REAL_INPUT_STRUCTURE_ONLY",
  "batch_id": "EXPLICIT_BATCH_EXAMPLE",
  "live_authorized": false,
  "generated_slots": ["goal", "preconditions", "trigger", "main_flow", "alternative_flow", "exception_flow", "postconditions"],
  "judge": {"judge_id": "l4-primary", "version": "NOT_BOUND"},
  "model": {
    "config_id": "L4_UNBOUND_V1",
    "binding_status": "NOT_BOUND",
    "provider": null,
    "model_id": null,
    "supports_images": null,
    "api_key_env": null
  },
  "source_policy": {"search_max": 12, "pages_max": 24, "request_rounds": 12, "tools_per_round": 4},
  "sources": [{"source_id": "SRC-JTV", "project_id": "jtvauctions", "path": "data/jtvauctions.pdf"}],
  "methods": ["AUCMR"],
  "targets": [{"task_id": "JTV-03", "project_id": "jtvauctions", "comparison_group": "MAIN", "name": "Browse and Search Products", "actor": "Customer"}],
  "input_bindings": [{"task_id": "JTV-03", "method": "AUCMR", "path": "outputs/aucmr_full/jtvauctions/Customer_Browse_and_Search_Products.json"}]
}
```

路径相对于仓库，不能逃出仓库。输入矩阵必须显式覆盖每个目标与每种方法的组合；文件缺失也保留绑定。原始来源按项目授权，对所有方法使用同一个 `source_policy`。`pages_max` 限制不同原始 PDF 页数，同一页文字和图像不重复占用页数；总工具调用另受轮数和每轮调用上限约束。可为来源登记 `expected_sha256`，不匹配时记 SOURCE_CHANGED。

## 最终输入和匿名化

输入采用现有最终 JSON 的 `use_case_name`、`actor`、`slots[slot].claims[].content`，保留断言顺序、`step_number` 和已有 `raw_text`。不拆分断言，不把 `pre_constraint_slots` 当成最终内容，不改写文本。每条断言增加仅用于定位的 `slot:index` ID，原有步骤号不变。

评分请求中只包含一个完整匿名制品、任务名称和参与者、七槽内容、来源 ID 与页数、量表及统一工具规则。方法名、原文件路径、旧分数、质量标记、修复历史、证据卡片和其他层结果不发送给评审。无引用制品也可以评价。方法身份若嵌在原文中，记录 IDENTITY_EXPOSURE 并停止该制品评分，不通过删除原文来伪装匿名。

来源和被评文本中的指令一律当作数据。离线测试验证封装和执行边界，不能证明真实模型一定抵抗所有提示注入。

## PCS 与 PRC 口径

| PCS | 含义 |
| --- | --- |
| 0 | 槽位内容基本错误或与槽位要求基本无关 |
| 1 | 主要内容存在明显错误，仅少量内容正确 |
| 2 | 总体正确，但仍有少量错误或不准确 |
| 3 | 在已核验材料范围内，未发现明显内容错误 |

七个槽位为 goal、preconditions、trigger、main_flow、alternative_flow、exception_flow、postconditions。名称和参与者不计分。判断必须结合原始来源、任务范围和槽位用途。语言顺畅、格式齐全或引用存在都不能替代内容核验。没有需求覆盖率，也不因没有穷尽需求而扣完整度分。

有效空槽固定 PCS=0，状态 EMPTY_SLOT。七项都有有效分数时才计算七槽平均 PCS。任一项未决或技术缺评，平均 PCS 为 null，但保留其余有效分项。

| PRC | Agent 的整体辅助性实用性预估 |
| --- | --- |
| 1 | 预计需要基本重写 |
| 2 | 预计需要大幅修改 |
| 3 | 预计经过一定修改后可用 |
| 4 | 预计只需少量修改 |
| 5 | 在当前检查范围内，未发现明显妨碍后续使用的问题 |

PRC 单独判断，不从 PCS 或其他层指标计算。必须给出具体位置和修改需要。PRC=5 也需说明检查了哪里、为何没有发现阻碍；不能同时报告需要修复的明确问题。PRC 不代表真实从业者认可、实际节时或后续任务成功，不估计修改时间或人工成本。

| 情况 | PCS | PRC 和统计 |
| --- | --- | --- |
| 有效空槽 | 固定 0，EMPTY_SLOT | 其他内容仍可独立判断 PRC |
| 七槽均为有效空槽 | 七个 0，均值 0 | null，EMPTY_OUTPUT，排除 PRC 均值 |
| 明确拒答 | 有效空槽可记 0，其他无法判断项 null | null，NO_EVALUABLE_CONTENT，单独统计拒答 |
| 缺失文件或损坏结构 | null，不伪造空槽 | null，技术失败；拒答标记仍保留 |
| 来源文件不可读或变化 | 非空槽 null | null，技术失败并保留具体来源状态 |
| 搜索不足或语义无法判断 | null，UNRESOLVED | 对应无法判断的 PRC 为 null |
| 漏槽响应、越界、无效断言/来源 ID、损坏 JSON | 对应项 null | 保存原始响应和校验错误；已有有效分项不抹去 |

既有早停拒答可能根本没有七个槽位。这同时属于明确拒答和结构缺失，记录保留两者，不补造七个空槽。拒答、未决与技术失败计数可能重叠，不能将这些计数相加作为唯一分母。

## 来源与记录格式

来源工具支持本地目录索引、字面文字搜索、原页文字和原页 PNG 图像。不构建检索服务，不抽取共同需求清单。错误来源、无效页号、不可读页、无匹配和预算耗尽有独立状态。图像渲染失败不会退回文字并装作成功。查找不到只能支持未决，不能自动作为反证。

非空槽响应需要逐断言的检查项。文本依据记录来源 ID、从 1 开始的 PDF 页号、访问 ID 和在该页实际存在的引文。图像依据必须指向真正读过的原图，并描述观察区域。事实错误需要相关断言和原文位置；组织或错槽问题需指出输出位置。结构校验不能证明模型的理由在语义上正确。

每份 `records/<artifact_id>.json` 包含：

- `record_type=AGENT_L4_ARTIFACT`，匿名制品 ID、task_id、project_id、comparison_group、私有 method、run_id。
- 输入、来源、配置、实现、提示和量表的哈希或版本，judge_id、judge_version、model_config_id。
- `pcs` 七个槽位，每项保存 score、status、reason、checks、unresolved、validation_errors。
- 独立 `prc`，保存 score、status、reason、modifications 和未决问题。
- `pcs_mean`、processing_status、artifact_state、explicit_refusal、structure_issues。
- 原始响应、解析状态、请求和工具响应轨迹、来源访问记录以及记录自身哈希。

真实客户端的 `runtime_receipts` 另保存每轮 request_id、实际返回模型、finish_reason、usage、费用、原响应和共享运行器证据路径。逐制品导出保留这些记录及 `runtime_cost_cny`、`real_model_calls`。未知费用为 null，不能写成 0；全 campaign 的最终花费以共享账本为准，不能只加已完成评分。

例如一个非空槽的检查项结构为：

```json
{
  "claim_id": "main_flow:0",
  "finding": "SUPPORTED",
  "rationale": "此处应填写针对当前断言的具体核验说明",
  "source_refs": [{"source_id": "SRC-FIXTURE", "pdf_page": 1, "access_id": "L4-ACCESS-3", "kind": "TEXT", "quote": "1. The system writes the document to a file."}]
}
```

这是构造示例，不是真实评价。每项分数都来自保留的原始响应或唯一允许的空槽固定规则。记录校验会重新解析响应，不允许把手工填入的有效分数直接作为已完成结果。

## 汇总和分母

`exports/artifacts.json` 与 `artifacts.csv` 提供逐制品信息；`slots.csv` 提供逐槽位依据；`summary.json` 与 `summary.csv` 按文档、预设比较组和方法汇总。CSV 中复合字段使用 JSON 字符串，null 数值在独立 CSV 数值列为空，状态列和嵌套 JSON 保留 null 含义。

每组保存 planned_task_ids，以及计划数、完成数、空输出数、明确拒答数、未决数、技术失败数。完成指七槽 PCS 与 PRC 均有效。

`available_pcs` 和 `available_prc` 是各自可用结果的描述性均值，附实际 task_ids 和 denominator。两者分母可以不同。例如 PCS 响应漏了一槽但 PRC 有效，PRC 仍保留。

`common_pcs` 和 `common_prc` 分别取同一文档、同一预设比较组中所有方法的共同可评价目标集合。仅在各指标自己的相同目标集合上比较方法。`all_target_statuses` 始终展示所有计划目标，包括未进入共同集合的失败、空输出和未决。不同可用集合的均值不能直接作为公平排名。不输出跨指标总分、显著性星号或从业者混合效应模型。

## 真实运行与恢复

真实客户端使用兼容 OpenAI Chat Completions 的 HTTP 请求格式。`client.py` 将本地原页 PNG 转为 `image_url` 的 data URL，图片字节不放入文字 JSON。来源 ID、页号和访问 ID 保留在图片旁的文字块中。客户端不会把图像读取成功直接当成内容判定正确。

当前配置仍为 `REAL_INPUT_STRUCTURE_ONLY`、`live_authorized=false`、裁判和模型 `NOT_BOUND`。因此下列真实命令现在会停止，不会自动使用本机已有密钥。实际运行前需要在独立 L4 配置中固定：

1. 经核对的输入批次。默认仍为既有工业来源的 14 个主目标和 4 个诊断目标，共 126 个绑定，不自动扩展新增用例。三层须核对目标、方法和最终输入 SHA256。
2. `data_class=REAL_AGENT_EVALUATION`、`live_authorized=true`，以及 judge 版本、`model.binding_status=BOUND`、provider、确切 model_id、base_url、api_key_env、必要参数和输出上限。
3. `supports_images=true` 和 `capabilities.images=true`。模型图像能力必须经过核实，不能只为通过门控填 true。
4. 完整计费规则。`billing` 需明确输入、输出、推理计价和所有收费是否都在 token usage 中；`complete`、`verified_upper_bound`、`all_charges_in_token_usage` 只有核实后才设为 true。`per_request_upper_bound` 的 input、output、reasoning、images、other 五项都是人民币上界，不能漏项。

L2、L3、L4 应使用同一 campaign 的 `outputs/evaluation_campaign/budget.sqlite`，上限为整个 campaign 共 500 元，并非每层各 500 元。运行中不允许更换该账本。缺少用量、未知费用或预算阻断会保存现有证据并停止后续请求，不能当作免费失败继续。完整但格式无效、或 finish_reason 不是 stop 的已计费响应记为该制品技术失败，保留原响应和费用，不自动重抽。

在仓库根目录执行新运行。命令中的授权开关与配置授权必须同时成立。

```bash
python -B -m eval.l4_agent run --config config/agent_evaluation_l4.json --campaign-ledger outputs/evaluation_campaign/budget.sqlite --authorize-live
```

入口会在服务器新建 `outputs/agent_l4/` 下的目录并冻结当地输入。它不需要本机 Windows 的历史清单或 L3 已经评分。命令输出实际 run_dir 和 export_dir。输入、来源、提示、量表、模型、共享运行器或实现变化时，应建立新运行，不能绕过哈希检查。

中断后将 `RUN_DIR` 替换为该次目录，执行：

```bash
python -B -m eval.l4_agent run --resume RUN_DIR --campaign-ledger outputs/evaluation_campaign/budget.sqlite --authorize-live
python -B -m eval.l4_agent validate --run RUN_DIR
python -B -m eval.l4_agent export --run RUN_DIR
```

恢复只使用冻结配置，不同时传 `--config` 或 `--run-dir`。全部已保存结果保持原字节，包括失败和未决；恢复不会再次向模型申请这些制品的分数。已保存的请求也由共享运行器按请求指纹复用。若先前请求的费用未知，先核对请求和账本，不能通过新 run 或新账本绕开。没有任何失败重试会依据 AUCMR 排名或评分方向。

已有 39 项离线记录只证明旧离线实现的构造测试通过。新验证脚本还发现 `test_runtime.py`，用真正的 L4 runner 和共享 runtime 经过 fake transport 检查消息、图片、用量和中断恢复，所有材料仍标为 MOCK_ONLY、NOT_RESEARCH_DATA。真实供应方的 API 兼容性、语义评分质量、实际计费与 Linux 运行仍需后续固定样例及小试确认。

## 服务器交付

发布需包含上表 17 个 L4 文件、`eval/evaluation_runtime.py`，以及仓库已有的 L3 来源与预算基础工具。不要覆盖服务器自己的模型路径、密钥或正在生成的输出。新增代码必须实际进入发布提交或经核对的同步清单，单纯保留在本地未跟踪目录无法通过 git pull 部署。

在服务器的现有环境和正确仓库内先检查依赖，再执行离线验收、盘点和 mock。这些命令没有模型调用。

```bash
python --version
python -m pip show PyMuPDF
python -B scripts/verify_agent_l4_offline.py
python -B -m eval.l4_agent inventory --config config/agent_evaluation_l4.json
python -B -m eval.l4_agent mock
python -B -m eval.l4_agent run --help
```

首次盘点会创建服务器本地清单。先比较 126 个绑定的实际哈希和已选批次，并确认来源可读。不要直接复用 Windows 的历史 run 目录。离线通过后仍需完成真实固定样例与小试，才进入已授权的正式批次。根入口和旧 `compute_metrics` 不会自动采用新 L4 结果，论文只能从这次独立导出读取 PCS/PRC。

## 以后需要同步的论文表述

本次没有修改论文。以后需要单独同步：L4 改为 Agent PCS/PRC 评分；撤下 PCmpS；撤下真实从业者身份、26 人及 BIBD 人员分配；撤下旧人工统计结论。PRC 只能描述 Agent 对后续使用便利程度的预估，不能写成人类认可、节时或下游成功。不能将新旧评价直接混合。
