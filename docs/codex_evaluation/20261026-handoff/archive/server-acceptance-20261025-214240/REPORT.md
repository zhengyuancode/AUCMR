# AUCMR 服务器离线验收与小批次条件核查

执行日期：2026-10-25，Asia/Shanghai。仓库：`/home/dzy/code/TOSEM2026/AUCMR`。环境：Conda `aucmr`，Python 3.10.20。

结论：指定修复已快进合入，服务器原样离线回归最终通过（260 项测试、60 个子测试）。现有草稿不具备小批次启动条件。模型调用为 0；未运行小批次、正式实验或任何付费探测。除获授权的 Git 快进更新外，未编辑代码或配置。此轮完成后停止，等待总控审核。

## 1. 代码更新与工作区保护

- 分支更新前后均为 `temp`。
- 更新前 HEAD：`8c90996ef95069c1b4c304f08c294f285673d1f2`。
- 更新后 HEAD：`50f3c594545d407db97e8d1574ab0aad4f2616f4`，正是指定待验收提交。
- 起初本地无目标对象，不能直接验证包含关系；获取 `origin/codex/eval-billing-budget` 后验证原 HEAD 为目标祖先，且变更路径与已修改、未跟踪路径无交集，才执行 `--ff-only`。
- 已检查仓库及上级目录适用的 AGENTS.md，未发现此类文件。仓库内递归查找也无结果。
- 原有生成输出、`test.txt` 改动及未跟踪目录均保留。前后 `git status --porcelain=v1` 相同（它将未跟踪输出折叠为目录，因此另用文件哈希核查内容）。未执行 reset、clean、stash 或子模块更新。
- `git --no-pager submodule status` 前后均退出 128：`.gitmodules` 缺少 `eval/vendor/ReAct` 映射。这是已有仓库元数据问题，未修复。单独核对三个实际 vendor 仓库的 HEAD 与 porcelain 状态，均前后一致：ReAct `6bdb3a1fd38b8188fc7ba4102969fe483df8fdc9`，VDocRAG `b438c325519c738f5121682cc9bef5c1fa859e0a`，ViDoRAG `2c1ec39c5e3a04daa01f91545feaaa79512134bf`。状态快照中的 `eval/vendor/__pycache__` 不是子仓库，其 rev-parse 回溯的是主仓库，不能用来判断子模块漂移。
- 更新前对 outputs 与 config/evaluation_batches 内 5,804 个既有文件记录 SHA-256，事后全部一致，无删除。包含原草稿、冻结批次、已有评价记录和已有 SQLite 文件。本次测试另新增 3,136 个文件，均位于独立 L2/L4 测试目录；报告材料另存于本目录。没有将隔离 mock 账本写入共同正式账本。

实际变更 19 个路径，完整清单及冲突检查见 `update-paths.json`，Git 更新输出见 `update.log`：

```text
docs/EVALUATION_BILLING_BUDGET.md
eval/agent_evaluation/__main__.py
eval/agent_evaluation/agents/executor.py
eval/agent_evaluation/agents/recovery.py
eval/agent_evaluation/budget.py
eval/agent_evaluation/transport.py
eval/evaluation_billing.py
eval/evaluation_request_budget.py
eval/evaluation_runtime.py
eval/implementation.py
eval/l2_independent/execution.py
eval/l2_independent/reporting.py
eval/l4_agent/export.py
eval/l4_agent/records.py
eval/l4_agent/runner.py
scripts/check_evaluation_budget.py
tests/test_evaluation_audit_regressions.py
tests/test_evaluation_billing_budget.py
tests/test_evaluation_runtime.py
```

## 2. 执行命令与测试结果

以下命令均在仓库根目录执行；每条命令为单行。测试输出、退出码单独保存在本目录。

| 命令 | 退出码与结果 |
| --- | --- |
| `git --no-pager branch --show-current` | 0，temp |
| `git --no-pager rev-parse HEAD` | 0，前后提交见上 |
| `git --no-pager status --short` | 0，既有改动已记录 |
| `git --no-pager submodule status` | 128，既有映射缺失 |
| `git --no-pager cat-file -t 50f3c594545d407db97e8d1574ab0aad4f2616f4` | 初次 128，本地无对象 |
| `git --no-pager merge-base --is-ancestor 50f3c594545d407db97e8d1574ab0aad4f2616f4 HEAD` | 初次 128，本地无对象 |
| `git -c core.pager=cat fetch --no-recurse-submodules origin codex/eval-billing-budget` | 沙箱首次 128，DNS 失败；获准沙箱外重试 0 |
| `git --no-pager merge-base --is-ancestor HEAD 50f3c594545d407db97e8d1574ab0aad4f2616f4` | 获取后 0，可快进 |
| `git --no-pager diff --name-only HEAD 50f3c594545d407db97e8d1574ab0aad4f2616f4` | 0，逐项核对无工作区冲突 |
| `git --no-pager -c submodule.recurse=false merge --ff-only 50f3c594545d407db97e8d1574ab0aad4f2616f4` | 0，Fast-forward |
| `/home/dzy/miniconda3/bin/conda run --no-capture-output -n aucmr python -B scripts/check_evaluation_budget.py --offline-tests` | 首轮 1；原样沙箱外重跑 0 |
| `/home/dzy/miniconda3/bin/conda run --no-capture-output -n aucmr python -B scripts/check_evaluation_budget.py --draft outputs/evaluation_preparation/model-binding-9c26ac1f3e35` | 0，检查完成但 ready=false |

| 测试组 | 首轮通过/失败 | 重跑通过 | 子测试通过 | 最终退出码 | 跳过 |
| --- | --- | --- | --- | --- | --- |
| billing_runtime | 41 / 0 | 41 | 45 | 0 | 0 |
| l3 | 37 / 0 | 37 | 0 | 0 | 0 |
| l2 | 36 / 1 | 37 | 13 | 0 | 0 |
| l4 | 50 / 1 | 51 | 2 | 0 | 0 |
| legacy_and_cohort | 94 / 0 | 94 | 0 | 0 | 0 |
| 合计 | 258 / 2 | 260 | 60 | 0 | 0 |

首轮失败项：`tests/agent_l2_independent/test_evaluation.py:134` 的 `EvaluationTests.test_network_and_old_paths_blocked`，以及 `tests/agent_l4/test_sources.py:95` 的 `SourceTests.test_network_connect_and_udp_blocked`。均为创建空 socket 时抛出系统 PermissionError，尚未到达期望的离线拦截断言。获准后在沙箱外原样运行整条命令，全部通过；未改断言、跳过测试或解除程序禁网。保留首轮失败日志 `offline.log` 和重跑日志 `offline-rerun.log`，没有掩盖失败。

脚本在父进程和每个 suite 子进程内拦截 socket 的 create_connection、getaddrinfo、connect、connect_ex、sendto；测试使用隔离 mock/fixture 材料。此结论限于执行的测试路径，不将 Python 补丁宣称为针对任意外部程序的操作系统网络隔离。

## 3. 五个角色的绑定及预算缺项

下表为实际草稿内容及 aucmr 进程环境检查。模型名称是已选候选，五者 `binding_status` 全为 `NOT_BOUND`，不能称为已验收绑定。只检查密钥变量是否为非空，未打印值或完整环境。

| 角色 | 草稿模型 | 所需密钥变量 | 本次存在 | 输出参数 | 预算与能力缺项 |
| --- | --- | --- | --- | --- | --- |
| L2 | glm-4.6v | AUCMR_EVAL_ZHIPU_API_KEY | 否 | max_tokens=8192 | 共通缺项；structured_json=null，revision=NOT_VERIFIED；当前 L2 文本请求不以图像能力作为必要条件 |
| L3 A | glm-4.6v | AUCMR_EVAL_ZHIPU_API_KEY | 否 | max_tokens=8192 | 共通缺项、全部图像上界；structured_json=null |
| L3 B | doubao-seed-2-1-pro-260915 | AUCMR_EVAL_ARK_API_KEY | 否 | max_completion_tokens=8192 | 共通缺项、全部图像上界；JSON=true 仅为草稿声明，账户能力未验 |
| L3 C | qwen3.8-max-0902 | AUCMR_JUNXI_DASHSCOPE_API_KEY | 是 | max_tokens=8192 | 共通缺项、全部图像上界；北京端点/工作空间/账户访问和 JSON 实测未验 |
| L4 | glm-4.6v | AUCMR_EVAL_ZHIPU_API_KEY | 否 | max_tokens=8192 | 共通缺项、全部图像上界；structured_json=null |

共通缺项：billing.complete=false、verified_upper_bound=false、all_charges_in_token_usage=null、reasoning_in_output=null；没有可执行的 billing.tariff 和 usage_contract；没有 request_limits；per_request_upper_bound 的 input/output/reasoning/images/other 五分量全部 null。五个 constructed_text_request_check 都是 null，因 BILLING_NOT_VERIFIED 被阻止，连构造文本检查也未通过。

完整具体字段见 `draft.log`，不能把退出码 0 当成启动许可。

## 4. 计费、完整上下文与共同预算判断

**价格档位与用量：** 草稿 pricing_reference 保存了 GLM 的字符串 32K/128K 档位、Doubao 的字符串区间以及 Qwen 的参考价，但未构成新实现所需的有版本/来源的整数半开档位 tariff。K 的精确换算、区间端点与超过范围后的规则均未确认。缓存命中 JSON 路径、字段缺失时是未知还是官方保证为零、图片是否已计入 prompt_tokens，均缺少 usage_contract。各角色虽设置关闭思考参数，但这不是 reasoning_in_output 计费约定的证明。显式缓存创建/存储等独立收费维度不在当前实现支持范围；不能把这些费用假设为零。

**请求依据：** 输出参数 8192 是代码可读的配置上限，供应商对具体模型/参数是否严格执行尚未验证。文本 UTF-8 字节扩张上界、消息/内容块/请求固定开销、输入上限、上下文上限、序列化请求字节上限全部缺失。视觉角色还缺每种 detail 的图像 token 上界、图片数量、像素、原始字节和 base64 字节上限。旧 capabilities.image_token_upper_bound=null 不能替代这些字段。未猜测任何图像 token 数。

**实现已有保障：** `eval/evaluation_request_budget.py:check_request` 遍历传入完整 messages，累加每次重发的历史文本、消息/块开销及重复图片；校验 PNG 头和尺寸、图片各上限、输入+输出是否超上下文、完整 JSON 字节大小，并拒绝未纳入估算的工具/schema 参数。L3 executor 在每次循环把完整累积 messages 送入检查，再由 TaskRecovery 预留；L2/L4 的 EvaluationRuntime 在 reserve 前调用检查。它不会截断原始证据。实际任务材料及未来工具返回历史的可容纳性仍须在新准备目录验证；本轮草稿模式只构造极短文本，不能覆盖这些条件。

**预留：** 实现按输入上界可触及的最大适用档位及缓存/非缓存较高价核算，以输出参数上限预留；单独收费的 reasoning 还要求专门上限。缺失、无效用量或实际费用/用量超上界会停机并保留不确定费用记录。当前五角色预留全部 null，因此无法确认任何单次预留覆盖实际请求。verified=true 与非空 evidence 只是代码门槛；必须有可审查证据，不能用布尔值代替验证。

**共同预算/账本：** L3 草稿 campaign_ledger 与 L4 runtime.campaign_ledger 均为 `outputs/evaluation_campaign/budget.sqlite`，上限均为 CNY 500。L2 草稿本身未绑定这些运行参数，但 `eval/l2_independent/__main__.py:23-24` / execution.py:129,150 的默认值也是同一路径与 500；未来启动必须显式核对 CLI 覆盖和运行 manifest。共同正式账本目前不存在，本轮未创建。Budget 的 meta cap 检查阻止同账本静默换上限，UNKNOWN 阻止新请求；但新路径会是新账本，所以仍须在准备及启动审查中固定路径。500 是累计保护上限，不是足以完成评价的费用证明，也不是本轮付费授权。实测校准、小批次、正式阶段须累计使用预期共同账本，不能阶段间重置。

证据分层：本轮代码条件和离线行为已核查；官方公开文档未重新检索，草稿中的 URL/checked_date/DOCUMENTED 声明仅作为待核资料，未据此确认当前价格或上界；账户实际能力未检查；模型实测未运行。密钥存在不证明授权、余额、模型可用或端点正确。

## 5. 指纹与旧记录

`eval/implementation.py` 已将两个新计费/预算模块纳入 L2/L3/L4 公共实现指纹；其他运行模块也变化。当前完整指纹存于 `current-implementation.json`。既有记录检查见 `preexisting-fingerprint-records.json`：找到 66 条带 implementation/implementation_manifest 的既有记录，L2 24 条、L4 42 条，全部与当前实现不匹配（主要为此前隔离测试记录）。不能用于当前实现的恢复/导出验收，保留为历史证据。

L3 的 `unified93-preflight-v1`、`unified93-preflight-v2` 的 validation/design/input manifest 没有完整运行实现指纹，也没有模型语义已完成证据。其输入/设计检查仍可作为当时事实，但不足以认证新计费运行实现，需在新目录重做当前代码下的准备检查。扫描现有 L2/L3/L4 输出未发现命名含 acceptance 的 JSON 验收文件。任何旧 model_fixture_acceptance.runtime_hash、pilot 验收、cost_plan.projection_runtime_hash 或 task fingerprint 如存在于其他未扫描位置，也不能沿用旧运行指纹：L3 frozen_runtime_hash 已包含实现。不得在旧记录或冻结批次中替换哈希伪造通过。

## 6. 阻止启动的条件与最小下一步（仅建议，未实施）

| 分类 | 具体阻塞 | 最小处理及拟涉及文件 |
| --- | --- | --- |
| 配置/证据 | 五角色 NOT_BOUND；价格、cache/reasoning 用量契约、请求和预留上界不全 | 从原草稿派生新的 `outputs/evaluation_preparation/<新准备目录>/l2.json`、`l3.json`、`l4.json`；只补模型绑定/计费/请求限制与证据字段，不改原草稿及冻结文件 |
| 配置/环境 | 智谱和 Ark 密钥变量缺失 | 由授权操作者在实际执行环境安全配置对应变量，不写入仓库；只复查是否存在 |
| 证据 | 官方价格边界、token/消息开销、图片映射、输出和 reasoning 强制上限未确认 | 新增 `<新准备目录>/binding-evidence.md`，逐项记录供应商来源、模型/地域/计费模式与证据；资料不足继续标未确认，不先填 verified=true |
| 配置/运行 | L2 共同预算尚靠默认参数；完整历史请求与累计费用预测未建立 | 新增 `<新准备目录>/execution-plan.json` 或同等运行记录，锁定同一 ledger 和 500 上限，核验完整材料及最坏工具循环；仅准备，不创建正式账本或发请求 |
| 代码 | 本轮指定回归通过，未确认新的必修计费功能缺陷 | 暂无计费代码修改建议；若后续官方证据表明存在未支持独立收费或参数语义，先针对 `eval/evaluation_billing.py` / `eval/evaluation_request_budget.py` 报告差距再批准修复，不能用配置绕过 |
| 仓库元数据 | 标准 submodule status 因 ReAct 映射缺失失败 | 另案核对 `.gitmodules` 与 gitlink 的来源和预期映射；必要时最小补映射，本轮不修改，也不更新 vendor |
| 验收记录 | 实现指纹改变，旧检查不足以认证当前实现 | 新目录生成当前实现的离线预检查与验收记录，保留旧记录；不修改旧 manifest/acceptance 哈希 |
| 必须另行授权后实测 | 账户模型访问、JSON 输出兼容、图像使用/用量返回、计费字段及真实输出限制、语义 fixtures 未验证 | 总控批准必要的有限模型测试范围、费用额度和共同账本后再执行；模型 fixtures 通过后再申请/开展小批次。当前 live_authorized=false 保持不变 |

小批次启动前需要完成上述绑定证据和环境条件、当前实现预检、正式授权及必要模型 fixtures。小批次后的技术完成度、分歧与告警分析以及基于真实账本的费用投影，是进一步进入正式实验的条件，不能声称本轮已经验证。

此报告不改变评价指标、提示词、角色、目标用例、方法集合或随机种子；L0 未涉及。实际模型调用次数 0。冻结批次、原始生成输出、原草稿、既有评价记录和既有费用账本均未改变。
