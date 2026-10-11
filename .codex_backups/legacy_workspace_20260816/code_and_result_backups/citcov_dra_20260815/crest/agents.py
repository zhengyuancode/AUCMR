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

import json
import logging
import os
import re
import time
from dataclasses import dataclass, field
from typing import List, Dict, Optional, Callable, Any, Tuple

from json_repair import repair_json

from config.settings import (
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

    def __init__(self, llm_call: Callable, lvlm_call: Callable):
        self._llm = llm_call
        self._lvlm = lvlm_call
        self._stats = {
            "llm_calls": 0,
            "lvlm_calls": 0,
            "llm_retry_calls": 0,
            "lvlm_retry_calls": 0,
            "llm_seconds": 0.0,
            "lvlm_seconds": 0.0,
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


def query_designer(
    actor: str,
    use_case_name: str,
    slot: CockburnSlot,
    filled_slots: Dict[str, SlotContent],
    current_slot_content: SlotContent,
    history_queries: List[str],
    history_page_paths: set,
    inconsistency_hint: str,
    retrieval_fn: Callable,
    file_names: List[str],
    topk: int,
    provider: ModelProvider,
    prior_evidence_cards: Optional[List[EvidenceCard]] = None,
    query_retry_max: int = _DEFAULT_QUERY_RETRY_MAX,
    overlap_rate_max: float = _DEFAULT_OVERLAP_RATE_MAX,
    min_novel_pages_per_round: int = _DEFAULT_MIN_NOVEL_PAGES_PER_ROUND,
) -> QueryPlan:
    """
    生成下一轮检索查询 + 内置终止判断 + 检索结果复用。

    三重终止逻辑（均在 query_designer 内部）:
    1. LLM 返回 [] → 判断证据链已完整，主动终止
    2. 新查询与历史查询文本重叠 > overlap_rate_max → 提醒重试，超 query_retry_max 强制终止
    3. 新查询检索结果与历史页面重叠 > overlap_rate_max 且新页面数 < min_novel_pages_per_round
       → 提醒重试，超 query_retry_max 强制终止
       (Fix 3: 重叠率 + novel page 联合判定，避免多页 section 被裸比例锁死)

    返回:
        (queries, search_results, should_stop)
        - queries: 新查询列表（可能为空）
        - search_results: 检索结果（直接传给 evidence_collector 复用，避免重复检索）
        - should_stop: 是否终止当前 slot 的 DCRRM 循环
    """
    filled_json = json.dumps(
        {k: v.to_dict() for k, v in filled_slots.items()}, ensure_ascii=False, indent=2
    )
    current_json = json.dumps(current_slot_content.to_dict(), ensure_ascii=False, indent=2)
    history_json = json.dumps(history_queries, ensure_ascii=False)

    # Fix 1: 前序 slot 的 evidence cards 摘要——
    # 以前措辞是 "avoid re-searching"，会把 Goal 已经发现的 page 25-27 当禁区，
    # 导致 Trigger/Main Flow 被迫去别处找证据（落到 page 96 的"搜索 Go"上）。
    # 现在改为"may also support current slot — only query NEW gaps"，并附 source_pages
    # 让 LLM 据此做"复用 vs 新增"的判断。
    prior_cards_context = ""
    if prior_evidence_cards:
        compact_prior = _select_active_cards(
            slot=slot,
            own_cards=[],
            prior_cards=prior_evidence_cards,
            filled_slots=filled_slots,
            use_case_name=use_case_name,
            budget=min(12, _DEFAULT_ACTIVE_CARD_BUDGETS.get(slot.value, 12)),
        )
        prior_summary = [
            {
                "card_id": c.card_id,
                "content": c.content[:120],
                "source_pages": c.source_pages,
                "slot_relevance": c.slot_relevance,
            }
            for c in compact_prior
        ]
        prior_cards_context = json.dumps(prior_summary, ensure_ascii=False, indent=2)

    # Fix 2 + 7.1: 提取 Goal 文本，goal slot 自举时回退到 use_case_name 做锚
    goal_text = _extract_goal_context(filled_slots, use_case_name=use_case_name)

    # Fix 6: trigger 槽位单独提示——避免被填成"搜索动作"而非"启动 Goal 的动作"
    slot_specific_hint = ""
    if slot == CockburnSlot.TRIGGER:
        slot_specific_hint = (
            "=== TRIGGER-SPECIFIC GUIDANCE ===\n"
            "The trigger is the user action that DIRECTLY initiates the Goal-achieving interaction.\n"
            f"The Goal is: {goal_text}\n"
            "Search for the click/select/submit action that starts the main flow.\n"
            "Example: if Goal is 'place bids', trigger queries should target 'click Place Bid',\n"
            "'press QuickBid button', 'submit bid confirmation' — NOT 'search by Product ID',\n"
            "which is an adjacent search use case, not a bidding trigger."
        )

    base_prompt = QUERY_DESIGNER.substitute(
        actor=actor,
        use_case_name=use_case_name,
        goal_text=goal_text,
        slot_type=slot.value,
        slot_description=SLOT_DESCRIPTIONS.get(slot.value, ""),
        leading_question=SLOT_LEADING_QUESTIONS.get(slot.value, ""),
        filled_slots_json=filled_json,
        current_slot_json=current_json,
        history_queries_json=history_json,
        prior_evidence_summary=prior_cards_context,
        slot_specific_hint=slot_specific_hint,
        inconsistency_hint=(
            f"IMPORTANT — Previous issues with this slot:\n{inconsistency_hint}"
            if inconsistency_hint else ""
        ),
    )

    for retry in range(query_retry_max):
        raw = provider.llm(base_prompt)
        result = _parse_json(raw)
        structured = isinstance(result, dict)
        reuse_card_ids: List[str] = []
        missing_aspects: List[str] = []
        reason = ""
        explicit_stop = False
        if isinstance(result, dict):
            raw_reuse = result.get("reuse_card_ids", [])
            if isinstance(raw_reuse, list):
                reuse_card_ids = [value for value in raw_reuse if isinstance(value, str)]
            raw_missing = result.get("missing_aspects", [])
            if isinstance(raw_missing, list):
                missing_aspects = [str(value) for value in raw_missing if str(value).strip()]
            reason = str(result.get("reason", ""))
            explicit_stop = _coerce_bool(result.get("stop", False))
            for key in ("queries", "query", "items", "list"):
                v = result.get(key)
                if isinstance(v, list):
                    result = v
                    break
        queries: List[str] = []
        if isinstance(result, list):
            if not result:
                logger.info(f"[{slot.value}] Query Designer returned [] — evidence complete.")
                return QueryPlan(
                    should_stop=True,
                    reuse_card_ids=reuse_card_ids,
                    missing_aspects=missing_aspects,
                    reason=reason,
                    structured=structured,
                )
            queries = [q for q in result if isinstance(q, str) and q not in history_queries]
        queries = queries[:max(1, _DEFAULT_QUERIES_PER_ROUND)]
        if explicit_stop:
            return QueryPlan(
                should_stop=True,
                reuse_card_ids=reuse_card_ids,
                missing_aspects=missing_aspects,
                reason=reason,
                structured=structured,
            )
        if not queries:
            return QueryPlan(
                should_stop=True,
                reuse_card_ids=reuse_card_ids,
                missing_aspects=missing_aspects,
                reason=reason,
                structured=structured,
            )

        # 检查 1: 查询文本重叠
        text_overlap = _calc_overlap_rate(queries, history_queries)
        if text_overlap > overlap_rate_max:
            logger.warning(f"[{slot.value}] Query text overlap {text_overlap:.2f} > {overlap_rate_max}")
            if retry < query_retry_max - 1:
                # 自查后修正 Logic F: retry 时把原始 inconsistency_hint（如 trigger 重跑
                # 时传入的 alignment 指导）和重叠警告拼接，而不是完全覆盖——
                # 否则关键方向约束会在 overlap 重试时丢失。
                overlap_warning = (
                    f"Your previous queries were too similar to already-used queries "
                    f"(overlap {text_overlap:.0%} > {overlap_rate_max:.0%}). "
                    f"Design completely DIFFERENT queries or return [] if done."
                )
                merged_hint = (
                    f"{inconsistency_hint}\n\nADDITIONAL: {overlap_warning}"
                    if inconsistency_hint else overlap_warning
                )
                base_prompt = QUERY_DESIGNER.substitute(
                    actor=actor, use_case_name=use_case_name,
                    goal_text=goal_text,
                    slot_type=slot.value,
                    slot_description=SLOT_DESCRIPTIONS.get(slot.value, ""),
                    leading_question=SLOT_LEADING_QUESTIONS.get(slot.value, ""),
                    filled_slots_json=filled_json,
                    current_slot_json=current_json,
                    history_queries_json=history_json,
                    prior_evidence_summary=prior_cards_context,
                    slot_specific_hint=slot_specific_hint,
                    inconsistency_hint=merged_hint,
                )
                continue
            else:
                logger.info(f"[{slot.value}] Query overlap retries exhausted, forcing stop.")
                return QueryPlan(should_stop=True, reason="query overlap exhausted")

        # 执行检索（结果后续复用）
        try:
            search_results = retrieval_fn(queries, file_names, topk)
        except Exception as e:
            # Retrieval infrastructure/model failures are not evidence that the use
            # case is unresolvable. Abort the run so --resume can retry after repair,
            # instead of silently writing a false Goal fast-fail artifact.
            logger.exception(f"[{slot.value}] Retrieval failed: {e}")
            raise RuntimeError(f"[{slot.value}] retrieval failed") from e

        # 检查 2: 页面重叠 — Fix 3: 联合判定（既重叠又无新页面才终止）
        page_overlap, novel_pages = _calc_page_overlap_rate(search_results, history_page_paths)
        should_terminate_on_overlap = (
            page_overlap > overlap_rate_max
            and novel_pages < min_novel_pages_per_round
        )
        if should_terminate_on_overlap:
            logger.warning(
                f"[{slot.value}] Page overlap {page_overlap:.2f} > {overlap_rate_max} "
                f"AND novel_pages={novel_pages} < {min_novel_pages_per_round}"
            )
            if retry < query_retry_max - 1:
                # 同样拼接原 hint
                overlap_warning = (
                    f"Your queries returned mostly already-seen pages "
                    f"(overlap {page_overlap:.0%} > {overlap_rate_max:.0%}, "
                    f"only {novel_pages} new page(s)). "
                    f"Design queries that target DIFFERENT parts of the document, "
                    f"or return [] if the evidence is exhausted."
                )
                merged_hint = (
                    f"{inconsistency_hint}\n\nADDITIONAL: {overlap_warning}"
                    if inconsistency_hint else overlap_warning
                )
                base_prompt = QUERY_DESIGNER.substitute(
                    actor=actor, use_case_name=use_case_name,
                    goal_text=goal_text,
                    slot_type=slot.value,
                    slot_description=SLOT_DESCRIPTIONS.get(slot.value, ""),
                    leading_question=SLOT_LEADING_QUESTIONS.get(slot.value, ""),
                    filled_slots_json=filled_json,
                    current_slot_json=current_json,
                    history_queries_json=history_json,
                    prior_evidence_summary=prior_cards_context,
                    slot_specific_hint=slot_specific_hint,
                    inconsistency_hint=merged_hint,
                )
                continue
            else:
                logger.info(f"[{slot.value}] Page overlap retries exhausted, forcing stop.")
                return QueryPlan(should_stop=True, reason="page overlap exhausted")
        elif page_overlap > overlap_rate_max:
            # 高度重叠但有新页面 → 不终止，记一行 info 便于排查
            logger.info(
                f"[{slot.value}] Page overlap {page_overlap:.2f} > {overlap_rate_max} "
                f"but novel_pages={novel_pages} >= {min_novel_pages_per_round}; continuing."
            )

        logger.info(f"[{slot.value}] Query Designer: {queries} (text_ovlp={text_overlap:.2f}, page_ovlp={page_overlap:.2f}, novel={novel_pages})")
        return QueryPlan(
            queries=queries,
            search_results=search_results,
            should_stop=False,
            reuse_card_ids=reuse_card_ids,
            missing_aspects=missing_aspects,
            reason=reason,
            structured=structured,
        )

    return QueryPlan(should_stop=True, reason="query retries exhausted")


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


def _build_evidence_card(
    claim_data: dict, card_id: str, source_pages: List[int],
    is_cross_page: bool = False, confidence: float = 0.9,
    source_doc: str = "",
) -> EvidenceCard:
    """
    从 claim_data dict 构建 EvidenceCard。

    页码（source_pages）由调用方代码绑定，LVLM 不参与页码决策。
    LVLM 只输出 location（页内相对位置）作为辅助溯源信息。
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

def evidence_collector(
    actor: str,
    use_case_name: str,
    slot: CockburnSlot,
    filled_slots: Dict[str, SlotContent],
    file_names: List[str],
    topk: int,
    retrieval_fn: Callable,
    provider: ModelProvider,
    precomputed_results: Optional[List[List[str]]] = None,
    max_page_chain: int = _DEFAULT_MAX_PAGE_CHAIN,
    inconsistency_hint: str = "",
    card_idx_start: int = 0,
) -> List[EvidenceCard]:
    """
    Evidence Collector: 检索 → 合并连续页为 region → 逐 region 读 → 完备判断 → 翻页 → 生成 claim。

    核心流程:
    1. 使用 precomputed_results（来自 query_designer）或自行检索
    2. 按连续页码分组（如 [1,2,5,8,9] → [[1,2], [5], [8,9]]）
    3. 每个 group 作为一个初始 region，LVLM 判断完备性
    4. 不完备则按翻页方向扩展，形成更大 region 后再判断
    5. 完备 region 生成 EvidenceCards（每条 claim 自带 source_pages 溯源）
    """
    cards: List[EvidenceCard] = []
    read_page_ids: set = set()
    card_idx = card_idx_start

    # --- Step 1: 使用已有检索结果（必须由 query_designer 提供） ---
    if precomputed_results is None:
        raise ValueError("evidence_collector requires precomputed_results from query_designer")
    search_results = precomputed_results
    # Fix α: 多 PDF 检索时同页号会撞 key；用 (doc_key, page_num) 做主键避免静默丢页。
    all_candidates: Dict[Tuple[str, int], str] = {}
    for result_set in search_results:
        for page_path in result_set:
            pn = _extract_page_num(page_path)
            if pn is None:
                continue
            doc = _extract_doc_key(page_path)
            key = (doc, pn)
            if key in read_page_ids:
                continue
            all_candidates[key] = page_path

    # --- Step 2: 按连续页码分组 ---
    # 同 doc 内连续才视为连续；跨 doc 强制断开。
    page_items = [{"doc": d, "num": pn, "path": pp} for (d, pn), pp in all_candidates.items()]
    groups = _group_consecutive_pages(page_items)
    logger.info(f"[{slot.value}] {len(all_candidates)} candidate pages → {len(groups)} region(s)")

    # --- Step 3: 逐 region 处理 ---
    filled_json = json.dumps(
        {k: v.to_dict() for k, v in filled_slots.items()}, ensure_ascii=False, indent=2
    )

    for group in groups:
        region_pages = [g["num"] for g in group]
        region_paths = [g["path"] for g in group]
        region_doc = group[0].get("doc", "")  # 同 group 内 doc 相同（_group_consecutive_pages 保证）

        # 标记为已读（用 (doc, page_num) 复合 key 防多 PDF 撞号）
        for pn in region_pages:
            read_page_ids.add((region_doc, pn))

        # 构造 hint 上下文
        hint_context = ""
        if inconsistency_hint:
            hint_context = (
                f"CONSISTENCY ISSUE TO RESOLVE: {inconsistency_hint}\n"
                "Focus on finding evidence that can resolve this inconsistency."
            )

        # --- Step 3a: 读当前 region ---
        if len(region_paths) == 1:
            # 单页 → 单页 prompt
            prompt = EVIDENCE_COLLECTOR_SINGLE_PAGE.substitute(
                actor=actor,
                use_case_name=use_case_name,
                slot_type=slot.value,
                slot_description=SLOT_DESCRIPTIONS.get(slot.value, ""),
                filled_slots_json=filled_json,
                previous_page_context=hint_context,
                slot_extraction_rules=_SLOT_EXTRACTION_RULES,
                paging_actions_schema=_PAGING_ACTIONS_SCHEMA,
            )
            raw = provider.lvlm(prompt, region_paths)
            result = _parse_lvlm_json(raw)
        else:
            # 多页 → region prompt
            prompt = EVIDENCE_COLLECTOR_REGION.substitute(
                actor=actor,
                use_case_name=use_case_name,
                slot_type=slot.value,
                slot_description=SLOT_DESCRIPTIONS.get(slot.value, ""),
                filled_slots_json=filled_json,
                region_pages=json.dumps(region_pages),
                slot_extraction_rules=_SLOT_EXTRACTION_RULES,
                paging_actions_schema=_PAGING_ACTIONS_SCHEMA,
            )
            raw = provider.lvlm(prompt, region_paths)
            result = _parse_lvlm_json(raw)

        if result is None or not isinstance(result, dict):
            logger.warning(f"[{slot.value}] Failed to parse LVLM output (type={type(result).__name__}) for region {region_pages}")
            continue

        # 格式校验：claims 和 paging_actions 必须是 dict 列表
        if not _is_valid_lvlm_format(result):
            logger.warning(f"[{slot.value}] Bad claims/paging_actions format, retrying...")
            retry_raw = provider.lvlm_retry(prompt, region_paths, raw)
            result = _parse_lvlm_json(retry_raw)
            if not _is_valid_lvlm_format(result):
                logger.warning(f"[{slot.value}] Retry also failed for region {region_pages}, skipping")
                continue

        # --- Step 3b: 完备判断 ---
        if _coerce_bool(result.get("is_complete", False)):
            is_cross = len(region_pages) > 1
            for claim_data in result.get("claims", []):
                cards.append(_build_evidence_card(
                    claim_data, f"EC-{_slot_abbrev(slot)}-{card_idx:03d}",
                    source_pages=region_pages,
                    is_cross_page=is_cross,
                    source_doc=region_doc,
                ))
                card_idx += 1
            continue

        # --- Step 3c: 不完备 → 链式翻页扩展（P0.2 + P0.3） ---
        # 只支持 next / previous 两个相邻方向；cross_ref 已删除（[[project-crest-architecture]]
        # 用户 2026-06-19 决定：cross-reference resolution to non-adjacent pages 留作 future work）
        chain_depth = 0
        current_result = result
        region_complete = False
        while chain_depth < max_page_chain:
            # 选第一个有效的 paging action
            chosen_target: Optional[int] = None
            for action in current_result.get("paging_actions", []) or []:
                direction = action.get("direction", "")
                if direction == "next":
                    candidate = max(region_pages) + 1
                elif direction == "previous":
                    candidate = min(region_pages) - 1
                else:
                    # cross_ref 或未知方向直接忽略
                    if direction and direction != "cross_ref":
                        logger.debug(f"[{slot.value}] Unknown paging direction '{direction}' ignored")
                    continue
                if candidate <= 0 or (region_doc, candidate) in read_page_ids:
                    continue
                base_path = region_paths[0]
                target_path = _resolve_page_path(base_path, candidate)
                if not os.path.exists(target_path):
                    logger.warning(f"[{slot.value}] Target page not found: {target_path}")
                    continue
                chosen_target = candidate
                chosen_path = target_path
                break

            if chosen_target is None:
                # 没有可用的相邻页 → 链式终止
                break

            read_page_ids.add((region_doc, chosen_target))
            region_pages.append(chosen_target)
            region_paths.append(chosen_path)
            chain_depth += 1

            sorted_pages = sorted(region_pages)
            sorted_paths = [region_paths[region_pages.index(p)] for p in sorted_pages]
            region_prompt = EVIDENCE_COLLECTOR_REGION.substitute(
                actor=actor,
                use_case_name=use_case_name,
                slot_type=slot.value,
                slot_description=SLOT_DESCRIPTIONS.get(slot.value, ""),
                filled_slots_json=filled_json,
                region_pages=json.dumps(sorted_pages),
                slot_extraction_rules=_SLOT_EXTRACTION_RULES,
                paging_actions_schema=_PAGING_ACTIONS_SCHEMA,
            )
            raw = provider.lvlm(region_prompt, sorted_paths)
            region_result = _parse_lvlm_json(raw)

            if not _is_valid_lvlm_format(region_result):
                logger.warning(f"[{slot.value}] Bad region result format for {sorted_pages}, retrying...")
                retry_raw = provider.lvlm_retry(region_prompt, sorted_paths, raw)
                region_result = _parse_lvlm_json(retry_raw)
                if not _is_valid_lvlm_format(region_result):
                    logger.warning(f"[{slot.value}] Retry failed for region {sorted_pages}, treating as incomplete")
                    current_result = {"is_complete": False, "claims": [], "paging_actions": []}
                    continue

            current_result = region_result
            if _coerce_bool(region_result.get("is_complete", False)):
                region_complete = True
                break

        # --- Step 3d: 链式终止后出 claim ---
        if chain_depth == 0 and not _coerce_bool(current_result.get("is_complete", False)):
            # 第一次读就不完备且没有任何相邻页可扩展 → 仍然出 claim 但带 region_incomplete flag
            logger.info(f"[{slot.value}] Region {region_pages} incomplete and no adjacent pages available")

        sorted_pages = sorted(region_pages)
        is_cross = len(sorted_pages) > 1
        confidence = 0.85 if is_cross else 1.0
        # 触底未完备 → 标 region_incomplete 并降 confidence
        region_incomplete = (chain_depth >= max_page_chain) and not region_complete
        if region_incomplete:
            confidence = min(confidence, 0.5)

        for claim_data in current_result.get("claims", []) or []:
            card = _build_evidence_card(
                claim_data,
                f"EC-{_slot_abbrev(slot)}-{card_idx:03d}",
                source_pages=sorted_pages,
                is_cross_page=is_cross,
                confidence=confidence,
                source_doc=region_doc,
            )
            if region_incomplete:
                # 标记到 region_type 便于下游 L1 验证识别
                card.region_type = (card.region_type or "text") + "_incomplete"
            cards.append(card)
            card_idx += 1

    logger.info(f"[{slot.value}] Evidence Collector: {len(cards)} cards from {len(read_page_ids)} pages")
    return cards


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
            "Write one canonical successful use-case instance, normally 5-12 steps.\n"
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
    data = _parse_json(raw)
    # 防御 LLM 返回非 dict（list / str / None）的边角情况——退化为空 dict 走后续 evidence_invalid 路径
    if not isinstance(data, dict):
        logger.warning(
            f"[{slot.value}] Answer Generator returned non-dict ({type(data).__name__}); "
            "treating as empty response."
        )
        data = {}

    if data.get("status") == "no_evidence":
        logger.info(f"[{slot.value}] Answer Generator: model reported no_evidence")
        return SlotContent(slot=slot.value)

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
        sc = SlotContent(slot=slot.value)
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
        retry_data = _parse_json(retry_raw)
        if not isinstance(retry_data, dict):
            logger.warning(
                f"[{slot.value}] Answer Generator retry returned non-dict ({type(retry_data).__name__}); "
                "keeping first-round data and dropping invalid refs per-claim."
            )
            # 不用空 dict 覆盖——保留第一轮 data，让后续 _filter_refs 逐 claim 丢无效引用 + raise flag，
            # 避免丢失第一轮里本来合法的 claim。
        elif retry_data.get("status") == "no_evidence":
            logger.info(f"[{slot.value}] Answer Generator: retry reported no_evidence")
            return SlotContent(slot=slot.value)
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
            sc = SlotContent(slot=slot.value)
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
    )
    if slot_content_invalid_flag is not None:
        sc.quality_flags.append(slot_content_invalid_flag)
    return sc


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
    result = _parse_json(raw)
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
    校验 detector 输出 issue 列表的格式（仅格式，不查内容）：
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
            "rule_violated": it.get("rule_violated", "unspecified"),
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
    parsed_v = _coerce_to_list(_parse_json(raw_v))
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
    parsed_l = _coerce_to_list(_parse_json(raw_l))
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
    patch = _parse_json(raw)
    if not isinstance(patch, dict):
        logger.warning(f"[patcher] {slot.value} idx={target_claim_index} returned non-dict")
        return None
    return patch


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


def _run_single_evidence_round(
    actor: str,
    use_case_name: str,
    slot: CockburnSlot,
    filled_slots: Dict[str, SlotContent],
    query: str,
    file_names: List[str],
    topk: int,
    retrieval_fn: Callable,
    provider: ModelProvider,
    existing_card_ids: Optional[set] = None,
) -> List[EvidenceCard]:
    """
    need_evidence 升级路径：直接用一个 query 检索一次，跑一次 evidence_collector，
    返回新生成的 evidence cards（不跑完整 DCRRM 5 轮）。

    existing_card_ids 用于避免 card_id 重号——evidence_collector 内部计数器从 0 起，
    新卡 ID 可能与同 slot 已存在的卡撞号。先用 card_idx_start 让计数器避开
    （等于 existing 中同 slot 卡的数量），仍冲突时再追加 -rN 后缀作兜底。
    """
    try:
        search_results = retrieval_fn([query], file_names, topk)
    except Exception as e:
        logger.error(f"[patch-need-evidence] retrieval failed: {e}")
        return []

    # 估算同 slot 已用的 card_idx 数量（基于 _slot_abbrev 前缀）
    existing = set(existing_card_ids or [])
    slot_prefix = f"EC-{_slot_abbrev(slot)}-"
    same_slot_count = sum(1 for cid in existing if cid.startswith(slot_prefix))

    new_cards = evidence_collector(
        actor=actor,
        use_case_name=use_case_name,
        slot=slot,
        filled_slots=filled_slots,
        file_names=file_names,
        topk=topk,
        retrieval_fn=retrieval_fn,
        provider=provider,
        precomputed_results=search_results,
        inconsistency_hint=f"Need evidence for: {query}",
        card_idx_start=same_slot_count,
    )
    # 兜底：万一 card_idx_start 仍撞号（例如 existing 中有非连续 idx），追加 -rN 后缀
    for card in new_cards:
        if card.card_id not in existing:
            existing.add(card.card_id)
            continue
        suffix = 1
        while f"{card.card_id}-r{suffix}" in existing:
            suffix += 1
        new_id = f"{card.card_id}-r{suffix}"
        logger.info(f"[patch-need-evidence] card_id collision: {card.card_id} → {new_id}")
        card.card_id = new_id
        existing.add(new_id)
    return new_cards


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

        # Fix 4: slot_rewrite 全槽重写路径
        if patch.get("action") == "slot_rewrite":
            if root_slot_str in slots_already_rewritten:
                logger.warning(
                    f"[patcher] {root_slot_str} already rewritten this DRA pass; "
                    "rejecting second slot_rewrite to avoid thrash"
                )
                for it in group_issues:
                    uc.add_flag(QualityFlag(
                        flag_type="unresolved_inconsistency",
                        slot=root_slot_str,
                        detail=(
                            f"[{it['rule_violated']}] {it['root_cause']} "
                            f"(slot already rewritten once; further slot_rewrite suppressed)"
                        ),
                    ))
                continue
            success, reason = _apply_slot_rewrite(
                actor=actor,
                use_case_name=use_case_name,
                slot=slot_enum,
                slot_content=slot_content,
                patch=patch,
                all_evidence_cards=uc.all_evidence_cards,
                filled_slots=uc.slots,
                provider=provider,
            )
            if not success:
                logger.warning(f"[patcher] {root_slot_str} slot_rewrite rejected: {reason}")
                for it in group_issues:
                    uc.add_flag(QualityFlag(
                        flag_type="unresolved_inconsistency",
                        slot=root_slot_str,
                        detail=f"[{it['rule_violated']}] {it['root_cause']} (slot_rewrite failed: {reason})",
                    ))
                continue
            slots_already_rewritten.add(root_slot_str)
            # 收集重写过程中 answer_generator 抛出的 flag（如 evidence_invalid / goal_evidence_missing）
            for f in slot_content.quality_flags:
                uc.add_flag(f)
            slot_content.quality_flags = []
            logger.info(f"[patcher] {root_slot_str} slot_rewrite OK ({patch.get('rewrite_rationale','')!r})")
            if root_slot_str not in revised_slots:
                revised_slots.append(root_slot_str)
            for it in group_issues:
                inconsistency_hints.append(f"[slot_rewrite] {it['root_cause']}")
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
            # slot_rewrite 可能出现在 retry 后；递归处理一次
            if patch.get("action") == "slot_rewrite":
                if root_slot_str in slots_already_rewritten:
                    for it in group_issues:
                        uc.add_flag(QualityFlag(
                            flag_type="unresolved_inconsistency",
                            slot=root_slot_str,
                            detail=f"[{it['rule_violated']}] {it['root_cause']} (already rewritten)",
                        ))
                    continue
                success, reason = _apply_slot_rewrite(
                    actor=actor, use_case_name=use_case_name, slot=slot_enum,
                    slot_content=slot_content, patch=patch,
                    all_evidence_cards=uc.all_evidence_cards,
                    filled_slots=uc.slots, provider=provider,
                )
                if success:
                    slots_already_rewritten.add(root_slot_str)
                    for f in slot_content.quality_flags:
                        uc.add_flag(f)
                    slot_content.quality_flags = []
                    if root_slot_str not in revised_slots:
                        revised_slots.append(root_slot_str)
                    for it in group_issues:
                        inconsistency_hints.append(f"[slot_rewrite] {it['root_cause']}")
                else:
                    for it in group_issues:
                        uc.add_flag(QualityFlag(
                            flag_type="unresolved_inconsistency",
                            slot=root_slot_str,
                            detail=f"[{it['rule_violated']}] {it['root_cause']} (slot_rewrite failed: {reason})",
                        ))
                continue
            card_map = {c.card_id: c for c in uc.all_evidence_cards}

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

        logger.info(f"[patcher] {root_slot_str}#{target_idx} action={patch.get('action')} OK")
        if root_slot_str not in revised_slots:
            revised_slots.append(root_slot_str)
        for it in group_issues:
            inconsistency_hints.append(it["root_cause"])

    return revised_slots, inconsistency_hints


_EXCEPTION_BRANCH_CUES = re.compile(
    r"\b(error|warning|invalid|fail(?:ed|ure)?|reject(?:ed|ion)?|prevent(?:ed|s|ion)?|"
    r"unable|cannot|can't|not allowed|not eligible|no .* created|terminate(?:d|s)?|"
    r"retry|recover(?:y|s|ed)?)\b",
    re.IGNORECASE,
)


def _enforce_branch_slot_ownership(uc: UseCase) -> Tuple[List[str], List[str]]:
    """Remove exception duplicates from Alternative Flow without losing evidence.

    This is a deterministic ontology guard inside the DRA pass. It only removes an
    Alternative Flow claim when (a) the claim has explicit exception semantics and
    (b) a strongly overlapping Exception Flow claim already preserves the condition,
    response, exit path and provenance. It therefore does not trade recall for brevity.
    """
    alternative = uc.slots.get(CockburnSlot.ALTERNATIVE_FLOW.value)
    exception = uc.slots.get(CockburnSlot.EXCEPTION_FLOW.value)
    if not alternative or not exception or not alternative.claims or not exception.claims:
        return [], []

    kept: List[Claim] = []
    removed: List[Tuple[Claim, Claim, float]] = []
    for alt_claim in alternative.claims:
        if not _EXCEPTION_BRANCH_CUES.search(alt_claim.content or ""):
            kept.append(alt_claim)
            continue
        best_claim: Optional[Claim] = None
        best_score = 0.0
        for exc_claim in exception.claims:
            score = _term_jaccard(alt_claim.content, exc_claim.content)
            if score > best_score:
                best_score = score
                best_claim = exc_claim
        if best_claim is not None and best_score >= 0.45:
            removed.append((alt_claim, best_claim, best_score))
        else:
            kept.append(alt_claim)

    if not removed:
        return [], []

    alternative.claims = kept
    for index, claim in enumerate(alternative.claims, 1):
        claim.step_number = index
    alternative.evidence_card_ids = list(dict.fromkeys(
        ref for claim in alternative.claims for ref in (claim.evidence_refs or [])
    ))
    alternative.covered_aspect_ids = []
    alternative.missing_aspects = []
    alternative.aspect_status_known = False
    hints = [
        (
            "[branch_slot_ownership] Removed Alternative Flow exception duplicate "
            f"(lexical overlap={score:.2f}); the more complete Exception Flow claim "
            "and all source evidence were preserved."
        )
        for _alt, _exc, score in removed
    ]
    logger.info(
        "[consistency-ownership] removed %d exception duplicate(s) from alternative_flow",
        len(removed),
    )
    return [CockburnSlot.ALTERNATIVE_FLOW.value], hints


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
    all_revised_slots, all_inconsistency_hints = _enforce_branch_slot_ownership(uc)

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


def per_slot_dcrrm_loop(
    actor: str,
    use_case_name: str,
    slot: CockburnSlot,
    filled_slots: Dict[str, SlotContent],
    file_names: List[str],
    topk: int,
    retrieval_fn: Callable,
    provider: ModelProvider,
    prior_evidence_cards: Optional[List[EvidenceCard]] = None,
    max_rounds: int = _DEFAULT_MAX_SLOT_ROUNDS,
    max_pages_ratio: float = _DEFAULT_PAGE_COUNT_RATIO,
    total_doc_pages: int = 100,
    inconsistency_hint: str = "",
    overlap_rate_max: float = _DEFAULT_OVERLAP_RATE_MAX,
    query_retry_max: int = _DEFAULT_QUERY_RETRY_MAX,
    answer_convergence_repeat: int = _DEFAULT_ANSWER_CONVERGENCE_REPEAT,
    min_novel_pages_per_round: int = _DEFAULT_MIN_NOVEL_PAGES_PER_ROUND,
) -> Tuple[SlotContent, List[EvidenceCard]]:
    """
    单个 Cockburn slot 的完整 DCRRM 循环。

    终止条件:
    1. Query Designer 判定终止（无新查询 / 查询重叠 / 页面重叠）
    2. 连续 answer_convergence_repeat 轮答案无实质语义变化 (LLM 判断)
    3. 已读页面数超过文档总页数的 max_pages_ratio

    返回:
        (slot_content, all_evidence_cards)  — evidence cards 用于溯源
    """
    logger.info(f"[{slot.value}] === DCRRM Loop START ===")

    slot_content = SlotContent(slot=slot.value)
    history_queries: List[str] = []
    history_page_paths: set = set()
    # 当前 slot 收集的 cards + 前序 slot 复用的 cards
    own_cards: List[EvidenceCard] = []
    prior_cards = prior_evidence_cards or []
    repeat_count = 0
    max_pages = int(total_doc_pages * max_pages_ratio)
    pages_read_total: set = set()

    for _round in range(max_rounds):
        logger.info(f"[{slot.value}] Round {_round + 1}/{max_rounds}")

        # Step 1: Query Designer（内置终止判断 + 检索结果复用）
        plan = query_designer(
            actor=actor,
            use_case_name=use_case_name,
            slot=slot,
            filled_slots=filled_slots,
            current_slot_content=slot_content,
            history_queries=history_queries,
            history_page_paths=history_page_paths,
            inconsistency_hint=inconsistency_hint if _round == 0 else "",
            retrieval_fn=retrieval_fn,
            file_names=file_names,
            topk=topk,
            provider=provider,
            prior_evidence_cards=prior_cards,
            query_retry_max=query_retry_max,
            overlap_rate_max=overlap_rate_max,
            min_novel_pages_per_round=min_novel_pages_per_round,
        )
        if plan.should_stop:
            logger.info(
                f"[{slot.value}] Query Designer signaled stop: {plan.reason or 'coverage complete'}"
            )
            # Reuse-first path: a downstream slot may be answerable entirely from cards
            # already cited by upstream slots, so stopping retrieval must not leave it empty.
            if slot_content.is_empty() and plan.reuse_card_ids:
                requested = set(plan.reuse_card_ids)
                reuse_cards = [card for card in prior_cards if card.card_id in requested]
                if reuse_cards:
                    slot_content = answer_generator(
                        actor=actor,
                        use_case_name=use_case_name,
                        slot=slot,
                        evidence_cards=reuse_cards,
                        prior_evidence_cards=[],
                        filled_slots=filled_slots,
                        current_slot_content=slot_content,
                        provider=provider,
                    )
            break
        history_queries.extend(plan.queries)

        # Step 2: Evidence Collector（复用 query_designer 的检索结果）
        # 累计 card_idx_start 防止跨轮重号 EC-{slot}-000
        new_cards = evidence_collector(
            actor=actor,
            use_case_name=use_case_name,
            slot=slot,
            filled_slots=filled_slots,
            file_names=file_names,
            topk=topk,
            retrieval_fn=retrieval_fn,
            provider=provider,
            precomputed_results=plan.search_results,
            inconsistency_hint=inconsistency_hint if _round == 0 else "",
            card_idx_start=len(own_cards),
        )
        own_cards.extend(new_cards)
        for card in new_cards:
            # Fix ζ: 用 (source_doc, page) 跟踪页面预算，跨 PDF 不会撞号
            for p in card.source_pages:
                pages_read_total.add((card.source_doc, p))
        # 记录历史页面路径
        for result_set in plan.search_results:
            history_page_paths.update(result_set)

        # Step 3: Answer Generator（用本 slot 的 cards + 前序 slot 的 cards）
        new_content = answer_generator(
            actor=actor,
            use_case_name=use_case_name,
            slot=slot,
            evidence_cards=own_cards,
            prior_evidence_cards=prior_cards,
            filled_slots=filled_slots,
            current_slot_content=slot_content,
            provider=provider,
        )

        # Step 4: Convergence check（语义变化判断 + 页面耗尽）
        if not slot_content.is_empty():
            if _slot_fingerprint(slot_content) == _slot_fingerprint(new_content):
                changed = False
                logger.info(f"[{slot.value}] Exact normalized fingerprint unchanged; skipping convergence LLM.")
            else:
                changed = check_convergence(
                    slot_content, new_content, slot, provider, filled_slots, use_case_name
                )
            if not changed:
                repeat_count += 1
                if repeat_count >= answer_convergence_repeat:
                    slot_content = new_content
                    logger.info(
                        f"[{slot.value}] No substantial change for {answer_convergence_repeat} rounds, converging."
                    )
                    break
            else:
                repeat_count = 0

        slot_content = new_content

        # Gap-driven stop: a structured author response explicitly confirms that all
        # essential slot aspects are covered. Optional details do not justify a new round.
        if (
            slot_content.aspect_status_known
            and not slot_content.is_empty()
            and not slot_content.missing_aspects
        ):
            logger.info(f"[{slot.value}] Essential aspects covered; gap-driven convergence.")
            break

        # 页面耗尽
        if len(pages_read_total) >= max_pages:
            logger.info(f"[{slot.value}] Page budget exhausted ({len(pages_read_total)}/{max_pages}), converging.")
            break

    # 不在此处填占位字符串——保持 slot_content.is_empty() 真实，让 orchestrator 据此打 no_evidence flag
    logger.info(f"[{slot.value}] === DCRRM Loop DONE: {len(own_cards)} cards, "
                f"{len(pages_read_total)} pages read ===")
    return slot_content, own_cards


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
