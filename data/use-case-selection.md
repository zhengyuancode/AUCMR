# AUCMR 工业 PDF 节选与候选用例清单

## 1. MIL-STD-6016B 第 4 章节选

输出文件：`6016B-sec4-visual-excerpt.pdf`

| 节选页 | 原 PDF 页 / 章内页 | 小节 | 页数 | 选择理由 |
|---|---:|---|---:|---|
| 1–88 | 147–234 / 4-147–4-234 | 4.4 Surveillance | 88 | 覆盖监视数据、报告责任、轨迹相关、身份、IFF/SIF、终止与清除等完整交互链；含相关测试表、窗口表、消息规则及公式。 |
| 89–146 | 263–320 / 4-263–4-320 | 4.7 Information Management | 58 | 覆盖差异消解、轨迹管理、数据更新、双重标识消解、IFF/SIF 管理和过滤；集中包含多张规则矩阵表与几何过滤图。 |

合计 146 页。两段之间的 4.5 Electronic Warfare（原 PDF 235–256）和 4.6 Amplification（257–262）被有意排除。节选至少包含 21 个带编号表格的页面和 1 个带编号图形的页面，另有多页公式、矩阵与结构化消息规则。该组合虽然跨节，但 4.4 产生和维护监视数据，4.7 处理这些数据的纠错、更新、过滤和标识消解，语义上构成闭环。

## 2. JTVauctions：9 个候选用例（6 个有效，3 个刻意无效）

| ID | 主要参与者 | 候选用例 | 判定 | 可用于精化的主要依据 / 无效理由 |
|---|---|---|---|---|
| JTV-01 | Customer | Place Bid on Auction Item | 有效 | PDF 19–28：商品模块、QuickBid/SmartBid、登录检查、投标确认弹窗、投标后行为和页面原型。 |
| JTV-02 | Customer | Register Customer Account | 有效 | PDF 39–47：两阶段注册、最低必填信息、支付方式、条款确认、重复账户处理及三张原型图。 |
| JTV-03 | Customer | Browse and Search Products | 有效 | PDF 19–22、77–83：分类/搜索结果、筛选排序、面包屑、搜索与导航层级，以及列表/目录视图原型。 |
| JTV-04 | Customer | Complete Checkout and Payment | 有效 | PDF 59、129–135：信用卡自动结算、PayPal/Amazon Payments、返回获胜者支付页、超期未付与账户状态。 |
| JTV-05 | Customer | Manage Account Settings | 有效 | PDF 48–58：账户信息、账单/配送地址、配送偏好、支付方式、邮件偏好及编辑页原型。 |
| JTV-06 | Customer | Monitor Current and Past Auction Status | 有效 | PDF 60–67：当前投标、关注列表、投标历史、已结束拍卖、排序、状态列和对应页面原型。 |
| JTV-07 | Customer | List Personal Jewelry for Auction | **无效（无文档依据）** | 文档没有消费者卖家角色、发布表单、审核或上架流程；PDF 8 和 145 还明确说明平台是多零售商供货而非 consumer-to-consumer 模型。 |
| JTV-08 | Customer | Retract a Confirmed Bid | **无效（无文档依据）** | PDF 25–28 的 Cancel 仅发生在“确认并提交投标”之前；PDF 130 明确说明投标在获胜后产生支付义务。全文没有已提交投标的撤回入口、条件或后置状态。 |
| JTV-09 | Customer | Transfer a Winning Order to Another Customer | **无效（无文档依据）** | PDF 129–135 将获胜订单、默认支付方式和支付页绑定到获胜者账户；全文没有订单受让人、转让动作、校验规则或通知流程。 |

负例设计说明：JTV-07 至 JTV-09 都符合拍卖系统中“表面上可能存在”的操作，但源材料无法支持 Cockburn 模板中的目标、触发器、前置条件、主流程和后置条件，因此应在目标槽位快速失败，而不是补写常识性流程。

## 3. MIL-STD-6016B 节选：9 个候选用例（8 个有效，1 个不可精化）

下表页码均指原 PDF / 章内页；换算到新节选时，4.4 的页码减 146，4.7 的页码减 174。

| ID | 主要参与者 | 候选用例 | 判定 | 可用于精化的主要依据 / 无效理由 |
|---|---|---|---|---|
| S16-01 | C2 JU | Publish Air and Surface Surveillance Tracks | 有效 | 4.4、4.4.4，PDF 147、169–200：J3.2/J3.3 消息、报告准则、相关、位置/速度/强度/质量、R² 与更新规则。 |
| S16-02 | C2 JU | Resolve Dual Designations via Track Correlation | 有效 | 4.4.4.3.1，PDF 171–184；4.7.3，PDF 287–292：相关测试、保留/丢弃 TN、限制条件、J7.2 消息与冲突分支。 |
| S16-03 | C2 JU | Manage Track Identity Classification | 有效 | 4.4.2.1，PDF 150–152；4.7.1.2.2，PDF 266–272：身份类别、身份差异识别、Table 4.7-2 消解矩阵和操作员告警。 |
| S16-04 | C2 JU | Terminate and Purge Surveillance Tracks | 有效 | 4.4.4.16，PDF 199–200；4.7.1，PDF 263–265：J7.0 Drop Track、无需发送的例外、远程轨迹超时清除。 |
| S16-05 | C2 JU | Transfer Reporting Responsibility | 有效 | 4.4.2.2，PDF 152–156；4.4.4.14，PDF 197–199：R² 建立、保持、转移/放弃、质量比较和无更新分支。 |
| S16-06 | C2 JU | Exchange IFF and SIF Transponder Data | 有效 | 4.4.4.9，PDF 190–193；4.7.6，PDF 292–298：监视数据报告、差异报告、清除、更新请求和特殊码处理。 |
| S16-07 | Non-C2 JU | Receive Situational Awareness Data | 有效 | PDF 147、169、233–234：监听 Surveillance NPG、接收轨迹和参考点数据、禁止在该 NPG 发送、处理 Drop Track 与本地超时清除。 |
| S16-08 | System Operator | Initiate Emergency Point Reports | 有效 | 4.4.3.2.2，PDF 166；4.4.3.8，PDF 167：仅由操作员发起、附加/更新信息、R² 接管、终止和超时清除。 |
| S16-09 | C2 JU | Define Reference Points and Geometric Areas | **无效（不可作为单一 Detail-UC 精化）** | 表面上有依据（4.4.3.1，PDF 158–165；4.7.7，PDF 299–305），但候选名称把参考点创建、线/区域编码、从属几何、过滤描述等多个目标合并为一个用例；证据分散且不存在单一局部目标/触发器/完成状态，应由目标槽位判为“多用例混杂”并快速失败。 |

## 4. 与当前论文的同步提醒

`samples/main-zh.tex` 当前实验设置仍写 PDF-B “约 70 页”，且表 `tab:uc-names` 把 JTV 的 Browse and Search Products、Schedule Auction Event 标成无效。前者在 PDF 19–22、77–83 有充分依据，后者在 PDF 14、93–103 也有明确需求与原型，因此不能再用作“文档找不到依据”的负例。

若采用本清单，论文应同步改为：PDF-B 共 146 页；JTV 有效用例为 JTV-01 至 JTV-06、无效用例为 JTV-07 至 JTV-09；6016B 有效用例为 S16-01 至 S16-08、不可精化用例为 S16-09。总计仍是 18 个候选、14 个有效、4 个无效，与当前 L1–L4 的有效样本数量口径一致；但更换候选名称后，既有运行输出与数值结果必须重新生成和复核，不能只改表中文字。
