# L1：证据落地评价的最终论文交接

整理日期：2026-11-04。本文只描述最终采用的材料、评价规则和结果，不收录未采纳的探索过程。所有以 `eval/`、`config/`、`data/`、`outputs/` 开头的路径均相对于 **AUCMR仓库根目录**；本目录内的文件采用相对链接。

## 1. 正式结果范围

本次L1评价以冻结的93个目标为共同样本，每种适用方法均纳入93份原始生成材料，合计558份。最终记录由12份小批次记录、504份全量记录和42份补齐记录组成。前两部分共516份原完整结果保持不变；42份补齐的是原材料的评价，不是重生成42个用例。

| 项目 | 最终口径 |
| --- | --- |
| 正式目标 | 93个，来自6份源PDF |
| L1适用方法 | AUCMR、CREST w/o cross-slot check、TextRAG-Elaborate、VDocRAG-Elaborate、ViDoRAG-Elaborate、ReAct-Elaborate |
| 每方法样本数 | 93份 |
| 最终材料总数 | 558份，目标—方法组合无缺项、无重复 |
| 审计断言总数 | 10,067条 |
| 有合法可解析引用的断言 | 10,065条 |
| 最终未完成的L1材料 | 0份；历史失败记录仍保留 |
| 未纳入的方法 | LVLM-Direct：既定实验中不提供稳定、可对齐的页面引用ID，L1引用指标不适用 |
| 不采用的替换材料 | `outputs/regeneration/five-empty-20261030/` 中另行生成的五份输出 |

全实验的七种方法与L1的六种适用方法不能混为一谈。651是93×7的全方法任务数，558是93×6的L1任务数。本次也不以其他评价层的共同完成子集筛选L1。

### 1.1 目标与来源

| 项目ID | 目标数 | 源PDF相对路径 | PDF页数 |
| --- | ---: | --- | ---: |
| jtvauctions | 20 | `data/jtvauctions.pdf` | 145 |
| milstd6016b_sec4 | 19 | `data/6016B-sec4-visual-excerpt.pdf` | 146 |
| qheadache | 8 | `data/qheadache.pdf` | 15 |
| microcare | 16 | `data/microcare.pdf` | 33 |
| rlcs | 19 | `data/rlcs.pdf` | 66 |
| peazip | 11 | `data/peazip.pdf` | 34 |
| 合计 | 93 | 6份PDF | 439 |

名单见[目标表](results/target-list.csv)及[data/cohort.json](data/cohort.json)。`formal_case_id`为FUC-001至FUC-093，`target_id`保留运行时稳定ID，不能靠英文名称相似度匹配记录。源页码采用PDF文件的1起始物理页号；尤其6016B截取文件的页号不能直接等同于完整标准的印刷页码。

目标名单源于 `outputs/evaluation_preparation/formal-cohort-source-review-20261029/formal-cohort.json`，ID为 `unified93-source-reviewed-20261029-v1`。这是生成和部分评价之后完成的agent来源复核，不是预注册筛选，也不是独立真人专家盲审。纳入依据是源文档是否支持目标和参与者范围，不以方法得分或完成率挑选目标。某目标被纳入不表示每种方法输出均正确或七槽都必须非空。

### 1.2 保留的五份零断言材料

| target_id | 方法 | 用例 |
| --- | --- | --- |
| UC-edfa7d4431250d85 | VDocRAG-Elaborate | Recover Forgotten Account Access |
| UC-5378f77e90aa8b68 | TextRAG-Elaborate | Maintain Voucher Service Provider Records |
| UC-5e1b75fbd45da207 | AUCMR | Review a Provider Claim Status |
| UC-5e1b75fbd45da207 | CREST w/o cross-slot check | Review a Provider Claim Status |
| UC-d9cc94863eb2c8c0 | VDocRAG-Elaborate | Securely Delete Files and Folders |

五份材料照常计入每方法93份的样本数。其审计断言数为0，不产生NLI请求；七个内容槽为空，计入ESC。单材料CitCov、NLI-G、CHG因零分母在本交接CSV中留空，不能当成0%或100%。原运行记录中的某些0.0是程序零分母占位，本包没有修改原记录。方法主表先汇总断言计数再求比例，零断言材料不贡献断言分母，但不会从材料样本或ESC中删除。

## 2. L1实际评价什么

L1衡量生成用例的断言是否给出了有效引用，以及被引用的冻结材料能否支持该断言在所在槽位中的含义。这里的评价器是模型裁判，**不是人工专家评审，也不等同于L4的专家式综合评分**。

每个模型判断单元是一条审计断言，包含完整断言文本、所在槽位，以及其合法引用材料的并集。复合断言不再被程序切成多个原子断言。裁判判断所有实质命题是否被联合支持，不要求同一张卡或同一页单独支持全部内容。无引用或引用不可用时由确定性规则记为未支持，不请求裁判。

评价七个内容槽：`goal`、`preconditions`、`trigger`、`main_flow`、`alternative_flow`、`exception_flow`、`postconditions`。`primary_actor`是给定身份信息，不计入L1断言分母，也不计入七槽ESC。

### 2.1 断言总体与保存阶段

分母来自输入JSON的 `citation_audit.pre_constraint_slots` 中各槽位的非空 `claims[].content`，不是简单从最终展示文本重新抽取，也不是只保留已被模型判对的断言。

| 方法组 | 保存的citation_audit版本 | 含义 |
| --- | --- | --- |
| AUCMR、CREST w/o cross-slot check | `final_semantic_verified_candidate_v2` | 流水线最终语义核验候选快照 |
| TextRAG、VDocRAG、ViDoRAG、ReAct | `pre_constraint_draft_v1` | 确定性引用白名单处理前的基线候选快照 |

六种方法每种93份均有相应版本。每种方法的N_all、N_cite、N_qualified共用同一审计总体。不要把这两种捕获阶段写成“所有方法完全相同阶段的最初草稿”；结果反映的是现有完整方法流水线的输出及其审计候选，不能据此单独归因于原始生成模型。ESC则读取最终 `slots`，与审计候选总体有意区分。

### 2.2 合法引用

一条断言计入N_cite需要至少一个引用同时满足：

1. 引用是字符串ID；
2. ID属于该槽审计快照记录的 `exposed_evidence_refs`；
3. ID能映射到对应证据文件中的卡；
4. 卡有非空文本，或者有可读取的本地来源图片。

存在合法引用只证明引用可定位。并不意味着证据蕴含断言，也不意味着断言位于合适的槽位。同一断言多次引用相同ID会去重；有多个合法ID时，按其联合材料判断一次。非法ID不被模糊匹配或自动换成其他页面。

### 2.3 裁判实际收到的证据

| 方法 | 冻结证据表示 | 本批次实际模态 |
| --- | --- | --- |
| AUCMR、CREST w/o cross-slot check、TextRAG-Elaborate | 证据卡文本 | `card_text` |
| VDocRAG-Elaborate、ViDoRAG-Elaborate、ReAct-Elaborate | 已引用且检索暴露的来源页图像 | `image` |

文本方法不被自动补充整页PDF图像。图片方法只附上该断言合法引用的图像，不让裁判重新搜索整份PDF。代码支持一张卡同时有文本和图像，但本批次保存的模态标签只有上述两类。来源页、卡ID和图像对应信息由请求构造程序提供；证据收集器的相关性说明只作为上下文，不是独立来源证明。

因此，L1结果应解释为“对方法所提供的冻结引用证据进行核验”。它不是同一裁判对全部PDF进行穷尽查证后的事实真值。文本抽取遗漏、卡片表述错误及视觉阅读误差都可能影响判断。不同方法的证据表示属于各自方法流程的一部分，不能声称这张表已经隔离了文本/视觉模态差异。

## 3. 指标和统计口径

令某方法的全部93份材料构成集合U。对每条审计断言c：

- V(c)=1：至少有一个合法可解析引用；
- E(c)=1：裁判给出 `entailment`；
- S(c)=1：裁判给出 `slot_fit=true`；
- Q(c)=V(c)×E(c)×S(c)：来源支持且适配所在槽位。

定义：

\[
N_{all}=\sum_{u\in U}|C_u|,\quad N_{cite}=\sum_c V(c),\quad N_{qualified}=\sum_c Q(c).
\]

\[
\mathrm{CitCov}=100\frac{N_{cite}}{N_{all}},\qquad
\mathrm{NLI\!\text{-}\!G}=100\frac{N_{qualified}}{N_{all}},\qquad
\mathrm{CHG}=100\frac{N_{cite}-N_{qualified}}{N_{cite}}.
\]

\[
\mathrm{ESC}=\frac{\sum_{u\in U}\sum_{s\in S_7}\mathbb{1}[\text{最终槽位 }s\text{ 无claim}]}{|U|}.
\]

| 指标 | 单位与解释 | 方向 |
| --- | --- | --- |
| CitCov | 有效引用覆盖率，% | 较高表示覆盖更充分，不代表正确 |
| NLI-G | 来源支持且槽位适配的断言比例，% | 较高表示本口径下落地更充分 |
| CHG | 有效引用断言中未达到上述双重条件的比例，% | 较低表示引用与合格落地之间差距较小 |
| ESC | 每份用例最终七槽中的平均空槽数，范围0–7 | 描述性指标，不设统一优劣箭头 |

**论文定义必须与实际实现一致。** 保存字段 `total_entailed_claims`、旧聚合字段 `l1_n_ent` 实际数的是N_qualified，不是仅有source entailment的数量。若论文仍保留N_ent符号，应明确把它定义为“来源蕴含且槽位适配”；不能把当前NLI-G表解读为纯NLI蕴含率。本包CSV使用 `n_qualified` 避免混淆，额外提供 `source_entailed` 和 `entailed_slot_misfit` 作为诊断计数，不替换主指标。

CHG也不是“CitCov减去NLI-G”的百分点差。只有CitCov恰为100%时两者数值才重合。CHG中的失败包括中立、矛盾和事实受支持但位置不合适，不能把整个CHG直接称为确定的幻觉比例。`neutral`也不等同于被证伪。

主表采用**微聚合**：先加总断言的分子分母，再求比例。不是先求93份用例的比例再平均，也不是七槽比例的简单平均。槽位表和项目表在各自子集使用同一计数公式。ESC按材料平均。CSV比例以百分数保存，例如76.62007624表示76.62007624%，不是0.7662；正文保留两位小数即可。表格计算中不先四舍五入各用例值。

## 4. 模型与实际执行设置

| 设置 | 实际采用值或证据边界 |
| --- | --- |
| L1文本裁判 | `qwen3.7-plus` |
| L1图像裁判 | `qwen3.7-plus` |
| 端点 | `https://dashscope.aliyuncs.com/compatible-mode/v1` |
| temperature | 0.0 |
| max_tokens | 配置为null，HTTP构造中不传该参数，不能写为256或其他固定上限 |
| 输出方式 | 非流式，系统提示要求JSON，接收后解析并校验 |
| seed | 请求没有固定seed，不宣称结果严格可重现或完全确定性 |
| 思考参数 | 通用入口发送 `chat_template_kwargs.enable_thinking=false`，但不能仅凭这个参数证明该端点实际关闭了思考 |
| 联网与外部工具 | L1请求只提供断言与冻结证据，不添加搜索或原生工具 |
| 评价单位 | 每条断言的引用并集与所属槽位，不是每份用例一次综合评分 |

原记录清单和354条补齐完整响应均指向上述裁判模型；补齐响应实际返回的model也均为 `qwen3.7-plus`。模型名是服务名称，保存记录没有不可变的供应商内部权重版本，后续同名API不保证逐字复现。

生成配置 `experiment.junxi.unified93.json` 中LLM为 `deepseek-flash`，LVLM为 `qwen3.5-plus`。这只是实际配置证据，不应在本L1交接中把所有生成阶段调用数或供应商内部版本进一步推断出来。尤其视觉生成与L1裁判同属Qwen系列，不能声称所有生成和评价完全跨模型家族独立。不要沿用论文模板中与本次记录不一致的裁判型号。

### 4.1 提示与返回协议

完整系统提示和实际用户模板保存在[data/protocol.json](data/protocol.json)，由运行manifest及代码提取，不是另拟的论文示例。

裁判返回四个字段：

```json
{"entailment":"entailment","slot_fit":true,"rationale":"...","slot_fit_rationale":"..."}
```

`entailment`只允许 `entailment`、`contradiction`、`neutral`；`slot_fit`必须为布尔值；两个理由必须为非空字符串。两项语义判断独立：真实内容放错槽可以是entailment且slot_fit=false。量词“每个实质命题都获得支持”、允许合理释义和跨卡联合支持、禁止引入未引用材料/常识等规则见完整提示。步骤编号属于组织元数据，不要求源PDF出现相同编号；内部链接正确性由独立L2处理。

七槽角色分别是：目标/成功意图、启动前必要状态、开始交互的事件、正常成功路径动作、达成相同目标的非错误分支、异常及处理、成功结束后的直接状态。不能把“事实真实”自动等同于“在任何槽位都合适”。

最终补齐阶段使用相同语义系统提示，用户请求补充单个四字段JSON、当前槽位提示，并说明“没有文本转录”不等于“图片为空”。这属于最终实际采用的请求呈现差异，已在协议快照中保留，不能把558份描述成字节级完全相同的用户提示。补齐不自动填判断，不根据分数选择重评结果。

### 4.2 保存与恢复

`eval/l1_recording.py`在派发前保存PENDING，响应到达后先保存原文，再保存解析结果。材料输入、证据、模型配置、系统提示和相关实现有身份/哈希约束；成功记录恢复时直接复用，不重发请求。技术失败不会替换成neutral或零分。

原成功记录沿用原解析器的已接受结果；补齐解析器额外要求完整单个对象、四个字段、无重复键，允许完整外层Markdown围栏。原失败响应继续保留。最终42份材料均有完整结果，本交接只读最终保存判断，不重新裁判、不修改分数或理由。

## 5. 最终方法结果

下表由[method-summary.csv](results/method-summary.csv)直接计算，每方法n=93。名称保留原运行标识，CREST w/o cross-slot check是当前消融的原始标签。

| 方法 | N_all | N_cite | N_qualified | CitCov (%) | NLI-G (%) | CHG (%) | 空槽总数 | ESC |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| AUCMR | 1,574 | 1,574 | 1,206 | 100.00 | 76.62 | 23.38 | 47 | 0.51 |
| CREST w/o cross-slot check | 1,563 | 1,563 | 1,191 | 100.00 | 76.20 | 23.80 | 47 | 0.51 |
| TextRAG-Elaborate | 1,781 | 1,781 | 1,079 | 100.00 | 60.58 | 39.42 | 51 | 0.55 |
| VDocRAG-Elaborate | 1,692 | 1,690 | 571 | 99.88 | 33.75 | 66.21 | 67 | 0.72 |
| ViDoRAG-Elaborate | 1,874 | 1,874 | 857 | 100.00 | 45.73 | 54.27 | 69 | 0.74 |
| ReAct-Elaborate | 1,583 | 1,583 | 802 | 100.00 | 50.66 | 49.34 | 89 | 0.96 |

可以据此作出的描述：六种方法的引用覆盖都接近100%，但联合考虑来源支持和槽位适配后差异较大。AUCMR在本次模型裁判口径下NLI-G为76.62%，比TextRAG高16.04个百分点；与消融的差距仅约0.42个百分点，不能据此宣称跨槽检查在L1上带来巨大改善。结果是该批次的描述性结果，没有附带统计显著性检验。

不能把低ESC机械解释为优。源文档不支持某槽时保留为空可能更合理；强行填满会引入无依据内容。五份全空材料也提醒读者，高引用率必须与空输出及空槽一起报告。

### 5.1 来源支持与槽位适配的分解

| 方法 | source entailment | 其中slot_fit=false | neutral | contradiction |
| --- | ---: | ---: | ---: | ---: |
| AUCMR | 1,289 | 83 | 282 | 3 |
| CREST w/o cross-slot check | 1,267 | 76 | 292 | 4 |
| TextRAG-Elaborate | 1,494 | 415 | 283 | 4 |
| VDocRAG-Elaborate | 911 | 340 | 762 | 19 |
| ViDoRAG-Elaborate | 1,418 | 561 | 418 | 38 |
| ReAct-Elaborate | 1,281 | 479 | 292 | 10 |

source entailment减去其中slot_fit=false即N_qualified。neutral、contradiction和source entailment三者之和等于N_all。VDoc的762条neutral中包含2条没有合法引用、由程序按规则处理的断言；其余是模型判断。不能把这些计数说成全部已人工验证的错误。

### 5.2 七槽分析

[slot-summary.csv](results/slot-summary.csv)包含6方法×7槽的42行完整计数，适合附录或绘图。下面以AUCMR示例说明口径，并非只保留该方法：

| 槽位 | 审计断言数 | N_qualified | NLI-G (%) | 最终空槽材料数 |
| --- | ---: | ---: | ---: | ---: |
| goal | 92 | 86 | 93.48 | 1 |
| preconditions | 189 | 150 | 79.37 | 3 |
| trigger | 88 | 76 | 86.36 | 5 |
| main_flow | 527 | 383 | 72.68 | 1 |
| alternative_flow | 202 | 123 | 60.89 | 18 |
| exception_flow | 214 | 170 | 79.44 | 17 |
| postconditions | 262 | 218 | 83.21 | 2 |

这里的每槽NLI-G按该槽断言求比例；整体76.62%应由七槽的N_qualified与断言数分别加总后相除，不能平均上列七个百分比。最终空槽计数来自最终输出，不能仅用审计断言数量反推。

### 5.3 项目和用例层明细

[project-summary.csv](results/project-summary.csv)有6项目×6方法的36行，用于检查是否由某一文档主导汇总。[case-method-results.csv](results/case-method-results.csv)保留全部558行，与93个目标ID及六种方法一一对应。所有材料级比例中，零分母留空。若论文另作配对统计，应按目标匹配方法并考虑同一项目内的相关性，不能将10,067条断言当作10,067个独立实验样本；本交接不自行追加显著性结论。

## 6. 实验材料、实现与追溯路径

### 6.1 必要代码

| 仓库相对路径 | 用途 |
| --- | --- |
| `eval/compute_metrics.py` | 审计断言读取、引用解析、提示词、NLI-G与ESC、方法聚合 |
| `eval/l1_recording.py` | 请求/材料持久化、哈希与恢复 |
| `eval/l1_repair.py` | 最终补齐阶段的请求呈现、严格单对象解析、复用成功记录 |
| `eval/legacy_contracts.py` | L1适用方法与审计输入约定 |
| `eval/run_experiments.py` | 方法—目标输入组合及指标执行 |
| `config/settings.py` | 模型配置及HTTP消息构造 |
| `experiment.junxi.unified93.json` | 本批生成与评价模型配置 |
| `tests/test_l1_repair.py` | 补齐呈现、解析、恢复及防重复派发的离线测试 |

文件级指纹见[data/provenance.json](data/provenance.json)。本次核对时，小批次与全量manifest中8个既定实现文件的SHA-256均与工作区对应文件一致。不能只用当前HEAD表示全部实验版本，因为仓库另有未提交工作，补齐模块也可能尚未被统一提交。当前核查HEAD为 `ef9d8988c79cc99530dae005218e33d1964dfda8`，仅供定位，运行manifest中的文件指纹才是对应结果的证据。

### 6.2 原始材料与最终结果

| 仓库相对路径 | 内容及采用范围 |
| --- | --- |
| `config/evaluation_batches/unified93-v1/batch.json` | 原冻结目标—方法绑定 |
| `outputs/junxi_unified93_deepseek_flash_20261022/` | 原生成输出根目录；含 `aucmr_full/`、`crest_without_dra/`、`baselines/` |
| `outputs/evaluation_preparation/pilot-l1-l4-v306-20261028/l1-workspace-r1/l1_checkpoints/` | 12份最终小批次材料及其请求记录 |
| `outputs/evaluation_preparation/full93-v306-20261028/l1-workspace/l1_checkpoints/` | 504份最终全量材料及原请求记录 |
| `outputs/evaluation_preparation/l1-repair-20261030/checkpoints/` | 42份原材料的补齐评价和新接受请求 |
| `outputs/evaluation_preparation/l1-repair-20261030/responses/` | 354条补齐阶段完整供应商响应，包含usage |
| `outputs/evaluation_preparation/l1-repair-20261030/original-hashes.json` | 8,496个历史文件的保护清单 |

准确的**实际评价输入路径**以[material-index.json](material-index.json)中的每项identity为准。工作区输入可能经过路径规范化，不应假设它与生成根目录的同名文件字节完全一致。索引同时保存输入、证据、结果的哈希，不能靠文件名替换材料。

本包 `data/final-records.jsonl` 保留全文审计断言，恢复来源是实际输入；旧nli_details中仅保留前100字符的claim通过顺序、槽位和前缀逐条核对后对应，未模糊匹配。模型判断与理由保持保存值，不修饰为人工结论。最终七槽claims也一并保留，足以重算空槽数及支持写作举例。

### 6.3 包内结果字段

| 字段 | 定义 |
| --- | --- |
| `n_materials` | 分组中的完整材料数；不删除零断言材料 |
| `n_all` / `n_cite` / `n_qualified` | 主指标的三个原始计数 |
| `source_entailed` | 仅来源蕴含为entailment的断言数，诊断用 |
| `entailed_slot_misfit` | 上述断言中slot_fit=false的数量 |
| `neutral` / `contradiction` | 最终保存标签数；neutral含明确无合法引用的规则处理 |
| `citcov_pct` / `nli_g_pct` / `chg_pct` | 以0–100百分数保存的比例；零分母空值 |
| `empty_final_slots` | 分组中最终空槽数量 |
| `esc_per_material` | 上项除以n_materials；槽位表中是该单槽为空的材料比例，范围0–1 |
| `zero_audit_claim_materials` | 分组中无相应审计断言的材料数；槽位表针对单槽 |
| `source_group` | pilot、main或completion，表示采纳记录出处，不代表重新生成内容 |
| `material_path` | 仓库相对的原封存评价记录路径 |

每条 `claims[]` 保留full content、审计槽位内原始序号、模型填写的引用、当时暴露的引用和保存判断。`valid_citation`区分是否进入实际NLI判断；其计数与原材料N_cite逐份对账。`final_slots`用于ESC。包内哈希用于检测文件变化，不提供外部签名认证。

## 7. 完整性、费用与验证边界

本次整理执行的是离线复算和只读核验，**模型调用0次、费用新增0元**。这里的0元仅指本次文档整理没有调用，不代表历史实验免费。

| 核验 | 结果 |
| --- | --- |
| 93×6目标—方法覆盖 | 558/558，无缺项、无重复 |
| 保存结果封装哈希 | 558份均通过 |
| 输入与证据哈希 | 每份均与对应identity一致 |
| 审计断言—判断对应 | 10,067条全部按槽位、顺序和文本前缀对应 |
| N_all/N_cite/N_qualified/ESC对账 | 逐份与保存计数一致 |
| 确定性原函数独立重算 | 558对输入/证据的CitCov和ESC与本包一致，无API派发 |
| 历史保护 | `original-hashes.json`中8,496个文件哈希全部一致 |
| CSV规模 | 方法6行、槽位42行、项目36行、用例—方法558行、目标93行 |
| 表格独立算术核对 | 五张CSV成功导入表格引擎；使用独立公式复核四张统计表的比例和ESC，与导出值一致 |
| 可移植性 | 单独复制本L1目录到不含AUCMR代码与outputs的临时仓库布局，离线验证通过 |

### 7.1 调用与费用记录如何使用

三个采用记录目录中的请求文件状态分别为：pilot 232条VALID；main 7,907条VALID、42条REJECTED；completion 354条VALID。它们是指定归档中的**请求记录条数**，不可直接当作独立付费调用总数：存在成功判断复用、缓存，以及不在这三个采用目录内的其他历史记录。断言总数10,067也不是API调用次数。

补齐阶段保存354条完整供应商响应，其中4条有界校准响应被后续复用，另350条为续跑请求；全部返回 `finish_reason=stop`。完整usage合计：

| usage字段 | 合计token |
| --- | ---: |
| prompt_tokens | 1,434,422 |
| completion_tokens | 39,745 |
| total_tokens | 1,474,167 |

上述354条记录均保留 `cost_cny=null`、`cost_basis=UNPRICED_NOT_ZERO`。原阶段缺少可完整定价的usage链，本包不猜测历史金额，也不把未知费用写成0。usage明细及原响应路径在[data/provenance.json](data/provenance.json)。没有完成供应商账单对账，不能将token总数称为实际账单。

### 7.2 哪些结论尚无证据

完成和格式有效不证明每条语义判断正确。本次没有对10,067条判断建立独立人工金标准，也没有测量人工一致性或模型裁判准确率。已有保存判断中可以发现槽位理由与系统角色定义不一致的个例，例如 `UC-7928476e7ef4eea9` 的ReAct goal理由把goal描述成需要trigger事件。该判断原样保留，未在整理过程中改分；它说明slot_fit也存在裁判误差。

不能把当前结果表写成“全部经人工复核无误”。本交接证明的是结果完整、原始记录可追溯、算术可复算。若要宣称裁判与从业者一致，需要额外的、独立设计的人工一致性证据；本次不自动开展该实验。

## 8. 论文写作建议与可直接使用的口径

### 8.1 方法段落建议

> 我们在93个来源支持的目标上评价六种提供可追溯引用的方法，每种方法保留93份冻结生成材料。L1以各方法保存的审计候选断言为单位，首先检查引用是否属于该槽位暴露的证据集合且能够解析。随后，使用Qwen3.7-Plus对每条断言的合法引用材料并集进行自动判断，分别记录来源蕴含关系和Cockburn槽位适配性。文本证据方法使用冻结证据卡，图像证据方法使用已引用的来源页图像。只有同时获得来源支持且适配所在槽位的断言计为合格落地。我们基于同一断言总体计算引用覆盖率CitCov、上下文NLI-G和引用—合格落地差距CHG，并报告最终七个内容槽的平均空槽数ESC。

正文或补充材料还应保留第2.1节的候选捕获阶段差异、第4节的自动裁判身份及第1.1节的回顾性筛选说明，不能仅引用这一段而遗漏这些条件。

### 8.2 主表标题和表注建议

> **L1 evidence grounding on 93 targets per method.** Ratios are micro-aggregated over the saved audit claims. NLI-G requires both source entailment and slot fitness. CHG is the fraction of cited claims failing this joint criterion. ESC is the mean number of empty final content slots per use case. LVLM-Direct is not applicable to citation-based L1 because stable source-reference identifiers are unavailable. Empty outputs are retained.

中文表注可对应写为：各方法n=93；比例基于审计断言微聚合；NLI-G同时要求来源蕴含和槽位适配；CHG为有效引用断言中未满足联合条件的比例；ESC为每份用例最终空内容槽数量；保留空输出。使用第5节六行实测结果，不混入其他样本口径的数据。

### 8.3 结果段落建议

> 六种方法的CitCov为99.88%–100.00%，说明可解析引用较充分，但不能据此判断内容已经获得来源支持。AUCMR的上下文NLI-G为76.62%，CHG为23.38%；TextRAG、ReAct、ViDoRAG和VDocRAG的NLI-G分别为60.58%、50.66%、45.73%和33.75%。AUCMR与去除跨槽检查的消融配置相差约0.42个百分点，显示本次L1上的差异较小。进一步分解表明，部分断言虽被来源支持，却被裁判判为槽位不适配，因此纯来源蕴含计数高于联合合格计数。所有方法均保留原冻结材料，包括五份零断言输出，并同时报告空槽情况。

这段是描述性陈述。不要追加“显著优于”“证明消融有效”“不存在幻觉”“模拟了若干真人专家”等未经本实验支持的表述。

### 8.4 局限段落应覆盖

1. 自动模型判断存在误差，缺少完整独立人工金标准；温度0不保证同名API严格确定性。
2. 各方法的证据表示和候选快照阶段不同，本表测量现有完整流程，不能单独隔离表示、抽取或检索能力。
3. 文本证据卡与图像证据均可能丢失或误读来源信息；未找到支持不等同于事实必然错误。
4. 93目标在6份PDF内有相关性，目标名单为回顾性来源复核；不能称为93个独立领域或盲审样本。
5. 引用率接近上限，需要结合NLI-G、CHG和ESC解释；空槽不能一概解释为质量差。
6. 历史费用记录不完整，本包不能支持精确实验总费用结论。

没有实际计算的 `abstention_f1` 默认占位值、L2规则字段和L3字段不属于本次L1主结果，不应从旧metrics对象中拿来补表。

## 9. 离线复现、上传与保管

所有命令均从AUCMR仓库根目录执行。仅检查已打包数据，不写文件：

```bash
python -B docs/codex_evaluation/paper-handoff/l1/scripts/summarize_l1.py --verify-only
```

仅使用本目录数据重建五张CSV和完整性汇总：

```bash
python -B docs/codex_evaluation/paper-handoff/l1/scripts/summarize_l1.py
```

在原服务器额外核对原文件和8,496项历史保护哈希：

```bash
python -B docs/codex_evaluation/paper-handoff/l1/scripts/summarize_l1.py --verify-only --check-source
```

重新从最终原记录构建本交接包（只写本目录，不调用模型；仅限归档齐备的维护者）：

```bash
python -B docs/codex_evaluation/paper-handoff/l1/scripts/summarize_l1.py --from-source --check-source
```

本包足以离线复算论文表格，并保留写作需要的最终内容和判断依据描述。完整源PDF/图像、完整HTTP请求响应及运行归档仍在第6节所列位置；要重看裁判实际图像或核对API原文，需要另行保管这些源材料。索引里的相对路径不会自动使被Git忽略的outputs上传。若只上传本交接目录，接收者应使用默认离线模式，不能宣称已核验服务器原件。

本次仅新增本L1交接目录，不修改L0–L4代码、原冻结批次、原输出、失败记录、费用记录或论文正文。仓库提交和推送由用户统一执行。
