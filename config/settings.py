import json
import os
from pathlib import Path
from dataclasses import dataclass
from typing import Optional


_PACKAGE_ROOT = Path(__file__).resolve().parents[1]
_CONFIG_PATH = Path(os.environ.get("AUCMR_CONFIG", _PACKAGE_ROOT / "experiment.json")).expanduser().resolve()


def _load_runtime_config() -> dict:
    """Load the optional server-run configuration without making imports fragile."""
    if not _CONFIG_PATH.is_file():
        return {}
    with _CONFIG_PATH.open("r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError(f"AUCMR config must be a JSON object: {_CONFIG_PATH}")
    return data


_RUNTIME_CONFIG = _load_runtime_config()


_HTTPX_PROXY_ENV_NAMES = (
    "ALL_PROXY", "all_proxy",
    "HTTP_PROXY", "http_proxy",
    "HTTPS_PROXY", "https_proxy",
)


def normalize_httpx_proxy_environment(environment=None) -> list[str]:
    """Normalize Clash-style SOCKS URLs for HTTPX/OpenAI clients."""
    target = os.environ if environment is None else environment
    normalized = []
    for name in _HTTPX_PROXY_ENV_NAMES:
        value = target.get(name)
        if value and value.lower().startswith("socks://"):
            target[name] = f"socks5://{value[len('socks://') :]}"
            normalized.append(name)
    return normalized


def _cfg(path: str, default=None):
    value = _RUNTIME_CONFIG
    for key in path.split("."):
        if not isinstance(value, dict) or key not in value:
            return default
        value = value[key]
    return value


def _configured_path(path: str, default: str) -> str:
    value = str(_cfg(path, default))
    candidate = Path(value).expanduser()
    if not candidate.is_absolute():
        candidate = _CONFIG_PATH.parent / candidate
    return str(candidate.resolve())


# ============================================================
# 模型统一配置（OpenAI 兼容格式）
#
# 每个角色一个 `ModelEndpoint`，字段:
#   - api_key      调用凭证（写空字符串可走本地免鉴权）
#   - base_url     OpenAI 兼容端点 URL（结尾含 /v1）
#   - model_name   模型标识（与所连端点支持的 model 一致）
#   - temperature  推理温度，None 走服务端默认
#   - max_tokens   单次响应 token 上限，None 走服务端默认
#
# 所有走 LLM/LVLM 调用的代码必须从这里取配置，不要再硬编码模型名。
# ============================================================

@dataclass
class ModelEndpoint:
    api_key: str = ""
    base_url: str = ""
    model_name: str = ""
    temperature: Optional[float] = None
    max_tokens: Optional[int] = None


def _make_endpoint(api_key: str, base_url: str, model_name: str,
                   temperature: Optional[float] = None,
                   max_tokens: Optional[int] = None) -> ModelEndpoint:
    return ModelEndpoint(
        api_key=api_key,
        base_url=base_url,
        model_name=model_name,
        temperature=temperature,
        max_tokens=max_tokens,
    )


def _endpoint_from_config(role: str, defaults: dict) -> ModelEndpoint:
    section = _cfg(f"models.{role}", {}) or {}
    key_env = section.get("api_key_env", defaults.get("api_key_env", ""))
    api_key = os.environ.get(key_env, "") if key_env else section.get("api_key", "")
    return _make_endpoint(
        api_key=api_key,
        base_url=section.get("base_url", defaults.get("base_url", "")),
        model_name=section.get("model_name", defaults.get("model_name", "")),
        temperature=section.get("temperature", defaults.get("temperature")),
        max_tokens=section.get("max_tokens", defaults.get("max_tokens")),
    )


# --- 角色 1：CREST 内部的 Query Designer / Answer Generator / DRA patcher ---
# 论文: query_designer + answer_generator + patcher 全部使用 Qwen3-Max
LLM_ENDPOINT = _endpoint_from_config("llm", {
    "api_key_env": "AUCMR_LLM_API_KEY",
    "base_url": "http://127.0.0.1:8000/v1",
    "model_name": "CHANGE_ME_LLM",
})

# --- 角色 2：CREST 内部的 Evidence Collector（多模态页面图像读取）---
# 论文: evidence_collector 使用 Qwen3-VL-235B-A22B-Instruct
LVLM_ENDPOINT = _endpoint_from_config("lvlm", {
    "api_key_env": "AUCMR_LVLM_API_KEY",
    "base_url": "http://127.0.0.1:8001/v1",
    "model_name": "CHANGE_ME_LVLM",
})

# --- 角色 3：L1 NLI 裁判 ---
# 文本与多模态证据均配置为同一 Qwen3.5-Plus 裁判，避免跨方法模型混杂。
NLI_JUDGE_ENDPOINT = _endpoint_from_config("nli_judge", {
    "api_key_env": "AUCMR_NLI_API_KEY",
    "base_url": "http://127.0.0.1:8002/v1",
    "model_name": "CHANGE_ME_NLI_JUDGE",
    "temperature": 0.0,
})

# L1 references may be text chunks or page images. Both roles must point to the
# same model deployment; this second endpoint only enables image attachments.
NLI_VISION_JUDGE_ENDPOINT = _endpoint_from_config("nli_vision_judge", {
    "api_key_env": "AUCMR_NLI_VISION_API_KEY",
    "base_url": "http://127.0.0.1:8003/v1",
    "model_name": "CHANGE_ME_NLI_VISION_JUDGE",
    "temperature": 0.0,
})

# --- 角色 4：L2 R1-R7 跨槽位一致性裁判 ---
# 与 L1 共用同一 Qwen3.5-Plus 裁判，温度 0。
L2_JUDGE_ENDPOINT = _endpoint_from_config("l2_judge", {
    "api_key_env": "AUCMR_L2_API_KEY",
    "base_url": "http://127.0.0.1:8002/v1",
    "model_name": "CHANGE_ME_L2_JUDGE",
    "temperature": 0.0,
})


def create_openai_client(endpoint: ModelEndpoint):
    """统一的 OpenAI 兼容客户端工厂。"""
    normalize_httpx_proxy_environment()
    from openai import OpenAI
    return OpenAI(api_key=endpoint.api_key, base_url=endpoint.base_url)


def create_qwen_client():
    """向后兼容的 helper，等价于 create_openai_client(LLM_ENDPOINT)。"""
    return create_openai_client(LLM_ENDPOINT)


# 模型路径配置
EMBEDDER_PATH = _configured_path("paths.embedder", "models/jina-embeddings-v4")
TOPN_MODEL_PATH = _configured_path("paths.linucb", "models/linucb_model.pkl")
PCA_ASSETS = _configured_path("paths.pca", "models/pca_assets.pkl")
N_ARMS = int(_cfg("algorithm.n_arms", 10))
N_FEATURES = int(_cfg("algorithm.n_features", 11))
BETA = float(_cfg("algorithm.beta", 0.5))


# ============================================================
# 通用 LLM 客户端工厂
# ============================================================


def chat_completion(
    endpoint: ModelEndpoint,
    messages,
    image_paths=None,
    think: bool = False,
):
    """统一的 chat completion 入口，按 endpoint 自动区分 LLM vs LVLM 调用。

    - image_paths 为空/None → 纯文本调用
    - image_paths 非空 → 把图片 base64 编码后塞进 user message（OpenAI Vision 格式）
    - think → llama.cpp 服务端 chat_template_kwargs 开关
    """
    from services.model_service import image_to_base64
    client = create_openai_client(endpoint)

    image_paths = image_paths or []

    # 构造 user message content
    if image_paths:
        content = []
        for p in image_paths:
            try:
                b64 = image_to_base64(p)
            except Exception:
                continue
            content.append({
                "type": "image_url",
                "image_url": {"url": f"data:image/png;base64,{b64}"},
            })
        if messages and messages[-1].get("role") == "user":
            content.append({"type": "text", "text": messages[-1]["content"]})
            messages = messages[:-1] + [{"role": "user", "content": content}]
    # model 不带图片时走纯文本分支；带图片时按 endpoint.model_name 调用
    completion_kwargs = {
        "model": endpoint.model_name,
        "messages": messages,
        "stream": False,
    }
    if endpoint.temperature is not None:
        completion_kwargs["temperature"] = endpoint.temperature
    if endpoint.max_tokens is not None:
        completion_kwargs["max_tokens"] = endpoint.max_tokens
    # llama.cpp 控制开关
    completion_kwargs["extra_body"] = {"chat_template_kwargs": {"enable_thinking": think}}

    completion = client.chat.completions.create(**completion_kwargs)
    return completion.choices[0].message.content



# Milvus 配置
MILVUS_URI = str(_cfg("services.milvus.uri", "http://127.0.0.1:19530"))
MILVUS_TOKEN = str(_cfg("services.milvus.token", "root:Milvus"))


# MinIO 配置
MINIO_ENDPOINT = str(_cfg("services.minio.endpoint", "localhost:9000"))
MINIO_ACCESS_KEY = os.environ.get(
    str(_cfg("services.minio.access_key_env", "AUCMR_MINIO_ACCESS_KEY")), "minioadmin"
)
MINIO_SECRET_KEY = os.environ.get(
    str(_cfg("services.minio.secret_key_env", "AUCMR_MINIO_SECRET_KEY")), "minioadmin"
)
MINIO_BUCKET_NAME = str(_cfg("services.minio.bucket", "a-bucket"))
MINIO_SECURE = bool(_cfg("services.minio.secure", False))


# 业务配置
# Fix 3: 调大 TOPK，给跨页 section（如 4 页连续区域）留余量；
# 跑完 Bidding Section 这类多页区域不会被 topk=3 截断
TOPK = int(_cfg("algorithm.topk", 5))


# 算法超参数 — DCRRM Loop（per-slot）
MAX_SLOT_ROUNDS = int(_cfg("algorithm.max_slot_rounds", 5))
PAGE_COUNT_RATIO = float(_cfg("algorithm.page_count_ratio", 0.8))
ANSWER_CONVERGENCE_REPEAT = int(_cfg("algorithm.answer_convergence_repeat", 2))
QUERIES_PER_ROUND = int(_cfg("algorithm.queries_per_round", 2))

# 算法超参数 — Query Designer
OVERLAP_RATE_MAX = float(_cfg("algorithm.overlap_rate_max", 0.65))
QUERY_RETRY_MAX = int(_cfg("algorithm.query_retry_max", 3))
# Fix 3: 每轮检索必须带回的最小新页面数；与 OVERLAP_RATE_MAX 联合判定终止——
# 既高度重叠 (>OVERLAP_RATE_MAX) 又没有任何新页面 (novel<MIN) 才终止，
# 避免 4 页 section 被 "{25,26,28} 与 {25,26,27} 重叠 0.67" 锁死最后一页
MIN_NOVEL_PAGES_PER_ROUND = int(_cfg("algorithm.min_novel_pages_per_round", 2))

# 算法超参数 — Evidence Collector
MAX_PAGE_CHAIN = int(_cfg("algorithm.max_page_chain", 3))

# 算法超参数 — Goal Slot Fail-Fast
GOAL_MIN_EVIDENCE_CARDS = int(_cfg("algorithm.goal_min_evidence_cards", 1))
GOAL_MIN_CONFIDENCE = float(_cfg("algorithm.goal_min_confidence", 0.3))
# Fix 7.2: Goal 证据 cohesion 阈值 — 引用的卡跨页 span 占文档总页数比例上限。
# 超过则判定 Goal 包含多个不相关 use case（典型例子：page 13 bid + page 130 checkout）。
# 0.4 表示证据跨度不能超过文档 40%；jtvauctions 145 页 → 阈值 58 页。
GOAL_COHESION_MAX_SPAN_RATIO = float(_cfg("algorithm.goal_cohesion_max_span_ratio", 0.4))

# 算法超参数 — DRA Pass (Fix 5)
# Detect → Repair 循环的最大轮数，最后一轮 Repair 之后才转 Annotate；
# 设 2 是为了让 slot_rewrite 之后的连锁不一致能再修一次（如 trigger 也需要重写）
DRA_MAX_ROUNDS = int(_cfg("algorithm.dra_max_rounds", 2))

# 进入 Answer Generator 的活动证据卡预算。完整卡片仍全部落盘；这里只限制提示上下文。
ACTIVE_CARD_BUDGETS = {
    slot: int(_cfg(f"algorithm.active_card_budget.{slot}", default))
    for slot, default in {
        "goal": 6,
        "primary_actor": 4,
        "preconditions": 12,
        "trigger": 6,
        "main_flow": 24,
        "alternative_flow": 16,
        "exception_flow": 16,
        "postconditions": 12,
    }.items()
}


# ROCm / HIP 稳定性配置
EMBEDDER_DEVICE = str(_cfg("runtime.embedder_device", "cpu"))
# 设为 "cpu" 可避免与 llama-server 等 GPU 进程争夺 HIP 驱动导致死锁
TORCH_ROCM_AOTRITON_ENABLE_EXPERIMENTAL = os.environ.get(
    "TORCH_ROCM_AOTRITON_ENABLE_EXPERIMENTAL", "0"
)
