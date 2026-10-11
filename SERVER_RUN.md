# AUCMR 服务器运行入口

不需要自己判断整条流程走到哪里。`SERVER_RUN.md` 是说明书，真正的流程控制入口是：

```bash
python run.py guide
```

`guide` 只做只读检查，不调用模型、不写 Milvus，也不开始实验。它每次只输出当前阶段、下一条命令和该命令完成后的判断条件。执行它给出的命令，再次运行 `guide`，直到显示 `DONE`。

## 第一次开始

```bash
cd /home/dzy/code/TOSEM2026/AUCMR

# 每次新 shell 都要设置；真实密钥不要写进 experiment.json 或命令历史文件
export AUCMR_DEEPSEEK_API_KEY='...'
export AUCMR_DASHSCOPE_API_KEY='...'

python run.py guide
```

如果希望脚本或监控程序读取状态：

```bash
python run.py guide --json
```

JSON 中 `next.status` 为 `READY`、`BLOCKED` 或 `DONE`，`next.command` 是下一条命令；`next.branches` 是完成条件与异常处理。

## 状态机实际顺序

`guide` 按以下顺序检查，前一项未满足时不会越过它：

1. Python 依赖与 ROCm PyTorch；
2. 两个 API key 环境变量；
3. 两份 PDF、完整页面截图、Jina、LinUCB 和 PCA 文件；
4. `jtvauctions` 与 `milstd6016b_sec4` 两个图像 collection；
5. 两个项目的 MinerU 结果与 `{project}_text` collection；
6. 两个项目的 VDocRAG 官方页面向量缓存；
7. AUCMR full 和 w/o cross-slot 的 18 个候选用例；
8. 五个 baseline 各自的 18 个候选用例；
9. 共同有效用例上的 L1/L2 裁判评测与论文表格。

生成命令均带 `--resume`。只有当前活动目录中的“结果 JSON + evidence JSON”成对存在且身份与当前英文用例一致时才会跳过，因此中断后直接再次执行相同命令即可。`outputs/history`、`outputs/archive` 和开发目录不会参与完成度、续跑或评测统计。若修改过活动生成结果，`guide` 会发现评测文件早于最新结果，并要求重新执行 `evaluate`。

如果修改了算法、prompt、normalizer 或最终实验配置，先把当前活动结果归档，再开始新一轮；不要让 `--resume` 复用旧代码生成的结果：

```bash
python run.py archive --label <descriptive-version>
```

归档写入 `outputs/history/<descriptive-version>/`，保留当时的 `experiment.json` 与 `archive_manifest.json`，并且不会移动 `_composites`、`_watermarked` 等昂贵缓存。重复 label 会被拒绝，不会覆盖历史。

## 关键分支

### 环境依赖

保留服务器已经可用的 ROCm PyTorch，只补应用依赖：

```bash
python -m pip install -r requirements-rocm.txt
```

`requirements-rocm.txt` 故意不包含 `torch`。检查结果应当是 `torch.version.hip` 非空且 GPU 可用：

```bash
python -c "import torch; print(torch.__version__, torch.version.hip, torch.cuda.is_available())"
```

若 `torch` 本身缺失，必须根据服务器 ROCm 版本安装官方 ROCm wheel，不能用普通 `pip install torch` 代替。

### 图像 collection

当前 `jtvauctions` 图像 collection 已经做过后期交互式多模态检索预处理。新 `6016B-sec4-visual-excerpt.pdf` 有 146 页，旧的 `data/images/surveillance_6016b` 只有其 4.4 段的 88 页，不能作为最终输入。最终配置使用新的项目与目录 `milstd6016b_sec4`，避免混用旧 88 页 collection。

`guide` 会先要求补齐新 PDF 的 146 张页面图：

```bash
python run.py prepare-images --projects milstd6016b_sec4
```

然后才会要求创建新的图像 collection：

```bash
python run.py build-image --projects milstd6016b_sec4
```

`build-image` 自身也会再次校验 PDF 页数与截图完整性，因此不会把不完整目录导入 Milvus。
6016B 图像嵌入在当前 AMD 设备上不是秒级操作：入口按单页小批流式编码，默认每 10 页提交一次 MinIO parquet。终端会持续显示 `Encoding and staging pages`；全部 146 页完成后才会出现 Milvus job ID。编码阶段不要清屏后误开第二个 `build-image` 进程。

若编码或提交明确抛错，`data/image_pipeline_jobs/<project>.json` 会记录 `status=failed`、异常和恢复命令。只有确认无活动 import 且 collection 的 `row_count=0` 时，才使用入口给出的 `--recreate-empty`；它不会删除有数据的 collection。

若 JTV collection 存在且有数据，什么也不用做。只有它不存在时才执行：

```bash
python run.py build-image --projects jtvauctions
```

该命令直接复用 `data/images/jtvauctions` 中已有的页面，不重复截图，并打印 Milvus bulk-import job ID，同时留档到 `data/image_pipeline_jobs/jtvauctions.json`。按输出中的 `pipelines.check_progress` 命令等到 `Completed`，再运行 `guide`。如果 collection 已存在（即使 `row_count=0`），统一入口会拒绝追加；`guide` 会从留档中恢复监控命令。

### 首次 MinerU 与文本 collection

当前按“从未运行 MinerU、当前环境、CPU pipeline”处理：

```bash
uv pip install "mineru[pipeline]"
mineru --version
python -c "import torch; print(torch.version.hip, torch.cuda.is_available())"
python run.py guide
```

安装完成后，`guide` 会按缺失项目给出命令。JTV 已完成；6016B 首次执行为：

```bash
python run.py build-text --projects milstd6016b_sec4
```

它执行 PDF → MinerU content-list → 图/表 LVLM caption → 纯文本 → 分块 → Jina v4 multi-vector → Milvus `milstd6016b_sec4_text`。中间产物在 `data/text_pipeline/milstd6016b_sec4/`；caption 缓存在中断后会复用。默认不会覆盖已存在的 text collection；只有明确决定废弃旧索引时才使用 `--recreate-text`。

### VDocRAG 与 ViDoRAG

`guide` 会先要求建立官方 VDocRetriever 的页面向量缓存：

```bash
python run.py build-vdocrag --projects milstd6016b_sec4
```

VDocRAG 使用上游 VDocRetriever。当前 AMD ROCm 配置显式使用 Phi-3 Vision 自带的 `eager` attention，不安装 NVIDIA `flash_attn`；它与官方 FlashAttention2 计算同一注意力，只是速度较慢并可能有微小数值差异。ViDoRAG 使用上游 Seeker → Inspector → Synthesizer，但候选检索器替换为与 AUCMR 相同的页面检索口径。二者缺权重或依赖时直接失败，不生成冒充正式结果的降级输出。

### 生成与断点续跑

`guide` 先生成成对消融，再按固定次序运行 baseline：

```bash
python run.py generate --resume --methods "AUCMR (CREST full)" "CREST w/o cross-slot"
python run.py generate --resume --methods "LVLM-Direct"
python run.py generate --resume --methods "TextRAG-Elaborate"
python run.py generate --resume --methods "VDocRAG-Elaborate"
python run.py generate --resume --methods "ViDoRAG-Elaborate"
python run.py generate --resume --methods "ReAct-Elaborate"
```

不用手工依次复制这些命令；每完成一项运行 `python run.py guide`，它会给出尚未完成的一项。

LVLM-Direct 会把 PDF 的全部页面依次压缩并拼成少量图片，在每个槽位的一次 LVLM 调用中同时输入全部拼接图。缓存位于 `outputs/baselines/_composites/<project>/`。

### 只重跑 AUCMR 的跨槽 DRA

如果 `crest_without_dra` 的 result JSON 与 evidence JSON 已经落盘，而修改只涉及 DRA 检测、槽归属或修复逻辑，不要重复前面的检索和 8 槽生成。使用：

```bash
python run.py revise
```

该命令从 `outputs/crest_without_dra/<project>/` 恢复完整 UseCase 与 EvidenceCard，执行当前 `algorithm.dra_max_rounds` 配置，再写入 `outputs/aucmr_full/<project>/`。它会覆盖对应 AUCMR full 开发结果，因此正式结果需要保留时应先执行 `python run.py archive --label <version>`。`--resume` 只适用于目标 full 尚未完成的批量恢复；算法更新后的主动重算不要带 `--resume`。

`revise` 不重新调用 Query Designer、页面检索或 LVLM Evidence Collector；如修改了 Goal、每槽检索/生成、active-card 或 gap-driven stopping，必须重新执行 `generate`，不能用 `revise` 代替。

### L1 / L2 自动评测

七种方法的 18 个候选用例都齐全后，执行：

```bash
python run.py evaluate
```

评测先依据 AUCMR full 的 `unresolvable_use_case` 取所有方法的共同有效集合。最终设计为 18 个候选、14 个有效、4 个应在 Goal 阶段拒绝。L1 对除外部给定 `primary_actor` 外的全部原子断言计算 CitCov、NLI-G、CHG、ESC；纯文本证据使用 DeepSeek 裁判，含页面图像的证据切换到 Qwen3.5 Plus。L2 使用 DeepSeek 对每个 Detail-UC 独立判断 R1–R7。

输出包括：

- `outputs/evaluation_cohort.json`：共同有效集合；
- `outputs/evaluation_audit.json`：逐项裁判审计；
- `outputs/aggregated_metrics.json`：聚合指标；
- `outputs/table_l1.tex` 与 `outputs/table_l2_consistency.tex`：论文表格。

裁判调用或 JSON 解析失败会使正式聚合停止，不会静默按 neutral 或零违规处理。修复接口后重复 `python run.py evaluate` 即可，不会重跑生成。L3 需求覆盖度和 L4 专家质量评估仍需人工标注/专家评审。

## 配置位置

所有路径、服务、模型、算法、18 个英文用例和评测开关统一在 `experiment.json`。相对路径均以该文件所在目录为基准。当前配置约定：

- 主 LLM：DeepSeek `deepseek-v4-flash`；
- 主 LVLM：Qwen `qwen3.5-plus`；
- 纯文本 L1 与 L2 裁判：DeepSeek `deepseek-v4-pro`；
- 含图像的 L1 裁判：Qwen `qwen3.5-plus`；
- ROCm 上的 PyTorch device 仍写 `cuda` / `cuda:0`；
- CMAB-TopN 使用 `models/LinUCB.py` 中与训练一致的 v2 14 维状态构造；启动前会校验模型与 PCA 的状态维度和训练 split；
- Hugging Face 下载端点由 `runtime.hf_endpoint` 统一配置，当前服务器使用 `https://hf-mirror.com`；
- 当前结果写入 `outputs/aucmr_full`、`outputs/crest_without_dra` 与 `outputs/baselines`；历史版本统一位于 `outputs/history`，且不会混入续跑或评测。

模型名以后变化时只改 `experiment.json`，不改业务代码。
