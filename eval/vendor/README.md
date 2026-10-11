# 外部基线接入

本目录保存三个上游开源仓库；AUCMR 通过同目录 adapter 把它们适配到统一的 Cockburn 槽位调度。正式实验不存在静默占位或自动降级：依赖、权重、图像或检索资产缺失时直接报错。

| 方法 | 上游实现 | AUCMR 中保留/替换的部分 |
|---|---|---|
| VDocRAG-Elaborate | `VDocRAG/src/vdocrag/vdocretriever` | 使用官方 VDocRetriever（Phi-3 Vision + LoRA）编码槽位查询和页面；生成器替换成论文规定的共享槽位生成器 |
| ViDoRAG-Elaborate | `ViDoRAG/vidorag_agents.py` | 使用官方 Seeker、Inspector、Synthesizer；候选检索器注入 AUCMR 同一页面检索器，Synthesizer 的短问答提示替换成槽位提示 |
| ReAct-Elaborate | `ReAct/` | 使用 ReAct 思考—行动—观察循环；唯一工具替换成 AUCMR 页面检索 |

VDocRAG 的依赖由根目录 `requirements.txt` 统一安装，源码由 adapter 直接从本目录载入，不需要再安装一个副本。模型和 LoRA 名称、设备、dtype、注意力实现、缓存目录与 top-k 都在 `experiment.json` 中。`python run.py build-vdocrag` 会下载/加载配置的权重并建立页面向量缓存；`generate` 发现缓存有效时直接复用。

ViDoRAG 不使用上游的 GMM/ColQwen 搜索服务，因为本实验明确要求可替换检索器并与 AUCMR 对齐。`registry.get_retrieval_adapter()` 先给出至多 20 个候选页面，官方 Seeker/Inspector 做迭代筛选，官方 Synthesizer 的图像拼接和页面映射继续保留。

统一检查命令：

```bash
python run.py preflight --methods VDocRAG-Elaborate ViDoRAG-Elaborate
python -c "from eval.vendor import print_status; print_status()"
```
