# AUCMR适配与正式生成

## 适配后的任务接口

输入是固定的 independent requirements、use-case name 和 primary actor；七个待生成部分均为空。参与者集合属于外部任务条件，不让多个角色默认同时执行同一场景。输出保留Goal、Trigger、Preconditions、Main scenario、Alternative flow、Error handling、Postconditions等部分，允许来源不支持的部分为空。

## 实际执行流程

1. 读取并验证冻结输入JSON；把原句按行累积，在达到约160词后切成证据片段。切分不改写文字，不保证每片严格最多160词。每片保留 part、PDF page、字符偏移及原文；另有TASK-META卡承载给定名称和参与者。
2. 每个槽位的文本规划器收到固定需求卡池、给定目标和已有槽位，选择有效卡ID；不能外查或访问隐藏B。
3. 按Cockburn依赖顺序调用原作者逻辑，以上游槽位和证据为上下文生成断言，断言必须引用登记证据卡。
4. 运行语义证据核验，采用 fail-closed；不支持的内容被拒绝或留空。空字段保留质量标记，不用常识凑流程。
5. 各槽位的有限源已提供给规划器，一轮作者后以允许源池已耗尽停止；不是继续检索整个原PDF。
6. 组装后运行原R1–R7跨槽DRA检测、局部修复和复查，最多两轮；只能利用已有源卡，不能新增外部业务事实。
7. 保存逐槽作者稿、核验稿、证据卡、DRA前后结果、质量标志、结构化结果、最终A.txt及逐次请求响应。

“源池已检查”是执行器向规划器提供完整源卡的事实，不是证明模型语义上充分理解了每一条源内容。

## 保留与调整的边界

保留槽位依赖、目标/范围锚定、证据引用、原语义核验与跨槽一致性控制。原DRA函数AST、schema及基础prompt的对应关系有代码谱系记录；适配函数涉及查询、证据收集、作者/核验接口和逐槽循环。因此不能声称整个程序与原多模态配置逐字相同。

纯文本场景用全文可见的有限需求卡和逐槽选证替代视觉页面检索。跨页图像恢复、原CMAB页面检索策略没有在本实验重新测量。作者提示不再要求通常5–12步，长度以来源支持行为为准。保留目标证据不足和未解决一致性标志，但不能将这些内部标志直接当作论文C/K/R评分。

## 生成配置与完成状态

| 配置 | 实际值 |
|---|---|
| 请求模型 | deepseek-v4-flash |
| 响应模型标识 | deepseek-flash |
| 服务 | DeepSeek API |
| temperature / max_tokens | 未显式设置，沿用服务默认；不能写成温度0或完全确定性 |
| 每槽作者轮数 | 1 |
| DRA最多修订轮数 | 2 |
| 网络自动重试 | 0 |
| 既有JSON格式修复 | 最多1次，不是质量择优采样 |
| 并行目标数 | 4 |
| 正式A | 29/29，每例一条产生内容的正式轨迹 |
| 正式生成调用 | 768次，实际响应均HTTP 200 |
| token usage | prompt 2,956,662；completion 2,670,727；合计5,627,389 |

上述token数是整批服务报告用量，不等于独立需求词数。没有因评价结果不利更换B或重生成A。完整空产物在规则上允许保留，但最终58份产物均有行为单元；空槽和空产物必须区分。

生成服务调用只说明产物已实际生成，不是质量通过。模型标识是响应报告名称，不保证底层权重永不变化。参数、源哈希和提示可固定，但不声称实时服务重跑会逐字一致。


## 文件依据（仓库相对路径）

- `experiments/l5/pure_human_20261101/runtime/pipeline.py`
- `experiments/l5/pure_human_20261101/runtime/inputs.py`
- `experiments/l5/pure_human_20261101/runtime/transport.py`
- `experiments/l5/pure_human_20261101/config/run.json`
- `experiments/l5/pure_human_20261101/prompts/text_plan.txt`
- `experiments/l5/pure_human_20261101/prompts/task_contract.txt`
- `experiments/l5/pure_human_20261101/evidence/core_lineage.json`
- `experiments/l5/pure_human_20261101/runs/formal-v2-credential-recovery/delivery_audit.json`
