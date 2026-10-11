from .settings import (
    # 模型 endpoint（OpenAI 兼容格式，按角色划分）
    LLM_ENDPOINT, LVLM_ENDPOINT, NLI_JUDGE_ENDPOINT, NLI_VISION_JUDGE_ENDPOINT,
    L2_JUDGE_ENDPOINT,
    ModelEndpoint, chat_completion, create_openai_client,
    # 向后兼容
    create_qwen_client,
    # 资源路径
    EMBEDDER_PATH, TOPN_MODEL_PATH, PCA_ASSETS,
    # Milvus / MinIO
    MILVUS_URI, MILVUS_TOKEN,
    MINIO_ENDPOINT, MINIO_ACCESS_KEY, MINIO_SECRET_KEY, MINIO_BUCKET_NAME, MINIO_SECURE,
    # 业务/算法超参数
    TOPK, MAX_SLOT_ROUNDS, PAGE_COUNT_RATIO, ANSWER_CONVERGENCE_REPEAT,
    OVERLAP_RATE_MAX, QUERY_RETRY_MAX, MIN_NOVEL_PAGES_PER_ROUND,
    MAX_PAGE_CHAIN,
    GOAL_MIN_EVIDENCE_CARDS, GOAL_MIN_CONFIDENCE, GOAL_COHESION_MAX_SPAN_RATIO,
    DRA_MAX_ROUNDS,
)

# 注意：MilvusColbertRetriever / client / uri 不在此导出。
# 需要它们的模块直接 `from config.milvus import ...`，
# 避免轻量消费者（crest 等）一旦 `from config import` 就把 pymilvus 全套带进来。

__all__ = [
    "LLM_ENDPOINT", "LVLM_ENDPOINT", "NLI_JUDGE_ENDPOINT", "NLI_VISION_JUDGE_ENDPOINT",
    "L2_JUDGE_ENDPOINT",
    "ModelEndpoint", "chat_completion", "create_openai_client", "create_qwen_client",
    "EMBEDDER_PATH", "TOPN_MODEL_PATH", "PCA_ASSETS",
    "MILVUS_URI", "MILVUS_TOKEN",
    "MINIO_ENDPOINT", "MINIO_ACCESS_KEY", "MINIO_SECRET_KEY", "MINIO_BUCKET_NAME", "MINIO_SECURE",
    "TOPK", "MAX_SLOT_ROUNDS", "PAGE_COUNT_RATIO", "ANSWER_CONVERGENCE_REPEAT",
    "OVERLAP_RATE_MAX", "QUERY_RETRY_MAX", "MIN_NOVEL_PAGES_PER_ROUND",
    "MAX_PAGE_CHAIN",
    "GOAL_MIN_EVIDENCE_CARDS", "GOAL_MIN_CONFIDENCE", "GOAL_COHESION_MAX_SPAN_RATIO",
    "DRA_MAX_ROUNDS",
]
