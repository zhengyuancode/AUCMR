"""
AUCMR 模型注册中心 — 单例 + 懒加载 + 依赖注入。

统一管理所有模型组件（embeder / LinUCB / PCA / Milvus retriever / LLM client），
幂等初始化，通过工厂方法注入到调用方，避免各处重复初始化。

用法:
    from services.model_registry import registry

    # 设置 Milvus 集合
    registry.set_collection("OR2UC", file_names=["jtvauctions"])

    # 获取注入好的检索函数
    retrieval_fn = registry.get_retrieval_adapter()

    # 获取注入好的 CREST ModelProvider
    provider = registry.get_model_provider(think=False)

    # 也可以直接访问底层组件
    embeder = registry.embeder
    pages = registry.total_pages
"""
import os
from config.settings import TORCH_ROCM_AOTRITON_ENABLE_EXPERIMENTAL
os.environ.setdefault(
    "TORCH_ROCM_AOTRITON_ENABLE_EXPERIMENTAL",
    TORCH_ROCM_AOTRITON_ENABLE_EXPERIMENTAL,
)
import logging
import pickle
from typing import Callable, List, Optional

# ---------------------------------------------------------------------------
# ROCm 实验性高效注意力 — 必须在 import torch 之前设置
# 原位置: pipelines/run_preprocess.py:21
# 用 setdefault 允许外部提前覆盖
# ---------------------------------------------------------------------------


logger = logging.getLogger(__name__)


class ModelRegistry:
    """
    单例模型注册中心。

    所有模型组件通过 @property 懒加载，首次访问时初始化，后续直接返回缓存。
    通过工厂方法 get_retrieval_adapter() / get_model_provider() 实现依赖注入。
    """

    _instance: Optional["ModelRegistry"] = None

    def __new__(cls) -> "ModelRegistry":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._init_state()
        return cls._instance

    def _init_state(self):
        """初始化内部状态（仅首次 __new__ 时调用）。"""
        # --- 缓存 ---
        self._embeder = None
        self._linucb = None
        self._pca_assets = None
        self._retriever = None
        self._llm_client = None

        # --- Milvus 集合信息 ---
        self._collection: Optional[str] = None
        self._file_names: Optional[List[str]] = None
        self._total_pages: int = 0
        self._topk: int = 5

    # ================================================================
    # 懒加载属性
    # ================================================================

    @property
    def embeder(self):
        """
        Jina embeddings v4 模型（多向量模式）。

        初始化参数:
          - local_files_only=True
          - trust_remote_code=True
          - torch_dtype=torch.float16
          - 加载到 cuda
        """
        if self._embeder is None:
            import torch
            from transformers import AutoModel
            from config.settings import EMBEDDER_PATH, EMBEDDER_DEVICE

            device = EMBEDDER_DEVICE
            if device not in ("cpu", "cuda"):
                raise ValueError(
                    f"EMBEDDER_DEVICE must be 'cpu' or 'cuda', got '{device}'"
                )
            if device == "cuda" and not torch.cuda.is_available():
                raise RuntimeError(
                    "EMBEDDER_DEVICE='cuda' but torch.cuda.is_available() == False. "
                    "Likely causes: another process is holding the GPU "
                    "(check `rocm-smi --showpidgpus`), the GPU is in a "
                    "low-power/reset state, ROCm/PyTorch versions are mismatched, "
                    "or /dev/kfd and /dev/dri are unavailable in a container. "
                    "Set EMBEDDER_DEVICE='cpu' in config/settings.py to bypass."
                )

            logger.info(
                "Loading Jina embedding model (v4, multi-vector): "
                f"device={device}, "
                f"hip={torch.version.hip}, "
                f"aotriton={os.environ.get('TORCH_ROCM_AOTRITON_ENABLE_EXPERIMENTAL')}"
            )
            dtype = torch.float16 if device == "cuda" else torch.float32
            model = AutoModel.from_pretrained(
                EMBEDDER_PATH,
                trust_remote_code=True,
                torch_dtype=dtype,
                local_files_only=True,
            )
            model.to(device)
            model.eval()
            self._embeder = model
            logger.info(f"Embedding model ready (device={device}).")
        return self._embeder

    @property
    def linucb(self):
        """CMAB-TopN 在线学习策略 (LinUCB)。"""
        if self._linucb is None:
            from models.LinUCB import OnlineLinUCB
            from config.settings import TOPN_MODEL_PATH

            logger.info("Loading CMAB-TopN (LinUCB) model...")
            self._linucb = OnlineLinUCB.load_model(TOPN_MODEL_PATH)
            logger.info(
                "LinUCB model ready (format=v2, arms=%s, state_dim=%s, scenario=%s).",
                self._linucb.n_arms,
                self._linucb.n_features,
                self._linucb.metadata.get("scenario", "unknown"),
            )
        return self._linucb

    @property
    def pca_assets(self):
        """PCA 降维资产（用于检索 query 特征提取）。"""
        if self._pca_assets is None:
            from config.settings import PCA_ASSETS

            logger.info("Loading PCA assets...")
            with open(PCA_ASSETS, "rb") as f:
                self._pca_assets = pickle.load(f)
            logger.info("PCA assets ready.")
        return self._pca_assets

    @property
    def retriever(self):
        """Milvus 多模态检索器。需要先调用 set_collection()。"""
        if self._retriever is None:
            if self._collection is None:
                raise RuntimeError(
                    "Must call registry.set_collection(project=...) before accessing retriever"
                )
            from config.milvus import MilvusColbertRetriever, client as milvus_client

            logger.info(f"Connecting to Milvus collection '{self._collection}'...")
            self._retriever = MilvusColbertRetriever(
                collection_name=self._collection,
                milvus_client=milvus_client,
            )
            logger.info("Milvus retriever ready.")
        return self._retriever

    @property
    def llm_client(self):
        """llama.cpp OpenAI-compatible 客户端。"""
        if self._llm_client is None:
            from config.settings import create_qwen_client

            logger.info("Creating LLM client (llama.cpp OpenAI-compatible)...")
            self._llm_client = create_qwen_client()
            logger.info("LLM client ready.")
        return self._llm_client

    # ================================================================
    # 集合信息（只读）
    # ================================================================

    @property
    def file_names(self) -> List[str]:
        return self._file_names or []

    @property
    def total_pages(self) -> int:
        return self._total_pages

    @property
    def topk(self) -> int:
        return self._topk

    # ================================================================
    # 配置方法
    # ================================================================

    def set_collection(self, project: str, file_names: Optional[List[str]] = None):
        """
        设置 Milvus 集合和文件过滤。

        调用后 retriever / file_names / total_pages 会被初始化。
        重复调用（切换集合）会重建 retriever。
        """
        from config.milvus import client as milvus_client

        # 验证集合是否存在
        if not milvus_client.has_collection(collection_name=project):
            available = milvus_client.list_collections()
            raise ValueError(
                f"Collection '{project}' does not exist in Milvus. "
                f"Available collections: {available}"
            )

        if self._collection != project or self._file_names != file_names:
            # 切换集合 → 重置 retriever
            self._collection = project
            self._retriever = None
            self._file_names = None
            self._total_pages = 0

        # 触发 retriever 初始化
        _ = self.retriever

        # 未指定 file_names → 自动查集合内所有 PDF
        if self._file_names is None:
            if file_names is None:
                all_files = self.retriever.search_all_file()
                self._file_names = list({f["file_name"] for f in all_files})
                logger.info(f"Auto-detected files in '{self._collection}': {self._file_names}")
                if not self._file_names:
                    logger.warning(
                        f"No files found in collection '{self._collection}'. "
                        f"Verify that data was imported correctly."
                    )
            else:
                self._file_names = file_names

        self._total_pages = self.retriever.count_page_by_file(self._file_names)
        from config.settings import TOPK
        self._topk = TOPK

        logger.info(
            f"Collection ready. Project='{self._collection}', "
            f"Files={self._file_names}, Total pages={self._total_pages}, TopK={self._topk}"
        )

    # ================================================================
    # 工厂方法（依赖注入）
    # ================================================================

    def get_retrieval_adapter(self) -> Callable[[List[str], List[str], int], List[List[str]]]:
        """
        返回检索适配器函数，已注入 embeder / linucb / pca_assets / retriever。

        返回的函数签名:
            (queries: List[str], file_names: List[str], topk: int) -> List[List[str]]

        每个 query 返回一个页面路径列表。
        """
        from utils.retrieval import retrieval_service

        # 触发懒加载
        _embeder = self.embeder
        _linucb = self.linucb
        _pca = self.pca_assets
        _retriever = self.retriever

        from models.LinUCB import validate_artifact_pair
        validate_artifact_pair(_linucb, _pca)

        def adapter(queries: List[str], file_names: List[str], topk: int) -> List[List[str]]:
            return retrieval_service(
                queries, file_names, topk,
                _embeder, _linucb, _pca, _retriever,
            )

        logger.info("Retrieval adapter created (embeder + linucb + pca + retriever injected).")
        return adapter

    def get_model_provider(self, think: bool = False):
        """
        返回 CREST ModelProvider，已注入 llm_client。

        qwen 签名: qwen(AIclient, image_paths, userInput, assistant="", isOverLap=False, think=False)

        参数:
            think: 是否启用推理/思考模式
        """
        from services.model_service import qwen as qwen_call
        from crest.agents import ModelProvider

        _client = self.llm_client

        def llm_fn(prompt: str, image_paths: List[str] = None,
                   assistant: str = "", is_overlap: bool = False) -> str:
            return qwen_call(_client, image_paths or [], prompt,
                             assistant=assistant, isOverLap=is_overlap, think=think)

        def lvlm_fn(prompt: str, image_paths: List[str],
                     assistant: str = "", is_overlap: bool = False) -> str:
            return qwen_call(_client, image_paths, prompt,
                             assistant=assistant, isOverLap=is_overlap, think=think)

        logger.info(f"ModelProvider created (think={think}).")
        return ModelProvider(llm_call=llm_fn, lvlm_call=lvlm_fn)

    # ================================================================
    # 一键初始化（便捷方法）
    # ================================================================

    def init_all(self, project: str, file_names: Optional[List[str]] = None):
        """
        一键初始化所有组件（幂等）。

        等价于依次触发所有懒加载属性 + set_collection。
        """
        self.set_collection(project, file_names)
        _ = self.embeder
        _ = self.linucb
        _ = self.pca_assets
        _ = self.llm_client
        logger.info("All components initialized.")


# ================================================================
# 模块级单例
# ================================================================

registry = ModelRegistry()
