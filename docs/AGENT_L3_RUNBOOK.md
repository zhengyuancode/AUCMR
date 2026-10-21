# AUCMR L3 2.0 运行说明

本模块只评价 L3 来源内容正确性与关键行为保留。生成算法、L1、L2、L4、原始 PDF、已有输出和论文没有修改。实现入口是 `eval.agent_evaluation`。设计副本位于 `eval/agent_evaluation/design`，协议为 `AUCMR_L3_2.0.0`。

## 本轮边界

本轮只执行输入结构盘点、标准 JSON Schema 校验、单元与集成测试、构造材料 mock 流程和导出格式测试。真实模型调用为 0，模型费用为 0。来源语义核验、模型构造样例测试、小试和正式实验均为 `NOT_RUN`，科学结果为 `NOT_ESTABLISHED`。

mock 的预设答案不能证明裁判能理解原文。真实制品只作结构检查和哈希登记，没有对真实断言产生 KEEP、DELETE 或行为标签。

## 文件位置

| 位置 | 用途 |
| --- | --- |
| `config/agent_evaluation_l3.json` | 独立评价配置，126 个明确输入绑定，模型保持 NOT_BOUND |
| `eval/agent_evaluation/adapters.py` | 最终断言、拒答和结构状态适配 |
| `eval/agent_evaluation/source_access.py` | 本项目原始 PDF 查询、原页图和访问限额 |
| `eval/agent_evaluation/agents/` | 参数化角色、材料隔离、请求执行和恢复 |
| `eval/agent_evaluation/references.py` | 来源核验后冻结、版本校验和统一失效 |
| `eval/agent_evaluation/budget.py` | 跨阶段共用的 SQLite 原子预算账本 |
| `eval/agent_evaluation/aggregate.py` | 共同非空子集、未决界限、场景状态、文档内配对 |
| `eval/agent_evaluation/export.py` | CSV、JSON、TeX 和带来源版本的 paper_values |
| `tests/agent_evaluation/` | O01 至 O24 及端到端验收 |
| `scripts/verify_agent_l3_offline.py` | 重现四条离线 CLI 和测试，并保存真实输出 |
| `outputs/agent_evaluation/l3-offline-20261021/` | 本机输入清单、验收证据与 mock 产物 |
| `docs/agent_l3_evidence/` | 随代码交付的紧凑验收证据和输入问题报告 |

原设计文件按原字节保留。设计 `config.json` 的 `modify_current_repo=false` 指设计阶段，本轮用户任务明确授权实现。运行配置另加 `runtime_scope` 记录此区别，没有更改设计的语义和限额。

设计中的行为抽检单位是整个场景，记录 Schema 的行为裁决单位是检查点。实现按场景抽检，再逐检查点保存裁决，不把检查点改成独立抽检单位。设计记录 Schema 没有时间字段，所以时间、请求输入、工具访问和响应保存在对应的请求及访问账本，不向原 Schema 强塞字段。

## 安装与离线验收

在 AUCMR 仓库根目录执行。服务器可以使用已有 `aucmr` 环境，但不需要加载模型。评价新增依赖与生成依赖分开登记。

```bash
python -m pip install -r requirements-evaluation.txt
```

```bash
python -B scripts/verify_agent_l3_offline.py --run-dir outputs/agent_evaluation/l3-offline-check
```

对应四个独立入口如下。

```bash
python -B -m eval.agent_evaluation inventory --config config/agent_evaluation_l3.json --run-dir outputs/agent_evaluation/l3-offline-check
```

```bash
python -B -m eval.agent_evaluation validate --run-dir outputs/agent_evaluation/l3-offline-check --offline
```

```bash
python -B -m eval.agent_evaluation fixtures --run-dir outputs/agent_evaluation/l3-offline-check --transport mock
```

```bash
python -B -m eval.agent_evaluation plan-cost --run-dir outputs/agent_evaluation/l3-offline-check
```

本机实际解释器为 `C:\Users\JunxiHu\AppData\Local\Python\pythoncore-3.14-64\python.exe`。标准校验依赖安装在当前仓库 `.local-checks/l3deps`，本机重现时把该目录加入 `PYTHONPATH`。没有使用 WindowsApps 占位程序，也没有在 AUCMR_learning 中实现或测试。

## 输入和结果状态

当前冻结候选批次只来自 `outputs/aucmr_full`、`outputs/crest_without_dra` 和 `outputs/baselines`。不读取 `results`、扩展的 61 个用例或其他历史批次。

实际盘点为 126 份，主比较 98 份，主比较最终断言 1564 条。每主评 304 个内容批次，A/B 共 608 批，尚不包含场景、复核、稳定性或工具交互。这是工作量，不是 API 请求数或费用估计。

主组有 92 份非空、6 份有效空输出。全部候选中有 2 份明确拒答早停、4 份结构异常，均在 `input_issues.json` 中保留。诊断结果不混入主比较。

每次输入登记都会重新校验文件和来源哈希。输入变化后不能覆盖原 run，必须建立新 run。已有输出的历史模型调用版本仍是 `NOT_VERIFIED`，不能用当前配置补造。

## 下一阶段的必要准备

以下是下一阶段的入口说明，本轮没有执行任何 live 命令。

1. 确认这批历史输入确实用于论文比较，在新评价配置设置 `input_batch_status` 为 `CONFIRMED`。若更换批次，必须更新明确输入绑定，不能按最新文件时间自动挑选。
2. 依据供应方正式资料补齐 A/B/C 的真实绑定。设计中的模型名称只是意图，不是已验证的实际可用版本。A/B 必须是不同模型，新任务使用独立会话。
3. 绑定需要 `provider`、`api_model_id`、`binding_status=BOUND`、`base_url`、`api_key_env`、`parameters`、`capabilities`、`billing`、`per_request_upper_bound`、`binding_evidence`。API Key 只放环境变量，不写进 JSON 或仓库。
4. `capabilities` 需要确认图片、结构 JSON 和图片 token 上界。`parameters` 必须有不超过 8192 的输出上限，不能覆盖 model、messages 或凭据字段。未支持的 temperature/seed 不设置，并在绑定证据中记明。
5. `billing` 要确认 `complete`、`verified_upper_bound`，以及输入、输出、推理是否重复计费。当前 live 计费适配支持 `all_charges_in_token_usage=true` 的完整 token 计费，包含图片在实际 usage 中的费用。若供应方有未包含在 usage 的额外收费，保持停止状态，先增加有证据的计费适配，不能填零绕过。
6. 预约上界必须包括 `input`、`output`、`reasoning`、`images`、`other` 五项人民币上界。`binding_evidence` 记录资料出处、核验时间、模型版本、参数能力和计费依据。只有得到下一阶段明确授权后才把 `budgets.live_authorized` 改为 true。

`campaign_ledger` 必须与其他评价层共用同一账本，不能每阶段换一个路径重新获得 500 元。本实现会阻止并发超限、未知计费以及预算超限。请求中断且收费未知时保持预约，不会清零继续。SQLite 遗留预约需要在确认供应方账单后单独核对，不能删除账本来恢复。

新配置保存为独立文件，例如 `config/agent_evaluation_l3.bound.json`。它不能包含密钥值。然后登记新的 live run。

```bash
python -B -m eval.agent_evaluation inventory --config config/agent_evaluation_l3.bound.json --run-dir outputs/agent_evaluation/l3-live-v1
```

```bash
python -B -m eval.agent_evaluation validate --run-dir outputs/agent_evaluation/l3-live-v1 --offline
```

下一阶段授权后，先执行真实模型构造样例。每个 A/B/C 模型都检查全部 16 项并保存首次结果。未达到固定阈值时停止，不按某方法排名反复调整。

```bash
python -B -m eval.agent_evaluation fixtures --run-dir outputs/agent_evaluation/l3-live-v1 --transport live
```

随后执行两个目标小试。命令内部依次建立来源范围与场景、独立核验、冻结，再评价，不要求用户手写真实场景答案。

```bash
python -B -m eval.agent_evaluation run --run-dir outputs/agent_evaluation/l3-live-v1 --phase pilot --transport live --resume
```

```bash
python -B -m eval.agent_evaluation plan-cost --run-dir outputs/agent_evaluation/l3-live-v1 --use-pilot-ledger
```

费用规划按任务族的小试费用高分位加 20% 余量。没有该任务族观测时，采用已核实的单任务最大请求费用上界，并显式记录。复核按最坏分歧工作量保守估算；估计不是硬封顶证明，实际每请求仍受共享账本控制。超过剩余预算时停止，不减少方法或挑容易目标。

```bash
python -B -m eval.agent_evaluation run --run-dir outputs/agent_evaluation/l3-live-v1 --phase formal --transport live --resume
```

恢复时重复相应的 `run ... --resume` 命令。成功任务复用已保存记录，不重复付费。输入、提示、模型或参照变更会拒绝静默复用。已耗尽技术重试的任务保留为失败；需要修复原因并发布明确的新版本，不能靠反复启动重抽答案。

```bash
python -B -m eval.agent_evaluation export --run-dir outputs/agent_evaluation/l3-live-v1 --view RECONCILED --paper
```

导出保留所有主目标及失败/未决状态。只有两个目标时标为 `PILOT_ONLY`，不能说正式实验完成。mock 导出只能在 `fixtures` 内，标记 `FIXTURE / NOT_RESEARCH_DATA`。正式数据不含旧 RCR、p 值、置信区间或显著性星号。

## 参照修正

`correct_reference` 接收经过来源核验的新参照、递增版本和全部方法列表。它保存旧版，并对目标的所有方法和机制变体写入失效台账。后续评分任务绑定新版本。旧汇总不能通过论文导出版本检查。

没有核验通过的核心场景时保留 `CORE_UNESTABLISHED`。有效空输出在已建立场景下是 `MISSING`，技术损坏是 `TECHNICAL_INCOMPLETE`。不把查找不到支持判成错误，不将未决界限称为置信区间。

## 需后续同步但本轮未改动的内容

- L4 中依赖完整共同参照的 PCmpS。
- 论文中的旧 RCR、旧分数、置信区间和显著性措辞。
- 原生成模型历史调用版本和批次来源的证据。
- 模型版本混用、未决可改变比较方向及裁判稳定性问题。

只有实际真实评价后才能形成研究结果。离线通过只说明本次测试覆盖的程序行为和统计规则通过，不证明评价语义有效性。
