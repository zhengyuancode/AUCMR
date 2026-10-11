"""
共享外层任务编排器（Shared Slot Orchestrator）。

论文第 770-774 行要求:
  - 为全部外部配置提供统一的外层任务编排器
  - 该编排器按照相同的 Cockburn 风格模板的槽位顺序发起精化请求
  - 在生成第 i 个槽位时，把已经生成的 1…i-1 个槽位作为只读上下文附加到提示词中
  - 各配置则保留自身的内层求解过程（per-slot 检索 + 生成）
  - 因此所有配置均获得相同的槽位调度和前序文本上下文，配置之间的差异
    来自槽内证据获取、状态维护、生成约束及组装后处理机制

本编排器接受一个 slot-fill callable，对每个 Cockburn 槽位调用该 callable
获取当前 slot 的内容；callable 内可以自由实现自己的检索/生成策略，
但拿到的 (slot, filled_slots_so_far) 接口是一样的。
"""

import json
import logging
import os
import sys
from typing import Any, Callable, Dict, List, Optional

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logger = logging.getLogger("shared_orchestrator")

COCKBURN_SLOT_ORDER = [
    "goal",
    "primary_actor",
    "preconditions",
    "trigger",
    "main_flow",
    "alternative_flow",
    "exception_flow",
    "postconditions",
]


def _format_filled_slots_for_prompt(filled_slots: Dict[str, Any]) -> str:
    """把已填槽位格式化为提示词中的 read-only 上下文。"""
    if not filled_slots:
        return "(none yet)"
    out = []
    for slot_name in COCKBURN_SLOT_ORDER:
        if slot_name not in filled_slots:
            continue
        sc = filled_slots[slot_name]
        if not sc:
            continue
        # 支持 dict-of-claims 或者 SlotContent-like
        if isinstance(sc, dict):
            claims = sc.get("claims") or []
            raw_text = sc.get("raw_text") or ""
        else:
            claims = getattr(sc, "claims", []) or []
            raw_text = getattr(sc, "raw_text", "") or ""
        if claims:
            lines = [f"  [{i}] {c.get('content') if isinstance(c, dict) else getattr(c, 'content', '')}"
                     for i, c in enumerate(claims)]
            out.append(f"{slot_name}:\n" + "\n".join(lines))
        elif raw_text:
            out.append(f"{slot_name}:\n  (raw) {raw_text}")
    return "\n\n".join(out) if out else "(none yet)"


def _build_use_case_output(
    use_case_name: str,
    actor: str,
    filled_slots: Dict[str, Any],
    method: str,
    extra_quality_flags: Optional[List[Dict[str, Any]]] = None,
    pre_constraint_slots: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """组装最终 Detail-UC JSON，schema 与 CREST main.json 对齐。"""
    out_slots = {}
    for slot_name in COCKBURN_SLOT_ORDER:
        sc = filled_slots.get(slot_name)
        if sc is None:
            out_slots[slot_name] = {"slot": slot_name, "claims": [], "raw_text": ""}
            continue
        if isinstance(sc, dict):
            # The audit snapshot belongs at the document root, not inside a
            # final Detail-UC slot.  Final slots retain their strict schema.
            out_slots[slot_name] = {
                key: value for key, value in sc.items()
                if key != "_pre_constraint_draft"
            }
        else:
            out_slots[slot_name] = {
                "slot": slot_name,
                "claims": [
                    {
                        "content": getattr(c, "content", ""),
                        "evidence_refs": list(getattr(c, "evidence_refs", []) or []),
                        **({"step_number": getattr(c, "step_number")}
                           if getattr(c, "step_number", None) is not None else {}),
                        **({"is_cross_page": True}
                           if getattr(c, "is_cross_page", False) else {}),
                    }
                    for c in (getattr(sc, "claims", []) or [])
                ],
                "raw_text": getattr(sc, "raw_text", "") or "",
                "evidence_card_ids": list(getattr(sc, "evidence_card_ids", []) or []),
            }

    return {
        "use_case_name": use_case_name,
        "actor": actor,
        "method": method,
        "slots": out_slots,
        "quality_flags": list(extra_quality_flags or []),
        "revision_history": [],
        "evidence_summary": {"total_cards": 0, "cross_page_cards": 0, "modalities": {}},
        "citation_audit": {
            "version": "pre_constraint_draft_v1",
            "description": (
                "Parsed baseline candidates before deterministic citation "
                "whitelisting. Final slots remain evidence-valid."
            ),
            "pre_constraint_slots": dict(pre_constraint_slots or {}),
        },
    }


def run_shared_orchestrator(
    use_case_name: str,
    actor: str,
    slot_filler: Callable[[str, Dict[str, Any], Dict[str, Any]], Optional[Dict[str, Any]]],
    method: str,
    project: str = "",
    raise_on_error: bool = False,
    slot_order: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """对一份 UC 跑共享编排器，按 slot_order 顺序逐槽调用 slot_filler。

    Args:
        use_case_name:   用例名称
        actor:           主要参与者
        slot_filler:     callable(slot_name, filled_slots_so_far, context)
                         其中 context 是 {"use_case_name": ..., "actor": ..., "method": ...}
                         返回 SlotContent-like dict（claims / raw_text / evidence_refs ...），
                         若返回 None 表示该 baseline 在此槽位无法生成内容（记 no_evidence flag）
        method:          方法名（写入 output.method，便于 L1/L2 区分）
        slot_order:      覆盖默认 Cockburn 顺序；默认是 8-slot 完整顺序

    Returns:
        Detail-UC JSON（schema 与 CREST main.json 对齐）
    """
    slot_order = slot_order or COCKBURN_SLOT_ORDER
    filled_slots: Dict[str, Any] = {}
    quality_flags: List[Dict[str, Any]] = []
    pre_constraint_slots: Dict[str, Any] = {}

    # primary_actor 由调用方强制注入（避免 baseline 自由发挥改写）
    filled_slots["primary_actor"] = {
        "slot": "primary_actor",
        "claims": [{"content": actor, "evidence_refs": []}],
        "raw_text": actor,
        "evidence_card_ids": [],
    }

    context = {
        "use_case_name": use_case_name,
        "actor": actor,
        "method": method,
        "project": project,
    }

    for slot_name in slot_order:
        if slot_name == "primary_actor":
            continue  # already set above
        logger.info(f"[orchestrator/{method}] filling slot={slot_name}")
        try:
            result = slot_filler(slot_name, filled_slots, context)
        except Exception as e:
            logger.error(f"[orchestrator/{method}] slot_filler raised: {e}")
            if raise_on_error:
                raise
            result = None

        # Collect the raw parsed candidate before the normal no-evidence branch
        # discards it.  This makes CitCov measurable without contaminating the
        # final Detail-UC with invalid claims.
        if isinstance(result, dict) and isinstance(result.get("_pre_constraint_draft"), dict):
            pre_constraint_slots[slot_name] = result["_pre_constraint_draft"]

        if result is None or (
            isinstance(result, dict)
            and not result.get("claims")
            and not result.get("raw_text")
        ):
            filled_slots[slot_name] = {"slot": slot_name, "claims": [], "raw_text": ""}
            quality_flags.append({
                "flag_type": "no_evidence",
                "slot": slot_name,
                "detail": f"No content produced for slot '{slot_name}' by {method}.",
            })
            continue

        # Normalize: ensure 'slot' key matches slot_name
        if isinstance(result, dict):
            result = dict(result)
            result["slot"] = slot_name
            filled_slots[slot_name] = result
        else:
            # SlotContent-like object
            filled_slots[slot_name] = result

    return _build_use_case_output(
        use_case_name, actor, filled_slots, method, quality_flags,
        pre_constraint_slots=pre_constraint_slots,
    )


def get_prompt_with_prior_context(
    base_prompt: str,
    use_case_name: str,
    actor: str,
    filled_slots: Dict[str, Any],
    current_slot: str,
) -> str:
    """把"已填的前序槽位"附加到 baseline 内部 prompt 的 helper。

    论文 772-774 行:
      在生成第 i 个槽位时，把已经生成的 1…i-1 个槽位作为只读上下文附加到提示词中。

    用法: baseline 在拼 prompt 时，把自己的 raw 任务描述作为 base_prompt 传入，
    本函数追加前序槽位段落，输出最终 user message。
    """
    prior_text = _format_filled_slots_for_prompt(filled_slots)
    block = (
        f"\n\n=== ALREADY-FILLED UPSTREAM SLOTS (read-only context for slot '{current_slot}') ===\n"
        f"Use Case: {use_case_name}\n"
        f"Primary Actor: {actor}\n\n"
        f"{prior_text}\n"
        f"=== END PRIOR CONTEXT ==="
    )
    return base_prompt + block
