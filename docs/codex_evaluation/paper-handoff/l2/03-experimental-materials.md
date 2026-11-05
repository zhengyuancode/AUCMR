# 原冻结主实验材料与补充输入清单

## 目标集合

最终计划目标集合为93个，来自6个来源项目。每个目标包含用例名和固定主参与者，按七种方法分别评价，总计划651项。完整身份信息见 [data/cases.json](data/cases.json)；方法—输入逐项映射见 [data/inputs.json](data/inputs.json)。以下数字来自最终绑定，不从论文旧稿的表6或历史样本量推断。

| 来源项目 | 仓库内PDF路径 | 计划用例 | 主集合用例 |
| --- | --- | --- | --- |
| jtvauctions | `data/jtvauctions.pdf` | 20 | 19 |
| milstd6016b_sec4 | `data/6016B-sec4-visual-excerpt.pdf` | 19 | 19 |
| qheadache | `data/qheadache.pdf` | 8 | 8 |
| microcare | `data/microcare.pdf` | 16 | 14 |
| rlcs | `data/rlcs.pdf` | 19 | 19 |
| peazip | `data/peazip.pdf` | 11 | 10 |

其中 `milstd6016b_sec4` 使用仓库中的第4节视觉摘录 PDF，不是未裁剪的完整标准。图像目录与 PDF 对应关系见 [data/source_documents.json](data/source_documents.json)。L2裁判不读取这些PDF；列出它们是为了说明生成材料来源。

## 七种方法及统一身份

论文与结果表请使用以下名称；涉及 AUCMR 全方法的显示名可写作 AUCMR（CREST full），数据连接键仍为 `AUCMR`。

| 结果连接键 | 方法说明（仅用于识别配置） |
|---|---|
| `LVLM-Direct` | 直接图像输入的共享槽位生成配置 |
| `TextRAG-Elaborate` | 文本检索支撑的共享槽位生成配置 |
| `VDocRAG-Elaborate` | 官方VDocRetriever页面检索、槽位查询Top-5、共享生成器 |
| `ViDoRAG-Elaborate` | 视觉文档检索与代理交互的参考配置 |
| `ReAct-Elaborate` | ReAct检索与交互的参考配置 |
| `CREST w/o cross-slot check` | 去掉跨槽检查的CREST消融配置 |
| `AUCMR` | 包含跨槽处理的完整配置 |

生成材料的配置入口为 `experiment.junxi.unified93.json`，共享编排代码为 `eval/shared_orchestrator.py`，参考方法入口为 `eval/baselines/__init__.py`。本包不以L2结果推断生成算法的每个部件都有效。

## 主实验输入选择

论文主实验统一采用 `outputs/junxi_unified93_deepseek_flash_20261022/` 下的原冻结输出。主结果选择入口为 `outputs/agent_l2_rules/final-repair-20261030/result-manifest.json`，其中已经包含完成的评价器修复；不是退回修复前的技术失败状态。绑定及选择清单的哈希见 [data/selection.json](data/selection.json)。L1、L4 保持原输入和现有结果。

主结果为646项完成、0项技术失败、5项无可评价内容。七方法公平比较采用89个共同有效用例。五项无内容计入生成覆盖率缺失，违规计数保持 `null`，不补零、不为补齐数量继续生成。

## 三份重生成仅作为补充输入

三份补充输入来自一次限定范围的生成重跑，使用新目录，原始生成输出与原始评价记录均保留。只纳入获得实际正文的三份输出，没有强求七槽全满：

| 目标／方法 | 正文断言数 | 处理 |
| --- | --- | --- |
| Maintain Voucher Service Provider Records / TextRAG-Elaborate | 2 | 仅补充评价，不进入主表 |
| Review a Provider Claim Status / AUCMR | 13 | 仅补充评价，不进入主表 |
| Review a Provider Claim Status / CREST w/o cross-slot check | 13 | 仅补充评价，不进入主表 |

两个“Review a Provider Claim Status”输出各有13条正文，异常槽为空；TextRAG维护服务提供者记录有2条正文，其他5槽为空。L2按实际已有正文判断，空缺不强行补齐。新输入和原输入的精确路径与哈希见 [data/input_substitutions.json](data/input_substitutions.json)，每项明确标为 `used_in_primary=false`、`used_in_supplemental=true`。

补充输入、三份评价记录和六份响应单列在 `data/supplemental/`。历史649项完成、91共同用例的表仅为补充视图，不再称作论文主结果。这三份新输入的 L2 分数不与原输入的 L1、L4 分数直接配对；不能按相同目标ID把不同输入哈希的分数拼接。

## 五项主实验生成覆盖缺失

| 目标ID | 方法 | 用例 | 最终处理 |
|---|---|---|---|
| `UC-5378f77e90aa8b68` | TextRAG-Elaborate | Maintain Voucher Service Provider Records | 原输入无正文；不计违规零值 |
| `UC-5e1b75fbd45da207` | AUCMR | Review a Provider Claim Status | 原输入明确中止／拒答；不计违规零值 |
| `UC-5e1b75fbd45da207` | CREST w/o cross-slot check | Review a Provider Claim Status | 原输入明确中止／拒答；不计违规零值 |
| `UC-edfa7d4431250d85` | VDocRAG-Elaborate | Recover Forgotten Account Access | 无有效正文；不计违规零值 |
| `UC-d9cc94863eb2c8c0` | VDocRAG-Elaborate | Securely Delete Files and Folders | 全空；不计违规零值 |

五项涉及四个不同目标，共同集合为93−4＝89。其余方法在这些目标上的23份可用评价保留在主输入全部可用辅助表中，不进入共同89用例的主表。三份重生成成功不消除主实验的生成覆盖缺失。

## 两项 VDocRAG 的有限检索核查结论

找回账户的补充输出只把第13页的“To be included in the next draft.”填入目标和触发，未被提升为有效输入。找回账户功能说明实际在PDF第36页。PeaZip安全删除说明在PDF第30、32页，但对应Top-5请求未取到这些关键页面。

既有有限核查覆盖：JTV145页、PeaZip34页索引完整性，页面路径排序与缓存指纹，关键页码映射，以及14个已保存请求中70张图片的传递。未发现导致这两项失败的页码、索引或传递错误；解析回放与最终输出一致。因此保留为该方法在这两项上的检索／生成失败，不人工指定答案页或继续调参追求全成功。

五项主实验排除身份及原输入路径见 [data/exclusions.json](data/exclusions.json)。仅在补充视图中，三份新输入使两项目标重新具备七方法评价，因此共同集合变为91；该口径单列于 [07-supplemental-results.md](07-supplemental-results.md)。

## 文件保存与可携带性

交接包主数据保存651项原冻结输入投影（原始claims正文、主参与者与索引）、对应记录和1231份主结果选用响应。补充目录仅保存三份新输入、三份评价及六份响应；历史649/91视图可由主记录加这三项替换精确重建，无需复制整套相同记录。数据值不按方法表现筛选，只按主／补充选择清单选取。

完整生成JSON、证据卡片、PDF、原始HTTP存档、配置与账本仍位于仓库原路径，不在本包中重复复制。`data/source_files.json`列出被引用源文件的相对路径、字节大小和SHA-256。响应中的绝对工作区路径仅转换为仓库相对路径以便迁移；原始文件哈希仍指向未修改的源文件，不能用包内投影冒充原文件字节内容。
