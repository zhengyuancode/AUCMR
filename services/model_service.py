"""
AUCMR 模型服务 — OpenAI 兼容格式封装。

按角色绑定 endpoint：
  - LLM_ENDPOINT       (Qwen3-Max)               → Query Designer / Answer Generator / DRA patcher
  - LVLM_ENDPOINT      (Qwen3-VL-235B-A22B-Inst) → Evidence Collector
  - NLI_JUDGE_ENDPOINT (GPT-5.4)                  → L1 NLI 裁判
  - L2_JUDGE_ENDPOINT  (GPT-5.4)                  → L2 R1-R7 裁判

所有 endpoint 配置集中在 config/settings.py，按需修改。
"""

import base64
import logging
from typing import List, Optional

from config.settings import (
    LLM_ENDPOINT,
    LVLM_ENDPOINT,
    ModelEndpoint,
    create_openai_client,
)

logger = logging.getLogger(__name__)


def get_endpoint(role: str) -> ModelEndpoint:
    """根据 role 字符串返回对应 endpoint，便于上层按 agent 角色选 endpoint。"""
    mapping = {
        "llm": LLM_ENDPOINT,        # Qwen3-Max
        "query_designer": LLM_ENDPOINT,
        "answer_generator": LLM_ENDPOINT,
        "patcher": LLM_ENDPOINT,
        "lvlm": LVLM_ENDPOINT,      # Qwen3-VL-235B-A22B-Instruct
        "evidence_collector": LVLM_ENDPOINT,
    }
    if role not in mapping:
        raise ValueError(f"Unknown model role: {role}")
    return mapping[role]

# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------

def image_to_base64(image_path: str) -> str:
    """将图片文件编码为 base64 字符串。"""
    with open(image_path, "rb") as f:
        return base64.b64encode(f.read()).decode("utf-8")


# ---------------------------------------------------------------------------
# 内部 helper：构建消息内容
# ---------------------------------------------------------------------------

def _build_content(user_input: str, image_paths: List[str]):
    """
    构建 messages 中单条消息的 content 字段。

    - 无图片：返回纯文本字符串
    - 有图片：返回 OpenAI Vision API 格式的 content list
    """
    if not image_paths:
        return user_input

    content: list = []
    for path in image_paths:
        try:
            b64 = image_to_base64(path)
        except Exception as e:
            logger.error(f"Failed to encode image {path}: {e}")
            continue
        content.append({
            "type": "image_url",
            "image_url": {"url": f"data:image/png;base64,{b64}"},
        })
    content.append({"type": "text", "text": user_input})
    return content


def _retry_user_message(is_overlap: bool):
    """返回 retry 场景下追加的 user message 文本。"""
    if is_overlap:
        return (
            "The query design in your previous response was too similar to the queries "
            "already used. You now have two options: the first is to redesign the list "
            "of queries, and the second is to return an empty list []."
        )
    else:
        return (
            "The format of the previous response was incorrect and could not be parsed. "
            "Please strictly follow the required format and return no extra text."
        )


def _build_messages(user_input: str, image_paths: List[str],
                    assistant: str, is_overlap: bool) -> list:
    """
    构建完整的 messages 列表。

    三种场景:
      - assistant 为空 → 单条 user message
      - assistant 非空 + is_overlap → user / assistant / retry(overlap)
      - assistant 非空 + !is_overlap → user / assistant / retry(format)
    """
    content = _build_content(user_input, image_paths)

    if not assistant:
        return [{"role": "user", "content": content}]

    # retry 场景：上一轮 assistant 输出有问题，追加纠错指令
    retry_text = _retry_user_message(is_overlap)
    return [
        {"role": "user", "content": content},
        {"role": "assistant", "content": assistant},
        {"role": "user", "content": retry_text},
    ]


# ---------------------------------------------------------------------------
# 主接口
# ---------------------------------------------------------------------------

def qwen(
    AIclient,
    image_paths: List[str],
    userInput: str,
    assistant: str = "",
    isOverLap: bool = False,
    think: bool = False,
    endpoint: Optional[ModelEndpoint] = None,
) -> str:
    """AUCMR 内部 LLM/LVLM 调用入口。

    参数:
        AIclient:    openai.OpenAI 客户端实例（保留兼容性，未必使用）
        image_paths: 图片路径列表，空列表 = 纯文本模式
        userInput:   用户输入文本
        assistant:   上一轮 assistant 输出（retry 场景）
        isOverLap:   True=查询重复重试，False=格式错误重试
        think:       是否启用推理/思考模式（通过 chat_template_kwargs 控制）
        endpoint:    指定 endpoint；为空则按 image_paths 自动选 LVLM 或 LLM
                     （LVLM 优先用于 evidence_collector；其它 agent 都走 LLM）

    返回:
        模型响应文本
    """
    if endpoint is None:
        endpoint = LVLM_ENDPOINT if image_paths else LLM_ENDPOINT

    messages = _build_messages(userInput, image_paths, assistant, isOverLap)
    client = create_openai_client(endpoint)

    completion_kwargs = {
        "model": endpoint.model_name,
        "messages": messages,
        "stream": False,
        # llama.cpp 通过 chat_template_kwargs.enable_thinking 控制思考模式
        # 注意：服务器可能默认启用 thinking，因此无论 think 是 True 还是 False
        # 都必须显式传入，否则 False 时不传参会导致服务器走默认（=开启）
        "extra_body": {"chat_template_kwargs": {"enable_thinking": think}},
    }
    if endpoint.temperature is not None:
        completion_kwargs["temperature"] = endpoint.temperature
    if endpoint.max_tokens is not None:
        completion_kwargs["max_tokens"] = endpoint.max_tokens

    try:
        completion = client.chat.completions.create(**completion_kwargs)
    except Exception as e:
        logger.error(f"API call failed: {e}")
        raise

    return completion.choices[0].message.content
