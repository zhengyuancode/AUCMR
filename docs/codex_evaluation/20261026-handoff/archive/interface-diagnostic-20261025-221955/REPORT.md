# 最小真实接口测试结果

本轮已真实发送 **1次** HTTPS模型请求。GLM文本返回HTTP 200，但输出带Markdown代码围栏，原始content不满足严格JSON解析。按照用户“格式失败立即停止”的要求，全局停止，剩余5次未发送。未重试、未换模型、未执行图片调用、语义校准、小批次或正式评价。

## 结果表

| 模型 | 文本请求 | 图片请求 | 原始content为可解析JSON | 返回usage | 费用 |
| --- | --- | --- | --- | --- | --- |
| glm-4.6v | HTTP 200、模型标识一致、finish_reason=stop；格式失败 | 未发送，触发全局停止 | 否；代码围栏内对象内容正确，但未去围栏放行 | prompt_tokens=24，completion_tokens=11，total_tokens=35；prompt_tokens_details.cached_tokens=5；completion_tokens_details.reasoning_tokens=0 | 保守估算0.000114元，非实际账单 |
| doubao-seed-2-1-pro-260915 | 未发送 | 未发送 | 未观测 | 无 | 无新增调用费用；没有伪造usage=0 |
| qwen3.8-max-0902 | 未发送 | 未发送 | 未观测 | 无 | 无新增调用费用；没有伪造usage=0 |

GLM返回内容原样为：

~~~~text
```json
{"status": "ok"}
```
~~~~

完整脱敏响应与原始usage保存在 `live-records/live-records-1-text/response.json`；发送payload在同目录 `request.json`，判断在 `assessment.json`，全局停止状态在 `live-records/state.json`。供应商request_id为 `20261025222447d710e48b304c4b1c`。调用时间在账本预留记录及供应商created字段中保留。

## 环境、执行与保护

- 密钥加载后，三个指定变量在实际auc​​mr子进程均非空。未查看、复制或输出任何密钥值。原先不可见问题可通过在同一启动链加载环境文件解决；不需要用户重新设置密钥。
- HEAD仍为 `50f3c594545d407db97e8d1574ab0aad4f2616f4`。未修改现有评价代码、模型选择、正式配置、BOUND或verified状态。
- 独立诊断入口是本目录 `diagnostic.py`，配置为 `diagnostic-config.json`，模式固定为 `FIXED_INTERFACE_DIAGNOSTIC_ONLY`。配置有SHA256固定校验；没有通用目标/批次输入参数，不会调用正式评价入口。
- 所有请求串行、无SDK重试、无HTTP跳转、无环境代理；GLM的max_tokens=256、thinking.type=disabled。其他模型仅准备相同输出上限与原有关闭思考参数，未发送。请求无联网搜索、原生工具或显式缓存服务。
- 复用原有脱敏函数、拒绝跳转HTTP处理器及共同Budget账本组件。HTTP调用前持久化请求与预留，再持久化dispatch意图。响应先保存再解析。任何已有run状态都禁止再次发送，包括进程在发送边界被中断的情况。
- 原始保护基线 **10010个既有文件SHA256均不变，无删除**。他人的已有改动保持不变，唯一新增Git状态行是共同账本目录。新准备目录原已包含在untracked父目录下。详见 `protection-result.json`。

## 费用记账

此前共同路径 `outputs/evaluation_campaign/budget.sqlite` 尚不存在。本轮按“计入共同500元预算”的授权创建，并设为live模式、cap=500元；未修改其他既有账本。

本次调用预留0.50元，属于诊断管理估算，**不是已验证单请求费用上界**。采用已核实GLM官方价格较高档输入2元/百万、输出6元/百万，不使用缓存折扣：

`24 × 2 / 1000000 + 11 × 6 / 1000000 = 0.000114元`

依据：[智谱官方价格](https://docs.bigmodel.cn/cn/guide/start/pricing)。模型回报5个缓存token，但估算按全部24个prompt token未缓存计费。此数是保守token费用估算，尚未核对平台实际账单，不代表已解决所有供应商计费契约问题。

共同账本reservations中记录SETTLED、accounted值114微元；这是为保守占用共同预算而结算的估算，不是供应商账单实付值。额外 `diagnostic_accounting` 表明确标注 `CONSERVATIVE_UNCACHED_TOKEN_ESTIMATE_NOT_INVOICE` 和证据路径，避免误读原组件列名actual。账本快照在 `campaign-ledger-snapshot.json`。本轮3元为管理停止阈值，不是供应商保证的账单封顶。

## 必要离线验证与实际命令

在仓库根目录运行下列单行命令。离线测试退出0：4项测试，其中失败即停测试覆盖5个子场景（网络异常、缺usage、格式错误、HTTP认证错误、超预留费用），无跳过。验证未授权零调用、最多六次、串行、无自动重试、密钥脱敏、请求/响应留存、已有状态和中断状态不重发。测试注入mock并禁网，模拟结果只在offline-tests.log内，不计为真实响应。未重跑260项完整回归。

`source "$HOME/.config/aucmr/evaluation.env" && /home/dzy/miniconda3/bin/conda run --no-capture-output -n aucmr python -B outputs/evaluation_preparation/interface-diagnostic-20261025-221955/diagnostic.py --offline-tests > outputs/evaluation_preparation/interface-diagnostic-20261025-221955/offline-tests.log 2>&1`

真实执行命令退出0，业务状态为STOPPED（退出0只表示入口正常完成停机处理，不表示模型测试通过）。没有第二次执行真实调用命令。

`source "$HOME/.config/aucmr/evaluation.env" && /home/dzy/miniconda3/bin/conda run --no-capture-output -n aucmr python -B outputs/evaluation_preparation/interface-diagnostic-20261025-221955/diagnostic.py --authorized-fixed-six > outputs/evaluation_preparation/interface-diagnostic-20261025-221955/live-execution.log 2>&1`

## 对现有配置的最小调整建议（未实施）

1. **执行环境**：后续实际执行命令保留同一命令链的source与conda启动。本次证实三个变量均可见。
2. **GLM JSON约束**：当前只靠提示要求JSON会返回代码围栏；本次请求未设置response_format。下一轮应先核实该具体GLM模型是否支持JSON对象约束，若支持，在新的诊断配置中增加该参数验证；不要凭本次HTTP 200直接设置structured_json或BOUND通过。正式L2/L4公共runtime也严格解析JSON（eval/evaluation_runtime.py:263），因此该现象对正式配置有实际意义。若需修改提示明确禁止围栏，应仅限新的诊断提示，正式评价提示变更另行审核。本轮不剥离围栏、不重试。
3. **GLM用量证据**：可以把本次实际观察到的缓存路径 `usage.prompt_tokens_details.cached_tokens`、推理路径 `usage.completion_tokens_details.reasoning_tokens` 和reasoning=0写入后续绑定证据。单次字段存在不能证明字段缺失的含义；文本观测也不能证明图片用量归属或全局预算上界。
4. **Doubao/Qwen**：本轮未调用，没有依据提出新的模型参数修改，也不能据此判断账户不可用。

图片选用此前144 DPI来源PNG `SRC-16a7c81357d8-p1-144.png`。发送前已人工查看，其上方标题确为 **Qheadache, Version 1.0**。预期答案未写入模型提示；该预期只用于离线对照。但图片请求尚未发送，三个模型图片能力均记为未观测。

本轮结果证明GLM账户/端点可达、指定模型返回、观测到的关闭思考行为和usage结构，同时发现严格JSON格式失败；不证明语义评价有效。本轮已按停止条件结束，等待总控决定下一次有限诊断范围，不启动两个用例小试或93个用例评价。
