"""Render author-facing documentation from final exported tables; no model calls."""
from pathlib import Path
import json,csv,collections
H=Path(__file__).resolve().parents[1]
E='experiments/l5/pure_human_20261101'
F=E+'/agent_evaluation_v1/scope_adjudication_v1/continuation_v2'
M=F+'/merged'
def read(name):return json.loads((H/name).read_text())
def csvread(name):return list(csv.DictReader((H/name).open()))
def put(name,text): (H/name).write_text(text.strip()+'\n')
def mdtable(headers,rows):return '\n'.join(['| '+' | '.join(headers)+' |','|'+'|'.join(['---']*len(headers))+'|']+['| '+' | '.join(str(x).replace('|','\\|').replace('\n',' ') for x in row)+' |' for row in rows])
def sources(paths):return '\n\n## 文件依据（仓库相对路径）\n\n'+'\n'.join('- `'+p+'`' for p in paths)

def main():
 overall=read('results/overall.json');projects=csvread('tables/projects.csv');cases=csvread('tables/cases.csv');diag=read('results/diagnostics.json');agreement=read('results/agreement_and_stability.json');index=read('materials/record_index.json')
 result_table=mdtable(['口径','指标','AUCMR A','既有人工稿 B','A−B','项目聚类95%区间','项目/目标'],[[{'independent_mean':'独立评分均值','adjudicated':'最终裁决'}[mode],metric,*[f'{overall[mode][metric][k]:.4f}' for k in ('A','B','A_minus_B')],'['+', '.join(f'{v:.4f}' for v in overall[mode][metric]['A_minus_B_CI95'])+']','10 / 29'] for mode in ('independent_mean','adjudicated') for metric in ('C','K','R')])
 project_table=mdtable(['项目','材料类型','原PDF页数','独立输入词数','正文出现','合格','排除','待定','重复','选中'],[[p[k] for k in ('project_id','material_type','pdf_pages','shared_input_words','body_occurrences','eligible','excluded','pending_boundary_not_selected','duplicate_occurrences','selected_targets')] for p in projects])
 put('README.md',f'''# L5 论文写作交接

本目录是最终采用的 L5 方法与结果的**写作/组织者材料**。已解除方法匿名，含 A/B 对应关系，不得作为新的盲评输入。所有实验来源路径以 AUCMR 仓库根目录为基准；本目录内的 Markdown 链接按文件相对路径解析。上传仓库后无需本机绝对路径。

## 可以直接使用的事实

- 目的：考察 AUCMR 在常规需求条件下的文本用例精化质量。输入为独立需求及给定用例名称/主要参与者；对照为同一文档的既有人工文本用例。
- 材料：官方 PURE 2.0，10 份文档对应 10 个项目，29 个目标、58 份产物。
- 评价：真实 API 调用的 Agent 独立来源参照、匿名双模型评分和裁决。没有真人评分，不是专家验证。
- 完成度：58 份产物各有两份独立评分及一份裁决，共174份最终评分记录；另有58份盲态单元对齐与10份稳定性重复评分。三个主要指标均可计算，技术待办为0。
- 最终来源参照：295条明确适用义务进入 C 分母；4条来源未决义务单列、不进入分母。指标无 NA 不代表源材料没有歧义。

{result_table}

C、K越高越好，R越低越好。结论应同时报告 AUCMR 的较高覆盖/来源支持与较高冗余，不能仅选择优势维度。

## 阅读顺序与论文用途

1. [目的与设计](01-purpose-and-design.md)：研究问题、比较对象和设计边界。
2. [材料与选择](02-materials-and-selection.md)、[输入与对照隔离](03-input-control-separation.md)：数据集、样本与公平条件。
3. [适配与生成](04-aucmr-adaptation-and-generation.md)：实际代码机制、模型及参数。
4. [参照和Agent评价](05-reference-and-agent-evaluation.md)、[指标与统计](06-metrics-and-statistics.md)：可复用的方法描述。
5. [结果与解释](07-results-and-interpretation.md)、[效度与局限](08-validity-and-limitations.md)：完整结果、异常方向与限制。
6. [论文写作指南](09-paper-writing-guide.md)、[文件索引与验证](10-artifact-index-and-verification.md)：段落依据、表图、相对路径及离线复核。

`tables/` 是可直接制表的 UTF-8 CSV，`results/` 是最终机器结果，`materials/` 包含29份共同输入、29份A、29份B及其原始提取、29份最终参照和174份最终逐项判断。体积较大的原PDF及原始调用记录通过 `artifact_manifest.json` 索引，不重复复制。

交接只组织最终采用的方法，不叙述未采纳方案或排障过程。写作仍必须披露：主要指标在生成前固定，Agent执行方案在生成后确定，两个目标的最终范围解释属于评分后的来源独立裁决，随后双方统一重评C。该时间顺序是方法效度事实，不是可省略的调试历史。

本目录不修改论文正文，不宣称完成论文PDF排版。可运行离线核验：

```bash
python -B docs/codex_evaluation/paper-handoff/l5/scripts/verify_handoff.py
```
''')
 put('01-purpose-and-design.md', '''# 目的与实验设计

## 研究定位

详细文本用例精化是已有的需求工程活动。AUCMR将该活动扩展到长篇、结构复杂的多模态需求材料，并引入证据约束与一致性控制。L0验证检索与文档理解基础能力，L1–L4评价目标复杂场景下的精化，L5补充常规需求条件下的文本用例质量比较。

建议研究问题：**在给定用例名称、主要参与者和独立需求说明后，AUCMR生成的文本用例相对于文档中的既有人工文本用例，在完整性、来源约束正确性与冗余度上呈现怎样的差异？**

研究动机可以表述为检验常规条件下能否保持与既有产物相当的质量，但实际分析未建立正式非劣界值及相应功效设计；不可将动机写成已经证明的“不劣”结论。L5不是跨任务迁移、用例识别成绩或一般辅助设计能力测试。

## 任务与实验单元

| 项目 | 最终定义 |
|---|---|
| 一个目标 | 某一项目中的固定用例名称、给定主要参与者和对应独立需求 |
| 产物 A | AUCMR从上述需求精化其余组成部分得到的正式文本 |
| 产物 B | 原文中对应的既有人工文本用例，保持原内容和缺陷 |
| 一个配对 | 同一目标的一份A与一份B |
| 聚类单位 | 项目/源文档；共10个，不是29个独立项目 |
| 指标 | C完整性、K来源约束正确性、R冗余度，分别报告 |
| 主要比较 | 两名独立评分的均值；最终裁决另列，不择优 |

B是待评价产物，不是标准答案。参照由共同独立需求建立，不能以B的内容判定A是否完整。双方可以采用不同模板；评价条件、动作、分支与结果的实际语义，不以标题数或字段齐全程度奖惩。

## 共同信息条件

AUCMR不承担发现全部用例或识别全部参与者的任务。名称和主要参与者为外部给定值，评分不能再次奖励其识别；具体行为的主体是否符合源约束仍可以评价。

评分双方时，评者得到同一套独立需求与最终参照。生成A时不能读取B。历史人工作者当时是否知道更多背景不可控制，因此不能声称这是两种创作主体获得完全相同信息、时间和人员条件的随机对照实验；能控制的是A的输入边界和双方评价时的共同证据范围。

## 最终实验结构

1. 生成前枚举候选、划分源与对照边界、确定资格并固定抽样。
2. 将源文字和给定任务信息输入文本源适配的AUCMR，完成29份A。
3. 以隔离角色从共同来源建立、复核和冻结目标级参照。
4. 两个独立模型角色逐份评价58份匿名产物；另进行盲态断言对齐、裁决及稳定性检查。
5. 按最终采用的参照解释和逐项判断计算29对结果，先目标内计算，再项目内、项目间等权汇总。

两个存在范围解释问题的目标采用有依据的最终来源裁决；新解释冻结后双方C统一重评，所有独立判断及异议留存。该决定不要求评者意见完全一致，但不能据此推断参照是唯一真相。
'''+sources([E+'/protocol/SCREENING_RULES_v2.md',E+'/config/run.json',F+'/PROTOCOL.md',M+'/results/completion.json']))
 put('02-materials-and-selection.md',f'''# 材料来源、筛选与样本

## 数据来源

采用官方 **PURE 2.0**，公开记录：[Zenodo 7118517](https://zenodo.org/records/7118517)。下载对象为 requirements.zip，官方MD5 `bc319fe28619f6290badff328ca159dd`，本地SHA-256 `a450417fb76e307b25ef6e7c8e6684676d7ec9f3f51b3cc686df0bec93be141f`。十份原PDF、页数、原始提取词数、成员路径和哈希均可由材料清单核查。

文档标识参照 UCGen Table 3 作为定位线索，不代表复用了该研究的完整样本集、专家答案或生成结果。当前29个目标是本实验按下列规则选取的样本。

## 预先固定的资格要求

1. 存在包含交互行为的人工文本用例，不能只有名称或用例图。
2. 隐藏对照正文后，独立规范/功能说明仍足以支持目标的核心行为；仅名称、愿景或通用登录条件不足。
3. 独立需求不是B的改写、摘要或平行完整场景副本。独立规范与B共享业务事实本身不等于泄漏。
4. 能明确界定目标、给定参与者、输入和B的边界。不能自行补充系统边界或角色。
5. 能以连贯文字或可读的需求表支持常规目标，不依赖未取得附件和不可读图像；没有任意的页数阈值。

不以B是否完善、是否有所有字段作为资格标准，也不按预期胜负选择。缺少某类条件或异常不自动排除；缺少目标核心行为依据才属于材料缺口。

## 数量和抽样

登记122处正文出现，其中6处重复，形成116个非重复候选：78个合格、35个排除、3个边界待定。78个合格目标中抽取29个，另外49个是“合格未抽中”，不能计为排除。

每项目对合格候选计算 SHA256(`PURE-L5-20261101-project-balanced-v1|` + candidate_id)，按十六进制值升序最多取3个；不足3个全部纳入。Peering仅2个，其余9项目各3个。重复正文固定最早对应稿，不择最完整稿。资格及抽样在查看A前完成。

{project_table}

原PDF共10份，页数8–176；真正进入生成的独立输入为1099–8410英文词，不是整份原PDF。项目中几个目标可能共享同一套项目需求片段，不能将重复输入算作新增独立文档。HATS输入仍较长，不能把全部样本宣传为“很短的需求描述”；PNNL使用的是较长SRS中的局部界面管理需求，不是整套诊断算法。

词数按已登记的空白分词口径报告，不等于模型token数。类型标签来自材料审计，不能将教学或研究原型一律称为工业项目。

## 逐例材料

[cases.csv](tables/cases.csv)覆盖全部29目标的名称、给定参与者、正文页码、共同源页码/片段数、输入/A/B路径、匿名码、哈希、原貌字段完整情况及输入充分性依据。[candidates.csv](tables/candidates.csv)登记所有122处候选与最终资格理由；这属于样本选择记录，不是方法探索记录。

## 与其他层级的关系

已登记的L1–L4六项目源文件（JTV、MIL-STD相关选段、QHeadache、Microcare、RLCS、PeaZip）与本轮十文档未发现项目名或文件哈希重合。该检查不等于语义上绝对无重叠；它们仍共享PURE集合来源。L0实际逐文档清单在本次材料审计中未恢复，不能宣称与L0已证实完全不重叠。
'''+sources([E+'/evidence/source_manifest.json',E+'/screening/candidates.json',E+'/screening/source_boundaries.json',E+'/inputs/manifest.json',E+'/evidence/overlap_audit.json']))
 boundary_table=mdtable(['项目','输入隔离范围','保留内容的判定依据'],[[p['project_id'],p['hidden_scope'],p['parallel_copy_boundary']] for p in projects])
 put('03-input-control-separation.md',f'''# 输入与对照分离

## 每例三部分

- 共同任务信息：原文确定的用例名称、主要参与者或带资格条件的可发起角色集合。
- A输入：上述任务信息，加从独立规范语境中直接提取的需求文字，带 source_part_id、PDF页码和规范化行范围。
- B：原文最早对应的完整人工文本用例，另存 raw 提取和展示文本。B不作为生成seed，也不作为来源参照答案。

输入样例可读 [inventory-MOD.UC.json](materials/inputs/inventory-MOD.UC.json)；对应人工稿见 [controls](materials/controls/inventory-MOD.UC.txt)，AUCMR稿见 [generated](materials/generated/inventory-MOD.UC.txt)。这些是写作审阅材料，不能将包含对照的本目录交给来源构建角色。

## 分界与来源定位

{boundary_table}

同一PDF页可以同时包含独立需求和被隔离正文，因此页号交集本身不构成泄漏；实际边界以片段及行范围判断。`tables/input_boundaries.csv`逐项登记生成输入的part/page/行号和文本哈希。输入的 normalized_lines 是规范化文本的1-based闭区间；候选B的 start_line 与 end_line_exclusive 是1-based、右端不包含的区间。二者都不是原PDF像素坐标。生成卡片还有精确字符范围。

## 不改变产物语义

B保留原来的内容、缺失条件、异常路径、缺号/重号和语言缺陷。展示仅处理空白、页眉页脚和不属于文本正文的图中文字等；raw提取与原PDF可审阅。原稿中的独立行为不能为了匿名而删除或替换。A展示文本由最终结构化结果直接渲染，没有评分后润色。

生成进程从固定输入清单验证文件SHA-256和路径白名单，只读取允许的输入JSON。B、原PDF和其他产物不进入生成上下文；全局独立需求中的共同事实可以保留，但不能人工摘要隐藏场景来补充行为。

## 已完成检查与限度

已有生成和评审包核验记录确认29份输入及对照哈希一致、A展示与结构化结果一致、58份匿名产物包不包含私有映射。输入与B连续12词以上匹配检查未发现匹配；这只是长串重复检查，不是不存在语义泄漏的证明。材料分界与充分性仍包含研究准备中的解释判断，没有据此宣称独立真人审计通过。

本交接目录复制A/B和共同输入供写作，并提供对应的只读快照；评价当时的访问隔离由实际API请求白名单保证，不由“放在不同文件夹”保证。
'''+sources([E+'/screening/source_boundaries.json',E+'/inputs/manifest.json',E+'/private_controls',E+'/runtime/inputs.py',E+'/validation/review_package_integrity.json']))
 put('04-aucmr-adaptation-and-generation.md',f'''# AUCMR适配与正式生成

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
'''+sources([E+'/runtime/pipeline.py',E+'/runtime/inputs.py',E+'/runtime/transport.py',E+'/config/run.json',E+'/prompts/text_plan.txt',E+'/prompts/task_contract.txt',E+'/evidence/core_lineage.json',E+'/runs/formal-v2-credential-recovery/delivery_audit.json']))
 roles=mdtable(['职责','模型家族/实际返回名','上下文边界'],[
 ['来源构建','DeepSeek / deepseek-flash','共同任务与需求，无产物'],['独立来源首稿、裁决者盲读、复核与来源裁决','Qwen / qwen3.7-plus','先独立读源，再按角色获得已保存来源意见'],['两目标最终范围解释','Qwen与DeepSeek独立意见；Qwen最终裁决','共同来源及来源参照，无产物/评分'],['独立评分一','Qwen / qwen3.7-plus','单份匿名产物、源、冻结参照'],['独立评分二','DeepSeek / deepseek-flash','同上，不互见首判'],['断言盲态对齐','Qwen / qwen3.7-plus','同一产物两套去判定标签单元及源'],['最终评分裁决','Qwen / qwen3.7-plus','同一产物、源、参照、首判、对齐及审阅计划'],['稳定性重复','DeepSeek / deepseek-flash','固定样本，新独立会话，不见首判']])
 put('05-reference-and-agent-evaluation.md',f'''# 独立参照与匿名Agent评价

## 正式评价身份

本实验使用真实模型调用模拟人工逐项审阅，不是人工参与实验。模型角色是独立会话和职责分工，并非三个独立厂商，也不保证统计独立。无 human_entered、专家人数或真人通过结论。

{roles}

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
'''+sources([E+'/agent_evaluation_v1/PROTOCOL.md',E+'/agent_evaluation_v1/revision_v5/config.json',E+'/agent_evaluation_v1/revision_v5/isolation.py',F+'/isolation.py',F+'/config.json',F+'/reference_freeze.json',F+'/reference_changes.json',F+'/composition_provenance.json']))
 put('06-metrics-and-statistics.md',r'''# 指标定义、边界与统计

## 文献依据与操作化

三个维度参考 Karimi & Tiwari, *UCGen: Leveraging LLMs for Automated Generation of Use Case Textual Descriptions from Requirements Specification*, ISEC 2026 Research Track, Article 11, DOI [10.1145/3796563.3796606](https://doi.org/10.1145/3796563.3796606)，§4.3/Table 2（本地PDF第7–8页）。本实验为适应给定目标的精化和可复核判断，明确重新操作化公式与边界；不是直接复制UCGen原公式或与其历史分数比较。

UCGen引用的Tiwari & Gupta (2017), IST 91:22–43, DOI [10.1016/j.infsof.2017.06.003](https://doi.org/10.1016/j.infsof.2017.06.003)仅核实书目信息与摘要，不声称其完整量表已复用。L3仅作为独立来源参照与复核流程的依据，不混用L3覆盖公式、未决分母或历史得分。

## 原子化单位

义务/行为断言是一个可独立判断的动作、条件或状态。独立动作如“校验并保存”可以拆分；条件—响应关系必须保留语义联系，不能拆成孤立条件凑数量。保留主体、对象、模态、否定和必要顺序。可选行为保持“允许/可以”的语气，不强行视为每次必须执行。

原文同一句可支持多个语义单元，但各自必须明确不同的原子含义并映射同一原文跨度。名称、纯标题、给定参与者、破折号、“Not specified”和空字段不计行为单元。

## 完整性 C

令目标的明确适用义务集合为O，N=|O|。每项覆盖值c取：

| 值 | 判定 |
|---:|---|
| 1 | 语义完整覆盖该适用义务，包括所需条件和结果 |
| 0.5 | 已明确覆盖一个必要组成部分，但仍缺其他必要成分；注明缺失 |
| 0 | 未覆盖、相反或仅出现名称；注明源要求与缺失 |

`C = sum(c_i) / N`。同义重复不重复得分，跨字段表达可以覆盖。四类condition/main/branch/result给诊断分项，但主C不是四类均值、更不是七槽均值。N不因某稿空槽而改变。UNRESOLVED源义务排除分母并报告；N=0时C=NA，不能按产物反造义务。本轮N均大于0，主C均为数值。

## 来源约束正确性 K

去重后每个原子断言判定：

| 标签 | 定义 |
|---|---|
| S | 共同源明确支持，或无需额外业务前提即可直接蕴含 |
| X | 与共同源明确矛盾，或产物内部行为关系矛盾 |
| U | 共同源无法证实的具体业务事实；不能理解的行为断言亦需说明后归入U |
| Q | 源自身歧义导致不可按S/X/U确定 |

`K = S / (S + X + U)`；Q单列并排除。S/Q应有源定位，X有矛盾源或明确内部冲突依据；U可以没有“证明不存在”的引句，但须说明检查范围和缺少前提。U不是现实世界错误或确定幻觉。

K使用同义去重后的行为断言。`same_assertion_as`和`duplicate_of`指向前面同一语义根的单元不重复纳入K。语言错误不按原始数量混入越高越好的分数；不影响语义的拼写问题不构成主要指标。

## 冗余 R

令M为未去重的全部行为单元数，D为在相同语义位置没有新增条件、结果或必要上下文的不必要重复单元数。`R = D / M`，第一次出现不计重复。

`same_assertion_as`用于K去重，不等于不必要冗余；`duplicate_of`专门表示R的冗余关系。前置与分支中的必要复述、动作和结果不同结构作用不自动扣分。源支持的额外必要行为不因B未包含而算冗余。范围外细节可诊断scope_excess，并按源支持性判断K，不直接统算重复。

## 空项与未决

| 情形 | 处理 |
|---|---|
| 源未要求某种条件/分支 | 该类别可为NA，不凭模板强制生成 |
| 源有要求但产物未体现 | 对应C=0 |
| 完整空行为产物且N>0 | C=0、K=0、R=NA，不以空白取得高质量 |
| 全为Q、S+X+U=0且非空 | K=NA，单列原因 |
| 无行为单元M=0 | R=NA |
| 技术调用/解析失败 | 技术缺失，不当质量零分 |
| 普通解释或评分分歧 | 保留首判，由最终裁决作有依据的操作性决定，不强求一致 |
| 源本身未解决的义务 | 源未决单列，不进入C分母 |

最终58份产物均非空，29对主指标均有值；分项类别或常量标签κ仍可合法为NA。不得用“主指标无NA”宣称所有原子行为无争议。

## 聚合

对每目标i、方法m、评分角色r分别计算指标y。独立均值模式先令 `y_ind(i,m)=(y_1(i,m)+y_2(i,m))/2`；裁决模式直接使用第三角色的最终逐项判断计算y_adj。两个模式分别报告。

项目j包含n_j个目标。先计算 `Y(j,m)=sum_i y(i,m)/n_j`，再以 `Y(m)=sum_j Y(j,m)/J` 得到总体，J=10。项目配对差 `Delta_j=mean_i(y(i,A)-y(i,B))`，总体差是十个Delta_j等权平均。不能直接把29个目标简单平均作为主汇总，也不能把所有断言合并后算比例冒充总体K或R。

置信区间：用固定seed=20261101，从10个项目差值中有放回抽取10个，计算均值，重复10000次；按线性插值的2.5%/97.5%分位数报告percentile区间。两种模式及三指标均用相同算法。重采样单位是项目，非目标、断言、调用或Agent。当前结果没有另行实施显著性检验或Holm校正的p值，不应把区间表改写为经多重校正的显著性结论。

## 一致性和稳定性

覆盖一致性对两名首判对应同一义务的0/.5/1标签计算线性加权Cohen κ，距离为|a-b|；同时报告完全一致率。将各产物可用义务对拼接是描述性汇总，不使590个义务判断成为590个独立实验样本。

断言nominal κ只用明确盲态对齐的一对一等价、去重断言；多对一、拆合、未匹配与未审查单元另报。自定义ID相同不等于相同断言。标签常量时κ可为NA，不能写成0或1。

评分一致性另给逐产物C/K/R绝对差的均值。稳定性在固定10份重复产物上按首次/重复评分计算绝对差，不能替换首判或增加项目样本量。二者都不等于真实准确性。
'''+sources([E+'/protocol/EVALUATION_v1.md',E+'/evidence/UCGen.pdf',E+'/agent_evaluation_v1/revision_v5/metrics.py',E+'/agent_evaluation_v1/scope_adjudication_v1/summarize.py',M+'/results/overall.json']))
 a=agreement['agreement'];s=agreement['stability']
 project_delta=mdtable(['项目','n','独立ΔC','独立ΔK','独立ΔR','裁决ΔC','裁决ΔK','裁决ΔR'],[[x['document'],x['planned_targets'],*[f"{x[mode][metric]['A_minus_B']:.4f}" for mode in ('independent_mean','adjudicated') for metric in ('C','K','R')]] for x in read('results/per_project.json')])
 direction_table=mdtable(['口径','指标','A较高目标','相同目标','A较低目标'],[[mode,metric,*[diag['direction_counts'][mode][metric]['targets'].get(k,0) for k in ('A_higher','equal','A_lower')]] for mode in ('independent_mean','adjudicated') for metric in ('C','K','R')])
 pooled=mdtable(['口径','方法','完整覆盖','部分覆盖','未覆盖','S','X','U','Q','重复D'],[[role,m,*[diag['by_role_and_method'][role][m]['pooled_counts_descriptive_not_main_aggregate'][k] for k in ('coverage_full','coverage_partial','coverage_missing','S','X','U','Q','D')]] for role in ('score_primary','score_secondary','score_adjudicated') for m in ('A','B')])
 put('07-results-and-interpretation.md',f'''# 最终结果及解释

## 完成度

10项目、29目标、58产物；独立评分一58份、独立评分二58份、裁决58份、盲态单元对齐58份，稳定性重复10份。两目标的最终覆盖记录已纳入同一29对汇总。所有主C/K/R为数值，未完成技术任务为0。正式生成A为29份，未收集真人分数。

## 总体结果

{result_table}

C、K越高越好，R越低越好。两种口径均表现为C/K较高且R较高：AUCMR在共同来源范围内覆盖和可支持比例较高，同时承担较多重复负担。R不是“文字多”的替代指标，原文平均词数见下文；不能将C/K优势合成为未经定义的总分。

独立评分均值C的A−B为0.1051，裁决为0.1065；两者方向一致。K分别为0.3125与0.3686；R分别为0.0738与0.0841。所有报告区间下界为正，但这是既定项目bootstrap的效应区间，不是已完成多重校正的假设检验。没有非劣界值或功效设计，不能由这些结果推导“严格不劣”。

## 项目异质性

{project_delta}

完整性并非每项目都占优：独立均值下Central trading、Peering较低；裁决下Central trading、HATS较低。Central trading的完整性差异在两种口径中均为负，不能仅引用总体结果掩盖这一点。K在两种口径的10项目均较高；R在独立均值的10项目、裁决的9项目较高，裁决Inventory例外。对R而言较高是较差方向。

{direction_table}

这些计数是描述性方向，非显著胜出次数。逐目标数据见 [per_case.json](results/per_case.json) 和 [results_per_case.csv](tables/results_per_case.csv)，不能只挑有利案例。

## 一致性

- 覆盖：{a['coverage_item_pairs']}个对应义务判断对，完全一致率{a['coverage_exact_agreement']:.4f}，线性加权κ={a['coverage_linear_weighted_kappa']:.4f}。
- 断言：397个可用的一对一等价、去重断言对，nominal κ={a['assertion_nominal_kappa']:.4f}。
- 未匹配或非一对一组227；重复组中排除的配对67；其中保守登记的未语义审查组27、单元27。这些类别不都互斥，不能相加当作总断言数。
- 原始两侧未获得等价配对的单元为51和311。不能将397对的κ推广成全部断言完整一致。
- 两名首判逐产物指标绝对差均值：C={a['metric_absolute_difference_mean']['C']:.4f}，K={a['metric_absolute_difference_mean']['K']:.4f}，R={a['metric_absolute_difference_mean']['R']:.4f}。

一致性说明评分行为的接近程度，不能解释为人工验证或准确率。C的一致性样本数590=295×2，因为同一源参照分别评价A、B；不是590个独立需求项目。

## 稳定性

固定10份样本的新会话重复评分，平均绝对差C={s['mean_absolute_difference']['C']:.4f}、K={s['mean_absolute_difference']['K']:.4f}、R={s['mean_absolute_difference']['R']:.4f}。样本ID为 `{', '.join(s['sample'])}`。该诊断使用DeepSeek评分角色，只覆盖每项目一份匿名产物，不代表两个模型在全部58份产物上的重复稳定性。重复断言未对齐，所以不提供重复断言κ。

## 诊断计数与文本长度

{pooled}

上表为逐单元计数池化，仅供诊断；不能用它计算论文主汇总。不同角色的断言划分不同，不能直接把S/X/U原始数量视作统一单元上的模型优劣。最终裁决的U在A、B分别为8和141，只能解释为共同源无法证实，不是现实错误的人工复核数量。

显示文本词数按空白分词、含标题标签：A平均{diag['display_word_counts']['A']['mean']:.2f}、中位数181、范围75–596；B平均{diag['display_word_counts']['B']['mean']:.2f}、中位数163、范围71–891。词数差不是质量指标，也不能代替行为单元冗余率。本轮没有完整空行为产物，但可能有空槽，需与材料不足区分。

## 可用于结果段的结论

在选定PURE材料及共同证据范围内，Agent评价显示AUCMR生成稿具有较高的需求义务覆盖和来源支持比例，同时存在更高的冗余负担。项目和个例表现存在差异，表明常规精化中的质量权衡仍需审阅。该结果不是同条件的人机编写能力比较，也不能证明专家水平或严格非劣。
'''+sources([M+'/results/overall.json',M+'/results/per_project.json',M+'/results/per_case.json',M+'/results/agreement.json',M+'/results/stability.json']))
 put('08-validity-and-limitations.md', '''# 效度、局限与结论边界

## 构念效度

C衡量共同源中明确适用义务的覆盖；K衡量共同源能支持的产物断言比例；R衡量经语义单元判定的不必要重复。三者覆盖不同质量方面，不等于整体可用性、专家满意度、效率或经济收益。不能把词汇相似度、字段数量、文本长度或给定名称/参与者识别当作这些指标的替代。

既有人工作者可能掌握未包含在共同源中的背景，需求与用例可能共同演化。B中的U不能称为现实错误、确定幻觉或人类能力不足。B不是标准答案，A中有来源支持而B未写的必要行为也不应被判冗余。

## 参照和范围解释

来源参照由Agent形成，不是官方gold或真人金标，可能过宽、遗漏或原子化不当。最终C纳入295条明确义务，4条源未决排除分母；指标均有值不等于来源完全确定。

四条未决分别为PNNL历史诊断窗口是否适用“Each window”的动画状态要求，以及Central trading卖出目标中的冻结账户归属、撮合价格条件解释和未命名后备原则。应把这些称为本次参照的适用性裁定。尤其“反直觉”本身不证明文本逻辑矛盾；这些排除理由尚未由领域专家独立确认。

VUB全文展示采用内容访问/下载/格式的较广解释；Inventory修改合并编辑与修改，并纳入相关权限作为条件。两者经过来源独立最终裁决，双方统一重评C，但不是所有解释都被证明无效。论文必须披露评分后确定这些解释的事实，以及结论对该操作化范围的依赖。不能写成全部评价细节在生成前预注册。

## Agent测量偏差

未收集真人评分，不能沿用人工计划中“评审者人数、经验年限、专家共识”等措辞。Qwen裁决与一个首判模型同源，DeepSeek首判与生成模型同源；会话隔离不消除共同偏差。低温度和跨模型并不保证判断真实正确。

引用定位校验仅证明引文存在，未必能证明推理受支持。两套单元分割不相同，断言κ只来自明确对齐的一部分单元；其余不能假设相同。稳定性只测固定10份样本的一个模型，不能推广至全样本、全模型。

匿名编码隐藏显式方法标签，但模板、文风和详细程度仍可能泄露身份。编排和材料准备人员可知映射，真正被隔离的是正式模型角色输入；不能声称整个组织过程完全盲态。

## 内部与外部效度

源/B隔离依据逐文档的独立规范语境判定。行区间与长串重复检测能排除部分机械泄漏，不能绝对排除语义复制；没有独立真人第二轮材料审计作为保证。候选筛选与抽样先于A，不能消除所有材料准备主观性。

只有10个项目、29个目标，课程/教学和研究原型材料较多。输入1099–8410词，不代表全部短文本任务，更不能从纯文本结果推出多模态检索收益。源PDF有长文档，但局部输入并非完整复杂原文任务。

项目内目标共享来源且相关，因此使用项目等权和项目聚类bootstrap。10个项目的区间仍可能不稳定。目标数、义务数、请求数和多个Agent不能增加独立项目样本量。公开PURE数据可能进入模型预训练语料，本实验不能排除污染。

与已审计L1–L4项目未发现同名或文件哈希重叠，但同属PURE不构成全新独立语料库；L0逐文档重叠未核实。不能在论文中笼统宣称与所有其他层级完全无重叠。

## 合理结论

允许：在选定PURE及共同证据范围内，Agent评价表明AUCMR在C/K上较高、R也较高，常规条件下具有一定源约束精化能力并存在重复负担。

不允许：已经完成人工评审；达到或超过专家水平；整体优于人工；严格证明不劣；所有项目都占优；U等于现实错误；一致性等于准确性；没有任何来源歧义；已验证原多模态检索机制在L5的优势。
''')
 put('09-paper-writing-guide.md',f'''# 论文写作整合指南

## 章节对应

| 论文部分 | 推荐内容 | 交接依据 |
|---|---|---|
| 引言/动机 | 已有精化活动向复杂多模态材料扩展；L5补充常规条件 | 01 |
| 研究问题 | 给定名称/参与者后的精化质量，而非识别或跨任务泛化 | 01 |
| 材料 | PURE2.0、10文档/项目、29目标、资格/抽样、体裁及输入规模 | 02、projects/cases表 |
| 公平比较 | 隔离B、同源评价、B不是gold、历史人作条件不可控 | 03 |
| 方法适配 | 全文有限源卡选证、保留证据核验与DRA、纯文本检索边界 | 04 |
| 评价方法 | 独立来源、盲态首判、单元对齐、裁决、实际模型及权限 | 05 |
| 指标/统计 | C/K/R公式、分母、项目等权、bootstrap、双口径 | 06 |
| 结果 | 完整六行总体表、项目差异、一致性和稳定性 | 07 |
| 效度威胁 | Agent偏差、源约束、后置裁决、材料体裁及10项目范围 | 08 |
| 数据可用性 | 仓库相对路径、实际数据/协议/评分/脚本索引 | 10、artifact_manifest |

## 可直接改写的设计段

“L5考察AUCMR在常规需求条件下的文本用例精化质量。我们从PURE 2.0的十份需求文档中，按生成前固定的资格与项目均衡抽样规则选择29个目标。AUCMR获得独立需求、给定用例名称和主要参与者；文档中的既有人工文本用例作为待评价对照，其正文及平行场景副本不进入生成输入。两类产物在相同共同需求和独立来源参照下接受匿名Agent评价，分别测量完整性、来源约束正确性和冗余度。”

## 可直接改写的评价段

“参照构建和独立复核角色先只读取共同需求，保存各自判断后再复核和裁决。对匿名产物，Qwen与DeepSeek两个独立会话角色逐项提交覆盖、断言与重复关系判断，程序计算C/K/R；最终裁决与独立首判分别保留。两个目标的范围解释经来源独立裁决后明确，新参照冻结后双方统一重评C。主要指标在生成前固定，Agent执行和上述范围裁决的时间顺序另行披露。评价均由Agent执行，不将其记录解释为人工评审。”

## 建议主结果表

{result_table}

该表可直接使用 [results_overall.csv](tables/results_overall.csv)，正文保留四位小数，机器文件保留原精度。A−B由未舍入数据计算，显示均值相减偶尔与显示差值末位不同属于正常舍入，不能人为“修正”。

## 可直接改写的结果段

“在项目等权汇总下，两名独立评分的完整性均值为AUCMR 0.6172、既有人工稿0.5121；来源约束正确性为0.9639和0.6514；冗余度为0.1098和0.0360。最终裁决保持相同总体方向，但并非每项目的完整性均占优。结果表明，在选定PURE与共同证据范围内，AUCMR具有较高的覆盖和来源支持，同时存在更高的重复负担。该Agent产物评价不能据以宣称整体优于人工或严格不劣。”

## 推荐附表和图

- 材料表：10项目的材料类型、原PDF页数、独立输入词数及目标数；不要只报原PDF页数。
- 主表：上述六行，明确指标方向、10项目/29目标、项目聚类区间。
- 项目差异图：十个项目分别绘制C/K/R的A−B点值，独立均值/裁决并列；R正值表示更差，不能统一着色为优势。
- 附表：29目标、源/B页码及最终分母；两模型一致性和10份稳定性摘要。
- 如需案例，只能从冻结产物原文引用并说明项目与范围；不得润色B、删除不利路径，不能把选择性例子代替整体数据。

## 引用与术语

数据来源引用官方PURE版本链接；维度来源引用已取得的UCGen §4.3/Table 2，指标操作化明确归于本实验。不是UCGen输出比较。没有完整取得的二级量表不要写成已经原样复用。

统一用“既有人工文本用例”“来源约束正确性”“Agent评价”“最终裁决”“共同独立需求”。不用“人工金标”“专家评审”“真实错误率”“三种独立模型”“全面显著优于”“证明不劣”。L5结果不使用L4槽位平均分，L3方法引用仅限独立参照流程。

写作时只需呈现最终有效方法和必要的版本时间顺序，无需纳入未采用路线、接口排障或尝试次数。现有论文其他层级的数值与样本范围应由各层交接材料分别确认，不从L5推导或覆盖。
''')
 put('10-artifact-index-and-verification.md',f'''# 文件索引、迁移与离线验证

## 路径约定

本目录为 `docs/codex_evaluation/paper-handoff/l5/`。JSON/CSV中的实验路径全部是 **AUCMR仓库根目录相对路径**，不是相对当前Markdown；本目录内文档链接是普通文件相对链接。原始日志可能仍有执行机路径字符串，交接派生文件已转为仓库相对路径，不改写原日志。

包内 `materials/inputs`、`generated`、`controls`、`references`、`ratings` 可独立阅读。`controls/*.raw.txt`是原始提取，`*.txt`是原展示稿；二者语义处理区别见03。参照与评分JSON是从最终有效记录中提取的写作快照，附原记录路径和哈希；它们不是重新调用模型得到的结果。`materials/record_index.json`逐份连接174份判断与其实际来源。

## 权威实验文件

| 内容 | 仓库相对路径 |
|---|---|
| 当前结果指针 | `{E}/agent_evaluation_v1/CURRENT_RESULT.json` |
| 官方PDF和来源版本 | `{E}/evidence/pure/`；`{E}/evidence/source_manifest.json` |
| 全部候选与边界 | `{E}/screening/candidates.json`；`{E}/screening/source_boundaries.json` |
| 冻结生成输入 | `{E}/inputs/`；`{E}/protocol/INPUT_AND_CONTROL_FREEZE.json` |
| 正式A、结构化输出和日志 | `{E}/runs/formal-v2-credential-recovery/<case>/` |
| 既有人工B | `{E}/private_controls/<case>.raw.txt` 与 `{E}/private_controls/<case>.txt` |
| 生成实现/提示 | `{E}/runtime/`；`{E}/prompts/`；`{E}/config/run.json` |
| 原始匿名展示材料 | `{E}/review/stage_R_requirements/` 与 `{E}/review/stage_S_products/` |
| 最终来源参照 | `{M}/references/<task>.json` |
| 最终首判、裁决与对齐 | `{M}/records/` |
| 最终结果JSON | `{M}/results/` |
| 最终范围解释与冻结 | `{F}/reference_changes.json`；`{F}/reference_freeze.json` |
| 最终分量来源 | `{F}/composition_provenance.json` |
| 核心计算 | `{E}/agent_evaluation_v1/revision_v5/metrics.py` |
| 最终分量合并/聚合 | `{E}/agent_evaluation_v1/scope_adjudication_v1/summarize.py` |

逐文件路径和哈希以 `artifact_manifest.json` 为准。目录名中的版本或技术命名只作文件定位，不需写入论文叙述。

## 模型调用与分量来源

`materials/call_provenance_index.json`给被最终采用记录引用的请求、实际响应内容及响应元数据路径；`tables/model_and_role_config.csv`列角色/分量的实际参数与模型标识。请求指纹约束原任务，技术格式修复可能增加消息；实际采用的语义判断以最终记录为准。

对T003/T009的四份产物，C来自最终冻结范围后的覆盖重评；K/R沿用源和语义单元未变时的原首判/裁决。`agent_for_C`和`agent_for_K_R`分别标识来源，不能把合并记录说成全部维度都由一次新调用产生。其余目标同样保留各自真实响应标识。原始评分记录仍在仓库，通过索引查验，不必在论文讲述中间流程。

## 离线复核

从AUCMR仓库根目录执行：

```bash
python -B docs/codex_evaluation/paper-handoff/l5/scripts/verify_handoff.py
```

该脚本不联网、不读取凭据、不启动生成/评分。它核对来源清单及包内内容哈希，验证29目标/10项目配对和输入/B隔离记录，基于174份逐项判断复算C/K/R、项目聚合、配对差和10000次bootstrap，核对一致性与稳定性，并检查路径迁移、原文引句和表格数值。JSON验证记录位于 `verification.json`。

如需从同一冻结结果重新导出派生文件，按以下顺序执行；最后的参数明确表示为主动重建的本包更新哈希封存：

```bash
python -B docs/codex_evaluation/paper-handoff/l5/scripts/build_data.py
python -B docs/codex_evaluation/paper-handoff/l5/scripts/write_docs.py
python -B docs/codex_evaluation/paper-handoff/l5/scripts/verify_handoff.py --refresh-manifest
```

日常核验不加`--refresh-manifest`，以便发现包内文件变化。构建脚本要求当前结果仍指向本交接选定版本；不自动换成其他结果。重建会更新本交接包，不能改写原实验记录。评分快照的`final_boundary_decisions`保留已采用的边界说明；复算脚本按最终裁决记录区分适用义务的明确评分和被排除的来源未决，不依据旧字段名称自行令整例缺失。

## 上传仓库和共享范围

`artifact_manifest.json`索引原实验依赖及其SHA-256；`package_manifest.json`索引本交接文件。不能只上传README而遗漏CSV/JSON/材料和所引用的原实验文件。原PDF和模型日志未复制进本包；若仓库不存放这些大文件，应保留带哈希的公开来源与归档位置，并在数据可用性声明中明确。

本包包含解盲的A/B编码和逐方法结果，适合论文作者/组织者，不适合作为盲评发放包。未复制API密钥；不要将个人环境文件、账户凭据或私有组织者映射另混入公开附件。原需求文档的再分发按官方来源许可处理，不据本包推断拥有额外授权。

## 交付边界

交接证明最终实验记录可定位、统计可复算，并提供论文所需方法与限制；不证明模型语义判断无误、不替代真人复核、不表示整篇论文其他层级已同步，也不包含对论文正文或PDF排版的修改。
''')
 print('Wrote README and 10 final-method handoff chapters.')

if __name__=='__main__':main()
