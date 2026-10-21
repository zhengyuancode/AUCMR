# 交给评价代码 Agent 的完整任务

请实现 AUCMR_L3_2.0.0。先读 README、PROTOCOL、CONTRACTS、PROMPTS、config、cohort、records.schema、fixtures、ACCEPTANCE、PAPER_EXPORT。本设计替代旧 design_v1 的 L3；不要实现全文需求穷举，也不要恢复 RCR。

## 范围与目录

唯一仓库 D:/Research/02_Code/AUCMR/AUCMR。你不是唯一操作代码的 Agent，不能回退其他人的修改。先读当前仓库指令和 git diff，再确定已有改动。只拥有以下新增评价模块及必要测试/配置：

    eval/agent_evaluation/
      __init__.py
      __main__.py
      agents/scene_builder.py
      agents/source_verifier.py
      agents/content_auditor.py
      agents/behavior_checker.py
      agents/reviewer.py
      prompts/
      schemas.py
      adapters.py
      source_access.py
      orchestrator.py
      aggregate.py
      export.py
    config/agent_evaluation_l3.json
    tests/agent_evaluation/

模块按需要合并，但 Agent 实现必须在 agents/ 下，提示在 prompts/ 下；模型 A/B 是绑定参数，不复制两套相同角色代码。已有 crest/agents.py 是生成角色，不搬迁或混入新评价职责。

采用仓库的 Python、类型标注、Path、配置与日志习惯。先静态核对 config/settings.py:ModelEndpoint/chat_completion 与 services/model_service.py 的能力；能保留供应方特定字段、图像、工具、usage和错误信息时复用。现有封装不能满足裁判记录时，在本模块新增窄适配器，不为兼容评价而改变生成服务行为。避免导入会初始化模型/索引的模块。

不改生成算法、外部基线、原PDF、原输出或论文。新结果写仓库 outputs/agent_evaluation/<run_id>；inventory明确排除此目录，不能重新将评价输出当生成制品。credentials只从既有环境/受支持配置读取，不写进记录或日志。

## 实现顺序

1. 只读适配所有当前七方法格式，核验目标、actor、最终claims与结构状态，建立精确输入manifest及去身份映射。
2. 实现项目白名单来源访问、文本搜索、PDF页图及放大、请求预算和访问账本。默认不调用生成器检索，不开放任意文件或网络。
3. 实现全部记录Schema与跨记录验证、来源提案/复核/冻结、版本传播、按目标的状态机。
4. 实现角色提示装配及模型传输；参数绑定和价格声明独立保存。先用mock完成调度、重试、费用预留、断点续跑，防止成功任务被重跑计费。
5. 实现逐断言A/B评价、行为checkpoint评价、C盲判后裁决、一致项抽检与稳定性。所有语义来自Agent，代码不能做关键词匹配或补造标签。
6. 实现确定性统计、共同非空集合、未决界限、完整场景聚合、状态转移表和论文TeX/CSV导出。
7. 执行O01–O24离线验收，特别测试空输出、部分错误、未决、预算中断、两个方法版本不一致、错误参照升级与跨方法重评。
8. 用当前候选输入做只读兼容检查和费用工作量估算，不把这些称为模型或正式实验通过。

本任务书本身不授权收费执行。若用户在代码对话明确要求启动小试/正式运行，按该授权执行已确定阶段，不重复询问已授权事项；没有这种授权时停在可运行实现和离线验证完成。不得因没有API密钥而伪造模型结果。

## 必须实现的CLI

以下是接口合同，目前尚未实现，不能称为已可运行命令。实现者完成后把它们写入实际runbook，并用离线命令证明入口可用。

    python -B -m eval.agent_evaluation inventory --config config/agent_evaluation_l3.json --run-dir outputs/agent_evaluation/RUN_ID
    python -B -m eval.agent_evaluation validate --run-dir outputs/agent_evaluation/RUN_ID --offline
    python -B -m eval.agent_evaluation fixtures --run-dir outputs/agent_evaluation/RUN_ID --transport mock
    python -B -m eval.agent_evaluation plan-cost --run-dir outputs/agent_evaluation/RUN_ID

获得模型运行授权、绑定完成后：

    python -B -m eval.agent_evaluation fixtures --run-dir outputs/agent_evaluation/RUN_ID --transport live
    python -B -m eval.agent_evaluation run --run-dir outputs/agent_evaluation/RUN_ID --phase pilot --transport live --resume
    python -B -m eval.agent_evaluation plan-cost --run-dir outputs/agent_evaluation/RUN_ID --use-pilot-ledger
    python -B -m eval.agent_evaluation run --run-dir outputs/agent_evaluation/RUN_ID --phase formal --transport live --resume
    python -B -m eval.agent_evaluation export --run-dir outputs/agent_evaluation/RUN_ID --view RECONCILED --paper

run内部按来源建立、核验、冻结、内容/行为评价、复核、聚合顺序执行；不得要求用户手工编造场景JSON。允许明确的技术暂停与状态恢复。formal必须核对冻结的全体主目标，不能只是重跑pilot。

## 完成标准

提交代码、实际CLI、O01–O24结果、输入兼容报告、模型/价格绑定状态、精确未完成事项、研究结果与测试fixture隔离证明。没有真实模型调用时，semantic_status=NOT_RUN，不能交付填了分数的论文表。

不得只交一个仍要求用户决定分母、场景、判定标签、失败口径和统计方法的框架；这些已由设计固定。如果实现发现互相矛盾的条款，指出具体位置并先完成不受影响部分，不能为了方便悄悄改实验定义。
