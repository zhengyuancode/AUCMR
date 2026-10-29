# 正式用例清单与 L4 任务对应

确定日期：2026-10-29T17:21:25+08:00。清单版本：`unified93-source-reviewed-20261029-v1`。

本次确定保留全部 **93 个目标用例**，新增排除 0 项、待定 0 项。每个目标对应既定七种方法，共 **651 个 L4 任务**。

这是依据六份源 PDF 完成的代理来源审查，发生在生成及部分评价之后，不是盲审、预注册筛选或真人领域专家确认。纳入只确认目标有来源依据且范围可以界定，不保证生成内容或评价结果有效。

旧论文表6为18选14；被排除的4项目标已不在本批93项中。本批保留旧14项并增加79项，不再次扣除4项。L2七种方法共同完成的74项是可用结果子集，不决定本名单。

`FUC-001`至`FUC-093`仅增加稳定清单编号；原target_id、英文任务名、参与者、方法和原输出均不变。页码均为一基PDF文件页码，不是页脚印刷编号。中文名称仅供阅读，不替换评价输入。

| 项目 | 正式目标 | 七方法任务 |
| --- | ---: | ---: |
| JTV 拍卖 | 20 | 140 |
| MIL-STD-6016B | 19 | 133 |
| QHeadache | 8 | 56 |
| Microcare | 16 | 112 |
| RLCS | 19 | 133 |
| PeaZip | 11 | 77 |
| 合计 | 93 | 651 |

## JTV 拍卖

| 清单号 | 原target_id | 原参与者 | 目标（中文索引 / 原名） | PDF页 |
| --- | --- | --- | --- | --- |
| FUC-001 | `JTV-01` | Customer | 对拍卖商品出价 / Place Bid on Auction Item | 25, 26 |
| FUC-002 | `JTV-02` | Customer | 注册客户账户 / Register Customer Account | 39, 40, 42, 43, 44 |
| FUC-003 | `JTV-03` | Customer | 浏览和搜索商品 / Browse and Search Products | 16, 21, 22, 81, 82 |
| FUC-004 | `JTV-04` | Customer | 结算并支付获拍订单 / Complete Checkout and Payment | 129, 130, 131 |
| FUC-005 | `JTV-05` | Customer | 管理账户设置 / Manage Account Settings | 48, 49, 51, 53, 55 |
| FUC-006 | `JTV-06` | Customer | 查看当前及历史竞拍状态 / Monitor Current and Past Auction Status | 60, 61, 62, 64, 65 |
| FUC-033 | `UC-9061895d3523d931` | Business Owner | 分析拍卖业务表现 / Analyze Auction Business Performance | 112, 114, 115, 117, 118 |
| FUC-034 | `UC-e5a498730abdcee8` | Business Owner | 分析客户购买活动 / Analyze Customer Purchasing Activity | 112, 120, 122 |
| FUC-035 | `UC-27b9f6ad9a4ecdf4` | Auctioneer | 分析库存可用性及库龄 / Analyze Inventory Availability and Aging | 124, 125, 127 |
| FUC-036 | `UC-c36fef189e184056` | Auctioneer | 配置全局拍卖规则 / Configure Global Auction Rules | 90, 91, 127 |
| FUC-037 | `UC-50e3a23f95e15ec4` | Auctioneer | 编辑或移除已有拍卖 / Edit or Remove Existing Auctions | 102, 103, 104, 127 |
| FUC-038 | `UC-0db007c82b9353dc` | Customer Service Representative | 调查客户拍卖咨询 / Investigate Customer Auction Inquiries | 109, 127 |
| FUC-039 | `UC-0a9b830db1bf17fd` | Auctioneer | 维护拍卖活动配置模板 / Maintain Auction Event Profiles | 93, 94 |
| FUC-040 | `UC-77f9f9d12382413b` | Auctioneer | 管理拍卖后台用户权限 / Manage Auction Administration User Access | 105, 106, 107, 127 |
| FUC-041 | `UC-edfa7d4431250d85` | Customer | 找回遗忘的账户访问信息 / Recover Forgotten Account Access | 36 |
| FUC-042 | `UC-8a43321a20b0cf29` | Customer | 响应第二次购买机会 / Respond to a Second-Chance Purchase Offer | 136, 137 |
| FUC-043 | `UC-d6419877cc32c1e1` | Customer Service Representative | 恢复未付款客户的出价权限 / Restore Bidding Privileges After Nonpayment | 135 |
| FUC-044 | `UC-e23930218cfb0032` | Auctioneer | 查看 SKU 拍卖表现 / Review SKU Auction Performance | 95, 96 |
| FUC-045 | `UC-b9a57628a917d78f` | Auctioneer | 安排新的拍卖活动 / Schedule New Auction Events | 97, 98, 99, 127 |
| FUC-046 | `UC-a0b239f33bac2a8b` | Customer Service Representative | 暂停或停用客户账户 / Suspend or Deactivate Customer Accounts | 109, 127 |

## MIL-STD-6016B

| 清单号 | 原target_id | 原参与者 | 目标（中文索引 / 原名） | PDF页 |
| --- | --- | --- | --- | --- |
| FUC-007 | `S16-01` | C2 JU | 发布空中和水面监视航迹 / Publish Air and Surface Surveillance Tracks | 1, 24, 25, 51 |
| FUC-008 | `S16-02` | C2 JU | 通过航迹关联解决双重标识 / Resolve Dual Designations via Track Correlation | 25, 26, 33, 39, 113, 114 |
| FUC-009 | `S16-03` | C2 JU | 管理航迹身份分类 / Manage Track Identity Classification | 4, 5, 43, 90, 91, 92 |
| FUC-010 | `S16-04` | C2 JU | 终止和清除监视航迹 / Terminate and Purge Surveillance Tracks | 53, 54 |
| FUC-011 | `S16-05` | C2 JU | 转移报告责任 / Transfer Reporting Responsibility | 51, 52 |
| FUC-012 | `S16-06` | C2 JU | 交换 IFF/SIF 应答机数据 / Exchange IFF and SIF Transponder Data | 47, 48, 118, 119, 120, 121, 122, 123 |
| FUC-013 | `S16-07` | Non-C2 JU | 接收态势感知数据 / Receive Situational Awareness Data | 1, 87, 88 |
| FUC-014 | `S16-08` | System Operator | 发起紧急点报告 / Initiate Emergency Point Reports | 12, 13, 20 |
| FUC-047 | `UC-ebe20697e727f762` | C2 JU | 关联相关监视航迹与点 / Associate Related Surveillance Tracks and Points | 137, 138 |
| FUC-048 | `UC-329ce9eb6a4e99cf` | C2 JU | 依据演习状态命令清除演习数据 / Clear Exercise Surveillance Data on an Exercise Status Order | 107, 108 |
| FUC-049 | `UC-5d871faa2b899b9a` | C2 JU | 协调监视发送过滤器 / Coordinate Surveillance Transmit Filters | 124, 125, 133, 134, 135, 137 |
| FUC-050 | `UC-fc4b3d8f61a5f5ed` | C2 JU | 解除已分离航迹的关联 / Decorrelate Diverging Air or Surface Tracks | 39, 40, 41 |
| FUC-051 | `UC-8be19df9253962a1` | C2 JU | 发起和清除监视航迹告警 / Initiate and Clear Surveillance Track Alerts | 100, 104, 105 |
| FUC-052 | `UC-ac31ca03f4206d22` | C2 JU | 发布弹道导弹监视报告 / Publish Ballistic Missile Surveillance Reports | 64, 65, 66 |
| FUC-053 | `UC-4efd41ee189bfdc8` | C2 JU | 发布电子战监视产品 / Publish Electronic Warfare Surveillance Products | 82, 83, 84, 85, 86 |
| FUC-054 | `UC-97ef3f19d7c7ecb3` | C2 JU | 发布水下监视及声学接触报告 / Publish Subsurface Surveillance and Acoustic Contact Reports | 54, 55, 56, 58 |
| FUC-055 | `UC-c64867e2d8f5a767` | C2 JU | 请求跨链路航迹标识 / Request Cross-Link Track Identifiers | 117, 118 |
| FUC-056 | `UC-46fbab4741209ad4` | C2 JU | 请求监视数据更新 / Request Surveillance Data Updates | 108, 109, 110 |
| FUC-057 | `UC-822279b81e1bd73f` | C2 JU | 向其他单元共享地理指针 / Share a Geographic Pointer with Other Units | 117 |

## QHeadache

| 清单号 | 原target_id | 原参与者 | 目标（中文索引 / 原名） | PDF页 |
| --- | --- | --- | --- | --- |
| FUC-015 | `UC-0b113737d3ff758c` | Player | 打开已保存的游戏 / Open a Saved Game | 12 |
| FUC-016 | `UC-243f662de0626f07` | Player | 保存当前游戏 / Save the Current Game | 12, 13 |
| FUC-017 | `UC-0b3c9df6a8bfa59d` | Player | 撤销最后一次方块移动 / Undo the Last Block Movement | 8 |
| FUC-018 | `UC-20f211b787631cb3` | Player | 完成拼图 / Complete a Puzzle | 9, 10 |
| FUC-058 | `UC-6094a643936a7ee0` | Player | 清除全部玩家统计 / Erase All Recorded Player Statistics | 11 |
| FUC-059 | `UC-6aa56069d69b81e5` | Player | 退出并处理未保存进度 / Exit the Game with Unsaved Progress Handling | 13 |
| FUC-060 | `UC-7928476e7ef4eea9` | Player | 重做最后撤销的方块移动 / Redo the Last Undone Block Movement | 8 |
| FUC-061 | `UC-9b5faf3c5de8492a` | Player | 查看玩家统计 / View Recorded Player Statistics | 11, 12 |

## Microcare

| 清单号 | 原target_id | 原参与者 | 目标（中文索引 / 原名） | PDF页 |
| --- | --- | --- | --- | --- |
| FUC-019 | `UC-2cb5d0624cdb3935` | VMU User | 登记凭证发放 / Record Voucher Distribution | 10, 16, 17 |
| FUC-020 | `UC-c40f364edd2c3d89` | MSIU Field Office User | 登记理赔材料提交 / Register a Claim Submission | 10, 21, 22 |
| FUC-021 | `UC-c0e26d379fe00f5a` | MSIU Field Office User | 录入并校验治疗理赔 / Enter and Validate a Treatment Claim | 22, 23, 24, 25, 26, 28 |
| FUC-022 | `UC-8b152029c7f5f1f4` | VMU User | 登记凭证销售退回 / Record a Voucher Sales Return | 29, 30 |
| FUC-023 | `UC-d986372bbf63d9ab` | MSIU Field Office User | 登记客户反馈 / Record Client Feedback | 30, 31 |
| FUC-062 | `UC-9b88321373e19c4d` | System Administrator | 配置用户组权限 / Configure User Group Permissions | 32, 33 |
| FUC-063 | `UC-e5b0a7ac1c6745f0` | System Administrator | 创建用户账户 / Create a User Account | 33 |
| FUC-064 | `UC-4ca8948f91fd20ca` | VMU User | 创建并打印凭证批次 / Create and Print a Voucher Batch | 11, 12, 13 |
| FUC-065 | `UC-0322f7e7255c507e` | MSIU Field Office User | 生成服务提供者报销报告 / Generate Provider Reimbursement Reports | 11, 31, 32 |
| FUC-066 | `UC-aa11f730f216bb96` | MSIU Field Office User | 生成治疗质量与趋势报告 / Generate Treatment Quality and Trend Reports | 31, 32 |
| FUC-067 | `UC-6999407edd2b3515` | VMU User | 维护经销商记录 / Maintain Distributor Records | 13, 14, 15 |
| FUC-068 | `UC-5378f77e90aa8b68` | VMU User | 维护凭证服务提供者记录 / Maintain Voucher Service Provider Records | 17, 18, 19, 20 |
| FUC-069 | `UC-39c6d8ad25afbea3` | MSIU Field Office User | 登记服务提供者工作人员 / Register a VSP Staff Member | 20, 21 |
| FUC-070 | `UC-59d553a6d05431d0` | VMU User | 登记 MSIU 销售员 / Register an MSIU Salesperson | 15 |
| FUC-071 | `UC-5b28c9094c95712f` | VMU User | 查看经销商销售历史 / Review a Distributor Sales History | 14 |
| FUC-072 | `UC-5e1b75fbd45da207` | VMU User | 查看服务提供者理赔状态 / Review a Provider Claim Status | 20 |

## RLCS

| 清单号 | 原target_id | 原参与者 | 目标（中文索引 / 原名） | PDF页 |
| --- | --- | --- | --- | --- |
| FUC-024 | `UC-a751f4143e0e101d` | Operator | 取得系统指挥控制权 / Acquire System Command Control | 11, 19 |
| FUC-025 | `UC-101b7b9803530ada` | Operator | 开启可逆车道入口 / Open a Reversible-Lane Entrance | 19, 28, 58, 59 |
| FUC-026 | `UC-b0989afaa1f40cdb` | Operator | 确认设备告警 / Acknowledge a Device Alarm | 23 |
| FUC-027 | `UC-1646fc0e8df691fb` | System Administrator | 配置员工访问权限 / Configure Staff Access Permissions | 11, 20, 21, 29 |
| FUC-028 | `UC-7843a66cc126f0a0` | Management | 生成故障汇总报告 / Generate a Failure Summary Report | 11, 31 |
| FUC-073 | `UC-4011f09c36ad7e16` | Operator | 改变系统运行模式 / Change the System Operating Mode | 11, 23 |
| FUC-074 | `UC-6a3d56be66e07d8a` | Operator | 关闭可逆车道入口 / Close a Reversible-Lane Entrance | 28, 59, 60 |
| FUC-075 | `UC-15acc98d9d7d801b` | System Administrator | 配置运行计划 / Configure Operational Schedules | 11, 27, 28, 34 |
| FUC-076 | `UC-bbdf0380c70f468b` | Field Maintenance Staff | 诊断故障设备 / Diagnose a Failed Device | 11, 23 |
| FUC-077 | `UC-996bd53fdb4ae8c9` | Operator | 显示及导出系统信息日志 / Display and Export System Information Logs | 21, 22 |
| FUC-078 | `UC-1346b235ce9f7289` | Control Unit | 初始化新上线的控制单元 / Initialize a Newly Online Control Unit | 26 |
| FUC-079 | `UC-d6943f4b2b050e3d` | Operator | 维护问题工单 / Maintain a Problem Work Order | 21, 27 |
| FUC-080 | `UC-2b2bc0ede1efd80d` | Operator | 监视设施及设备状态 / Monitor Facility and Device Status | 20, 22 |
| FUC-081 | `UC-f07afa7ba4e0f0c8` | Operator | 记录操作员工作日记 / Record an Operator Daily Diary | 22 |
| FUC-082 | `UC-961b6e2852cd66c9` | Management | 调取历史报告 / Retrieve Historical Reports | 11, 22, 34 |
| FUC-083 | `UC-ce3c4bf6f700e639` | External Traffic System | 获取公开的 RLCS 状态 / Retrieve Published RLCS Status | 11, 18 |
| FUC-084 | `UC-6260b20e8f3e0093` | System Administrator | 修订设备命令安全规则 / Revise Device Command Safety Rules | 21, 26, 30 |
| FUC-085 | `UC-3de20ca5ecd805b8` | System Administrator | 更新设施地图及显示设备 / Update the Facility Map and Displayed Devices | 20, 21 |
| FUC-086 | `UC-0add6e4f536011c2` | Operator | 核验控制单元完整性 / Verify Control Unit Integrity | 30, 61 |

## PeaZip

| 清单号 | 原target_id | 原参与者 | 目标（中文索引 / 原名） | PDF页 |
| --- | --- | --- | --- | --- |
| FUC-029 | `UC-fb12681b7e9f5a34` | User | 创建压缩归档 / Create a Compressed Archive | 17, 18 |
| FUC-030 | `UC-9bd694a3162f3147` | User | 向已有归档添加文件 / Add Files to an Existing Archive | 17, 18 |
| FUC-031 | `UC-47eb5a5e72f296eb` | User | 解压归档 / Extract an Archive | 15, 16 |
| FUC-032 | `UC-aa8473435416bb2b` | User | 生成密码和密钥文件 / Generate a Password and Keyfile | 20, 21, 22, 23 |
| FUC-087 | `UC-fc6fc84083c75f61` | User | 浏览和搜索文件及归档 / Browse and Search Files and Archives | 8, 9, 10, 29, 30 |
| FUC-088 | `UC-b5e0be4a8cc19292` | User | 检查重复文件和文件损坏 / Check Files for Duplicates and Corruption | 14 |
| FUC-089 | `UC-d83a4e8e66e6f3d1` | User | 逐字节比较两个文件 / Compare Two Files Byte by Byte | 14 |
| FUC-090 | `UC-0f00eefffe105f06` | User | 配置应用偏好 / Configure Application Preferences | 24, 25, 26 |
| FUC-091 | `UC-ae03c6e134913ca2` | User | 将未压缩文件复制到其他位置 / Copy Uncompressed Files to Another Location | 19, 20 |
| FUC-092 | `UC-5bf2661fcc7a6a11` | User | 查看文件及归档信息 / Inspect File and Archive Information | 14, 23, 24 |
| FUC-093 | `UC-d9cc94863eb2c8c0` | User | 安全删除文件和文件夹 / Securely Delete Files and Folders | 14 |

## 来源判定和范围

逐项纳入理由、范围限制、章节、原文定位片段、PDF及文本哈希保存在 formal-cohort.json。主要边界如下：

- JTV早期用户故事存在待补文字，但后续详细需求提供了这些目标的规则；按详细需求核查。
- 军事标准包含平台/协议单元间交互，不要求全部任务都是人工界面操作。紧急点报告的具体初始界面未规定，不据此虚构按钮步骤。
- Microcare的报表目标已列明，部分布局留待设计；角色授权按原文限制，不推定任意用户均有权限。
- RLCS的外部交通系统和控制单元是接口参与者，不算内部登录用户；维护角色不能改变系统模式。
- PeaZip保留格式、平台和已知校验值限制；例如从应用向系统拖放仅在文档所述Windows条件下成立。

这些说明用于核查名单，不注入旧请求、不修改模型提示或量表。源文档支持目标，并不表示每个槽位都必须有非空内容。

## L4任务分配与已有结果

l4-task-allocation.json按“一个目标 × 一种方法”列出651项，组织成93个目标包；每项有原输出路径与哈希、源PDF、完整上下文路径与哈希、已有artifact_id、记录路径、处理状态及保留方式。七种方法沿用同一目标的完整来源上下文，所列证据页不是缩减后的评价输入。

| 既定方法代码 | 方法 |
| --- | --- |
| M01 | AUCMR |
| M02 | CREST w/o cross-slot check |
| M03 | LVLM-Direct |
| M04 | TextRAG-Elaborate |
| M05 | VDocRAG-Elaborate |
| M06 | ViDoRAG-Elaborate |
| M07 | ReAct-Elaborate |

| 已有记录状态 | 数量 | 本次处理 |
| --- | ---: | --- |
| COMPLETED | 55 | 保留，不重发 |
| TECHNICAL_FAILURE | 591 | 保留失败，继续暂停 |
| EMPTY_OUTPUT | 3 | 保留空输出记录，不补分 |
| REFUSED | 2 | 保留拒绝记录，不补分 |

现有记录分别来自v3.0.6两用例小批次和其余91例批次，来源分别保留；核对相同模型、专家提示、量表和实现指纹，不复制为本次新生成结果。这里没有汇总分数，也没有结清未知费用。

任务索引继续引用当前的 l4-primary / glm-4.6v，不新设模型或评审人格。93个目标包不是93位专家；本次也没有创建26名从业者或三模型复核实验。后续若另行设计复核者分配，应以本名单为固定目标全集。

L4仍暂停，live_authorized=false；本次调用0次。没有创建可直接运行的评价配置，没有授权重发失败项。

## 文件和校验

- [formal-cohort.json](/home/dzy/code/TOSEM2026/AUCMR/outputs/evaluation_preparation/formal-cohort-source-review-20261029/formal-cohort.json)
- [l4-task-allocation.json](/home/dzy/code/TOSEM2026/AUCMR/outputs/evaluation_preparation/formal-cohort-source-review-20261029/l4-task-allocation.json)
- [checks.json](/home/dzy/code/TOSEM2026/AUCMR/outputs/evaluation_preparation/formal-cohort-source-review-20261029/checks.json)
- [artifact-hashes.json](/home/dzy/code/TOSEM2026/AUCMR/outputs/evaluation_preparation/formal-cohort-source-review-20261029/artifact-hashes.json)

本轮核对6份源PDF、93项来源定位、651份输入绑定、651项历史L4记录和93份上下文。重新计算6383个保护文件的哈希，无变化。评价代码、冻结批次、原始输出、已有L4响应/组件、L2记录和共同费用账本均保持原状；原批次的NOT_REVIEWED字段未被覆盖。
