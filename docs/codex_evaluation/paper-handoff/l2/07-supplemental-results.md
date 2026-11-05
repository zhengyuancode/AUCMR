# L2 三份重生成的补充结果（不用于论文主表）

**论文主实验仍为原冻结输入的646项完成、89个共同有效用例。** 本页只保存三份重生成输出及其已完成L2评价，以及历史649项完成、91个共同用例的合并视图。三份新输入不参与主表，也不与原输入的L1、L4分数直接配对；L1、L4保持现状。

## 三份补充输出的独立结果

| 用例 | 方法 | R1 | R2 | R3 | R4 | R5 | R6 | R7 | 总次数 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Maintain Voucher Service Provider Records | TextRAG-Elaborate | 0 | 0 | 1 | 1 | 0 | 0 | 0 | 2 |
| Review a Provider Claim Status | AUCMR | 1 | 1 | 0 | 0 | 0 | 0 | 0 | 2 |
| Review a Provider Claim Status | CREST w/o cross-slot check | 1 | 1 | 0 | 0 | 0 | 0 | 0 | 2 |

对应输入正文分别为2、13、13条断言。输入、记录及六份实际响应分别保存在 [data/supplemental/inputs.json](data/supplemental/inputs.json)、[final_records.json](data/supplemental/final_records.json)、[accepted_responses.json](data/supplemental/accepted_responses.json)。所有原判断、请求和响应原样保留；路径与原／新输入哈希见 [data/input_substitutions.json](data/input_substitutions.json)。

这三项L2补充评价共六次响应，已记录估算0.011708元，包含在历史L2合计3.234992元中；生成费用另列且未据此清零。主实验的五项生成覆盖缺失不会因补充成功而消失。

## 历史649项、91例视图的复现

使用651项主记录并仅替换上述三项，得到649项完成、0项技术失败、2项无可评价内容、91个共同有效用例。此视图明确标为 `SUPPLEMENTAL_THREE_REGENERATED_INPUTS`、`paper_primary=false`。机器表保留旧字段名 `primary_common_case_table` 仅用于该补充视图内部的共同集合，不能据此将其当作论文主表。

该合并视图的未替换648项仍引用主记录，不重复复制；其整数次数、均值、覆盖及不适用统计与历史649/91表一致。完整精度见 [data/supplemental/tables.json](data/supplemental/tables.json)，默认核验命令输出的仍是89例主表，必须显式加 `--scope supplemental` 才输出本页口径。

### 补充表S1：共同91例整数违规次数

| 方法 | N | R1 | R2 | R3 | R4 | R5 | R6 | R7 | 总计 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| LVLM-Direct | 91 | 17 | 22 | 4 | 1 | 4 | 9 | 8 | 65 |
| TextRAG-Elaborate | 91 | 26 | 45 | 47 | 12 | 3 | 84 | 8 | 225 |
| VDocRAG-Elaborate | 91 | 28 | 31 | 39 | 6 | 9 | 66 | 42 | 221 |
| ViDoRAG-Elaborate | 91 | 36 | 43 | 40 | 10 | 14 | 84 | 50 | 277 |
| ReAct-Elaborate | 91 | 25 | 31 | 20 | 6 | 4 | 29 | 25 | 140 |
| CREST w/o cross-slot check | 91 | 12 | 39 | 27 | 1 | 6 | 11 | 29 | 125 |
| AUCMR | 91 | 16 | 32 | 22 | 0 | 6 | 24 | 33 | 133 |

### 补充表S2：共同91例每用例均值

| 方法 | N | R1 | R2 | R3 | R4 | R5 | R6 | R7 | 总计 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| LVLM-Direct | 91 | 0.19 | 0.24 | 0.04 | 0.01 | 0.04 | 0.10 | 0.09 | 0.71 |
| TextRAG-Elaborate | 91 | 0.29 | 0.49 | 0.52 | 0.13 | 0.03 | 0.92 | 0.09 | 2.47 |
| VDocRAG-Elaborate | 91 | 0.31 | 0.34 | 0.43 | 0.07 | 0.10 | 0.73 | 0.46 | 2.43 |
| ViDoRAG-Elaborate | 91 | 0.40 | 0.47 | 0.44 | 0.11 | 0.15 | 0.92 | 0.55 | 3.04 |
| ReAct-Elaborate | 91 | 0.27 | 0.34 | 0.22 | 0.07 | 0.04 | 0.32 | 0.27 | 1.54 |
| CREST w/o cross-slot check | 91 | 0.13 | 0.43 | 0.30 | 0.01 | 0.07 | 0.12 | 0.32 | 1.37 |
| AUCMR | 91 | 0.18 | 0.35 | 0.24 | 0.00 | 0.07 | 0.26 | 0.36 | 1.46 |

### 补充表S3：该视图的完成覆盖

| 方法 | 计划 | 完成 | 技术失败 | 无可评价 | 完成率 |
| --- | --- | --- | --- | --- | --- |
| LVLM-Direct | 93 | 93 | 0 | 0 | 100.00% |
| TextRAG-Elaborate | 93 | 93 | 0 | 0 | 100.00% |
| VDocRAG-Elaborate | 93 | 91 | 0 | 2 | 97.85% |
| ViDoRAG-Elaborate | 93 | 93 | 0 | 0 | 100.00% |
| ReAct-Elaborate | 93 | 93 | 0 | 0 | 100.00% |
| CREST w/o cross-slot check | 93 | 93 | 0 | 0 | 100.00% |
| AUCMR | 93 | 93 | 0 | 0 | 100.00% |

整体649/651＝99.69%，共同91/93＝97.85%，仅描述补充视图，不替代原冻结主实验覆盖率。

### 补充表S4：该共同集合内规则不适用用例数

| 方法 | R1 | R2 | R3 | R4 | R5 | R6 | R7 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| LVLM-Direct | 39 | 38 | 60 | 35 | 39 | 63 | 62 |
| TextRAG-Elaborate | 7 | 2 | 2 | 1 | 2 | 9 | 27 |
| VDocRAG-Elaborate | 3 | 4 | 4 | 3 | 5 | 33 | 9 |
| ViDoRAG-Elaborate | 6 | 3 | 6 | 3 | 5 | 30 | 18 |
| ReAct-Elaborate | 16 | 10 | 9 | 7 | 11 | 26 | 35 |
| CREST w/o cross-slot check | 5 | 4 | 1 | 0 | 0 | 17 | 16 |
| AUCMR | 5 | 4 | 1 | 0 | 0 | 17 | 16 |

### 补充表S5：该视图全部可用输出

| 方法 | N | R1 | R2 | R3 | R4 | R5 | R6 | R7 | 总次数 | 总均值 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| LVLM-Direct | 93 | 17 | 22 | 4 | 1 | 4 | 9 | 9 | 66 | 0.7097 |
| TextRAG-Elaborate | 93 | 26 | 46 | 47 | 12 | 3 | 84 | 8 | 226 | 2.4301 |
| VDocRAG-Elaborate | 91 | 28 | 31 | 39 | 6 | 9 | 66 | 42 | 221 | 2.4286 |
| ViDoRAG-Elaborate | 93 | 37 | 43 | 41 | 11 | 14 | 84 | 50 | 280 | 3.0108 |
| ReAct-Elaborate | 93 | 25 | 32 | 20 | 6 | 4 | 29 | 25 | 141 | 1.5161 |
| CREST w/o cross-slot check | 93 | 12 | 40 | 27 | 1 | 6 | 11 | 30 | 127 | 1.3656 |
| AUCMR | 93 | 16 | 33 | 22 | 0 | 6 | 24 | 34 | 135 | 1.4516 |

各方法分母不同，不能将本表与89例主表拼接。不得将三份新输入上的L2值与原输入上的L1、L4值配成同一制品的跨层总评。
