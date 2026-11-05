# 独立参照与匿名Agent评价

## 正式评价身份

本实验使用真实模型调用模拟人工逐项审阅，不是人工参与实验。模型角色是独立会话和职责分工，并非三个独立厂商，也不保证统计独立。无 human_entered、专家人数或真人通过结论。

| 职责 | 模型家族/实际返回名 | 上下文边界 |
|---|---|---|
| 来源构建 | DeepSeek / deepseek-flash | 共同任务与需求，无产物 |
| 独立来源首稿、裁决者盲读、复核与来源裁决 | Qwen / qwen3.7-plus | 先独立读源，再按角色获得已保存来源意见 |
| 两目标最终范围解释 | Qwen与DeepSeek独立意见；Qwen最终裁决 | 共同来源及来源参照，无产物/评分 |
| 独立评分一 | Qwen / qwen3.7-plus | 单份匿名产物、源、冻结参照 |
| 独立评分二 | DeepSeek / deepseek-flash | 同上，不互见首判 |
| 断言盲态对齐 | Qwen / qwen3.7-plus | 同一产物两套去判定标签单元及源 |
| 最终评分裁决 | Qwen / qwen3.7-plus | 同一产物、源、参照、首判、对齐及审阅计划 |
| 稳定性重复 | DeepSeek / deepseek-flash | 固定样本，新独立会话，不见首判 |

最终采用记录的实际参数见 [model_and_role_config.csv](tables/model_and_role_config.csv)：一般来源/评分/对齐请求设置max_tokens=32768，两个目标的最终来源裁决及C重评为16384；这是输出token上限，不能写成模型上下文长度。评价 temperature=0，Qwen enable_thinking=false，DeepSeek thinking disabled，返回JSON object。不能用一个参数值笼统覆盖全部角色。表中C与K/R分量可能共享同一次调用，行数不等于独立请求数。生成阶段参数另见04文档。请求模型deepseek-v4-flash与响应标识deepseek-flash分别记录。

## 来源参照流程

构建角色与独立复核角色分别从共同来源列出需求义务，复核者先保存自己的首稿，再查看构建稿。裁决角色也先盲读来源，再取得多份草稿和复核意见。对来源支持、目标范围、条件模态、重复和遗漏进行审阅，给最终义务稳定ID、类别、引用、适用范围及原子化依据。正式评分前锁定相应目标的共同参照；同目标两份产物使用相同版本。源输入只包含已隔离的独立需求，不能访问原完整PDF、B或生成资料。

最终参照有295条CONFIRMED义务和4条UNRESOLVED义务。前者进入C分母；后者保留文字和来源，不计零覆盖也不使整例失效。逐目标分母范围2–46，全部目标N>0。范围外/重复候选的处置理由可以在原参照记录中追踪，不通过把条目名称重复列入分母增加权重。该参照是Agent形成的操作性标注，不是PURE官方gold。

## 最终采用的两项目标解释

- **T003 / VUB / Display the full text of a publication**：采用来源所述内容访问的较广解释，纳入全文保存/访问、有关下载权限及导出格式能力；排除上传编辑等修改行为。最终8条明确义务。不能把其结果改写成仅评“点击全文按钮”的窄任务。
- **T009 / Inventory / Modify Use Case**：将独立要求中的Editing Assets与Modifying assets解释为同一资产修改功能范围；包含字段不可修改ID、批量能力和授权条件。授权层级作为行为适用条件，而不是要求再次识别所有角色；不要求写出全部无关权限目录。最终14条明确义务。

这两目标的范围解释在评分后作来源独立的最终裁决，新参照冻结后，对双方四份产物统一重新给出两份独立C判断与最终裁决。最终决定选择有依据的解释，不要求意见一致；其他27目标及全部K/R判断不变。论文应简洁披露这一时间顺序和解释依赖，不需叙述排障过程。对应最终解释与异议见本包 `materials/references/T003.json` 和 `T009.json`。

## 输入隔离与匿名化

API没有文件、shell、浏览器或搜索工具。宿主以角色白名单发送JSON，源码中检查字段和身份信息；角色不能自主读取仓库。独立评分不接收方法标签、另一候选稿、生成证据卡、内部验证、修复日志或另一首判。来源角色不能接收产物。数据中的指令文本视为待评材料，不可扩张访问范围。

两名首判以固定种子20261101派生的各自乱序、独立会话逐份阅读；同一目标两种方法同轮按相同规则评价。随机种子控制队列、抽样与统计，不代表API支持确定性seed。此写作包是解盲后的组织者材料，不能直接用于执行这些角色。

## 逐项判断和裁决

每个CONFIRMED义务提交覆盖值、源定位、相应产物引文及理由；每个行为单元提交原文逐字映射、S/X/U/Q判定、重复关系与理由。源与产物引句可以用精确文字或发放的行目录定位；排版规范化允许追溯到原字符，不能用语义近似伪造引文。定位成功只证明引用位置存在，不证明判断正确。

两套单元ID不直接对齐。盲态对齐角色不见标签，判 equivalent、split_merge 或 unmatched；只有可用的一对一等价、去重断言进入nominal κ。最终仍有27个保守登记但未经语义对齐的单元，不当作等价断言计一致性，数量另报。

裁决审阅所有58份产物的覆盖、断言/原子化和冗余分歧，并按项目分层抽样检查一致项。每项目抽ceil(10%匿名产物)、至少一份，固定10份；全量审阅不替代这一预定抽样标记。首判与裁决并列，不互相覆写。最终采用的普通解释分歧由有理由的裁决解决，而非强制要求所有角色同意；来源事实真正无法支持的条目仍单列。

## 稳定性

DeepSeek对固定10份样本在新会话中重复评分，不看首判，不替换原得分。该样本每项目一份，并非全部29对的重新实验；重复评分不是额外项目或独立人工评者。重复断言未另作盲态对齐，不报告其断言κ。


## 文件依据（仓库相对路径）

- `experiments/l5/pure_human_20261101/agent_evaluation_v1/PROTOCOL.md`
- `experiments/l5/pure_human_20261101/agent_evaluation_v1/revision_v5/config.json`
- `experiments/l5/pure_human_20261101/agent_evaluation_v1/revision_v5/isolation.py`
- `experiments/l5/pure_human_20261101/agent_evaluation_v1/scope_adjudication_v1/continuation_v2/isolation.py`
- `experiments/l5/pure_human_20261101/agent_evaluation_v1/scope_adjudication_v1/continuation_v2/config.json`
- `experiments/l5/pure_human_20261101/agent_evaluation_v1/scope_adjudication_v1/continuation_v2/reference_freeze.json`
- `experiments/l5/pure_human_20261101/agent_evaluation_v1/scope_adjudication_v1/continuation_v2/reference_changes.json`
- `experiments/l5/pure_human_20261101/agent_evaluation_v1/scope_adjudication_v1/continuation_v2/composition_provenance.json`
