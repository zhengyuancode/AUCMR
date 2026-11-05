"""
CREST 核心智能体函数。

三个 role: Query Designer (LLM), Evidence Collector (LVLM), Answer Generator (LLM)
外加 convergence check（per-slot 收敛判断）和 cross-slot DRA Pass（detect → repair → annotate）。

关键设计原则:
- 所有 JSON 解析走 json_repair，失败再 LLM 格式修正重试
- 检索后先合并连续页面为 region，再逐 region 处理
- 翻页路径基于真实文件命名（1.png → 2.png），不依赖 mock
- 每条 claim 出生时自带 source_pages 溯源，Author 生成时强制引用 evidence_refs
"""

import copy
import json
import logging
import os
import re
import time
from dataclasses import dataclass, field
from typing import List, Dict, Optional, Callable, Any, Tuple

from json_repair import repair_json

from .settings import (
    MAX_PAGE_CHAIN as _DEFAULT_MAX_PAGE_CHAIN,
    OVERLAP_RATE_MAX as _DEFAULT_OVERLAP_RATE_MAX,
    QUERY_RETRY_MAX as _DEFAULT_QUERY_RETRY_MAX,
    ANSWER_CONVERGENCE_REPEAT as _DEFAULT_ANSWER_CONVERGENCE_REPEAT,
    PAGE_COUNT_RATIO as _DEFAULT_PAGE_COUNT_RATIO,
    MAX_SLOT_ROUNDS as _DEFAULT_MAX_SLOT_ROUNDS,
    MIN_NOVEL_PAGES_PER_ROUND as _DEFAULT_MIN_NOVEL_PAGES_PER_ROUND,
    DRA_MAX_ROUNDS as _DEFAULT_DRA_MAX_ROUNDS,
    QUERIES_PER_ROUND as _DEFAULT_QUERIES_PER_ROUND,
    ACTIVE_CARD_BUDGETS as _DEFAULT_ACTIVE_CARD_BUDGETS,
)

from .schemas import (
    CockburnSlot,
    EvidenceCard,
    Claim,
    SlotContent,
    QualityFlag,
    RevisionRecord,
    UseCase,
    COCKBURN_DEPENDENCY_ORDER,
    SLOT_DESCRIPTIONS,
    SLOT_LEADING_QUESTIONS,
)
from .prompts import (
    QUERY_DESIGNER,
    EVIDENCE_COLLECTOR_SINGLE_PAGE,
    EVIDENCE_COLLECTOR_REGION,
    ANSWER_GENERATOR,
    CONVERGENCE_CHECK,
    CONSISTENCY_DETECT_VERTICAL,
    CONSISTENCY_DETECT_LATERAL,
    CLAIM_PATCH,
    _SLOT_EXTRACTION_RULES,
    _PAGING_ACTIONS_SCHEMA,
)

logger = logging.getLogger(__name__)


@dataclass
class QueryPlan:
    """Query Designer 的结构化结果；兼容旧版裸查询数组。"""

    queries: List[str] = field(default_factory=list)
    search_results: List[List[str]] = field(default_factory=list)
    should_stop: bool = False
    reuse_card_ids: List[str] = field(default_factory=list)
    missing_aspects: List[str] = field(default_factory=list)
    reason: str = ""
    structured: bool = False


@dataclass
class EvidenceCollectionResult:
    """Collector 的可审计输出：可供 Author 使用的卡片及本轮覆盖反馈。"""

    cards: List[EvidenceCard] = field(default_factory=list)
    covered_aspect_ids: List[str] = field(default_factory=list)
    missing_aspects: List[str] = field(default_factory=list)
    status: str = "no_evidence"

# ============================================================
# JSON 解析：json_repair → LLM 格式修正重试
# ============================================================

def _parse_json(raw: str, provider: "ModelProvider" = None,
                retry_prompt: str = "") -> Optional[Any]:
    """
    通用 JSON 解析：先用 json_repair 修复语法错误再解析。
    失败时如果提供了 provider 和 retry_prompt，走 LLM 格式修正重试（isOverLap=False）。
    raw=None / 非字符串 也安全（直接返回 None）。
    """
    # 自查后修正：raw 可能为 None / 非 str（LLM 调用异常或 stub），下面 raw[:200] 会崩
    if not isinstance(raw, str):
        logger.debug(f"_parse_json: raw is {type(raw).__name__}, returning None")
        return None
    try:
        return json.loads(repair_json(raw))
    except Exception:
        logger.debug(f"json_repair failed, raw[:200]: {raw[:200]}")

    if provider and retry_prompt:
        logger.info("Retrying with LLM format correction...")
        retry_raw = provider.llm_retry(retry_prompt, raw, is_overlap=False)
        try:
            return json.loads(repair_json(retry_raw))
        except Exception:
            logger.error("JSON parse failed after LLM retry.")
    return None


def _parse_lvlm_json(raw: str, provider: "ModelProvider" = None,
                     retry_prompt: str = "", image_paths: List[str] = None) -> Optional[Any]:
    """LVLM 版本的 JSON 解析，重试用 lvlm_retry。raw=None 时安全返回 None。"""
    if not isinstance(raw, str):
        logger.debug(f"_parse_lvlm_json: raw is {type(raw).__name__}, returning None")
        return None
    try:
        return json.loads(repair_json(raw))
    except Exception:
        logger.debug(f"json_repair failed for LVLM, raw[:200]: {raw[:200]}")

    if provider and retry_prompt and image_paths:
        logger.info("Retrying LVLM with format correction...")
        retry_raw = provider.lvlm_retry(retry_prompt, image_paths, raw)
        try:
            return json.loads(repair_json(retry_raw))
        except Exception:
            logger.error("LVLM JSON parse failed after retry.")
    return None


# ============================================================
# LLM/LVLM 调用抽象
# ============================================================

class ModelProvider:
    """
    模型调用抽象层。llm_call 和 lvlm_call 必须显式提供。

    llm_call 签名: (prompt, image_paths, assistant, is_overlap) -> str
    lvlm_call 签名: (prompt, image_paths, assistant, is_overlap) -> str

    用法:
        provider = ModelProvider(llm_call=my_llm_fn, lvlm_call=my_lvlm_fn)
    """

    def __init__(
        self, llm_call: Callable, lvlm_call: Callable,
        judge_call: Optional[Callable] = None,
    ):
        self._llm = llm_call
        self._lvlm = lvlm_call
        self._judge = judge_call
        self._stats = {
            "llm_calls": 0,
            "lvlm_calls": 0,
            "judge_calls": 0,
            "llm_retry_calls": 0,
            "lvlm_retry_calls": 0,
            "llm_seconds": 0.0,
            "lvlm_seconds": 0.0,
            "judge_seconds": 0.0,
        }

    def stats_snapshot(self) -> Dict[str, Any]:
        return dict(self._stats)

    @staticmethod
    def stats_delta(before: Dict[str, Any], after: Dict[str, Any]) -> Dict[str, Any]:
        keys = set(before) | set(after)
        result: Dict[str, Any] = {}
        for key in keys:
            value = after.get(key, 0) - before.get(key, 0)
            result[key] = round(value, 3) if isinstance(value, float) else value
        return result

    def llm(self, prompt: str, image_paths: Optional[List[str]] = None) -> str:
        """调用 LLM (纯文本)。"""
        logger.debug(f"[LLM] prompt[:300]: {prompt[:300]}")
        started = time.perf_counter()
        try:
            result = self._llm(prompt, image_paths or [], "", False)
        finally:
            self._stats["llm_calls"] += 1
            self._stats["llm_seconds"] += time.perf_counter() - started
        logger.debug(f"[LLM] response[:300]: {result[:300]}")
        return result

    def lvlm(self, prompt: str, image_paths: List[str]) -> str:
        """调用 LVLM (文本 + 图像)。"""
        logger.debug(f"[LVLM] prompt[:300]: {prompt[:300]}  images: {len(image_paths)}")
        started = time.perf_counter()
        try:
            result = self._lvlm(prompt, image_paths, "", False)
        finally:
            self._stats["lvlm_calls"] += 1
            self._stats["lvlm_seconds"] += time.perf_counter() - started
        logger.debug(f"[LVLM] response[:300]: {result[:300]}")
        return result

    def judge(self, prompt: str) -> str:
        """Call the independent text judge; fall back to the main LLM in tests/legacy use."""
        if self._judge is None:
            return self.llm(prompt)
        logger.debug(f"[JUDGE] prompt[:300]: {prompt[:300]}")
        started = time.perf_counter()
        try:
            result = self._judge(prompt, [], "", False)
        finally:
            self._stats["judge_calls"] += 1
            self._stats["judge_seconds"] += time.perf_counter() - started
        logger.debug(f"[JUDGE] response[:300]: {result[:300]}")
        return result

    def llm_retry(self, prompt: str, assistant: str, is_overlap: bool = False) -> str:
        """带 assistant 上下文的重试调用（用于格式修正/查重重试）。"""
        logger.debug(f"[LLM-RETRY] is_overlap={is_overlap}")
        started = time.perf_counter()
        try:
            result = self._llm(prompt, [], assistant, is_overlap)
        finally:
            self._stats["llm_calls"] += 1
            self._stats["llm_retry_calls"] += 1
            self._stats["llm_seconds"] += time.perf_counter() - started
        logger.debug(f"[LLM-RETRY] response[:300]: {result[:300]}")
        return result

    def lvlm_retry(self, prompt: str, image_paths: List[str], assistant: str) -> str:
        """带 assistant 上下文的 LVLM 重试调用。"""
        logger.debug(f"[LVLM-RETRY] images: {len(image_paths)}")
        started = time.perf_counter()
        try:
            result = self._lvlm(prompt, image_paths, assistant, False)
        finally:
            self._stats["lvlm_calls"] += 1
            self._stats["lvlm_retry_calls"] += 1
            self._stats["lvlm_seconds"] += time.perf_counter() - started
        logger.debug(f"[LVLM-RETRY] response[:300]: {result[:300]}")
        return result


# ============================================================
# Phase 1-N: Query Designer（内置三重终止 + 检索结果复用）
# ============================================================

def _calc_overlap_rate(items: List[str], history: List[str]) -> float:
    """计算 items 与 history 的文本重叠率。"""
    if not items or not history:
        return 0.0
    hset = set(h.lower() for h in history)
    overlap = sum(1 for it in items if it.lower() in hset)
    return overlap / len(items)


def _calc_page_overlap_rate(search_results: List[List[str]],
                            history_page_paths: set) -> Tuple[float, int]:
    """
    计算检索结果与历史页面的重叠率，并返回新增（novel）页面数。

    Fix 3: 返回 (overlap_rate, novel_page_count)，让调用方做"既重叠又无新页面"
    的联合判定，而不是裸比例。这样能避免 4 页 section 因为最后一页和前 3 页
    一起返回时（{25,26,28} vs history={25,26,27} → overlap=0.67）被强制终止，
    导致永远抓不到 page 28。

    novel_page_count 基于去重后的新页面集合，所以多查询返回同一新页只计 1 次。
    """
    all_new: List[str] = []
    for r in search_results:
        all_new.extend(r)
    if not all_new:
        return 0.0, 0
    overlap = sum(1 for p in all_new if p in history_page_paths)
    rate = overlap / len(all_new)
    novel_unique = {p for p in all_new if p not in history_page_paths}
    return rate, len(novel_unique)


def query_designer(*args, **kwargs):
    raise RuntimeError("Legacy page path disabled in L5; use runtime.pipeline text path")



# ============================================================
# 检索结果 → 连续页面分组
# ============================================================

def _group_consecutive_pages(page_items: List[Dict[str, Any]]) -> List[List[Dict[str, Any]]]:
    """
    将检索结果按连续页码分组。
    跨 doc（item["doc"] 不同）即使页号连续也强制断开，避免错误合并不同文档的相邻页。

    例: [{doc:A, num:1}, {doc:A, num:2}, {doc:B, num:3}]
      → [[{doc:A, num:1}, {doc:A, num:2}], [{doc:B, num:3}]]
    """
    if not page_items:
        return []
    sorted_items = sorted(page_items, key=lambda x: (x.get("doc", ""), x["num"]))
    groups = [[sorted_items[0]]]
    for item in sorted_items[1:]:
        last = groups[-1][-1]
        same_doc = item.get("doc", "") == last.get("doc", "")
        if same_doc and item["num"] == last["num"] + 1:
            groups[-1].append(item)
        else:
            groups.append([item])
    return groups


def _resolve_page_path(base_path: str, target_page: int) -> str:
    """
    根据已知页面的路径，推导目标页码的真实路径。

    真实格式: data/images/jtvauctions/1.png → data/images/jtvauctions/2.png
    Mock 格式: /mock/pages/xxx/page_0001.png → /mock/pages/xxx/page_0002.png
    """
    # 真实路径: 替换 /数字.扩展名
    m = re.search(r'/(\d+)\.(png|jpg|jpeg|gif|bmp|tiff)$', base_path)
    if m:
        return base_path.replace(f"/{m.group(1)}.{m.group(2)}", f"/{target_page}.{m.group(2)}")
    # Mock 路径: 替换 page_XXXX
    m = re.search(r'page[_-]?\d+', base_path)
    if m:
        return base_path.replace(m.group(0), f"page_{target_page:04d}")
    # Fallback: 不能推导，返回原路径
    return base_path


def _is_valid_lvlm_format(r: Any) -> bool:
    """检查 LVLM 输出格式：claims 和 paging_actions 必须是 dict 列表。"""
    if not isinstance(r, dict):
        return False
    c = r.get("claims", [])
    p = r.get("paging_actions", [])
    return (isinstance(c, list) and all(isinstance(x, dict) for x in c) and
            isinstance(p, list) and all(isinstance(x, dict) for x in p))


_COLLECTOR_COVERAGE_STATUSES = {
    "sufficient", "need_adjacent_page", "need_retrieval", "no_evidence",
}


def _string_list(value: Any) -> List[str]:
    """Parse model-provided labels conservatively while preserving first-seen order."""
    if not isinstance(value, list):
        return []
    seen: set = set()
    result: List[str] = []
    for item in value:
        text = str(item).strip()
        if text and text not in seen:
            seen.add(text)
            result.append(text)
    return result


def _collector_coverage(result: Dict[str, Any], *, is_complete: bool) -> Tuple[str, List[str], List[str]]:
    """Read optional coverage feedback without turning a formatting miss into data loss."""
    raw = result.get("coverage", {})
    raw = raw if isinstance(raw, dict) else {}
    status = str(raw.get("status", "")).strip().lower()
    if status not in _COLLECTOR_COVERAGE_STATUSES:
        status = "sufficient" if is_complete else "need_adjacent_page"
    if not is_complete:
        # An incomplete visual region never satisfies a slot, regardless of an
        # accidental contradictory status in the LVLM response.
        status = "need_adjacent_page"
    return (
        status,
        _string_list(raw.get("covered_aspects", [])),
        _string_list(raw.get("missing_essential_aspects", [])),
    )


def _claim_supporting_pages(claim_data: Dict[str, Any], region_pages: List[int]) -> List[int]:
    """Map LVLM *input-image* indices to exact document pages, fail-closed for regions.

    A multi-page region used to attach every page to every card, making page-level
    provenance overly broad.  A missing index is tolerable only for a one-image
    call, where the mapping is unambiguous; otherwise the card is discarded.
    """
    raw_indices = claim_data.get("supporting_page_indices")
    if raw_indices is None:
        if len(region_pages) == 1:
            logger.warning("Evidence card omitted supporting_page_indices on one-page input; mapping to its only page")
            return list(region_pages)
        logger.warning("Dropping evidence card: multi-page region lacks supporting_page_indices")
        return []
    if not isinstance(raw_indices, list) or not raw_indices:
        logger.warning("Dropping evidence card: supporting_page_indices must be a non-empty list")
        return []

    indices: List[int] = []
    for raw_index in raw_indices:
        # bool is an int subclass but is never a meaningful image index.
        if isinstance(raw_index, bool):
            logger.warning("Dropping evidence card: boolean supporting page index")
            return []
        try:
            index = int(raw_index)
        except (TypeError, ValueError):
            logger.warning("Dropping evidence card: non-integer supporting page index")
            return []
        if index < 0 or index >= len(region_pages):
            logger.warning("Dropping evidence card: supporting page index %s is outside region", index)
            return []
        if index not in indices:
            indices.append(index)
    return [region_pages[index] for index in indices]


def _build_evidence_card(
    claim_data: dict, card_id: str, source_pages: List[int],
    is_cross_page: bool = False, confidence: float = 0.9,
    source_doc: str = "",
) -> EvidenceCard:
    """
    从 claim_data dict 构建 EvidenceCard。

    页码（source_pages）由调用方将 LVLM 的输入图索引安全映射后绑定，
    不接受模型直接声称的文档页码。LVLM 的 location 只作页内辅助定位。
    source_doc 标识页面所属文档（多 PDF 检索时用于区分同页号）。
    """
    # 把 LVLM 输出的 location 映射到 source_locations 结构（保留页内位置信息）
    location = claim_data.get("location", "")
    source_locations = (
        [{"page": p, "location": location} for p in source_pages]
        if location else []
    )
    return EvidenceCard(
        card_id=card_id,
        content=claim_data.get("content", ""),
        source_pages=source_pages,                       # 代码强绑定，不读 LVLM 输出
        source_locations=source_locations,
        is_cross_page=is_cross_page,                     # 代码控制，不读 LVLM 输出
        region_type=claim_data.get("region_type", "text"),
        slot_relevance=claim_data.get("relevance", ""),
        confidence=confidence,
        source_doc=source_doc,
    )


# ============================================================
# Phase 1-N: Evidence Collector（重构版 — 先合并连续页再处理）
# ============================================================

def evidence_collector(*args, **kwargs):
    raise RuntimeError("Legacy page path disabled in L5; use runtime.pipeline text path")



# ============================================================
# Evidence working-set selection
# ============================================================

def _normalized_terms(text: str) -> set:
    """Lightweight deterministic terms for ranking/dedup; no extra model call."""
    return {
        token for token in re.findall(r"[a-z0-9]+", str(text).lower())
        if len(token) > 2
    }


def _term_jaccard(left: str, right: str) -> float:
    a, b = _normalized_terms(left), _normalized_terms(right)
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _filled_referenced_ids(filled_slots: Dict[str, SlotContent]) -> set:
    refs: set = set()
    for content in filled_slots.values():
        refs.update(content.evidence_card_ids or [])
        for claim in content.claims:
            refs.update(claim.evidence_refs or [])
    return refs


def _scope_text(filled_slots: Dict[str, SlotContent], use_case_name: str) -> str:
    goal = filled_slots.get(CockburnSlot.GOAL.value)
    parts = [use_case_name]
    if goal is not None:
        if goal.raw_text:
            parts.append(goal.raw_text)
        parts.extend(claim.content for claim in goal.claims if claim.content)
        if goal.scope_contract:
            parts.append(json.dumps(goal.scope_contract, ensure_ascii=False))
    return " ".join(parts)


def _select_active_cards(
    slot: CockburnSlot,
    own_cards: List[EvidenceCard],
    prior_cards: List[EvidenceCard],
    filled_slots: Dict[str, SlotContent],
    use_case_name: str,
    budget: Optional[int] = None,
) -> List[EvidenceCard]:
    """Select a compact, deterministic evidence working set.

    All collected cards remain in the UseCase evidence journal. This function only
    limits what is sent to planning/authoring prompts. Current-slot cards and cards
    already cited by upstream slots receive priority; unreferenced prior cards must
    show lexical scope overlap to compete for the remaining budget.
    """
    limit = max(1, int(budget or _DEFAULT_ACTIVE_CARD_BUDGETS.get(slot.value, 12)))
    referenced = _filled_referenced_ids(filled_slots)
    scope = _scope_text(filled_slots, use_case_name)
    scope_terms = _normalized_terms(scope)
    slot_terms = _normalized_terms(
        f"{slot.value} {SLOT_DESCRIPTIONS.get(slot.value, '')} "
        f"{SLOT_LEADING_QUESTIONS.get(slot.value, '')}"
    )

    combined: List[Tuple[EvidenceCard, bool, int]] = []
    seen_ids: set = set()
    for is_own, source in ((True, own_cards), (False, prior_cards)):
        for card in source:
            if card.card_id in seen_ids:
                continue
            seen_ids.add(card.card_id)
            combined.append((card, is_own, len(combined)))

    ranked: List[Tuple[float, int, EvidenceCard]] = []
    for card, is_own, index in combined:
        card_terms = _normalized_terms(f"{card.content} {card.slot_relevance}")
        scope_overlap = len(card_terms & scope_terms) / max(1, len(scope_terms))
        slot_overlap = len(card_terms & slot_terms) / max(1, len(slot_terms))
        is_referenced = card.card_id in referenced
        # Unused prior cards are the main source of context explosion. Keep one only
        # when it is visibly aligned with the current use-case scope/slot.
        if not is_own and not is_referenced and scope_overlap == 0 and slot_overlap == 0:
            continue
        score = (
            (4.0 if is_own else 0.0)
            + (3.0 if is_referenced else 0.0)
            + 2.5 * scope_overlap
            + 1.5 * slot_overlap
            + float(card.confidence or 0.0)
            - (0.5 if "incomplete" in (card.region_type or "") else 0.0)
        )
        ranked.append((score, index, card))

    ranked.sort(key=lambda item: (-item[0], item[1]))
    selected: List[EvidenceCard] = []
    normalized_seen: set = set()
    for _score, _index, card in ranked:
        normalized = " ".join(sorted(_normalized_terms(card.content)))
        if normalized and normalized in normalized_seen:
            continue
        duplicate = False
        for existing in selected:
            same_source = card.source_doc == existing.source_doc
            page_overlap = bool(set(card.source_pages) & set(existing.source_pages))
            similarity = _term_jaccard(card.content, existing.content)
            if similarity >= 0.92 or (same_source and page_overlap and similarity >= 0.75):
                duplicate = True
                break
        if duplicate:
            continue
        selected.append(card)
        if normalized:
            normalized_seen.add(normalized)
        if len(selected) >= limit:
            break

    logger.info(
        f"[{slot.value}] Active evidence: {len(selected)}/{len(combined)} cards "
        f"(budget={limit})"
    )
    return selected


# ============================================================
# Phase 1-N: Answer Generator
# ============================================================

def _build_pre_constraint_draft(
    slot: CockburnSlot,
    data: Dict[str, Any],
    exposed_card_ids: List[str],
) -> Dict[str, Any]:
    """Capture the author's parsed candidate before deterministic ref filtering.

    This is an audit artifact only.  It preserves candidate claims with missing or
    invalid references so CitCov measures the model output rather than the later
    whitelist gate.  It is never used as generation context or rendered as a
    final Detail-UC slot.
    """
    claims: List[Dict[str, Any]] = []
    raw_claims = data.get("claims", []) or []
    if isinstance(raw_claims, list):
        for item in raw_claims:
            if not isinstance(item, dict):
                continue
            content = str(item.get("content", "")).strip()
            if not content:
                continue
            refs = item.get("evidence_refs", []) or []
            claims.append({
                "content": content,
                "evidence_refs": list(refs) if isinstance(refs, list) else [],
                **({"step_number": item["step_number"]}
                   if isinstance(item.get("step_number"), int) else {}),
            })

    # Simple slots are commonly returned as raw_text + top-level refs.  Mirror
    # the normalizer: raw_text is a candidate only when claims are absent.
    if not claims:
        raw_text = data.get("raw_text", "")
        if not isinstance(raw_text, str) or not raw_text.strip():
            raw_text = data.get("content", "")
        if isinstance(raw_text, str) and raw_text.strip():
            refs = data.get("evidence_refs", []) or []
            claims.append({
                "content": raw_text.strip(),
                "evidence_refs": list(refs) if isinstance(refs, list) else [],
            })

    return {
        "slot": slot.value,
        "status": str(data.get("status", "")),
        "claims": claims,
        "exposed_evidence_refs": list(exposed_card_ids),
    }

def answer_generator(
    actor: str,
    use_case_name: str,
    slot: CockburnSlot,
    evidence_cards: List[EvidenceCard],
    filled_slots: Dict[str, SlotContent],
    current_slot_content: SlotContent,
    provider: ModelProvider,
    prior_evidence_cards: Optional[List[EvidenceCard]] = None,
) -> SlotContent:
    """用 Evidence Cards 填/修订一个 Cockburn slot。每条 claim 强制引用 evidence_refs。"""
    all_cards = _select_active_cards(
        slot=slot,
        own_cards=list(evidence_cards),
        prior_cards=list(prior_evidence_cards or []),
        filled_slots=filled_slots,
        use_case_name=use_case_name,
    )

    if not all_cards:
        logger.info(f"[{slot.value}] Answer Generator: no evidence cards, returning empty")
        return SlotContent(slot=slot.value)

    evidence_json = json.dumps([c.to_prompt_dict() for c in all_cards], ensure_ascii=False, indent=2)
    filled_json = json.dumps(
        {k: v.to_dict() for k, v in filled_slots.items()}, ensure_ascii=False, indent=2
    )

    # Fix 2 + 7.1: 提取 Goal 文本，goal slot 自举时回退到 use_case_name 做锚
    goal_text = _extract_goal_context(filled_slots, use_case_name=use_case_name)

    # Fix 7.1 + 7.4: slot 专用提示——
    # goal: 强制单一 use case 聚焦，禁止把多 use case 拼成一句
    # alternative_flow: 强制 divergence from main flow step，拒绝写不相关 use case
    slot_specific_hint_ag = ""
    if slot == CockburnSlot.GOAL:
        slot_specific_hint_ag = (
            "=== GOAL-SLOT FOCUS RULE ===\n"
            f'The use case name is "{use_case_name}". The Goal MUST describe ONLY '
            f'the LITERAL user-facing action denoted by this name.\n'
            "If evidence cards describe MULTIPLE distinct user-facing actions (e.g.,\n"
            "  - one card says 'bid on items'\n"
            "  - another says 'complete checkout / select shipping / pay'\n"
            "these are DIFFERENT use cases. Pick ONLY the cards matching the use case\n"
            "name; reject all others. Do NOT merge multiple use cases into one sentence.\n"
            "If no card clearly matches the use case name, return\n"
            '{\"status\": \"goal_evidence_missing\", \"missing_aspects\": [\"clear evidence of the literal use case action\"]}\n'
        )
    elif slot == CockburnSlot.PRECONDITIONS:
        slot_specific_hint_ag = (
            "=== PRECONDITION SEMANTIC GATE ===\n"
            "Keep only states that MUST already be true before the trigger can occur.\n"
            "Reject generic page/UI descriptions, feature availability, general policies, later\n"
            "customer-service actions, and conditions checked only after the flow starts.\n"
            "Apply a counterfactual entry test to every candidate: if the actor can still\n"
            "initiate the trigger without that state, it is not a precondition. A claim\n"
            "such as 'the actor can access/open the page named by the trigger' is usually\n"
            "tautological. Preserve at most one minimal evidence-backed entry-context claim\n"
            "when the trigger explicitly begins in a role-restricted administration workspace. Product\n"
            "eligibility, field constraints, and date-range validation encountered after\n"
            "the trigger are business rules or exceptions, not preconditions. Aim for the\n"
            "smallest set of independently necessary entry states, normally 1-3 claims."
        )
    elif slot == CockburnSlot.MAIN_FLOW:
        slot_specific_hint_ag = (
            "=== SINGLE-SCENARIO MAIN FLOW ===\n"
            "Write one canonical successful use-case instance, as many atomic steps as the source supports; no minimum length.\n"
            "If evidence describes several selectable categories or methods, include one\n"
            "selection step and keep category-specific paths out of the canonical sequence.\n"
            "Stop at the immediate success boundary; exclude downstream use cases."
        )
    elif slot == CockburnSlot.POSTCONDITIONS:
        slot_specific_hint_ag = (
            "=== POSTCONDITION SEMANTIC GATE ===\n"
            "Keep only immediate observable states caused by successful completion, normally\n"
            "1-5 claims. Reject cancellation/exception outcomes, later reports, unrelated\n"
            "account policy, and remote downstream side effects."
        )
    elif slot == CockburnSlot.ALTERNATIVE_FLOW:
        slot_specific_hint_ag = (
            "=== ALTERNATIVE-FLOW STRICT DIVERGENCE RULE ===\n"
            "Each alt_flow claim MUST be a DIVERGENCE from a SPECIFIC main_flow step\n"
            "that still reaches the same successful postcondition, and that step MUST\n"
            "exist in the current main_flow claims above.\n"
            "REJECT (do NOT include) any candidate that is:\n"
            "  - a precondition failure (belongs to preconditions slot, not alt_flow)\n"
            "  - an error, warning, invalid-input rejection, prevented operation, retry,\n"
            "    recovery or termination path (belongs to exception_flow)\n"
            "  - a post-success event (belongs to postconditions, not alt_flow)\n"
            "  - an action that belongs to a DIFFERENT use case entirely\n"
            "    (e.g., for 'place bid' use case, REJECT checkout/payment/2nd-chance\n"
            "     branches — these belong to other use cases)\n"
            "  - a system-internal automation that the actor does not participate in\n"
            "Each claim's content should reference 'diverges from step N' or similar\n"
            "explicit pointer to the main flow step it branches from.\n"
            "If no valid divergence exists, return an empty claims list — DO NOT\n"
            "fabricate divergences to fill the slot.\n"
            "\n"
            "=== STEP-REFERENCE PRECISION (Polish C) ===\n"
            "When writing 'Diverges from Step N':\n"
            "  1. N MUST be a step_number that currently exists in main_flow above.\n"
            "  2. Use EXACTLY ONE step number per claim — DO NOT mix Step N and Step M\n"
            "     in the same claim (e.g., do NOT say 'diverges from Step 4' but then\n"
            "     'within the popup displayed in Step 5'). Pick the step whose action\n"
            "     is the actual branching point.\n"
            "  3. Before writing the claim, re-read main_flow's Step N content and\n"
            "     ensure your divergence description is COMPATIBLE with what Step N\n"
            "     actually does. If Step N is 'system displays popup', your divergence\n"
            "     should describe a variation of that display — not a different step.\n"
        )
    elif slot == CockburnSlot.EXCEPTION_FLOW:
        slot_specific_hint_ag = (
            "=== EXCEPTION-FLOW STEP-REFERENCE PRECISION (Polish C) ===\n"
            "Each exception flow claim must clearly identify:\n"
            "  - WHICH main_flow step is the divergence point (cite step_number)\n"
            "  - the triggering CONDITION at that step\n"
            "  - the EXIT path (use case terminates / returns to step N / handoff to alt)\n"
            "STEP-NUMBER RULES:\n"
            "  1. The cited step_number MUST exist in main_flow above.\n"
            "  2. The cited step's actual content must MATCH the kind of validation\n"
            "     where this exception triggers. Example: if your claim is about an\n"
            "     expired credit card, cite a step that handles payment/card validation,\n"
            "     NOT an unrelated step like 'sign-in' or 'profile completion'.\n"
            "  3. If no main_flow step performs the relevant check, do NOT invent a\n"
            "     divergence point — either skip this exception or add a note that\n"
            "     main_flow should be extended (the DRA Pass will then surface this).\n"
        )

    prompt = ANSWER_GENERATOR.substitute(
        actor=actor,
        use_case_name=use_case_name,
        goal_text=goal_text,
        slot_type=slot.value,
        slot_description=SLOT_DESCRIPTIONS.get(slot.value, ""),
        evidence_cards_json=evidence_json,
        filled_slots_json=filled_json,
        current_slot_json=json.dumps(current_slot_content.to_dict(), ensure_ascii=False, indent=2),
        slot_guidance=_slot_guidance(slot),
        slot_specific_hint=slot_specific_hint_ag,
    )
    raw = provider.llm(prompt)
    data = _parse_json(raw, provider=provider, retry_prompt=prompt)
    # 防御 LLM 返回非 dict（list / str / None）的边角情况——退化为空 dict 走后续 evidence_invalid 路径
    if not isinstance(data, dict):
        logger.warning(
            f"[{slot.value}] Answer Generator returned non-dict ({type(data).__name__}); "
            "treating as empty response."
        )
        raise ValueError("Author response is not an object; technical failure")

    if not (data.get("status") in ("no_evidence", "goal_evidence_missing") or isinstance(data.get("claims"), list) or isinstance(data.get("raw_text"), str)):
        raise ValueError("Author response lacks content/status")

    pre_constraint_draft = _build_pre_constraint_draft(
        slot, data, sorted(card.card_id for card in all_cards)
    )

    if data.get("status") == "no_evidence":
        logger.info(f"[{slot.value}] Answer Generator: model reported no_evidence")
        return SlotContent(slot=slot.value, pre_constraint_draft=pre_constraint_draft)

    # Fix 2: 新返回状态 goal_evidence_missing — Goal-alignment gate 拒绝了所有邻接证据，
    # 但 Goal-aligned 证据池中暂未出现。返回空 SlotContent 并附 goal_evidence_missing flag，
    # 让外层（per_slot_dcrrm_loop 下一轮 或 orchestrator）感知需要定向再检索。
    if data.get("status") == "goal_evidence_missing":
        missing = data.get("missing_aspects", [])
        if not isinstance(missing, list):
            missing = [str(missing)] if missing else []
        logger.info(
            f"[{slot.value}] Answer Generator: goal_evidence_missing — "
            f"missing aspects: {missing}"
        )
        sc = SlotContent(slot=slot.value, pre_constraint_draft=pre_constraint_draft)
        sc.quality_flags.append(QualityFlag(
            flag_type="goal_evidence_missing",
            slot=slot.value,
            detail=(
                f"Goal-alignment gate rejected all available evidence as "
                f"describing adjacent (non-goal) processes. Missing: {missing}"
            ),
        ))
        return sc

    # 索引 cards 以便快速查找（合并 own + prior）
    card_map = {c.card_id: c for c in all_cards}

    # P0.1: 检测未知引用 → 重试一次让 LLM 修正 → 仍含未知则丢弃这些 claim 并打 flag
    invalid_refs_round1 = _collect_invalid_refs(data, card_map)
    if invalid_refs_round1:
        logger.warning(
            f"[{slot.value}] Answer Generator returned unknown evidence_refs "
            f"{sorted(invalid_refs_round1)}; retrying once with valid IDs."
        )
        valid_ids = sorted(card_map.keys())
        # 把矫正约束直接拼进 prompt 走普通 llm()——
        # 不用 llm_retry，因为它的 retry_text 是写死的"格式错误"模板，无法注入 evidence-ref 矫正。
        correction = (
            "\n\n=== CORRECTION REQUIRED ===\n"
            f"A previous attempt cited evidence_ref IDs that DO NOT EXIST: {sorted(invalid_refs_round1)}.\n"
            f"The ONLY valid evidence_ref values are: {valid_ids}.\n"
            "Re-emit the JSON using ONLY these valid IDs. If a claim cannot be supported "
            "by any of these IDs, remove that claim entirely."
        )
        retry_raw = provider.llm(prompt + correction)
        retry_data = _parse_json(
            retry_raw, provider=provider, retry_prompt=prompt + correction,
        )
        if not isinstance(retry_data, dict):
            logger.warning(
                f"[{slot.value}] Answer Generator retry returned non-dict ({type(retry_data).__name__}); "
                "keeping first-round data and dropping invalid refs per-claim."
            )
            # 不用空 dict 覆盖——保留第一轮 data，让后续 _filter_refs 逐 claim 丢无效引用 + raise flag，
            # 避免丢失第一轮里本来合法的 claim。
        elif retry_data.get("status") == "no_evidence":
            logger.info(f"[{slot.value}] Answer Generator: retry reported no_evidence")
            return SlotContent(slot=slot.value, pre_constraint_draft=pre_constraint_draft)
        elif retry_data.get("status") == "goal_evidence_missing":
            # 自查后修正：retry 时 LLM 终于意识到所有证据都是邻接流程，
            # 应该把第一轮 misaligned data 也丢掉，避免保留错误 claims。
            missing = retry_data.get("missing_aspects", [])
            if not isinstance(missing, list):
                missing = [str(missing)] if missing else []
            logger.info(
                f"[{slot.value}] Answer Generator: retry reported goal_evidence_missing — "
                f"missing aspects: {missing}"
            )
            sc = SlotContent(slot=slot.value, pre_constraint_draft=pre_constraint_draft)
            sc.quality_flags.append(QualityFlag(
                flag_type="goal_evidence_missing",
                slot=slot.value,
                detail=(
                    f"Goal-alignment gate rejected all available evidence after retry. "
                    f"Missing: {missing}"
                ),
            ))
            return sc
        else:
            # 判断 retry 是否实质有内容——空 {} 或 {"claims": []} 不应覆盖第一轮的有效 claim。
            retry_claims = retry_data.get("claims") or []
            retry_raw_text = retry_data.get("raw_text") or ""
            retry_has_content = bool(retry_claims) or bool(retry_raw_text)
            if not retry_has_content:
                logger.warning(
                    f"[{slot.value}] Answer Generator retry returned empty content; "
                    "keeping first-round data and dropping invalid refs per-claim."
                )
                # 保留第一轮 data（不覆盖），让后续 _filter_refs 逐 claim 处理
            else:
                invalid_refs_round2 = _collect_invalid_refs(retry_data, card_map)
                if invalid_refs_round2:
                    logger.warning(
                        f"[{slot.value}] Retry still returned unknown refs {sorted(invalid_refs_round2)}; "
                        "claims with unknown refs will be dropped and an evidence_invalid flag will be raised."
                    )
                # retry 实质有内容（无论是否仍有 invalid ref）→ 采用 retry 版本，
                # 后续 _filter_refs 会逐 claim 处理剩余无效引用。
                data = retry_data

    claims = []
    evidence_card_ids = []
    dropped_claim_count = 0
    dropped_refs: set = set()

    def _filter_refs(refs: List[str]) -> Tuple[List[str], List[str]]:
        # Fix γ: refs 项可能含非字符串（dict/list/None）—— 提前过滤，避免 unhashable 崩溃
        str_refs = [r for r in refs if isinstance(r, str)]
        non_str = [r for r in refs if not isinstance(r, str)]
        if non_str:
            logger.warning(
                f"[{slot.value}] Answer Generator returned {len(non_str)} non-string evidence_ref(s); dropped."
            )
        valid = [r for r in str_refs if r in card_map]
        invalid = [r for r in str_refs if r not in card_map]
        return valid, invalid

    raw_claims = data.get("claims", []) or []
    if not isinstance(raw_claims, list):
        raw_claims = []
    for cdata in raw_claims:
        if not isinstance(cdata, dict):
            # 偶尔 LLM 把 claim 写成纯字符串；统计为 dropped 但不崩
            dropped_claim_count += 1
            continue
        raw_refs = cdata.get("evidence_refs", []) or []
        if not isinstance(raw_refs, list):
            raw_refs = []
        valid_refs, invalid_for_claim = _filter_refs(raw_refs)
        if not valid_refs:
            # 没有任何合法证据支撑 → 丢弃整条 claim
            dropped_claim_count += 1
            dropped_refs.update(invalid_for_claim)
            continue
        if invalid_for_claim:
            dropped_refs.update(invalid_for_claim)
        is_cross = any(card_map[r].is_cross_page for r in valid_refs)
        claims.append(Claim(
            content=cdata.get("content", ""),
            evidence_refs=valid_refs,
            step_number=cdata.get("step_number"),
            is_cross_page=is_cross,
        ))
        evidence_card_ids.extend(valid_refs)

    raw_text = data.get("raw_text", "")
    if not isinstance(raw_text, str):
        raw_text = ""
    if raw_text and not claims:
        raw_refs = data.get("evidence_refs", []) or []
        if not isinstance(raw_refs, list):
            raw_refs = []
        valid_refs, invalid_for_claim = _filter_refs(raw_refs)
        if valid_refs:
            if invalid_for_claim:
                dropped_refs.update(invalid_for_claim)
            is_cross = any(card_map[r].is_cross_page for r in valid_refs)
            claims = [Claim(content=raw_text, evidence_refs=valid_refs, is_cross_page=is_cross)]
            evidence_card_ids = list(valid_refs)
        else:
            # raw_text 完全没有合法引用 → 丢弃
            dropped_refs.update(invalid_for_claim)
            raw_text = ""
            dropped_claim_count += 1

    if dropped_claim_count or dropped_refs:
        slot_content_flag = QualityFlag(
            flag_type="evidence_invalid",
            slot=slot.value,
            detail=(
                f"Dropped {dropped_claim_count} claim(s) due to unresolved "
                f"evidence_refs after retry. Unknown ids: {sorted(dropped_refs)}"
            ),
            evidence_refs=sorted(dropped_refs),
        )
        # flag 通过返回的 SlotContent 由 orchestrator 收集
        slot_content_invalid_flag = slot_content_flag
    else:
        slot_content_invalid_flag = None

    logger.info(f"[{slot.value}] Answer Generator: {len(claims)} claims, refs={evidence_card_ids}")
    # 保序去重（main_flow 等顺序敏感的 slot 需要保留引用顺序）
    seen = set()
    ordered_refs = []
    for r in evidence_card_ids:
        if r not in seen:
            seen.add(r)
            ordered_refs.append(r)

    raw_covered = data.get("covered_aspect_ids", [])
    covered_aspect_ids = (
        [str(value) for value in raw_covered if str(value).strip()]
        if isinstance(raw_covered, list) else []
    )
    raw_missing = data.get("missing_aspects", [])
    missing_aspects = (
        [str(value) for value in raw_missing if str(value).strip()]
        if isinstance(raw_missing, list) else []
    )
    aspect_status_known = "covered_aspect_ids" in data or "missing_aspects" in data

    scope_contract: Dict[str, Any] = {}
    raw_contract = data.get("scope_contract", {})
    if slot == CockburnSlot.GOAL and isinstance(raw_contract, dict):
        scope_contract = dict(raw_contract)
        contract_refs = scope_contract.get("evidence_refs", [])
        if isinstance(contract_refs, list):
            scope_contract["evidence_refs"] = [
                ref for ref in contract_refs if isinstance(ref, str) and ref in card_map
            ]
        else:
            scope_contract["evidence_refs"] = list(ordered_refs)
        # A planning contract without a goal anchor must not silently become free-form truth.
        if not scope_contract.get("goal_statement"):
            scope_contract["goal_statement"] = raw_text or (
                claims[0].content if claims else use_case_name
            )
    sc = SlotContent(
        slot=slot.value,
        claims=claims,
        raw_text=raw_text if not claims else "",
        evidence_card_ids=ordered_refs,
        scope_contract=scope_contract,
        covered_aspect_ids=covered_aspect_ids,
        missing_aspects=missing_aspects,
        aspect_status_known=aspect_status_known,
        pre_constraint_draft=pre_constraint_draft,
    )
    if slot_content_invalid_flag is not None:
        sc.quality_flags.append(slot_content_invalid_flag)
    return sc


def semantic_grounding_verifier(
    slot: CockburnSlot,
    slot_content: SlotContent,
    evidence_cards: List[EvidenceCard],
    provider: ModelProvider,
    fail_closed: bool = False,
    use_source_images: bool = False,
) -> SlotContent:
    """Rewrite a slot to the minimal propositions supported by its cited cards.

    Citation-ID validation alone only proves traceability.  This second authoring
    pass uses the main LLM (not the evaluation judge) to remove unsupported
    conditions, recovery paths, and outcomes before a claim becomes part of the
    delivered Detail-UC.  Missing source-level facts are returned to the normal
    gap-driven retrieval loop through ``missing_aspects``.
    """
    if slot_content.raw_text and not slot_content.claims:
        slot_content.claims = [Claim(
            content=slot_content.raw_text,
            evidence_refs=list(slot_content.evidence_card_ids or []),
        )]
        slot_content.raw_text = ""
    if slot_content.is_empty() or not slot_content.claims:
        # DRA may legitimately remove the last claim.  Clear the audit snapshot
        # as well, otherwise L1 would score a stale pre-DRA candidate that is no
        # longer present in the delivered slot.
        slot_content.pre_constraint_draft = _build_pre_constraint_draft(
            slot, {"status": "semantic_verified", "claims": []},
            sorted(card.card_id for card in evidence_cards),
        )
        return slot_content

    card_map = {card.card_id: card for card in evidence_cards}
    referenced_ids = list(dict.fromkeys(
        ref for claim in slot_content.claims
        for ref in (claim.evidence_refs or [])
        if ref in card_map
    ))
    if not referenced_ids:
        slot_content.claims = []
        slot_content.raw_text = ""
        slot_content.evidence_card_ids = []
        slot_content.pre_constraint_draft = _build_pre_constraint_draft(
            slot, {"status": "semantic_verified", "claims": []}, sorted(card_map),
        )
        return slot_content

    claim_payload = [
        {
            "index": index,
            "content": claim.content,
            "evidence_refs": list(claim.evidence_refs or []),
            "step_number": claim.step_number,
        }
        for index, claim in enumerate(slot_content.claims)
    ]
    evidence_payload = [card_map[ref].to_prompt_dict() for ref in referenced_ids]
    # Normal authoring may re-check an LVLM-authored card against its source page.
    # Atomic DRA patches explicitly disable this: their validation must use the
    # same cited-card representation that the patcher and L1 card-based audit see.
    image_paths: List[str] = []
    image_bindings: List[Dict[str, Any]] = []
    seen_images = set()
    if use_source_images:
        for ref in referenced_ids:
            card = card_map[ref]
            source_dir = os.path.expanduser(str(card.source_doc or "").strip())
            if not source_dir:
                continue
            for page in card.source_pages or []:
                resolved = ""
                for extension in (".png", ".jpg", ".jpeg", ".webp"):
                    candidate = os.path.realpath(os.path.join(source_dir, f"{page}{extension}"))
                    if os.path.isfile(candidate):
                        resolved = candidate
                        break
                if not resolved or resolved in seen_images:
                    continue
                seen_images.add(resolved)
                image_paths.append(resolved)
                image_bindings.append({
                    "attached_image": len(image_paths),
                    "evidence_ref": ref,
                    "source_page": page,
                })
    prompt = f"""You are the internal semantic evidence verifier of an evidence-bound
use-case author.  Review every candidate claim in ONE Cockburn slot against only
its cited evidence cards.  This is an authoring/self-correction pass, not the
external evaluation judge.

Slot: {slot.value}

Candidate claims:
{json.dumps(claim_payload, ensure_ascii=False, indent=2)}

Cited evidence cards:
{json.dumps(evidence_payload, ensure_ascii=False, indent=2)}

Attached source-page bindings (when non-empty):
{json.dumps(image_bindings, ensure_ascii=False, indent=2)}

For every input index, emit exactly one decision:
- keep: every source-level actor, condition, action, response, and outcome is
  supported by the UNION of that claim's cited cards.
- rewrite: the core fact is supported but the claim contains unsupported added
  detail.  Return the minimally sufficient supported wording and valid refs.
- drop: the core fact is not supported by its cited cards.

Strict rules:
1. A valid citation ID is not by itself semantic support.
2. Faithful paraphrase and combining complementary cited cards are allowed.  A
   use-case precondition may restate a documented prerequisite as the state that
   must hold before the interaction; a postcondition may restate a documented
   successful-completion effect as the resulting state.
3. Do not infer validation loops, retries, return/resume steps, termination,
   successful completion, or continued execution unless a cited card supports it.
4. "At/Diverges from Step N" and step numbering are organizational metadata.
   Any claimed retry, recovery, return, continuation, success, or exit behavior
   after that label is a source-level proposition and needs evidence.
5. For alternative_flow or exception_flow, if the condition/response is supported
   but its recovery/exit is not, rewrite to the supported condition/response and
   add a precise missing_aspect requesting explicit recovery/exit evidence.
6. Never add a new evidence ref.  A rewritten claim may use only refs already
   attached to that input claim.
7. Preserve the input language.

Return only JSON:
{{"claims":[{{"index":0,"decision":"keep|rewrite|drop","content":"...",
"evidence_refs":["..."],"missing_aspects":["..."]}}]}}"""

    try:
        if image_paths:
            parsed = _parse_lvlm_json(
                provider.lvlm(prompt, image_paths), provider=provider,
                retry_prompt=prompt, image_paths=image_paths,
            )
        else:
            parsed = _parse_json(
                provider.llm(prompt), provider=provider, retry_prompt=prompt,
            )
    except Exception as exc:
        logger.warning("[%s] semantic grounding verifier call failed: %s", slot.value, exc)
        parsed = None
    rows = parsed.get("claims") if isinstance(parsed, dict) else None
    if not isinstance(rows, list):
        logger.warning(
            "[%s] semantic grounding verifier returned invalid JSON; %s claims",
            slot.value,
            "failing the technical attempt instead of silently accepting",
        )
        raise ValueError("Semantic verifier response is not a claims list; technical failure")

    indices = [row.get("index") for row in rows if isinstance(row, dict)]
    if len(indices) != len(claim_payload) or set(indices) != set(range(len(claim_payload))):
        raise ValueError("Semantic verifier did not decide every input claim exactly once")
    if any(row.get("decision") not in ("keep", "rewrite", "drop") for row in rows):
        raise ValueError("Invalid semantic verifier decision")

    decisions: Dict[int, Dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        try:
            index = int(row.get("index"))
        except (TypeError, ValueError):
            continue
        if 0 <= index < len(slot_content.claims) and index not in decisions:
            decisions[index] = row

    verified_claims: List[Claim] = []
    audit_claims: List[Dict[str, Any]] = []
    verifier_missing: List[str] = []
    rewritten = dropped = 0
    for index, original in enumerate(slot_content.claims):
        row = decisions.get(index)
        decision = (
            str(row.get("decision", "keep")).strip().lower()
            if row else ("drop" if fail_closed else "keep")
        )
        if decision not in {"keep", "rewrite", "drop"}:
            decision = "keep"
        raw_missing = row.get("missing_aspects", []) if row else []
        if isinstance(raw_missing, list):
            verifier_missing.extend(str(item).strip() for item in raw_missing if str(item).strip())
        if decision == "drop":
            dropped += 1
            continue
        if decision == "rewrite":
            content = str(row.get("content", "")).strip()
            raw_refs = row.get("evidence_refs", [])
            if content:
                audit_claims.append({
                    "content": content,
                    "evidence_refs": list(raw_refs) if isinstance(raw_refs, list) else [],
                    **({"step_number": original.step_number}
                       if isinstance(original.step_number, int) else {}),
                })
            allowed = set(original.evidence_refs or [])
            refs = [ref for ref in (raw_refs if isinstance(raw_refs, list) else [])
                    if isinstance(ref, str) and ref in allowed and ref in card_map]
            if not content or not refs:
                dropped += 1
                continue
            rewritten += 1
            verified_claims.append(Claim(
                content=content,
                evidence_refs=list(dict.fromkeys(refs)),
                step_number=original.step_number,
                is_cross_page=any(card_map[ref].is_cross_page for ref in refs),
            ))
        else:
            verified_claims.append(original)
            audit_claims.append(original.to_dict())

    # Missing/duplicate verifier rows must never silently delete content.  They
    # default to keep above; the external judge remains an independent backstop.
    if slot == CockburnSlot.MAIN_FLOW:
        for step, claim in enumerate(verified_claims, start=1):
            claim.step_number = step

    evidence_ids = list(dict.fromkeys(
        ref for claim in verified_claims for ref in (claim.evidence_refs or [])
    ))
    merged_missing = list(dict.fromkeys([
        *(slot_content.missing_aspects or []), *verifier_missing,
    ]))
    slot_content.claims = verified_claims
    slot_content.raw_text = "" if verified_claims else ""
    slot_content.evidence_card_ids = evidence_ids
    slot_content.missing_aspects = merged_missing
    slot_content.aspect_status_known = True
    if slot == CockburnSlot.GOAL and verified_claims:
        # Downstream query design is anchored on the scope contract.  A grounded
        # goal rewrite must update that anchor or later slots would search for
        # the rejected, pre-verification objective.
        slot_content.scope_contract = dict(slot_content.scope_contract or {})
        slot_content.scope_contract["goal_statement"] = verified_claims[0].content
        slot_content.scope_contract["evidence_refs"] = list(
            verified_claims[0].evidence_refs or []
        )
    slot_content.pre_constraint_draft = _build_pre_constraint_draft(
        slot,
        {
            "status": "semantic_verified",
            # Preserve the verifier's model-authored refs before the final
            # deterministic whitelist.  This keeps CitCov non-tautological
            # while NLI still evaluates the same final semantic candidate set.
            "claims": audit_claims,
            "missing_aspects": merged_missing,
        },
        sorted(card_map),
    )
    logger.info(
        "[%s] Semantic grounding verification: kept=%d rewritten=%d dropped=%d missing=%d",
        slot.value, len(verified_claims) - rewritten, rewritten, dropped, len(verifier_missing),
    )
    return slot_content


# ============================================================
# Convergence Check
# ============================================================

def check_convergence(
    prev_content: SlotContent,
    new_content: SlotContent,
    slot: CockburnSlot,
    provider: ModelProvider,
    filled_slots: Optional[Dict[str, SlotContent]] = None,
    use_case_name: str = "",
) -> bool:
    """判断两版 slot 内容是否实质不同。返回 True = 变了。

    Fix 2: filled_slots 用于提取 goal_text 喂给 prompt，让 convergence check 把
    "Goal-alignment 翻转"视为 substantial change，不让"两轮都不对齐"被判为收敛。
    Fix 7.1: 当 goal 还没填时，use_case_name 作为 fallback 锚。
    """
    if prev_content.is_empty() and not new_content.is_empty():
        return True
    if prev_content.is_empty() and new_content.is_empty():
        return False

    goal_text = (
        _extract_goal_text(filled_slots, use_case_name=use_case_name)
        if filled_slots else "(unknown)"
    )
    prompt = CONVERGENCE_CHECK.substitute(
        slot_type=slot.value,
        goal_text=goal_text,
        version_a_json=json.dumps(prev_content.to_dict(), ensure_ascii=False, indent=2),
        version_b_json=json.dumps(new_content.to_dict(), ensure_ascii=False, indent=2),
    )
    raw = provider.llm(prompt)
    result = _parse_json(raw, provider=provider, retry_prompt=prompt)
    # Fix δ: 守护 LLM 偶尔返回非 dict 的情况；非 dict 时保守判定为"变了"，让循环继续
    if not isinstance(result, dict):
        logger.warning(
            f"[{slot.value}] Convergence check returned non-dict ({type(result).__name__}); "
            "assuming changed=True conservatively."
        )
        return True
    changed = _coerce_bool(result.get("substantially_changed", True))
    logger.info(f"[{slot.value}] Convergence: changed={changed}, {result.get('summary', '')}")
    return changed


# ============================================================
# Final Phase: Cross-Slot Consistency — DRA Pass
# ============================================================
# 三步走（替换原本 7×N 次 LLM 调用 + 整 slot 重跑的多轮迭代实现）：
#   Detect    — 2 次 LLM（vertical / lateral 分组检测，全局上下文 + LLM 自然去重排序）
#   Repair    — 每个 issue 1 次 patcher LLM 调用，单 claim 就地修改；
#               仅在现有证据卡都不支持时升级一次单轮检索（不跑完整 5 轮 DCRRM）
#   Annotate  — 重新跑两组 detect 复检，剩余 issue 转 unresolved_inconsistency flag，不再循环

# Cockburn 依赖顺序：上游优先于下游修复
_SLOT_REPAIR_ORDER = {s.value: i for i, s in enumerate(COCKBURN_DEPENDENCY_ORDER)}

# Keep the operational DRA detector aligned with the paper's original seven
# cross-slot rules, even if a model returns an unsolicited rule name.
_DRA_RULES = {
    "trigger_preconditions_coverage",
    "mainflow_trigger_continuity",
    "mainflow_postconditions_reachability",
    "actor_mainflow_presence",
    "goal_mainflow_alignment",
    "altflow_diverge_points",
    "exceptionflow_exit_coverage",
}


def _slot_json(uc: UseCase, slot_value: str) -> str:
    """安全地把一个 slot 序列化成 JSON；slot 不存在时返回空对象。"""
    sc = uc.slots.get(slot_value)
    if sc is None:
        return "{}"
    return json.dumps(sc.to_dict(), ensure_ascii=False, indent=2)


def _validate_detector_issues(
    issues: Any, uc: UseCase, group_label: str = "",
) -> List[Dict[str, Any]]:
    """
    校验 detector 输出 issue 列表的格式与规则适用性（不代替内容判断）：
      - root_slot 必须是 use case 已填的 slot
      - target_claim_index 越界则降级为 -1（slot-wide）
      - root_cause / fix_intent 必填

    Polish A: 程序级兜底——root_cause 文本里如果出现"自我推翻"的关键词
    （"no issue here", "is present", "rule is likely PASS" 等），即使 LLM
    没遵守 SELF-CHECK 指令也强制丢弃这条 issue，避免误报流入 quality_flags。
    """
    if not isinstance(issues, list):
        logger.warning(f"[detect{group_label}] detector returned non-list: {type(issues).__name__}")
        return []
    valid_slots = set(uc.slots.keys())
    # Polish A: 自我推翻关键词（小写匹配）
    SELF_REFUTING_PHRASES = (
        "no issue here",
        "rule is likely pass",
        "rule is satisfied",
        "the rule passes",
        "actor is present",
        "no obvious misalignment",
        "no inconsistency",
        "this rule is pass",
        "this is not an issue",
    )
    cleaned: List[Dict[str, Any]] = []
    for it in issues:
        if not isinstance(it, dict):
            continue
        rule = str(it.get("rule_violated", ""))
        if rule not in _DRA_RULES:
            logger.debug(f"[detect{group_label}] dropped non-R1--R7 rule={rule!r}")
            continue
        if rule == "exceptionflow_exit_coverage":
            exception = uc.slots.get("exception_flow")
            # R7 checks exits of authored branches, not missing coverage metadata.
            # A raw-text-only slot is not empty; retain its semantic check.
            if exception is None or exception.is_empty():
                logger.debug(f"[detect{group_label}] R7 not applicable: empty exception_flow")
                continue
        root = it.get("root_slot")
        if root not in valid_slots:
            logger.debug(f"[detect{group_label}] dropped issue with invalid root_slot={root!r}")
            continue
        if not it.get("root_cause") or not it.get("fix_intent"):
            logger.debug(f"[detect{group_label}] dropped issue with missing root_cause/fix_intent")
            continue
        # Polish A: 自我推翻检查
        rc_lower = str(it.get("root_cause", "")).lower()
        if any(phrase in rc_lower for phrase in SELF_REFUTING_PHRASES):
            logger.info(
                f"[detect{group_label}] dropped self-refuting issue "
                f"(rule={it.get('rule_violated','?')}, root_slot={root})"
            )
            continue
        idx = it.get("target_claim_index", -1)
        # Issue I: bool 是 int 子类，但语义上不是合法 idx
        if isinstance(idx, bool):
            idx = -1
        else:
            try:
                idx = int(idx)
            except (TypeError, ValueError):
                idx = -1
        # 负数除 -1 外均规一化为 -1（slot-wide）
        if idx < -1:
            idx = -1
        slot_claims = uc.slots[root].claims
        if idx >= len(slot_claims):
            logger.debug(
                f"[detect{group_label}] target_claim_index {idx} out of range "
                f"({len(slot_claims)} claims in {root}); downgrading to -1"
            )
            idx = -1
        # Issue G: 抓 content 指纹，让后续 patch 阶段在 slot 已被前序 patch 改过时
        # 仍能准确定位原本 detector 想说的那条 claim。
        target_fingerprint = (
            slot_claims[idx].content if 0 <= idx < len(slot_claims) else None
        )
        cleaned.append({
            "rule_violated": rule,
            "root_slot": root,
            "target_claim_index": idx,
            "target_claim_fingerprint": target_fingerprint,
            "root_cause": it["root_cause"],
            "fix_intent": it["fix_intent"],
            "evidence_hint": it.get("evidence_hint"),
        })
    return cleaned


def _detect_consistency_grouped(
    actor: str, use_case_name: str, uc: UseCase, provider: ModelProvider,
) -> Tuple[List[Dict[str, Any]], List[str]]:
    """
    分两组各跑一次 LLM detect。
    A 组（vertical）: preconditions ↔ trigger ↔ main_flow ↔ postconditions
    B 组（lateral） : main_flow ↔ {goal, primary_actor, alt_flow, exception_flow}
    返回 (issues, parse_failures)：
      - issues: 合并后已校验的 issue 列表
      - parse_failures: 出现 parse 失败的组名（"vertical" / "lateral"），用于上层决定是否打 flag
    """
    issues: List[Dict[str, Any]] = []
    parse_failures: List[str] = []

    # ---- A 组：vertical ----
    vertical_prompt = CONSISTENCY_DETECT_VERTICAL.substitute(
        use_case_name=use_case_name,
        actor=actor,
        preconditions_json=_slot_json(uc, "preconditions"),
        trigger_json=_slot_json(uc, "trigger"),
        main_flow_json=_slot_json(uc, "main_flow"),
        postconditions_json=_slot_json(uc, "postconditions"),
        goal_json=_slot_json(uc, "goal"),
        primary_actor_json=_slot_json(uc, "primary_actor"),
    )
    def _coerce_to_list(parsed: Any) -> Optional[List[Any]]:
        """
        宽容处理 LLM 输出形状：
        - bare list → 直接用
        - 单个 issue dict（含 root_slot/rule_violated 字段）→ 包成 1-element list
        - wrapped dict（{"issues": [...]} / {"items": [...]} 等）→ 解开内层 list
        - 其他非 list 类型 → None（视为 parse 失败，由调用方转 consistency_check_failed flag）
        """
        if parsed is None:
            return None
        if isinstance(parsed, list):
            return parsed
        if isinstance(parsed, dict):
            # 优先：识别常见 wrapper key
            for key in ("issues", "items", "list", "violations", "results"):
                v = parsed.get(key)
                if isinstance(v, list):
                    return v
            # 其次：如果 dict 本身像 issue（带 issue 字段），按单 issue 处理
            if "root_slot" in parsed or "rule_violated" in parsed:
                return [parsed]
            # 最后：dict 不像 issue 也没 wrapper → parse 失败
            return None
        return None

    raw_v = provider.llm(vertical_prompt)
    parsed_v = _coerce_to_list(_parse_json(
        raw_v, provider=provider, retry_prompt=vertical_prompt,
    ))
    if parsed_v is None:
        logger.warning("[consistency-detect-vertical] parse failed or returned non-list/dict")
        parse_failures.append("vertical")
    else:
        cleaned_v = _validate_detector_issues(parsed_v, uc, group_label="-vertical")
        logger.info(f"[consistency-detect-vertical] {len(cleaned_v)} issue(s) found")
        issues.extend(cleaned_v)

    # ---- B 组：lateral ----
    lateral_prompt = CONSISTENCY_DETECT_LATERAL.substitute(
        use_case_name=use_case_name,
        actor=actor,
        main_flow_json=_slot_json(uc, "main_flow"),
        goal_json=_slot_json(uc, "goal"),
        primary_actor_json=_slot_json(uc, "primary_actor"),
        alternative_flow_json=_slot_json(uc, "alternative_flow"),
        exception_flow_json=_slot_json(uc, "exception_flow"),
    )
    raw_l = provider.llm(lateral_prompt)
    parsed_l = _coerce_to_list(_parse_json(
        raw_l, provider=provider, retry_prompt=lateral_prompt,
    ))
    if parsed_l is None:
        logger.warning("[consistency-detect-lateral] parse failed or returned non-list/dict")
        parse_failures.append("lateral")
    else:
        cleaned_l = _validate_detector_issues(parsed_l, uc, group_label="-lateral")
        logger.info(f"[consistency-detect-lateral] {len(cleaned_l)} issue(s) found")
        issues.extend(cleaned_l)

    return issues, parse_failures


def _merge_issues_per_target(
    issues: List[Dict[str, Any]],
) -> List[Tuple[str, int, List[Dict[str, Any]]]]:
    """
    把所有 issue 按 (root_slot, target_claim_index) 聚合；
    同一目标的多条 issue 合并由一次 patcher 调用一并处理。
    返回 [(root_slot, target_claim_index, [issue, ...]), ...]，按 Cockburn 依赖顺序排序。
    """
    grouped: Dict[Tuple[str, int], List[Dict[str, Any]]] = {}
    order: List[Tuple[str, int]] = []
    for it in issues:
        key = (it["root_slot"], it["target_claim_index"])
        if key not in grouped:
            grouped[key] = []
            order.append(key)
        grouped[key].append(it)

    # 稳定排序：先按 Cockburn 依赖（上游优先），同 slot 内按出现顺序
    insertion_idx = {k: i for i, k in enumerate(order)}
    order.sort(key=lambda k: (
        _SLOT_REPAIR_ORDER.get(k[0], 99),
        insertion_idx[k],
    ))
    return [(slot, idx, grouped[(slot, idx)]) for (slot, idx) in order]


def claim_patcher(
    actor: str,
    use_case_name: str,
    slot: CockburnSlot,
    target_claim_index: int,
    issues: List[Dict[str, Any]],
    slot_content: SlotContent,
    evidence_cards: List[EvidenceCard],
    provider: ModelProvider,
    filled_slots: Optional[Dict[str, SlotContent]] = None,
    repair_feedback: str = "",
) -> Optional[Dict[str, Any]]:
    """
    单条 claim 的 patch 决策。返回解析后的 patch dict，失败返回 None。

    Fix 4: filled_slots 用于提取 goal_text 喂给 CLAIM_PATCH，让 patcher 能用
    slot_rewrite 动作做全槽重写时以 Goal 为锚选证据。
    """
    issues_block_lines = []
    for i, it in enumerate(issues, 1):
        evh = it.get("evidence_hint")
        evh_repr = evh if evh else "null"
        issues_block_lines.append(
            f"Issue #{i}\n"
            f"  rule_violated  : {it.get('rule_violated', 'unspecified')}\n"
            f"  target_index   : {target_claim_index}\n"
            f"  root_cause     : {it['root_cause']}\n"
            f"  fix_intent     : {it['fix_intent']}\n"
            f"  evidence_hint  : {evh_repr}"
        )
    issues_block = "\n\n".join(issues_block_lines)
    if repair_feedback:
        issues_block += "\n\n=== REQUIRED CORRECTION TO THE PREVIOUS PATCH ===\n" + repair_feedback

    goal_text = (
        _extract_goal_text(filled_slots, use_case_name=use_case_name)
        if filled_slots else "(unknown)"
    )

    cards_summary = [c.to_prompt_dict() for c in evidence_cards]
    prompt = CLAIM_PATCH.substitute(
        actor=actor,
        use_case_name=use_case_name,
        goal_text=goal_text,
        slot_type=slot.value,
        slot_description=SLOT_DESCRIPTIONS.get(slot.value, ""),
        slot_json=json.dumps(slot_content.to_dict(), ensure_ascii=False, indent=2),
        evidence_cards_json=json.dumps(cards_summary, ensure_ascii=False, indent=2),
        issues_block=issues_block,
    )
    raw = provider.llm(prompt)
    patch = _parse_json(raw, provider=provider, retry_prompt=prompt)
    if not isinstance(patch, dict):
        logger.warning(f"[patcher] {slot.value} idx={target_claim_index} returned non-dict")
        return None
    return patch


def _patch_needs_concrete_target(
    patch: Dict[str, Any],
    detector_target_idx: int,
    slot_content: SlotContent,
) -> bool:
    """Return whether a modify/remove patch lacks one safe atomic target.

    A detector may legitimately report a slot-wide issue with index ``-1``.
    That does not authorize a slot-wide mutation: DRA must still select one
    concrete claim (or choose add/need_evidence).  Retrying once here turns a
    common malformed patch into an actionable atomic repair without guessing
    which claim the model intended to delete or rewrite.
    """
    if patch.get("action") not in {"modify", "remove"}:
        return False
    if 0 <= detector_target_idx < len(slot_content.claims):
        return False
    raw_idx = patch.get("target_claim_index")
    if isinstance(raw_idx, bool):
        return True
    try:
        idx = int(raw_idx)
    except (TypeError, ValueError):
        return True
    return not (0 <= idx < len(slot_content.claims))


def _is_ordered_slot(slot_content: SlotContent) -> bool:
    """
    判断一个 slot 是否按 step_number 有序：
    只要任何一条 claim 带 step_number，就当作 ordered。
    main_flow / alternative_flow / exception_flow 通常都是。
    """
    return any(c.step_number is not None for c in slot_content.claims)


def _normalize_step_numbers(slot_content: SlotContent) -> None:
    """
    把所有 claim 的 step_number 归一化为 int 或 None。
    answer_generator 偶尔会把 step_number 写成字符串数字 ("1") 或 bool (True)，
    会让后续比较运算 TypeError 或语义错乱。本函数在每次 ordered 操作前防御性调用一遍。
    """
    for c in slot_content.claims:
        if c.step_number is None:
            continue
        # Issue D: bool 是 int 子类，必须先单独检测；True/False 当 None 处理
        if isinstance(c.step_number, bool):
            logger.warning(f"step_number {c.step_number!r} is bool; setting to None")
            c.step_number = None
            continue
        if isinstance(c.step_number, int):
            continue
        try:
            c.step_number = int(c.step_number)
        except (TypeError, ValueError):
            # 转不动就当 None（保留 claim 内容，但失去序列地位）
            logger.warning(f"step_number {c.step_number!r} cannot be cast to int; setting to None")
            c.step_number = None


def _insert_ordered_claim(
    slot_content: SlotContent, new_claim: Claim,
) -> Tuple[bool, str]:
    """
    把 new_claim 按 step_number 插入到正确位置；冲突时把后续 claim 整体 +1 重编号。
    new_claim.step_number 必须是 >= 1 的正整数（接受字符串数字，自动转换）；
    否则返回 False。

    现有 claim 的 step_number 在校验通过后才被归一化（Issue C：拒绝路径不留副作用）。
    """
    # 先校验 new_claim.step_number——这步失败的话不能修改 slot_content
    raw_step = new_claim.step_number
    if raw_step is None:
        return False, "add: ordered slot requires step_number on new_claim"
    # Issue D: bool 是 int 的子类——单独拒绝，避免 True 被当作 step 1
    if isinstance(raw_step, bool):
        return False, f"add: step_number must be an integer, got bool {raw_step!r}"
    try:
        target_step = int(raw_step)
    except (TypeError, ValueError):
        return False, f"add: step_number must be an integer, got {raw_step!r}"
    if target_step < 1:
        return False, f"add: step_number must be >= 1, got {target_step}"

    # 校验通过 → 现在可以安全 mutate slot_content
    _normalize_step_numbers(slot_content)
    new_claim.step_number = target_step  # 标准化为 int

    # 冲突重编号：所有 step_number >= target_step 的现有 claim 整体 +1
    occupied = {c.step_number for c in slot_content.claims if c.step_number is not None}
    if target_step in occupied:
        for c in slot_content.claims:
            if c.step_number is not None and c.step_number >= target_step:
                c.step_number += 1

    # Issue 5 fix: 找到正确插入位置——只比较有 step_number 的 claim。
    # 策略：在第一个 step_number > target_step 的 numbered claim 之前插入；
    # 都 ≤ target_step 时，插入到最后一个 numbered claim 之后；
    # 没有任何 numbered claim 时（理论上不会到这里，因为 _is_ordered_slot 已为真），插到末尾。
    # None claim 不影响插入决策——它们留在原位置，保持"已有内容尽量不动"的承诺。
    insert_at = len(slot_content.claims)
    last_numbered_idx = -1
    for i, c in enumerate(slot_content.claims):
        if c.step_number is None:
            continue
        last_numbered_idx = i
        if c.step_number > target_step:
            insert_at = i
            break
    else:
        # 所有 numbered claim 都 ≤ target_step → 放到最后一个 numbered claim 之后
        if last_numbered_idx >= 0:
            insert_at = last_numbered_idx + 1
    slot_content.claims.insert(insert_at, new_claim)
    return True, ""


def _apply_slot_rewrite(
    actor: str,
    use_case_name: str,
    slot: CockburnSlot,
    slot_content: SlotContent,
    patch: Dict[str, Any],
    all_evidence_cards: List[EvidenceCard],
    filled_slots: Dict[str, SlotContent],
    provider: ModelProvider,
) -> Tuple[bool, str]:
    """
    Fix 4: 全槽重写——丢弃 slot_content.claims，按 patch 提供的
    target_evidence_card_ids 调用一次 answer_generator 重新生成整槽。

    patch 必备字段:
      - target_evidence_card_ids: list[str]，必须都在 all_evidence_cards 中
      - rewrite_rationale: str，记录到 log
    返回 (success, reason)。
    """
    raw_ids = patch.get("target_evidence_card_ids")
    if not isinstance(raw_ids, list) or not raw_ids:
        return False, "slot_rewrite: target_evidence_card_ids must be a non-empty list"
    if any(not isinstance(x, str) for x in raw_ids):
        return False, "slot_rewrite: target_evidence_card_ids must be all strings"

    card_map = {c.card_id: c for c in all_evidence_cards}
    unknown = [cid for cid in raw_ids if cid not in card_map]
    if unknown:
        return False, f"slot_rewrite: unknown card_ids {unknown}"

    target_cards = [card_map[cid] for cid in raw_ids]
    rationale = patch.get("rewrite_rationale", "")
    logger.info(
        f"[slot_rewrite] {slot.value}: discarding {len(slot_content.claims)} claim(s), "
        f"regenerating from {len(target_cards)} target card(s) + prior pool. "
        f"rationale={rationale!r}"
    )

    # 调 answer_generator 重新生成整槽——它会再跑 Goal-alignment gate。
    # 自查后修正 Smell C：保留 prior_evidence_cards（=证据池的其余卡）作为补充，
    # 让 Goal-alignment gate 有补救空间——
    # 万一 patcher 选错了 target_cards（如把邻接流程的卡也选进来），
    # answer_generator 仍能从全池里挑出真正对齐 Goal 的卡，而不是被 patcher 锁死。
    # target_cards 作为主证据放在 evidence_cards 位置（在 prompt 中排在前面，
    # answer_generator prompt 的 evidence selection 自然会优先考虑它们）。
    other_cards = [c for c in all_evidence_cards if c.card_id not in set(raw_ids)]
    fresh_sc = answer_generator(
        actor=actor,
        use_case_name=use_case_name,
        slot=slot,
        evidence_cards=target_cards,
        prior_evidence_cards=other_cards,
        filled_slots=filled_slots,
        current_slot_content=SlotContent(slot=slot.value),
        provider=provider,
    )
    fresh_sc = semantic_grounding_verifier(
        slot=slot,
        slot_content=fresh_sc,
        evidence_cards=target_cards + other_cards,
        provider=provider,
    )

    if fresh_sc.is_empty():
        return False, "slot_rewrite: answer_generator returned empty (goal_evidence_missing or no_evidence)"

    # 就地覆盖整槽（保留 slot_content 对象，让外部引用继续有效）
    slot_content.claims = fresh_sc.claims
    slot_content.raw_text = fresh_sc.raw_text
    slot_content.evidence_card_ids = fresh_sc.evidence_card_ids
    slot_content.scope_contract = fresh_sc.scope_contract
    slot_content.covered_aspect_ids = fresh_sc.covered_aspect_ids
    slot_content.missing_aspects = fresh_sc.missing_aspects
    slot_content.aspect_status_known = fresh_sc.aspect_status_known
    slot_content.pre_constraint_draft = fresh_sc.pre_constraint_draft
    # 把 answer_generator 内部抛出的 quality_flags 也带过来（外层会统一收集）
    slot_content.quality_flags.extend(fresh_sc.quality_flags)
    return True, ""


def _apply_patch(
    slot_content: SlotContent,
    patch: Dict[str, Any],
    detector_target_idx: int,
    card_map: Dict[str, EvidenceCard],
) -> Tuple[bool, str]:
    """
    应用一个 patch 到 slot_content.claims（就地修改）。
    返回 (success, reason)。reason 在 success=False 时填失败原因。

    target_claim_index 来源（modify/remove）：
      1. 优先用 patch.get('target_claim_index')（patcher 在 slot-wide 问题里指认了具体 claim）
      2. 退回 detector 给的 detector_target_idx
      3. 仍越界 / -1 → 拒绝（modify/remove 必须指认具体 claim）

    ordered slot（含 step_number 的 claim）有特殊处理：
      - add: 按 step_number 插入，冲突时后续 claim 自动重编号
      - remove: 删除后将后续 step_number 整体 -1，保持序列连续
    """
    action = patch.get("action")
    is_ordered = _is_ordered_slot(slot_content)
    # Issue 1: 只有 slot 原本就用 raw_text 模式时才同步；
    # 永远不主动给从未用过 raw_text 的 slot（preconditions / postconditions / main_flow 等）填 raw_text。
    had_raw_text = bool(slot_content.raw_text)
    # Issue C: 不在入口归一化 step_number——拒绝的 patch 不应有副作用。
    # 归一化推迟到每个 action 分支中所有 validation 通过、即将真正 mutate 之前。

    def _resolve_target_idx() -> int:
        """
        target_claim_index 决议规则：
        - detector_target_idx >= 0：detector 已指认具体 claim，**该值权威**；
          patch 自带的 target_claim_index 仅作 sanity 用（与 detector 一致才用）。
          这避免 patcher 自由发挥 idx 把修改导向不相关的 claim。
        - detector_target_idx == -1（slot-wide）：patcher 自带 idx 才有意义。
        """
        if detector_target_idx >= 0:
            return detector_target_idx
        # slot-wide 情况：尝试取 patch 自带 idx
        raw = patch.get("target_claim_index")
        # Issue I: bool 不算合法 idx
        if raw is not None and not isinstance(raw, bool):
            try:
                v = int(raw)
                if 0 <= v < len(slot_content.claims):
                    return v
            except (TypeError, ValueError):
                pass
        return detector_target_idx

    if action == "remove":
        idx = _resolve_target_idx()
        if not (0 <= idx < len(slot_content.claims)):
            return False, f"remove: target_claim_index {idx} out of range"
        # Validation 通过 → 现在可以安全 normalize 然后 mutate
        if is_ordered:
            _normalize_step_numbers(slot_content)
        removed = slot_content.claims.pop(idx)
        # ordered slot 删除后：后续 step_number > removed.step_number 的整体 -1
        if is_ordered and removed.step_number is not None:
            for c in slot_content.claims:
                if c.step_number is not None and c.step_number > removed.step_number:
                    c.step_number -= 1
    elif action == "modify":
        idx = _resolve_target_idx()
        if not (0 <= idx < len(slot_content.claims)):
            return False, f"modify: target_claim_index {idx} out of range"
        new_claim_data = patch.get("new_claim")
        if not isinstance(new_claim_data, dict):
            return False, f"modify: new_claim must be an object, got {type(new_claim_data).__name__}"
        raw_refs = new_claim_data.get("evidence_refs") or []
        # Fix γ: evidence_refs 必须是字符串列表
        if not isinstance(raw_refs, list) or any(not isinstance(r, str) for r in raw_refs):
            return False, "modify: evidence_refs must be a list of strings"
        refs = raw_refs
        unknown = [r for r in refs if r not in card_map]
        if unknown:
            return False, f"modify: unknown evidence_refs {unknown}"
        if not refs:
            return False, "modify: new_claim must have non-empty evidence_refs"
        # Validation 通过 → mutate 前归一化（modify 用到 old.step_number 的比较场合）
        if is_ordered:
            _normalize_step_numbers(slot_content)
        old = slot_content.claims[idx]
        slot_content.claims[idx] = Claim(
            content=new_claim_data.get("content", ""),
            evidence_refs=list(refs),
            # modify 不允许改 step_number（避免破坏序列），强制保留原值
            step_number=old.step_number,
            is_cross_page=any(card_map[r].is_cross_page for r in refs),
        )
    elif action == "add":
        new_claim_data = patch.get("new_claim")
        if not isinstance(new_claim_data, dict):
            return False, f"add: new_claim must be an object, got {type(new_claim_data).__name__}"
        raw_refs = new_claim_data.get("evidence_refs") or []
        # Fix γ: evidence_refs 必须是字符串列表
        if not isinstance(raw_refs, list) or any(not isinstance(r, str) for r in raw_refs):
            return False, "add: evidence_refs must be a list of strings"
        refs = raw_refs
        unknown = [r for r in refs if r not in card_map]
        if unknown:
            return False, f"add: unknown evidence_refs {unknown}"
        if not refs:
            return False, "add: new_claim must have non-empty evidence_refs"
        # Issue E: unordered slot 的 add 必须丢弃 step_number，
        # 否则 patcher 偶尔多带 step_number 会让后续 _is_ordered_slot 误判 slot 类型。
        step_number_for_new = new_claim_data.get("step_number") if is_ordered else None
        new_claim = Claim(
            content=new_claim_data.get("content", ""),
            evidence_refs=list(refs),
            step_number=step_number_for_new,
            is_cross_page=any(card_map[r].is_cross_page for r in refs),
        )
        if is_ordered:
            # _insert_ordered_claim 内部会调 _normalize_step_numbers
            ok, reason = _insert_ordered_claim(slot_content, new_claim)
            if not ok:
                return False, reason
        else:
            slot_content.claims.append(new_claim)
    else:
        return False, f"unsupported action {action!r} for direct apply"

    # 同步 evidence_card_ids
    all_refs: List[str] = []
    for cl in slot_content.claims:
        for r in cl.evidence_refs:
            if r not in all_refs:
                all_refs.append(r)
    slot_content.evidence_card_ids = all_refs

    # Atomic DRA edits invalidate the author's old coverage metadata. Keeping it would
    # make a subsequent detector reason about aspects that no longer exist in claims.
    slot_content.covered_aspect_ids = []
    slot_content.missing_aspects = []
    slot_content.aspect_status_known = False
    if slot_content.slot == CockburnSlot.GOAL.value:
        # The contract was authored for the previous goal wording. Keeping it after an
        # atomic goal patch can reintroduce the exact scope inconsistency just repaired.
        slot_content.scope_contract = {}

    # Bug 4 / Issue 1: 同步 raw_text，但仅当 slot 原本就用 raw_text 模式时（had_raw_text=True）。
    # primary_actor / goal / trigger 这类简单 slot 在 orchestrator/answer_generator 里
    # 同时填了 raw_text 和 claims；patch 后两者会不一致。
    # 而 preconditions / postconditions / main_flow 等列表 slot 永远 raw_text=""，
    # 不应被本路径意外填上 raw_text。
    if had_raw_text:
        if len(slot_content.claims) == 1:
            slot_content.raw_text = slot_content.claims[0].content
        else:
            # 0 claim 或多 claim 都无法用单一 raw_text 准确表达 → 清空，让 to_dict 只看 claims
            slot_content.raw_text = ""

    return True, ""


def _run_single_evidence_round(**kwargs):
    # L5 reads the entire allowed requirements text before authoring. All exact-source
    # cards are already in uc.all_evidence_cards. There is no external source.
    return []



def _verify_atomic_dra_patch(
    slot: CockburnSlot,
    patch: Dict[str, Any],
    card_map: Dict[str, EvidenceCard],
    provider: ModelProvider,
) -> Tuple[Optional[Dict[str, Any]], str]:
    """Fail-closed grounding check for the one claim introduced by a DRA patch.

    The base slot has already passed semantic verification.  Re-verifying the
    whole slot after one atomic DRA edit lets judge variance rewrite or delete
    unrelated, previously accepted claims.  Validate only the delta instead.
    """
    action = patch.get("action")
    if action not in {"add", "modify"}:
        return patch, ""
    raw_claim = patch.get("new_claim")
    if not isinstance(raw_claim, dict):
        return None, "atomic grounding: new_claim must be an object"
    content = str(raw_claim.get("content", "")).strip()
    raw_refs = raw_claim.get("evidence_refs") or []
    if not content or not isinstance(raw_refs, list):
        return None, "atomic grounding: candidate content/refs are missing"
    refs = list(dict.fromkeys(
        ref for ref in raw_refs if isinstance(ref, str) and ref in card_map
    ))
    if not refs:
        return None, "atomic grounding: candidate has no valid evidence refs"

    requested_step = raw_claim.get("step_number")
    candidate = SlotContent(
        slot=slot.value,
        claims=[Claim(
            content=content,
            evidence_refs=refs,
            step_number=requested_step if isinstance(requested_step, int) else None,
            is_cross_page=any(card_map[ref].is_cross_page for ref in refs),
        )],
        evidence_card_ids=refs,
    )
    verified = semantic_grounding_verifier(
        slot=slot,
        slot_content=candidate,
        evidence_cards=[card_map[ref] for ref in refs],
        provider=provider,
        fail_closed=True,
        use_source_images=False,
    )
    if len(verified.claims) != 1:
        return None, "atomic grounding: candidate was not supported"
    if verified.missing_aspects:
        return None, (
            "atomic grounding: candidate remains incomplete ("
            + "; ".join(verified.missing_aspects[:3]) + ")"
        )

    grounded = verified.claims[0]
    result = copy.deepcopy(patch)
    result["new_claim"] = {
        "content": grounded.content,
        "evidence_refs": list(grounded.evidence_refs),
    }
    # The verifier judges semantics, not insertion position.  Preserve the
    # patcher's requested location for ordered-slot add operations.
    if action == "add" and requested_step is not None:
        result["new_claim"]["step_number"] = requested_step
    return result, ""


def _validate_covered_removal(
    slot: CockburnSlot,
    slot_content: SlotContent,
    patch: Dict[str, Any],
    detector_target_idx: int,
    provider: ModelProvider,
) -> Tuple[bool, str]:
    """Allow deletion only when retained claims explicitly subsume its facts.

    Detector findings identify a consistency concern, not permission to discard
    an already grounded detail.  The patcher must therefore name surviving
    witnesses; an independent strict check accepts removal only for a genuine
    duplicate/subsumed claim.
    """
    if patch.get("action") != "remove":
        return True, ""

    if 0 <= detector_target_idx < len(slot_content.claims):
        target_idx = detector_target_idx
    else:
        raw_idx = patch.get("target_claim_index")
        if isinstance(raw_idx, bool):
            return False, "coverage-preserving removal: invalid target_claim_index"
        try:
            target_idx = int(raw_idx)
        except (TypeError, ValueError):
            return False, "coverage-preserving removal: invalid target_claim_index"
    if not (0 <= target_idx < len(slot_content.claims)):
        return False, "coverage-preserving removal: target_claim_index out of range"

    raw_witnesses = patch.get("coverage_witness_claim_indices")
    if not isinstance(raw_witnesses, list) or not raw_witnesses:
        return False, "coverage-preserving removal: no retained witness claims supplied"
    witness_indices: List[int] = []
    for raw_idx in raw_witnesses:
        if isinstance(raw_idx, bool):
            return False, "coverage-preserving removal: witness index is invalid"
        try:
            idx = int(raw_idx)
        except (TypeError, ValueError):
            return False, "coverage-preserving removal: witness index is invalid"
        if idx == target_idx or not (0 <= idx < len(slot_content.claims)):
            return False, "coverage-preserving removal: witness index is out of range"
        if idx not in witness_indices:
            witness_indices.append(idx)

    target = slot_content.claims[target_idx].content
    witnesses = [
        {"index": idx, "content": slot_content.claims[idx].content}
        for idx in witness_indices
    ]
    prompt = (
        "You are a strict information-preservation checker for a structured use case.\n"
        f"Slot: {slot.value}\n"
        f"Claim proposed for deletion (index {target_idx}): {json.dumps(target, ensure_ascii=False)}\n"
        f"Retained witness claims: {json.dumps(witnesses, ensure_ascii=False)}\n\n"
        "Answer true ONLY if the witness claims, together, explicitly state every material "
        "fact in the deleted claim. Do not infer facts from general knowledge, a broader "
        "goal, or another branch. Compatible, related, or less-specific claims are not "
        "coverage. This is not a consistency judgement.\n"
        "Return ONLY JSON: {\"covered\": true|false, \"reason\": \"brief\"}."
    )
    decision = _parse_json(provider.llm(prompt), provider=provider, retry_prompt=prompt)
    if not isinstance(decision, dict) or decision.get("covered") is not True:
        reason = decision.get("reason") if isinstance(decision, dict) else "invalid verifier response"
        return False, f"coverage-preserving removal: not subsumed ({reason})"
    return True, ""


def _refresh_dra_slot_audit(
    slot: CockburnSlot,
    slot_content: SlotContent,
    card_map: Dict[str, EvidenceCard],
) -> None:
    """Make L1 audit observe the post-DRA claim population exactly once."""
    slot_content.pre_constraint_draft = _build_pre_constraint_draft(
        slot,
        {
            "status": "semantic_verified",
            "claims": [claim.to_dict() for claim in slot_content.claims],
            "missing_aspects": list(slot_content.missing_aspects or []),
        },
        sorted(card_map),
    )
    if slot == CockburnSlot.GOAL and slot_content.claims:
        slot_content.scope_contract = dict(slot_content.scope_contract or {})
        slot_content.scope_contract["goal_statement"] = slot_content.claims[0].content
        slot_content.scope_contract["evidence_refs"] = list(
            slot_content.claims[0].evidence_refs or []
        )


def _repair_one_round(
    actor: str,
    use_case_name: str,
    uc: UseCase,
    issues: List[Dict[str, Any]],
    file_names: List[str],
    topk: int,
    retrieval_fn: Callable,
    provider: ModelProvider,
    slots_already_rewritten: set,
) -> Tuple[List[str], List[str]]:
    """
    Fix 5: 单轮 Repair 主体，从 cross_slot_revision 里抽出来供多轮调用。

    返回 (revised_slots, inconsistency_hints)，由外层用于决定是否记 revision_history。
    slots_already_rewritten 在多轮间共享——同一 slot 在整个 DRA Pass 内最多被
    slot_rewrite 一次，避免无限抖动。
    """
    issue_groups = _merge_issues_per_target(issues)
    logger.info(f"[consistency-merge] {len(issue_groups)} issue group(s) after merging")

    revised_slots: List[str] = []
    inconsistency_hints: List[str] = []

    for root_slot_str, target_idx, group_issues in issue_groups:
        slot_enum = _slot_from_str(root_slot_str)
        if slot_enum is None:
            continue

        # Issue B: primary_actor 来自用户 CLI 输入，不允许 patcher 改动；
        # 直接把 issue 转成 quality_flag 让用户自行处理。
        if slot_enum == CockburnSlot.PRIMARY_ACTOR:
            for it in group_issues:
                uc.add_flag(QualityFlag(
                    flag_type="unresolved_inconsistency",
                    slot=root_slot_str,
                    detail=(
                        f"[{it['rule_violated']}] {it['root_cause']} "
                        "(primary_actor is user-supplied and not auto-revisable; review manually)"
                    ),
                ))
            continue

        slot_content = uc.slots.get(root_slot_str)
        if slot_content is None or slot_content.is_empty():
            for it in group_issues:
                uc.add_flag(QualityFlag(
                    flag_type="unresolved_inconsistency",
                    slot=root_slot_str,
                    detail=f"[{it['rule_violated']}] {it['root_cause']} (slot empty/missing)",
                ))
            continue

        # Issue G: 前一个 patch 可能已经修改过同 slot 的 claims，原 target_idx 失效。
        # 用 detect 时 snapshot 的 fingerprint（claim content）重新定位；找不到 → 退化 -1（slot-wide）。
        fingerprints = [it.get("target_claim_fingerprint") for it in group_issues]
        relocate_fp = next((fp for fp in fingerprints if fp is not None), None)
        if relocate_fp is not None:
            try:
                target_idx = next(
                    i for i, c in enumerate(slot_content.claims) if c.content == relocate_fp
                )
            except StopIteration:
                logger.info(
                    f"[consistency-repair] fingerprint of {root_slot_str}#{target_idx} no longer "
                    "found in slot (likely removed by earlier patch); downgrading to -1"
                )
                target_idx = -1

        card_map = {c.card_id: c for c in uc.all_evidence_cards}

        patch = claim_patcher(
            actor=actor,
            use_case_name=use_case_name,
            slot=slot_enum,
            target_claim_index=target_idx,
            issues=group_issues,
            slot_content=slot_content,
            evidence_cards=uc.all_evidence_cards,
            provider=provider,
            filled_slots=uc.slots,
        )

        if patch is None:
            for it in group_issues:
                uc.add_flag(QualityFlag(
                    flag_type="unresolved_inconsistency",
                    slot=root_slot_str,
                    detail=f"[{it['rule_violated']}] {it['root_cause']} (patcher failed to return JSON)",
                ))
            continue

        # DRA is monotonic and claim-local.  Full-slot regeneration is too
        # disruptive for an ablation comparison because a one-claim repair can
        # otherwise rewrite unrelated, already-grounded author output.
        if patch.get("action") == "slot_rewrite":
            for it in group_issues:
                uc.add_flag(QualityFlag(
                    flag_type="unresolved_inconsistency",
                    slot=root_slot_str,
                    detail=(
                        f"[{it['rule_violated']}] {it['root_cause']} "
                        "(full-slot rewrite rejected by monotonic DRA policy)"
                    ),
                ))
            continue

        # need_evidence 升级路径：每组至多一次（第二次仍 need_evidence 则转 flag）
        if patch.get("action") == "need_evidence":
            raw_query = patch.get("evidence_query")
            query = raw_query.strip() if isinstance(raw_query, str) else ""
            if not query:
                logger.warning(f"[patcher] {root_slot_str}#{target_idx} need_evidence with empty/invalid query")
                for it in group_issues:
                    uc.add_flag(QualityFlag(
                        flag_type="unresolved_inconsistency",
                        slot=root_slot_str,
                        detail=f"[{it['rule_violated']}] {it['root_cause']} (patcher requested evidence but gave empty/invalid query)",
                    ))
                continue
            logger.info(f"[patcher] {root_slot_str}#{target_idx} escalating: query={query!r}")
            new_cards = _run_single_evidence_round(
                actor=actor,
                use_case_name=use_case_name,
                slot=slot_enum,
                filled_slots=uc.slots,
                query=query,
                file_names=file_names,
                topk=topk,
                retrieval_fn=retrieval_fn,
                provider=provider,
                existing_card_ids={c.card_id for c in uc.all_evidence_cards},
            )
            uc.add_evidence_cards(new_cards)
            patch = claim_patcher(
                actor=actor,
                use_case_name=use_case_name,
                slot=slot_enum,
                target_claim_index=target_idx,
                issues=group_issues,
                slot_content=slot_content,
                evidence_cards=uc.all_evidence_cards,
                provider=provider,
                filled_slots=uc.slots,
            )
            if patch is None or patch.get("action") == "need_evidence":
                for it in group_issues:
                    uc.add_flag(QualityFlag(
                        flag_type="unresolved_inconsistency",
                        slot=root_slot_str,
                        detail=f"[{it['rule_violated']}] {it['root_cause']} (still no supporting evidence after retrieval)",
                    ))
                continue
            # Evidence retry is still constrained to atomic edits.
            if patch.get("action") == "slot_rewrite":
                for it in group_issues:
                    uc.add_flag(QualityFlag(
                        flag_type="unresolved_inconsistency",
                        slot=root_slot_str,
                        detail=(
                            f"[{it['rule_violated']}] {it['root_cause']} "
                            "(full-slot rewrite rejected after evidence retry)"
                        ),
                    ))
                continue
            card_map = {c.card_id: c for c in uc.all_evidence_cards}

        # A slot-wide detector finding still requires an atomic target.  Give
        # the patcher one corrective retry instead of silently losing an
        # otherwise repairable DRA issue to target_claim_index=-1.
        if _patch_needs_concrete_target(patch, target_idx, slot_content):
            feedback = (
                "Your previous patch selected modify/remove without one valid concrete "
                "target_claim_index. Return exactly one atomic action: choose an existing "
                f"index in [0, {max(0, len(slot_content.claims) - 1)}], or use add/need_evidence. "
                "Do not return -1 and do not rewrite the whole slot."
            )
            retry_patch = claim_patcher(
                actor=actor,
                use_case_name=use_case_name,
                slot=slot_enum,
                target_claim_index=target_idx,
                issues=group_issues,
                slot_content=slot_content,
                evidence_cards=uc.all_evidence_cards,
                provider=provider,
                filled_slots=uc.slots,
                repair_feedback=feedback,
            )
            if retry_patch is None or _patch_needs_concrete_target(
                retry_patch, target_idx, slot_content,
            ):
                reason = "atomic DRA patch lacks a concrete target after correction retry"
                logger.warning("[patcher] %s#%s patch rejected: %s", root_slot_str, target_idx, reason)
                for it in group_issues:
                    uc.add_flag(QualityFlag(
                        flag_type="unresolved_inconsistency",
                        slot=root_slot_str,
                        detail=f"[{it['rule_violated']}] {it['root_cause']} ({reason})",
                    ))
                continue
            patch = retry_patch

        # Validate only the new/modified claim.  The untouched base claims have
        # already passed the same semantic gate and must remain byte-stable.
        patch, grounding_reason = _verify_atomic_dra_patch(
            slot=slot_enum,
            patch=patch,
            card_map=card_map,
            provider=provider,
        )
        if patch is None:
            logger.warning(
                "[patcher] %s#%s patch rejected: %s",
                root_slot_str, target_idx, grounding_reason,
            )
            for it in group_issues:
                uc.add_flag(QualityFlag(
                    flag_type="unresolved_inconsistency",
                    slot=root_slot_str,
                    detail=f"[{it['rule_violated']}] {it['root_cause']} ({grounding_reason})",
                ))
            continue

        before_patch = copy.deepcopy(slot_content)
        removal_ok, removal_reason = _validate_covered_removal(
            slot=slot_enum,
            slot_content=slot_content,
            patch=patch,
            detector_target_idx=target_idx,
            provider=provider,
        )
        if not removal_ok:
            logger.warning(
                "[patcher] %s#%s patch rejected: %s",
                root_slot_str, target_idx, removal_reason,
            )
            for it in group_issues:
                uc.add_flag(QualityFlag(
                    flag_type="unresolved_inconsistency",
                    slot=root_slot_str,
                    detail=f"[{it['rule_violated']}] {it['root_cause']} ({removal_reason})",
                ))
            continue
        # 应用普通 modify/remove/add
        success, reason = _apply_patch(slot_content, patch, target_idx, card_map)
        if not success:
            logger.warning(f"[patcher] {root_slot_str}#{target_idx} patch rejected: {reason}")
            for it in group_issues:
                uc.add_flag(QualityFlag(
                    flag_type="unresolved_inconsistency",
                    slot=root_slot_str,
                    detail=f"[{it['rule_violated']}] {it['root_cause']} ({reason})",
                ))
            continue

        # An atomic repair may not erase an entire previously filled slot.  If
        # consistency cannot be repaired without abstaining on the whole slot,
        # preserve the grounded base output and expose the unresolved issue.
        if slot_content.is_empty():
            uc.slots[root_slot_str] = before_patch
            slot_content = before_patch
            reason = "monotonic DRA: patch would empty a previously filled slot"
            logger.warning("[patcher] %s#%s rollback: %s", root_slot_str, target_idx, reason)
            for it in group_issues:
                uc.add_flag(QualityFlag(
                    flag_type="unresolved_inconsistency",
                    slot=root_slot_str,
                    detail=f"[{it['rule_violated']}] {it['root_cause']} ({reason})",
                ))
            continue

        _refresh_dra_slot_audit(slot_enum, slot_content, card_map)

        logger.info(f"[patcher] {root_slot_str}#{target_idx} action={patch.get('action')} OK")
        if root_slot_str not in revised_slots:
            revised_slots.append(root_slot_str)
        for it in group_issues:
            inconsistency_hints.append(it["root_cause"])

    return revised_slots, inconsistency_hints


def cross_slot_revision(
    actor: str,
    use_case_name: str,
    uc: UseCase,
    file_names: List[str],
    topk: int,
    retrieval_fn: Callable,
    provider: ModelProvider,
    max_rounds: int = _DEFAULT_DRA_MAX_ROUNDS,
) -> UseCase:
    """
    DRA Pass: 多轮 Detect → Repair（Fix 5 + 自查后的修正）。

    流程:
      1. 初始 Detect — 没有 issue 直接返回
      2. 最多 max_rounds 轮 Repair → Detect 验证：
         - Repair 改不动 → 保留剩余 issues 进 Annotate，退出
         - Repair 后 Detect 显示已解决 → 退出
         - 仍有 issues 且未到最后一轮 → 进入下一轮 Repair
         - 最后一轮：Repair 后 Detect 的残留作为 Annotate
      3. Annotate — 把残留 issues 转 unresolved_inconsistency flag

    同一 slot 在整个 pass 内最多被 slot_rewrite 一次（_repair_one_round 内保护）。
    自查后修正：旧实现在最后一轮直接 break，导致 max_rounds=2 时实际只 Repair 1 次。
    新实现保证每轮都真正执行 Repair，配额 max_rounds 是真实的 Repair 次数上限。
    """
    slots_already_rewritten: set = set()
    # DRA is restricted to the original R1--R7 consistency rules.  Slot-specific
    # semantic guidance remains in the author prompts, but no extra deterministic
    # branch-ownership rewrite is applied as an eighth/ninth DRA rule.
    all_revised_slots: List[str] = []
    all_inconsistency_hints: List[str] = []

    # ---- Initial Detect ----
    logger.info("[consistency] === DRA initial Detect ===")
    issues, parse_failures = _detect_consistency_grouped(actor, use_case_name, uc, provider)
    if not issues:
        logger.info("[consistency] no issues detected; skipping repair.")
        for group in parse_failures:
            uc.add_flag(QualityFlag(
                flag_type="consistency_check_failed",
                slot=None,
                detail=f"Consistency detector ({group}) returned malformed JSON; this group's issues were not evaluated.",
            ))
        if all_revised_slots:
            uc.add_revision(RevisionRecord(
                round_num=0,
                action="consistency_revision",
                revised_slots=all_revised_slots,
                inconsistency_hints=all_inconsistency_hints,
            ))
        return uc

    last_issues = issues
    last_parse_failures = parse_failures

    # ---- Repair → Detect 循环 ----
    for round_idx in range(max_rounds):
        logger.info(f"[consistency] === DRA round {round_idx + 1}/{max_rounds}: Repair {len(last_issues)} issue(s) ===")
        revised, hints = _repair_one_round(
            actor=actor,
            use_case_name=use_case_name,
            uc=uc,
            issues=last_issues,
            file_names=file_names,
            topk=topk,
            retrieval_fn=retrieval_fn,
            provider=provider,
            slots_already_rewritten=slots_already_rewritten,
        )
        for s in revised:
            if s not in all_revised_slots:
                all_revised_slots.append(s)
        all_inconsistency_hints.extend(hints)

        if not revised:
            # 本轮一个 slot 都没修动 → 剩余 issues 留 annotate，退出
            logger.info(f"[consistency] round {round_idx + 1}: no slot modified; keeping issues for annotate")
            break

        # 修过了 → 复检
        logger.info(f"[consistency] === DRA round {round_idx + 1}: re-Detect ===")
        new_issues, new_failures = _detect_consistency_grouped(actor, use_case_name, uc, provider)
        last_parse_failures = new_failures  # 总是只记最近一次的
        if not new_issues:
            logger.info(f"[consistency] round {round_idx + 1}: all issues resolved")
            last_issues = []
            break
        last_issues = new_issues
        # 如果还有 max_rounds 配额就继续；否则进入 Annotate

    # ---- Annotate ----
    for group in last_parse_failures:
        uc.add_flag(QualityFlag(
            flag_type="consistency_check_failed",
            slot=None,
            detail=f"Consistency detector ({group}) returned malformed JSON; remaining issues not verified.",
        ))

    if all_revised_slots:
        uc.add_revision(RevisionRecord(
            round_num=0,
            action="consistency_revision",
            revised_slots=all_revised_slots,
            inconsistency_hints=all_inconsistency_hints,
        ))

    for it in last_issues:
        uc.add_flag(QualityFlag(
            flag_type="unresolved_inconsistency",
            slot=it["root_slot"],
            detail=f"[{it['rule_violated']}] {it['root_cause']}",
        ))
    if last_issues:
        logger.info(f"[consistency-annotate] {len(last_issues)} issue(s) remain → quality_flags")

    return uc


# ============================================================
# Per-Slot DCRRM Loop
# ============================================================

def _slot_fingerprint(content: SlotContent) -> Tuple:
    """Canonical information fingerprint used before the optional LLM convergence check."""
    claims = tuple(
        (
            claim.step_number,
            " ".join(sorted(_normalized_terms(claim.content))),
            tuple(sorted(claim.evidence_refs)),
        )
        for claim in content.claims
    )
    return (
        " ".join(sorted(_normalized_terms(content.raw_text))),
        claims,
        tuple(sorted(content.covered_aspect_ids)),
        tuple(sorted(content.missing_aspects)),
    )


def per_slot_dcrrm_loop(*args, **kwargs):
    raise RuntimeError("Legacy page path disabled in L5; use runtime.pipeline text path")



# ============================================================
# 辅助函数
# ============================================================

def _slot_abbrev(slot: CockburnSlot) -> str:
    mapping = {
        CockburnSlot.GOAL: "G", CockburnSlot.PRIMARY_ACTOR: "PA",
        CockburnSlot.PRECONDITIONS: "PC", CockburnSlot.TRIGGER: "TR",
        CockburnSlot.MAIN_FLOW: "MF", CockburnSlot.ALTERNATIVE_FLOW: "AF",
        CockburnSlot.EXCEPTION_FLOW: "EF", CockburnSlot.POSTCONDITIONS: "PO",
    }
    return mapping.get(slot, "XX")


def _slot_guidance(slot: CockburnSlot) -> str:
    guidance = {
        CockburnSlot.GOAL: "Write ONE sentence describing the user's high-level objective.",
        CockburnSlot.PRIMARY_ACTOR: "Output a single actor name or role.",
        CockburnSlot.PRECONDITIONS: "List each precondition as a separate claim. Each must be a state assertion.",
        CockburnSlot.TRIGGER: "Describe the triggering event or user action in one sentence.",
        CockburnSlot.MAIN_FLOW: "Numbered steps. Each step = one claim. Steps must be logically sequential.",
        CockburnSlot.ALTERNATIVE_FLOW: "Numbered alternative paths. Each must reference which main flow step it diverges from.",
        CockburnSlot.EXCEPTION_FLOW: "Numbered exception branches. Each must have an exit (return to step / terminate / go to alt flow).",
        CockburnSlot.POSTCONDITIONS: "List each postcondition as a separate claim. Each describes system state after success.",
    }
    return guidance.get(slot, "")


def _slot_from_str(slot_str: str) -> Optional[CockburnSlot]:
    try:
        return CockburnSlot(slot_str)
    except ValueError:
        return None


def _collect_invalid_refs(data: Dict[str, Any], card_map: Dict[str, EvidenceCard]) -> set:
    """收集 LLM 返回结构里所有不在 card_map 中的 evidence_ref ID。"""
    invalid: set = set()
    if not isinstance(data, dict):
        return invalid
    claims = data.get("claims", []) or []
    if isinstance(claims, list):
        for cdata in claims:
            if not isinstance(cdata, dict):
                continue
            refs = cdata.get("evidence_refs", []) or []
            if not isinstance(refs, list):
                continue
            for r in refs:
                if isinstance(r, str) and r not in card_map:
                    invalid.add(r)
    top_refs = data.get("evidence_refs", []) or []
    if isinstance(top_refs, list):
        for r in top_refs:
            if isinstance(r, str) and r not in card_map:
                invalid.add(r)
    scope_contract = data.get("scope_contract", {}) or {}
    if isinstance(scope_contract, dict):
        contract_refs = scope_contract.get("evidence_refs", []) or []
        if isinstance(contract_refs, list):
            for r in contract_refs:
                if isinstance(r, str) and r not in card_map:
                    invalid.add(r)
    return invalid


def _extract_page_num(page_path: str) -> Optional[int]:
    """从页面路径中提取页码。支持多种格式。"""
    # 格式1: data/images/jtvauctions/1.png
    m = re.search(r'/(\d+)\.(?:png|jpg|jpeg|gif|bmp|tiff)', str(page_path))
    if m:
        return int(m.group(1))
    # 格式2: page_0001.png (mock 路径)
    m = re.search(r'page[_-]?(\d+)', str(page_path))
    if m:
        return int(m.group(1))
    return None


def _extract_doc_key(page_path: str) -> str:
    """
    从页面路径提取所属文档的标识，用于多 PDF 检索时区分同页号。
    取页号文件之前的目录路径作为 doc key（os.path.dirname）；提取不到则用整个路径作 fallback。
    """
    p = str(page_path)
    parent = os.path.dirname(p)
    return parent if parent else p


def _coerce_bool(v: Any) -> bool:
    """把 LLM 偶尔输出的字符串布尔（"true"/"false"/"True"/"FALSE"）正确还原为 Python bool。"""
    if isinstance(v, bool):
        return v
    if isinstance(v, str):
        return v.strip().lower() == "true"
    return bool(v)


def _extract_goal_text(filled_slots: Dict[str, SlotContent], use_case_name: str = "") -> str:
    """
    从 filled_slots 中提取 Goal slot 的人类可读文本，喂给后续 prompt。

    Fix 2 / Fix 6: 让 query_designer / answer_generator 显式以 Goal 为 alignment 锚。
    优先用 raw_text；缺则拼 claims 的 content；都没有时返回占位串。

    Fix 7.1: goal slot 自举时 filled_slots 中 goal 还不存在，旧实现返回
    "(goal slot not yet filled)" 这种占位符让 Goal-alignment gate 完全失效。
    现在如果传入 use_case_name，就用 "the use case name is X (interpret literally)"
    作为最小可用的 alignment 锚，至少能阻止 LLM 把多个 use case 的证据 merge 在一起。
    """
    goal_sc = filled_slots.get("goal")
    if goal_sc is None or (goal_sc.is_empty() if hasattr(goal_sc, "is_empty") else False):
        if use_case_name:
            return (
                f'the use case name is "{use_case_name}" '
                f'(treat this as the LITERAL user-facing action; '
                f'do NOT broaden it to include post-action consequences, '
                f'adjacent administrative processes, or downstream use cases)'
            )
        return "(goal slot not yet filled)"
    if goal_sc.raw_text:
        return goal_sc.raw_text
    if goal_sc.claims:
        return " | ".join(c.content for c in goal_sc.claims if c.content)
    if use_case_name:
        return (
            f'the use case name is "{use_case_name}" '
            f'(treat this as the LITERAL user-facing action)'
        )
    return "(goal slot empty)"


def _extract_goal_context(
    filled_slots: Dict[str, SlotContent], use_case_name: str = "",
) -> str:
    """Goal text plus the evidence-derived planning boundary for downstream agents."""
    goal_text = _extract_goal_text(filled_slots, use_case_name=use_case_name)
    goal_sc = filled_slots.get(CockburnSlot.GOAL.value)
    if goal_sc is None or not goal_sc.scope_contract:
        return (
            f"{goal_text}\n"
            "Scope rule: describe one canonical successful instance of the literal use-case "
            "name. Treat selectable categories as variants, and exclude adjacent upstream "
            "or downstream use cases."
        )
    return (
        f"{goal_text}\n"
        "Evidence-derived Scope Contract (planning boundary, not an extra factual claim):\n"
        f"{json.dumps(goal_sc.scope_contract, ensure_ascii=False, indent=2)}"
    )


# 注: 原 trigger_aligns_with_goal 与 _TRIGGER_GOAL_ALIGNMENT_CHECK 已删除。
# 论文表 1 未列 trigger_goal_misalignment flag，因此不对 Trigger 单独做 Goal-alignment
# 软检查（与 R2 一致性规则的职责重叠）。如果后续需要 Trigger/Goal 一致性防护，
# 可在 DRA Pass 的 R2 规则里覆盖。
