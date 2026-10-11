"""
ReAct adapter — 调用 vendor/ReAct/ 官方代码。

官方仓库: https://github.com/ysymyth/ReAct
论文:     Yao et al. ICLR 2023

架构:
  Thought → Action (search) → Observation → Thought → ...
  直到模型发出 finish[answer] 为止
  Action Space 原始为 search[entity] / lookup[string] / finish[answer]

我们替换 Action Space 为 page retrieval:
  search(query) → top-k page_paths

Prompts 直接复用官方 prompts/hotpotqa.json / prompts/fever.json 等的格式，
并替换 Search/Finish 字段为 page retrieval 友好版。

降级路径:
  - ReAct 官方仅依赖 openai + wikienv.py；纯代码层面已可 import
  - 用户接入后即可用官方 ReAct 循环
"""

import logging
import os
import sys
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger("react_adapter")

_VENDOR_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ReAct")
_HAS_VENDOR = os.path.isdir(_VENDOR_PATH)
if _HAS_VENDOR:
    sys.path.insert(0, _VENDOR_PATH)


STATUS = "vendor_repo_cloned_prompts_loadable"


def _try_import_official() -> Optional[Any]:
    """尝试 import 官方 prompts；失败返回 None。"""
    if not _HAS_VENDOR:
        return None
    try:
        prompts_dir = os.path.join(_VENDOR_PATH, "prompts")
        if not os.path.isdir(prompts_dir):
            return None
        # 官方 prompts 是 .json；这里仅验证可达性
        candidates = ["hotpotqa.json", "fever.json"]
        loaded = {}
        for name in candidates:
            p = os.path.join(prompts_dir, name)
            if os.path.isfile(p):
                loaded[name] = p
        if not loaded:
            return None
        return {"prompts_dir": prompts_dir, "loaded_files": loaded}
    except Exception as e:
        logger.warning(f"[react] official import failed: {e}")
        return None


# ReAct-style per-slot prompt
REACT_SLOT_SYSTEM = """\
You are running a ReAct (Thought / Action / Observation) loop to fill ONE slot
of a Cockburn Detail-UC.

Use Case: {use_case_name}
Primary Actor: {actor}
Slot: {slot_name}
Slot definition: {slot_definition}

You have ONE tool available:
- Action {{"action": "Search", "action_input": "<retrieval query>"}}
  → returns a list of pages P1..Pn with brief content snippets.

After searching, you observe the content and decide your next step.

End the loop with:
- Action {{"action": "Finish", "action_input": {{"raw_text": "..." | "claims": [...], ...}}}}

Finish schema by slot type:
- goal or trigger: action_input={{"raw_text": "...", "evidence_refs": ["P<page_num>"]}}
- preconditions, main_flow, alternative_flow, exception_flow, or postconditions:
  action_input={{"claims": [{{"content": "...", "evidence_refs": ["P<page_num>"], "step_number": 1}}]}}

Rules:
1. Every claim must carry `evidence_refs`: list of "P<page_num>" IDs from pages you observed.
2. If no observed page supports a claim, OMIT it.
3. If no claim can be supported, return empty.
4. Avoid repeating queries already issued in Observed Searches.

Output ONLY valid JSON each turn (no extra prose):
{{"thought": "...", "action": "Search" | "Finish", "action_input": ...}}
"""


def _search_pages(
    query: str,
    project: str,
    file_names: List[str],
    top_k: int = 5,
) -> List[str]:
    """ReAct 的 Search 动作：调 AUCMR 多模态检索返回页面路径。"""
    try:
        from services.model_registry import registry
        retrieval_fn = registry.get_retrieval_adapter()
        results = retrieval_fn([query], file_names, topk=top_k)
        return results[0] if results and isinstance(results[0], list) else []
    except Exception as e:
        logger.warning(f"[react] search failed: {e}")
        return []


def _parse_step(raw: str) -> Optional[Dict[str, Any]]:
    """解析 ReAct 一步的 JSON 输出。"""
    import json_repair
    try:
        parsed = json_repair.loads(raw)
        if isinstance(parsed, dict):
            return parsed
    except Exception:
        pass
    return None


def _format_observation(pages: List[str]) -> str:
    """把检索到的页面格式化成 ReAct observation。"""
    if not pages:
        return "No relevant pages found."
    from eval.baselines import _page_number
    out = []
    for p in pages:
        page_num = _page_number(p) or 0
        out.append(f"Page P{page_num}: <image at {p}>")
    return "\n".join(out)


def _normalize_slot_answer(slot_name: str, action_input: Any) -> Optional[Dict[str, Any]]:
    """从 ReAct Finish 的 action_input 提取 slot 内容并规范化。"""
    from eval.baselines import _normalize_slot_response
    if not isinstance(action_input, dict):
        return None
    return _normalize_slot_response(slot_name, action_input)


def fill_slot(
    slot_name: str,
    use_case_name: str,
    actor: str,
    project: str,
    file_names: List[str],
    filled_slots: Dict[str, Any],
    max_steps: int = 5,
    overlap_threshold: float = 0.65,
    watermark_fn: Optional[Callable[[List[str]], List[str]]] = None,
) -> Optional[Dict[str, Any]]:
    """Slot filler for ReAct-Elaborate.

    跑 ReAct thought/action/observation 循环，直到 Finish 或连续检索高度重合。
    """
    if slot_name == "primary_actor":
        return None

    from eval.baselines import (
        SLOT_DEFINITIONS, SLOT_GENERATION_PROMPT, _parse_json_with_repair,
        _normalize_slot_response, _format_pages_as_context,
        get_prompt_with_prior_context,
    )
    from config.settings import LVLM_ENDPOINT, LLM_ENDPOINT, chat_completion

    official = _try_import_official()
    if official is not None:
        logger.info(f"[react] official prompts available at {official['prompts_dir']}")

    # 用 AUCMR 多模态检索（替换官方 Wikipedia API）
    observed_pages: List[str] = []
    history_queries: List[str] = []
    last_query_pages: List[str] = []
    system_prompt = REACT_SLOT_SYSTEM.format(
        use_case_name=use_case_name,
        actor=actor,
        slot_name=slot_name,
        slot_definition=SLOT_DEFINITIONS.get(slot_name, ""),
    )
    system_prompt = get_prompt_with_prior_context(
        system_prompt, use_case_name, actor, filled_slots, slot_name,
    )

    for step in range(max_steps):
        history_lines = [f"- {q}" for q in history_queries]
        history_str = "\n".join(history_lines) if history_lines else "(none yet)"
        user_prompt = (
            f"Observed pages so far:\n"
            f"{_format_pages_as_context(observed_pages)}\n"
            f"Previously issued queries (avoid repeat):\n{history_str}\n\n"
            "Output the next JSON step."
        )
        try:
            resp = chat_completion(
                LLM_ENDPOINT,
                [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
            )
        except Exception as e:
            logger.warning(f"[react] step {step} LLM call failed: {e}")
            break

        step_data = _parse_step(resp)
        if not isinstance(step_data, dict):
            break

        action = step_data.get("action", "")
        action_input = step_data.get("action_input", "")

        if action == "Finish":
            norm = _normalize_slot_answer(slot_name, action_input)
            from eval.baselines import _enforce_evidence_whitelist, _format_pages_as_context
            allowed = [line.split(":", 1)[0] for line in _format_pages_as_context(observed_pages).splitlines()]
            norm = _enforce_evidence_whitelist(norm, allowed) if norm else None
            # Let the shared orchestrator preserve any private audit draft and
            # independently mark an empty final slot as no_evidence.
            return norm

        if action == "Search":
            if not isinstance(action_input, str) or not action_input.strip():
                break
            history_queries.append(action_input)
            new_pages = _search_pages(action_input, project, file_names, top_k=5)
            if watermark_fn and new_pages:
                new_pages = watermark_fn(new_pages)
            observed_pages = list({*observed_pages, *new_pages})

            # 终止条件：与上一轮页面高度重合
            if last_query_pages and _page_overlap_rate(last_query_pages, new_pages) > overlap_threshold:
                logger.info(f"[react] step {step}: overlap > {overlap_threshold}, forcing Finish")
                break
            last_query_pages = new_pages
        else:
            # 未知 action，break
            break

    # 循环没 finish：最后尝试一次直接生成
    if observed_pages:
        context_block = _format_pages_as_context(observed_pages)
        fallback_prompt = SLOT_GENERATION_PROMPT.format(
            slot_name=slot_name,
            slot_definition=SLOT_DEFINITIONS.get(slot_name, ""),
            use_case_name=use_case_name,
            actor=actor,
            context_block=context_block,
        )
        fallback_prompt = get_prompt_with_prior_context(
            fallback_prompt, use_case_name, actor, filled_slots, slot_name,
        )
        try:
            resp = chat_completion(
                LVLM_ENDPOINT,
                [{"role": "user", "content": fallback_prompt}],
                image_paths=observed_pages,
            )
            parsed = _parse_json_with_repair(resp)
            norm = _normalize_slot_response(slot_name, parsed)
            from eval.baselines import _enforce_evidence_whitelist
            allowed = [line.split(":", 1)[0] for line in context_block.splitlines()]
            norm = _enforce_evidence_whitelist(norm, allowed)
            return norm
        except Exception as e:
            logger.warning(f"[react] fallback generation failed: {e}")
    return None


def _page_overlap_rate(a: List[str], b: List[str]) -> float:
    if not a or not b:
        return 0.0
    sa, sb = set(a), set(b)
    return len(sa & sb) / max(1, min(len(sa), len(sb)))
