# L2、L3、L4 服务器运行交付

本次补齐评价代码的实际运行入口。L2 保留原 R1–R7，另外运行独立整体一致性评价。L3 保留来源内容审计和有限场景行为检查，只修复请求中断恢复。L4 使用 Agent PCS 和辅助 PRC，撤下 PCmpS。L0、L1、生成算法、原始来源及已有生成输出未改。

本轮没有调用真实模型、运行正式评价、连接服务器或推送远程。离线测试验证程序行为，不能证明裁判准确性或 AUCMR 有优势。实际验收结果见同目录 `evaluation_release_20261021/summary.json`。

## 1. 拉取代码与离线检查

在服务器的 AUCMR 仓库根目录执行，Python 需要 3.10 或以上。发布提交应包含 L2/L4 整个包、新的共享运行器和相应测试，不能只同步单个入口文件。

```bash
git pull --ff-only
python -m pip install -r requirements-evaluation.txt
python -B -m unittest discover -s tests -p test_evaluation_runtime.py -v
python -B scripts/verify_agent_l2_independent_offline.py
python -B scripts/verify_agent_l3_offline.py --run-dir outputs/agent_evaluation/l3-server-offline-v1
python -B scripts/verify_agent_l4_offline.py
```

`l3-server-offline-v1` 应是新目录。L2/L4 验收自动产生带时间和随机后缀的目录。不要复制本机 `outputs/agent_*` 的历史验收目录来代替服务器建清单。

当前版本使用旧工业批次，18 个候选目标、7 种方法、126 份最终 JSON。其中 14 个主目标形成 98 份主比较，另 28 份属于诊断组。两份来源是 `data/jtvauctions.pdf` 和 `data/6016B-sec4-visual-excerpt.pdf`。这些输入已经由仓库跟踪，正常完整拉取不需要另外提供审核 ZIP。若服务器已有不同内容，先保留该批次，另建相应输入配置；不要覆盖输入来通过哈希检查。

`.gitattributes` 固定这三个输出目录的 JSON 检出换行，并保留冻结设计文件的原始字节。冻结的 126 份 JSON 哈希来自原 CRLF 文件，不能把 Git 默认换行转换误判为新的实验批次。本次已从暂存版本按 `core.autocrlf=false` 检出并检查，128 份输入哈希与冻结值一致，三个评价入口的结构检查通过。这是 Git 检出验证，Linux 实际执行仍未运行。

## 2. 在服务器填写独立运行配置

仓库默认禁止真实调用。模型、API 地址、价格和环境变量需要依据实际服务器账号填写。它们属于运行配置，代码不会猜测或自动替换模型。不要把 API Key 写入 JSON、日志或提交。

先建立 L2 的服务器本地输入清单。

```bash
python -B -m eval.l2_independent prepare-inputs --config config/agent_evaluation_l2_independent.json
```

命令返回 `config.bound.json`。在这份派生配置中完成模型绑定，保留其中的输入清单字段。L3、L4 分别复制原配置为独立的服务器运行配置。所有配置应在第一次真实调用前冻结，中断恢复时不修改配置。

| 配置 | 需要设置的运行字段 |
| --- | --- |
| L2 派生配置 | `live_authorized=true`，`model.binding_status=BOUND`，模型和计费绑定 |
| L3 运行配置 | `input_batch_status=CONFIRMED`，`budgets.live_authorized=true`，A/B/C 三个模型绑定，A/B 是不同模型 |
| L4 运行配置 | `data_class=REAL_AGENT_EVALUATION`，`live_authorized=true`，裁判版本、模型配置版本、图像能力和计费绑定 |

统一账本路径为 `outputs/evaluation_campaign/budget.sqlite`。L3 必须把运行配置中的 `campaign_ledger` 改为这个路径；L2/L4 命令也显式传入同一路径。500 元是全 campaign 累计上限，包括小试、失败及正式请求，不是每层各 500 元。不要更换或删除账本来获得新额度。

共享模型绑定的字段如下。L2 使用 `model_id`、`model_revision`；L4 使用 `model_id`、`config_id`、`supports_images`；L3 A/B/C 使用 `api_model_id`。如果同时填写两个模型 ID 字段，它们必须指向同一模型。

- `provider`、准确模型 ID、`binding_status=BOUND`、HTTPS `base_url`、`api_key_env`。
- `parameters`，包含明确输出上限 `max_tokens` 或 `max_completion_tokens`。L3 不超过现有配置的 8192。
- `billing.complete`、`verified_upper_bound`、`all_charges_in_token_usage`，均需真实计费依据。输入和输出价格使用 `input_cny_per_million`、`output_cny_per_million`。
- `billing.reasoning_in_output` 明确推理 token 是否已包含于输出收费。若不是，还需 `reasoning_cny_per_million`。
- `per_request_upper_bound` 包含 `input`、`output`、`reasoning`、`images`、`other` 五项人民币上界。未知费用不能填零。
- L4 需要 `supports_images=true` 和 `capabilities.images=true`。L3 还需 `capabilities.structured_json=true`、`image_token_upper_bound` 和 `binding_evidence`。这些应与所用模型实际能力一致。

这里没有增加裁判模型选择或评价指标要求。具体字段说明见 `AGENT_L2_INDEPENDENT_RUNBOOK.md`、`AGENT_L3_RUNBOOK.md`、`AGENT_L4_RUNBOOK.md`。

## 3. 真实运行命令

以下命令供服务器获得真实调用授权后使用，本次没有执行。将配置及 `RUN_DIR` 占位名称替换成实际返回路径。三层没有语义结果的先后依赖，可以分别运行。首次使用新供应方时先检查其接口和少量响应；L3 沿用已有的固定样例、小试与费用估计流程。

L2 独立一致性评价。

```bash
python -B -m eval.l2_independent inventory --config L2_BOUND_CONFIG
python -B -m eval.l2_independent run --config L2_BOUND_CONFIG --authorize-live --campaign-ledger outputs/evaluation_campaign/budget.sqlite --budget-cap-cny 500 --max-attempts 2
python -B -m eval.l2_independent run --config L2_BOUND_CONFIG --authorize-live --campaign-ledger outputs/evaluation_campaign/budget.sqlite --budget-cap-cny 500 --max-attempts 2 --resume L2_RUN_DIR
python -B -m eval.l2_independent export --run-dir L2_RUN_DIR
```

第二条启动新批次，第三条只在需要恢复时使用。原 R1–R7 仍按原入口评价；新独立结果不覆盖旧指标。

L3 来源内容与有限场景行为。

```bash
python -B -m eval.agent_evaluation inventory --config L3_BOUND_CONFIG --run-dir outputs/agent_evaluation/l3-live-v1
python -B -m eval.agent_evaluation validate --run-dir outputs/agent_evaluation/l3-live-v1 --offline
python -B -m eval.agent_evaluation fixtures --run-dir outputs/agent_evaluation/l3-live-v1 --transport live
python -B -m eval.agent_evaluation run --run-dir outputs/agent_evaluation/l3-live-v1 --phase pilot --transport live --resume
python -B -m eval.agent_evaluation plan-cost --run-dir outputs/agent_evaluation/l3-live-v1 --use-pilot-ledger
python -B -m eval.agent_evaluation run --run-dir outputs/agent_evaluation/l3-live-v1 --phase formal --transport live --resume
python -B -m eval.agent_evaluation export --run-dir outputs/agent_evaluation/l3-live-v1 --view RECONCILED --paper
```

L4 Agent PCS/PRC。

```bash
python -B -m eval.l4_agent inventory --config L4_BOUND_CONFIG
python -B -m eval.l4_agent run --config L4_BOUND_CONFIG --campaign-ledger outputs/evaluation_campaign/budget.sqlite --authorize-live
python -B -m eval.l4_agent run --resume L4_RUN_DIR --campaign-ledger outputs/evaluation_campaign/budget.sqlite --authorize-live
python -B -m eval.l4_agent validate --run L4_RUN_DIR
python -B -m eval.l4_agent export --run L4_RUN_DIR
```

L4 恢复使用原目录中的冻结配置，不同时传入新 `--config`。L4 原页图通过真正的 `image_url` 图片内容发送给模型。

## 4. 记录、恢复和结果边界

L2/L4 逐制品保存结果，L3 逐任务和逐请求保存结果。已保存的正常响应在恢复时复用，防止重复付费。原始响应、实际返回模型、用量、费用与技术失败保留。超限、未知收费或响应丢失时停止追加请求，核对供应方账单后再处理，不把未决记为正确或免费。

跨层连接使用文档、目标、方法和最终输入哈希。两份 JTV-07 早停拒答在 L2/L3 属拒答，在 L4 同时带结构缺失。这两个维度均保留，不能把各层的拒答与结构错误计数直接相加。

论文只能使用本次新运行的导出结果。L3 不恢复旧 RCR 或全面覆盖结论，有限场景不能代表全部需求。L4 PRC 是 Agent 的辅助预估，不是从业者认可、实际节时或下游成功。旧人工分数与新 Agent 分数不能混用。基线组件替换、检索预算、跨页读取、通用修订对照仍是独立实验修订任务，不作为本次三层代码发布的额外门槛。
