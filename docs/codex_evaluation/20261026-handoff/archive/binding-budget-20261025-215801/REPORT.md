# 新模型配置与预算准备报告

2026-10-25，Asia/Shanghai。工作目录 `/home/dzy/code/TOSEM2026/AUCMR`，Conda `aucmr`。新准备目录为本报告所在目录。**已产出可审核草稿，尚不能启动模型调用。** 本轮模型调用0，未运行实验、正式评价或完整回归。

## 完成内容

HEAD仍为 `50f3c594545d407db97e8d1574ab0aad4f2616f4`、分支temp；未发现新的代码变更或AGENTS.md。当前已有test.txt、vendor及生成输出等工作区改动全部保留；本轮以实际读取的现状建立保护基线，不还原他人改动。子模块映射不在范围内，不列为启动前必修。

新建 `l2.json`、`l3.json`、`l4.json`，模型选择、所有非model字段以及既有parameters逐项与原草稿相等。没有改指标、提示词、角色、方法、目标、seed、来源策略或输出8192参数。模型始终NOT_BOUND，live_authorized=false。

- Ark已填标准在线的整数半开价格档位、缓存路径、推理输出归属、图像detail映射、像素/字节候选限制和部分预留。
- Qwen已填北京价格档位、精确上下文、缓存/图片/推理用量契约、旧域名16MiB请求体限制、默认图像token候选界及五项条件费用预留。usage_contract.verified=true仅标记有公开文档支持的受限标准Chat用量契约；整体billing和request_limits仍未verified。
- GLM核实了关闭思考和模型专属32768最大输出参数，保留8192并填最大公布输出价对应的0.049152元分量。整数价格边界和严格输入/图像界仍不能猜填。
- `binding-evidence.md` 将每个字段与官方来源、适用条件和A/B/C证据状态对应。未使用第三方模型聚合站作为证据。
- `execution-plan.json` 明确三个层的配置绝对路径、共同账本 `/home/dzy/code/TOSEM2026/AUCMR/outputs/evaluation_campaign/budget.sqlite`、累计500元上限；L2显式列明账本/预算CLI参数。未创建正式账本。

## 密钥环境结论

| 环境 | 智谱变量 | Ark变量 | Qwen变量 |
| --- | --- | --- | --- |
| Codex命令子进程（登录shell） | 不可见 | 不可见 | 可见 |
| 非登录shell子进程 | 不可见 | 不可见 | 可见 |
| conda run -n aucmr Python | 不可见 | 不可见 | 可见 |
| 用户此前设置密钥的交互终端 | 未直接观察 | 未直接观察 | 未直接观察 |

**未证明唯一原因是环境继承，但排除了仅在conda边界丢失。** 用户曾设置过密钥与此结果并不矛盾。只检查三变量非空状态，未打印值、读取历史或搜寻密钥文件。`environment-instructions.md` 给出了用户zsh终端的单行存在性检查、隐藏输入和父子进程继承说明；需要重新启动实际执行器并从有变量的环境继承，单纯在另一个终端export不会改变现有进程。没有要求重新充值或换模型。

## 实际离线检查

| 命令（均在仓库根目录） | 退出码 | 结果 |
| --- | --- | --- |
| `/home/dzy/miniconda3/bin/conda run --no-capture-output -n aucmr python -B scripts/check_evaluation_budget.py --draft outputs/evaluation_preparation/binding-budget-20261025-215801` | 0 | 检查完成，五角色均未live-ready；完整结果在draft-check.json |
| `/home/dzy/miniconda3/bin/conda run --no-capture-output -n aucmr python -B outputs/evaluation_preparation/binding-budget-20261025-215801/check_materials.py` | 0 | 真实材料重新收集、来源与输出哈希、原始请求构造通过；网络连接被补丁禁止 |
| `/home/dzy/miniconda3/bin/conda run --no-capture-output -n aucmr python -B outputs/evaluation_preparation/binding-budget-20261025-215801/check_configuration.py` | 0 | 非模型字段/parameters/模型选择不变；已填价格与费用分量结构和条件算术通过；缺项保持缺项 |

完整回归未重跑。材料脚本在补充L3当前collect后重新运行一次，不是模型/语义执行；没有构造Runtime、Transport、Budget或Orchestrator实例，没有写原数据。输出在本目录，含6张来源第一页的144DPI派生PNG，用于下一轮候选材料。

| 实际初始请求 | 数量 | 最大文本UTF-8字节 | 最大序列化JSON字节 |
| --- | --- | --- | --- |
| L2完整用例 | 646 | 15294 | 16041 |
| L3 A场景构建 | 93 | 18272 | 21022 |
| L4完整用例 | 646 | 16516 | 17413 |

651项绑定中的非PRESENT对象不生成评价请求；没有强行给缺失/空输出补内容。L3 B需要A的真实场景提案，C需要前序真实决定，内容/行为阶段需要冻结语义参考；这些尚未产生，故不能声称已检查它们的真实请求。没有伪造参考替代。L3新输入清单保存在 `l3-current-input-manifest.json`。

六份来源共439页，当前全部READY且与冻结预期哈希一致。扫描所有页文本与页面尺寸；选每份的第一页、最长文本页、最大尺寸页去重，在144/216/288 DPI共渲染36次。样本均未超过本轮Ark/Qwen候选图像像素与字节保护值。36个渲染样本的最小边长与宽高比也满足已收集的Ark/Qwen文档约束；这不证明任意未来截图合规。详细记录在 `page-sizes.json`、`rendered-page-sizes.json`、`image-shape-check.json`。

最长页为jtvauctions第4页，文本8707字节；最大样本PNG为该页288DPI：2448×3168、659217字节、base64 878956字节。24次重复携带这一图像，**仅Data URI就达21095472字节**，超过Qwen旧域名16777216限制；这是可证实的字节风险，不依赖猜测token。12张约10547736字节，仍需加文本、历史回复和消息JSON。48个最长页文本为417936字节，也不能当作token实测。历史压力样例是大小分析，不是实际模型决定或全局最坏情况证明。

1385个实际可构造初始请求的预算检查都被BILLING_NOT_VERIFIED阻止。原始字节量并不证明能满足token输入/上下文上界；报告没有把“检查脚本退出0”写成“请求预算通过”。

## 到达限制时如何停止

- `eval/evaluation_request_budget.py:check_request` 在完整历史上检查输入+输出上下文、JSON字节、PNG图像及费用预留，不截断材料。
- L2/L4在EvaluationRuntime reserve之前检查，限制失败转RuntimeDeferred，不发送请求；L2在events记录DEFERRED_BEFORE_DISPATCH并保持attempt可恢复；L4写scheduling_events并保留此前付费回执，不固化失败评分。
- L3在executor预留前检查，失败抛Blocked，已写task_inputs；CLI退出2并输出BLOCKED。这个前置失败不会生成一条假付费call记录。全程日志应另存新运行目录，不能声称已有专用budget拒绝账本。
- 若已发送后返回用量缺失、费用未知或超上界，既有实现保留回执与预留，暂停后续请求，不能自动重发。

## 集中剩余条件及最小处理

1. **共通严格token估计证据（B）**：文本UTF-8倍率、消息/内容块/请求封装开销缺可靠全域界，三个模型均null。取得匹配服务版本的tokenizer+模板或供应商认可上界后证明；不靠一次模型测量、平均比例或任意安全倍数补齐。
2. **GLM计费与视觉契约（B）**：K整数边界、正式cache路径/缺失语义、图片和推理归属及图像/请求限制。只补官方明确值；当前空tiers和null不改变。Ark补context整数语义、数量界及独立存储费用范围；Qwen需确认近似图像算法能否当严格界，其他detail保持未声明。
3. **具体实现与文档适配（B）**：不更换Qwen现有max_tokens；若将来改max_completion_tokens，官方允许10 token误差，而现代码把参数值当严格上界，需先单独设计“发送限制/计费上界/上下文上界”一致的最小适配，不能只多预留钱或反复调大参数。本轮不改代码。供应商宽高/比例约束也超出当前仅像素乘积的通用检查；实际样本可离线筛查，不认证任意后续截图。累计16MiB冲突应由预算停机控制；如总控选迁移北京workspace域名，需要实际WorkspaceId并更新新草稿，不能伪造ID或删图压缩证据。
4. **执行环境和有限观测（C）**：用户终端与执行器变量继承、账户访问、JSON/图像与usage返回符合性。只在预算证据闭合及另行授权后测试。接口成功不代表语义校准通过。

这些条件不是要求完整48轮所有最坏工具循环都低于500元；单请求保护、拟议有限测试额度、完整评价投影分别管理。完整评价投影目前NOT_ESTIMATED，不作为有限接口测试的前提。

## 下一轮最小授权方案（未执行）

请总控审核：**仅三个既定模型各2次，共最多6次接口调用，串行、零自动重试，每次输出最多1024，拟输入已验证上界8192；分配每次最多0.50元，整体停止额度3元，计入共同500元账本。** 此额度是待批准的支出限制，不是已经证明可执行的预留。请求保护缺证未解决时，即使批准3元仍不得发送。

每个模型一次构造文本JSON（status=ok），一次来源PNG+简短可见标题JSON。图片选 `SRC-16a7c81357d8-p1-144.png`，来源/页面哈希与尺寸已留存。GLM覆盖共享模型接口，不能代替L2/L3/L4各层语义验收。计划不改实际评价配置的8192输出上限，1024只属于未来接口测试profile。

在8192输入+1024输出及标准收费条件成立时，最大公布未缓存价格算术为GLM0.022528、Ark0.079872、Qwen0.135168元/次，六次合计0.475136元。execution-plan.json列出五分量候选及条件；其图像/推理/other=0须分别证明已包含总量或无独立收费，不能据此绕过现有预算检查。3元留有管理余量，但不得用管理余量替代单请求保护。

通过条件：模型身份符合、正常结束、JSON满足构造schema，图片返回可见信息，关闭思考行为和原始usage符合已审契约，用量不超已验证界，已知费用不超预留。任一错误、费用未知、认证失败、截断、上界矛盾均立即停止，已失败请求也计入6次名额；保存回执、保留预留、人工对账，不自动重发。缓存未命中本身不算失败；缓存字段缺失只允许依已审unknown策略记上界估算，不能称精确费用。

这6次全部是接口检查，**语义校准调用数为0**。后续语义fixtures、原定阈值及小批次授权另行审核，不能借本计划启动。

## 保护与交付

本轮基线9240个既有文件全部哈希不变，无删除；git porcelain前后相同。冻结批次、原草稿、生成输出、既有评价记录/费用账本、代码配置及他人改动保持现状。正式共同账本仍不存在。细节见 `protected-before.json`、`protection-result.json`、`git-before.txt`、`git-after.txt`。

主要交付：三份新草稿、binding-evidence.md、execution-plan.json、environment-instructions.md、两个离线检查脚本及其JSON结果、来源页尺寸/渲染记录。模型调用0；本轮结束后停止，等待总控审核。
