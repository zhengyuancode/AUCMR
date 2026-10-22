# 新主实验的评价准备

本次只开放文件驱动的批次输入。生成程序、生成提示、评价提示、指标和评分流程不变。
用例数量来自主实验配置，不固定为 93。文档来自同一配置的 text_pipeline.documents，结果目录来自 paths.output。
每次改变目标清单或已冻结结果，应建立新评价批次，不覆盖旧批次。

## 生成期间

在本机或服务器仓库根目录准备草稿。下面的文件名对应当前主实验，后续可以换成其他配置。

```bash
python -B scripts/prepare_evaluation_batch.py \
  --generation-config experiment.junxi.unified93.json \
  --destination outputs/evaluation_preparation/unified93-draft \
  --models-file config/evaluation_models.domestic.json --draft
```

工具只读原文档和生成结果，生成 batch.json、l2.json、l3.json、l4.json 和 preparation.json。
草稿保留缺失文件，不拿旧结果补齐，不可用于 L3 正式评价。本机生成的结果哈希不代替服务器最终结果哈希。
配置文件不保存主实验的模型密钥，仅记录主配置路径和哈希。

## 国产评价模型

| 角色 | 模型 | 说明 |
| --- | --- | --- |
| L2 独立一致性、L4 | glm-4.6v | 智谱官方，独立于当前 DeepSeek/Qwen 生成模型 |
| L3 A | glm-4.6v | 来源场景建立、独立内容及行为判定 |
| L3 B | kimi-k2.6 | 月之暗面官方，独立核验与判定 |
| L3 C | qwen3.8-max | 百炼，盲评后复核 |

官方能力资料于 2026-10-22 核对。

- GLM 图像和多图输入、思考模式：https://docs.bigmodel.cn/cn/guide/models/vlm/glm-4.6v
- Kimi 图像输入和非思考模式：https://platform.kimi.ai/docs/guide/kimi-k2-6-quickstart
- Kimi 参数约束：https://platform.kimi.ai/docs/api/models-overview
- Qwen 视觉接口：https://help.aliyun.com/zh/model-studio/vision

这些是模型选型和配置模板，不是已成功调用的记录。A/B 使用不同模型家族。C 与视觉生成模型同属 Qwen 家族，论文不得声称全部裁判与生成模型在厂商和家族上完全独立。L0、L1、原 R1–R7 的模型设置保持原有配置。

启动前复制模型配置为服务器本地文件，填写实际账户的 API 端点、密钥环境变量、费用和请求上限。模板不包含密钥。Kimi 境内与国际账户端点应按实际账户确认；Qwen 的 base_url 留空，需要从百炼账户确认地域及业务空间。不能仅因模型文档公开就把真实接口兼容性记为已验证。

Kimi 非思考模式不强行设置 temperature=0。官方约束为固定 0.6，模板省略该参数。所有角色明确请求非思考模式，保留现有输出长度上限。费用及图像 token 上界待账户确认，因此模板保持 NOT_BOUND 和禁止真实调用。

## 生成完成后

先确认正式方法的生成进程已结束。新建最终批次，不能把草稿直接改名充当冻结结果。

```bash
python -B scripts/prepare_evaluation_batch.py \
  --generation-config experiment.junxi.unified93.json \
  --destination config/evaluation_batches/unified93-v1 \
  --models-file config/evaluation_models.domestic.json
```

若某些生成任务最终失败且没有结果文件，确认日志后可显式加 --allow-missing-results。
工具仍保留全部目标与方法组合，缺失项不会被删除或变成正确结果。
现有格式的拒答、空输出会保留，不要求把它们重跑成成功结果。

最终批次读取实际文件并记录哈希。L2、L3、L4对应同一组文件，之后文件变化会拒绝继续使用原批次。
L3 不再从旧两份文档和旧 18 个目标读取新实验。

在新生成的三个评价配置中完成模型绑定。L2、L4 设置 live_authorized；L3 设置 budgets.live_authorized。
L4 还需将 data_class 设置为 REAL_AGENT_EVALUATION，并填写 judge.version。
所有真实运行配置首次调用前固定，后续不修改它们来恢复原运行。
具体模型绑定字段和共同账本要求沿用 EVALUATION_SERVER_RUNBOOK.md，不另设评价流程。

接着执行无模型调用的结构检查。

```bash
python -B -m eval.l2_independent prepare-inputs --config config/evaluation_batches/unified93-v1/l2.json
python -B -m eval.agent_evaluation inventory --config config/evaluation_batches/unified93-v1/l3.json --run-dir outputs/agent_evaluation/unified93-v1
python -B -m eval.agent_evaluation validate --run-dir outputs/agent_evaluation/unified93-v1 --offline
python -B -m eval.l4_agent inventory --config config/evaluation_batches/unified93-v1/l4.json
```

L2 prepare-inputs 返回新的 config.bound.json，并将 live_authorized 重置为 false。
确认这份派生配置后开启授权，L2 的 inventory/run 使用它，不继续使用未绑定的 l2.json。
运行命令中的样例批次名应随实际配置调整，数量没有特殊限制。

完成原有固定样例和小试后，按 EVALUATION_SERVER_RUNBOOK.md 执行真实评价与导出。
原 L1 与规则 L2 继续使用 run.py evaluate --config 对应的主实验配置。
该入口不自动运行新增的独立 L2、L3、L4，它们需要各自的独立入口。
L3 的场景建立按既定流程先于输出打分，不从 AUCMR 生成内容构造来源答案。

## L0、消融与论文口径

L0 是独立 MMLongBench-Doc 实验。本轮未找到其完整答案评分入口，不能把检索检查脚本当作 L0 评分器，也不阻塞本批 Detail-UC 评价。
消融保持现有单独配置和评价入口。本工具只准备主比较结果，不把随机排列或消融变体冒充七种主比较方法。

L1 同时检查引用支持与槽位适配。L3 不报告全文需求覆盖率，未决上下界不是置信区间。
L4 空槽记零是既定计分约定，PRC 是 Agent 辅助预估，不是从业者评审或真实下游使用效果。

## 离线验证

```bash
python -B -m unittest discover -s tests -p test_evaluation_batch.py -v
python -B -m unittest discover -s tests/agent_evaluation -v
python -B -m unittest discover -s tests/agent_l4 -v
```

服务器如缺少依赖，使用独立评价环境安装 requirements-evaluation.txt。不要为此修改正在运行的生成环境。
本轮未调用模型、未连接服务器、未修改任何真实生成结果。
