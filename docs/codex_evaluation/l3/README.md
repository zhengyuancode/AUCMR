# L3 完成结果与复核入口

2026-11-05 完成：54 个 PURE 目标 × 7 种方法，共 378/378 项，0 项技术失败。协议为 `AUCMR_L3_PURE_COVERAGE_3.0.0`，执行入口为 `eval.l3_reference.batch` 和 `eval.l3_coverage`。历史 `eval.agent_evaluation` 的 L3 2.0 入口不用于本次结果。

**统一以原冻结输出作为论文主实验输入，三份重生成结果仅作为补充实验保留。** 本次 L3 的 378 份输入均来自原冻结批次；输入路径、哈希、参照版本及需求状态见 [输入索引](../../../outputs/agent_evaluation/l3-coverage-pure54-20261104-v1/publication/inputs.json)。L2 的主口径为 646 项完成、0 项技术失败、5 项无可评价内容，七方法共同有效用例为 89；重生成的 L2 结果及原 649 项、91 例口径属于补充结果，不进入主表，也不与原输入的 L1、L4 分数直接配对。

正式结果见 [results.json](../../../outputs/agent_evaluation/l3-coverage-pure54-20261104-v1/results.json)。每种方法的固定分母为 1,039 个需求，其中 901 个 `CONFIRMED`、138 个 `REFERENCE_UNRESOLVED`；后者统一保留在分母，`PARTIAL` 不计半分。主汇总为四份文档等权的宏平均，同时保留微平均。参照由来源约束的 Agent 构建，`human_validation=NOT_RUN`、`official_gold=false`，不得称为人工验证或 PURE 官方金标准。

原始正式结果、复核/稳定性抽样、恢复授权、7 份实现修订清单和最终独立核验记录按原字节保存。`publication/label-index.json` 只导出已保存判断的标签和来源哈希，供独立复算；`publication/configurations.json` 仅将原配置中指向本仓库的绝对路径转为仓库相对路径，并记录原文件哈希和转换字段。凭据仅保留环境变量名，不包含值。

本次提交的覆盖率实现与最后一份执行修订 `2fb7b24b8fecef6044596c6d99f734ad38d9bca7e8bf2deca15ce90fe88a19ec` 完全一致。较早阶段使用过其他修订，运行未记录每条响应与修订的一一对应关系；不能把提交 HEAD 当作所有调用的生成版本，也不能声称所有历史响应由同一实现产生。15 个任务采用已记录的技术恢复，215 个初始判断位置均进入 C 的盲审和终审；历史失败、原响应及其标签仍保留，不能改写为最初即成功。参照批次沿用两份已批准的试点参照，再完成其余 52 份；此前验收规则在该批次中作为 advisory 使用，未宣称额外人工验收。

评分阶段记录 3,102 次调用，已知账本金额为 ¥214.106696，另有 1 笔历史 `UNPRICED` 调用，金额保持 `null`。这不是整个 L3 项目的费用总额，也不是供应商发票金额；早期试点、参照验收、最终参照批次分别列在 [费用索引](../../../outputs/agent_evaluation/l3-coverage-pure54-20261104-v1/publication/call-index.json)，不混入评分阶段金额。

在仓库根目录运行以下只读检查，不需要密钥、网络或模型调用：

```bash
python -B docs/codex_evaluation/l3/verify_release.py
# 另核对本机保留的完整档案（仅持有档案的工作区可运行）：
python -B docs/codex_evaluation/l3/verify_release.py --with-local-archive
```

实现的 150 项离线测试覆盖参照构建、来源限制、中断恢复、费用未知、评分与技术恢复。测试使用临时目录和模拟传输；其中一项冻结输入集成检查依赖本机源文档与已批准试点，普通克隆不能直接完成该项。结果包的只读复算不依赖这些原始材料。

完整档案保留在 `outputs/agent_evaluation/l3-{pure-reference-pilot-20261031-v1,reference-acceptance-20261031-v1,pure54-agent-final-20261102-v1,coverage-pure54-20261104-v1}/`，源材料保留在本目录的日期子目录，生成输入保留在索引中的原路径。PDF/XML、完整参照文本、原始响应、请求、日志、缓存、重复快照、完整本机清单及共享费用数据库不纳入普通 Git。[档案索引](../../../outputs/agent_evaluation/l3-coverage-pure54-20261104-v1/publication/archive-index.json) 给出各组数量/体积和完整本机清单的路径、SHA-256；不删除、移动或另行上传。源材料再分发授权尚未统一确认。

[2026-10-31 准备记录](20261031-pure54-v1/README.md)、[协议](20261031-pure54-v1/PROTOCOL.md)和验收规则保留为历史冻结文件；其中“尚未运行”的状态描述仅对应当时阶段。本页仅提供提交与核验入口；面向写作的 `paper-handoff/l3/` 交接包由原 L3 对话另行维护。
