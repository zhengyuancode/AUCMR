# GLM JSON围栏问题：原因与最小修复方案

## 已核实原因

真实请求使用普通Chat文本输出，只用user提示要求JSON；没有response_format。GLM返回HTTP 200、finish_reason=stop、11个输出token、0个推理token，但content为完整的单个Markdown JSON代码块。对象本身是正确的 `{"status":"ok"}`。诊断入口直接json.loads整个content，因开头反引号而失败。离线重放结果见format-cause-replay.json：原始解析失败，仅移除完整外层围栏后对象符合预期。没有再次调用模型，旧失败记录未改写。

该问题同样影响正式路径：evaluation_runtime.py:263、l2_independent/evaluation.py:99、agent_evaluation/agents/executor.py:91、l4_agent/records.py:50均严格解析原始字符串。因此只改诊断入口不能解决正式评价接入。

## 官方文档结论

[对话补全参数](https://docs.bigmodel.cn/api-reference/模型-api/对话补全)明确写response_format“仅文本模型支持此字段”；[GLM-4.6V模型页](https://docs.bigmodel.cn/cn/guide/models/vlm/glm-4.6v)未提供该模型使用json_object的明确保证。不能因本次输入是文本就推断多模态模型支持此参数。因此撤回把“直接添加response_format”作为确定修复的倾向；它最多是另行授权的兼容性试验，不是本方案前置条件。

## 推荐方案：显式版本化的单围栏包装规范化

只处理格式包装，不修复对象内容：

1. 裸JSON沿用原有解析规则。
2. 允许整个非空白响应恰好由一个首行 ` ```json ` 或 ` ``` `、中间JSON文本、末行 ` ``` ` 构成。实施时按整行边界匹配，首尾行仅允许规定标签，不扫描提取花括号。
3. 去除这一对边界后，仍走各层现有严格JSON解析、对象类型和schema校验。多段代码块、块外解释、其他标签、截断、畸形JSON、非法字段等仍失败。保留L4已有重复键/非有限数拒绝逻辑，不能先用宽松解析再序列化以掩盖重复键。
4. 原始content、HTTP响应、usage和账本保持原样；另记规范化版本、是否去围栏、规范化后文本/哈希，以及raw_json_valid与normalized_schema_valid两个状态。
5. 原始格式失败记录永久保留；离线重放只标记“按候选规则可通过”，不能把此前真实测试改成原始JSON成功，也不自动把BOUND或verified改为通过。

这属于响应接入规则变更，须显式审核、版本化并对所有模型一致应用。它不更改评分指标、对象字段、证据要求或语义判定。正式验收仍须针对新实现指纹执行相关检查。

## 拟修改文件（本轮未修改）

| 文件 | 最小内容 |
| --- | --- |
| 新增eval/evaluation_response_format.py | 共用的完整单围栏识别/去包装函数；只返回文本和规范化元数据，不修复JSON |
| eval/evaluation_runtime.py | 保留原始回执；严格检查前调用规范化函数，记录元数据；明确向下游提供规范化文本 |
| eval/l2_independent/evaluation.py | 使用规范化文本进入现有schema校验，避免仍解析原始围栏字符串 |
| eval/agent_evaluation/agents/executor.py | L3接入同一规则并写入trace，保留原tools/records校验和停止条件 |
| eval/l4_agent/records.py | 规范化后仍使用原no_duplicate_pairs与非有限数校验 |
| 新诊断目录中的入口与配置 | 使用同一版本规则，分别报告原始JSON与规范化结果；保留旧目录及停止状态，不解除旧运行的禁止重发保护 |
| 新增tests/test_evaluation_response_format.py及必要接入测试 | 本次真实回执离线回放；裸JSON、完整围栏通过；额外文本、多块、错误标签、缺闭合、坏JSON、重复键/NaN按相应原规则拒绝；原始回执/usage不变；不会触发模型调用 |

不推荐只增强提示“不要Markdown”：可在后续诊断提示中补充，但不能保证每次裸JSON，也不能消除三个层的解析不一致。不推荐通用JSON repair、从解释文字里抽对象、因解析失败自动重发。

## 后续执行顺序

先离线实现/验收包装规范化，再按新的明确授权继续有限真实接口检查。已支付的GLM文本响应可离线验证规范化，不必为验证去围栏函数重复付费；其余五项真实接口/图片观测仍待执行。若要证明修改提示后原始响应就是裸JSON，需要另列一次GLM文本请求，不能由离线回放替代。调用名额、3元管理阈值、256输出上限、无重试与失败即停继续显式约束。本轮只交方案，真实调用0，正式配置和代码未改。
