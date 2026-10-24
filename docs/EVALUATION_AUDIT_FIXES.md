# L1–L4 审查修复与离线验收

2026-10-24。本次在现有评价方案下修复实现问题，不改变指标定义。L0 单独处理。

## 修复范围

| 审查项 | 修复内容 | 关键验收 |
|---|---|---|
| F01 L1 响应校验 | `slot_fit` 必须为布尔值，来源与槽位理由必须为非空文本。缺字段或非法响应进入技术失败，不能当成负例。 | 缺字段、错误类型、空理由、有效负例，以及失败阻止正式汇总。 |
| F02 请求前阻断与恢复 | 共享运行器区分 `RuntimeDeferred`。L2、L4 对尚未发送的请求只保存调度事件，恢复原任务；已付费失败继续保留原记录。 | 缺环境变量、第一次请求前阻断、完成一轮后的阻断、L2 仅一次尝试额度下恢复，以及已付费响应不重复请求。 |
| F03 旧规则 L2 | 索引必须为整数且不小于 -1。拒绝空理由、组外规则及空槽上的违规位置，不再强制转换为合法索引。 | 非法字符串、布尔值、小数、空槽、规则分组。 |
| F04 L4 拒答与正文 | 拒答标志独立保留。存在合法正文时继续 PCS，拒答的 PRC 仍为空值。 | 非空正文加拒答标志、有效空槽、评分和导出状态。 |
| F05 旧表格导出 | 根据实际或指定的方法列表导出。缺失或无效结果用 `--` 和 `NOT_RUN`，不补零。规则 L2 增加有效用例数、ESC 总数和均值。 | 缺失方法、别名冲突和实际导出。 |
| F06 L3 响应留存 | 保留脱敏原始响应、用量和可知费用。异常 choices 不再丢失这些材料。只接受正常结束且有模型标识的响应。 | 构造异常 HTTP 响应、保存后结算、异常结束状态和缺模型字段。 |
| F07 实现版本冻结 | 新增 `eval/implementation.py`，固定实际评价实现及共享预算代码哈希。恢复、缓存、校准绑定和重新导出检查版本。 | 实现变化阻止旧缓存复用和重新导出。 |
| F08 输入身份与评价总体 | 记录项目、目标 ID、方法、主文件及 evidence 哈希、候选审计版本。目录或基线文件名独立绑定方法，正文及证据身份必须一致。拒绝重复身份和修订版本不匹配，登记缺失组合。正式 L1 拒绝静默回退及异常候选成员。绑定方法实际用于图像证据路由。 | 错误方法归属、重复身份、主文件与证据不匹配、候选人口静默丢项、无内嵌方法时的视觉证据路由。 |

## 保持的评价口径

- L1 保留 CitCov、来源支持与槽位适配联合判定的 NLI-G、CHG 和 ESC。断言只使用自身引用证据。候选审计与最终正文分别登记，不混为同一评价总体。
- L2 保留旧 R1–R7 检查和独立的整份用例关系检查，两者分别报告。
- L3 保留最终内容 K/D/U/F 审计、确认 CP 和有限场景行为检查。未决界限不是置信区间，不恢复旧 RCR 或全文需求覆盖率。
- L4 保留七槽 PCS 和辅助 PRC。有效空槽 PCS 为 0，无法判断或技术失败为空值。PCS 和 PRC 分别建立共同完成集合。
- 不合成跨层总分，不把多层审查同一输出当成增加独立样本量。

## 离线验收

测试在当前仓库文件的隔离副本执行。启动器禁止 socket 网络访问，模型输出由构造数据或 mock 提供。未调用真实模型、未启动生成、未运行正式评价，也未修改既有输出、配置或论文。

| 测试组 | 通过数 |
|---|---:|
| 旧 L1、规则 L2、导出、输入身份、主入口及新增审查回归 | 61 |
| 独立 L2 | 37 |
| L3 | 37 |
| L4 | 51 |
| 共享运行器及新批次准备 | 23 |
| 合计 | 209 |

另外通过 26 个 subtests。各组分开启动 Python 进程，避免旧评价模块导入污染新模块的离线导入隔离检查。

安装项目测试依赖后，可在仓库根目录分别运行以下离线测试。测试的构造输出不属于论文实验数据。

```bash
python -B -m pytest -q --import-mode=importlib -p no:cacheprovider tests/test_evaluation_audit_regressions.py tests/test_paper_evaluation.py tests/test_partial_evaluation.py tests/test_configured_evaluation_cohort.py tests/test_run_workflow.py
python -B -m pytest -q --import-mode=importlib -p no:cacheprovider tests/agent_l2_independent
python -B -m pytest -q --import-mode=importlib -p no:cacheprovider tests/agent_evaluation
python -B -m pytest -q --import-mode=importlib -p no:cacheprovider tests/agent_l4
python -B -m pytest -q --import-mode=importlib -p no:cacheprovider tests/test_evaluation_runtime.py tests/test_evaluation_batch.py
```

本次本地日志、命令、差异清单、原文件备份及写回校验保存在工作区 `research/evaluation_fixes_20261024/`。

## 运行与论文衔接

1. 修复后首次运行使用新目录。缺少当前实现哈希的旧记录不会自动升级为可恢复或可发表记录。需要复查旧结果时保留其原代码和原始文件。
2. 正式 L1 需要可识别的 `pre_constraint_draft_v1` 或 `final_semantic_verified_candidate_v2` 审计。两者含义不同，报告中保留版本，不能统一描述为所有检查之前的候选。
3. 原结果目录及文件名须符合生成器命名规则。历史自定义布局需要显式整理绑定，不能靠正文内方法名称猜测。主文件和 evidence 若带修订标识，二者必须一致。
4. 旧 `generate_latex_table_l3` 名称实际对应规则 L2，不是新 L3。该表新增两列，使用其行文本前应同步论文表头。本次未改 TeX。
5. 旧 L1/规则 L2 未纳入新三层共享费用账本，运行清单已标明范围。不得将共享账本累计值称为全部 L1–L4 成本。
6. 本次离线通过不表示供应方接口、模型语义判定、校准、小试、服务器环境或正式结果已经通过。实际运行仍遵循已有模型绑定、来源场景冻结、校准与小试安排，不能直接把 mock 分数填入论文。
