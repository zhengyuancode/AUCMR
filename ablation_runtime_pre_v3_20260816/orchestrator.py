"""
CREST 编排器 — AUCMR 的 elaboration 模块入口。

Phase 1-N: Per-Slot DCRRM Loop (Cockburn dependency order)
  → Final Phase: Cross-Slot Consistency
"""

import json
import logging
import time
from typing import List, Dict, Callable, Optional

from json_repair import repair_json

from config.settings import (
    MAX_SLOT_ROUNDS as _DEFAULT_MAX_SLOT_ROUNDS,
    PAGE_COUNT_RATIO as _DEFAULT_MAX_PAGES_RATIO,
    ANSWER_CONVERGENCE_REPEAT as _DEFAULT_ANSWER_CONVERGENCE_REPEAT,
    OVERLAP_RATE_MAX as _DEFAULT_OVERLAP_RATE_MAX,
    QUERY_RETRY_MAX as _DEFAULT_QUERY_RETRY_MAX,
    GOAL_MIN_EVIDENCE_CARDS as _DEFAULT_GOAL_MIN_EVIDENCE_CARDS,
    GOAL_MIN_CONFIDENCE as _DEFAULT_GOAL_MIN_CONFIDENCE,
    MIN_NOVEL_PAGES_PER_ROUND as _DEFAULT_MIN_NOVEL_PAGES_PER_ROUND,
    DRA_MAX_ROUNDS as _DEFAULT_DRA_MAX_ROUNDS,
    GOAL_COHESION_MAX_SPAN_RATIO as _DEFAULT_GOAL_COHESION_MAX_SPAN_RATIO,
)
from .schemas import (
    CockburnSlot,
    Claim,
    EvidenceCard,
    SlotContent,
    QualityFlag,
    RevisionRecord,
    UseCase,
    COCKBURN_DEPENDENCY_ORDER,
)
from .agents import (
    ModelProvider,
    per_slot_dcrrm_loop,
    cross_slot_revision,
)

logger = logging.getLogger(__name__)


class CRESTOrchestrator:
    """
    CREST 编排器。

    用法:
        orchestrator = CRESTOrchestrator(
            retrieval_fn=my_retrieval_service,
            file_names=["my_doc"],
            topk=5,
            provider=my_model_provider,
        )
        uc = orchestrator.elaborate(actor="Pilot", use_case_name="Authenticate via Link16 Terminal")
        print(uc.to_json())
    """

    def __init__(
        self,
        retrieval_fn: Callable,
        file_names: List[str],
        topk: int,
        provider: ModelProvider,
        total_doc_pages: int = 100,
        max_slot_rounds: int = _DEFAULT_MAX_SLOT_ROUNDS,
        goal_min_evidence_cards: int = _DEFAULT_GOAL_MIN_EVIDENCE_CARDS,
        goal_min_confidence: float = _DEFAULT_GOAL_MIN_CONFIDENCE,
        overlap_rate_max: float = _DEFAULT_OVERLAP_RATE_MAX,
        query_retry_max: int = _DEFAULT_QUERY_RETRY_MAX,
        answer_convergence_repeat: int = _DEFAULT_ANSWER_CONVERGENCE_REPEAT,
        max_pages_ratio: float = _DEFAULT_MAX_PAGES_RATIO,
        min_novel_pages_per_round: int = _DEFAULT_MIN_NOVEL_PAGES_PER_ROUND,
        dra_max_rounds: int = _DEFAULT_DRA_MAX_ROUNDS,
        goal_cohesion_max_span_ratio: float = _DEFAULT_GOAL_COHESION_MAX_SPAN_RATIO,
        slot_order: Optional[List] = None,
        reuse_prior_evidence: bool = True,
        single_page_only: bool = False,
        enforce_evidence_refs: bool = True,
    ):
        self.retrieval_fn = retrieval_fn
        self.file_names = file_names
        self.topk = topk
        if provider is None:
            raise ValueError("ModelProvider is required — use registry.get_model_provider()")
        self.provider = provider
        self.total_doc_pages = total_doc_pages
        self.max_slot_rounds = max_slot_rounds
        # P0.4: Goal slot 合法性 fail-fast 阈值
        # 如果 GOAL slot 跑完后 evidence_cards 数量 < goal_min_evidence_cards 或
        # 中位 confidence < goal_min_confidence，则判定 use_case 不在文档里，立即返回。
        self.goal_min_evidence_cards = goal_min_evidence_cards
        self.goal_min_confidence = goal_min_confidence
        # P1.3: query/page overlap 阈值（启发式，避免 query designer 在同一池子里反复挑）
        self.overlap_rate_max = overlap_rate_max
        # query_designer 内部覆盖率/格式问题可重试次数；超出强行返回 [] 终止
        self.query_retry_max = query_retry_max
        # 多轮 answer 语义无变化次数达此上限即收敛终止 DCRRM
        self.answer_convergence_repeat = answer_convergence_repeat
        # per-slot 页面预算上限 = total_doc_pages * max_pages_ratio
        self.max_pages_ratio = max_pages_ratio
        # Fix 3: 每轮检索必须带回的最小新页面数（与 overlap_rate_max 联合判定终止）
        self.min_novel_pages_per_round = min_novel_pages_per_round
        # Fix 5: DRA Pass 最大 Detect→Repair 轮数
        self.dra_max_rounds = dra_max_rounds
        # Fix 7.2: Goal cohesion 检查阈值（跨页 span 占文档总页比例上限）
        self.goal_cohesion_max_span_ratio = goal_cohesion_max_span_ratio
        normalized_order = [
            CockburnSlot(value) if isinstance(value, str) else value
            for value in (slot_order or COCKBURN_DEPENDENCY_ORDER)
        ]
        if len(normalized_order) != len(COCKBURN_DEPENDENCY_ORDER) or set(normalized_order) != set(COCKBURN_DEPENDENCY_ORDER):
            raise ValueError("slot_order must be a permutation of all Cockburn slots")
        self.slot_order = normalized_order
        self.reuse_prior_evidence = bool(reuse_prior_evidence)
        self.single_page_only = bool(single_page_only)
        self.enforce_evidence_refs = bool(enforce_evidence_refs)
        self._goal_cohesion_cache: Dict[tuple, bool] = {}
        self._current_use_case_name = ""

    def elaborate(self, actor: str, use_case_name: str) -> UseCase:
        """
        完整 elaboration pipeline。

        参数:
            actor:         Primary Actor 名称
            use_case_name: 用例名称（仅名称）

        返回:
            UseCase: 包含完整 Cockburn 8-slot 用例 + 证据溯源 + 质量标记
        """
        logger.info(f"=== CREST Elaboration: '{actor}' — '{use_case_name}' ===")
        started = time.perf_counter()
        model_stats_before = self.provider.stats_snapshot()
        self._current_use_case_name = use_case_name

        uc = UseCase(use_case_name=use_case_name, actor=actor)

        # ============================================================
        # Phase 1-N: Per-Slot DCRRM Loop (Cockburn dependency order)
        # ============================================================
        for slot in self.slot_order:
            logger.info(f"Phase: Filling slot '{slot.value}'...")

            # P2: primary_actor 直接用用户输入，跳过 DCRRM 循环
            if slot == CockburnSlot.PRIMARY_ACTOR:
                slot_content = SlotContent(
                    slot=slot.value,
                    raw_text=actor,
                    claims=[Claim(content=actor, evidence_refs=[])],
                )
                uc.set_slot(slot, slot_content)
                logger.info(f"[primary_actor] Set from user input: {actor}")
                uc.add_revision(RevisionRecord(
                    round_num=0,
                    action="user_input",
                    revised_slots=[slot.value],
                ))
                continue

            slot_content, cards = per_slot_dcrrm_loop(
                actor=actor,
                use_case_name=use_case_name,
                slot=slot,
                filled_slots=uc.slots,
                prior_evidence_cards=(
                    uc.all_evidence_cards if self.reuse_prior_evidence else []
                ),
                file_names=self.file_names,
                topk=self.topk,
                retrieval_fn=self.retrieval_fn,
                provider=self.provider,
                max_rounds=self.max_slot_rounds,
                total_doc_pages=self.total_doc_pages,
                overlap_rate_max=self.overlap_rate_max,
                query_retry_max=self.query_retry_max,
                answer_convergence_repeat=self.answer_convergence_repeat,
                max_pages_ratio=self.max_pages_ratio,
                min_novel_pages_per_round=self.min_novel_pages_per_round,
                single_page_only=self.single_page_only,
                enforce_evidence_refs=self.enforce_evidence_refs,
            )

            # Fix 6 (removed): Trigger 槽位的 Goal-alignment 软检查与重跑逻辑已删除。
            # 论文表 1（CREST 质量标志分类）只列 6 种质量标志：
            #   unresolvable_use_case / no_evidence / goal_evidence_missing /
            #   evidence_invalid / unresolved_inconsistency / consistency_check_failed
            # ——没有 trigger_goal_misalignment。若仍想做 Trigger/Goal 一致性防护，
            # 可由 DRA Pass 的 R2 规则（mainflow_trigger_continuity）统一处理。

            # Fix 7.2: Goal slot 填完后做 evidence cohesion 检查；
            # 卡片跨页 span 超阈值 → 判定 Goal 混杂多 use case，重跑一次带 cohesion hint。
            # 只重跑一次防 LLM 抖动；二次仍不 cohesive 直接走 _goal_is_resolvable 的 abort 路径。
            if slot == CockburnSlot.GOAL and not slot_content.is_empty():
                if not self._goal_evidence_is_cohesive(slot_content, cards):
                    logger.warning(
                        f"[goal] Cohesion violation detected; re-running DCRRM once "
                        f"with focus hint."
                    )
                    # 先把第一次的 cards 加进 uc（重跑时作为 prior，可能仍有用）
                    uc.add_evidence_cards(cards)
                    cohesion_hint = (
                        f"The previous Goal evidence cards spanned multiple unrelated "
                        f"document sections, indicating the Goal conflated multiple use cases. "
                        f'The use case name is "{use_case_name}" — interpret this as a SINGLE '
                        f"user-facing action. Pick evidence ONLY from cards describing this "
                        f"single action; reject cards describing downstream consequences, "
                        f"adjacent administrative processes, or different use cases."
                    )
                    slot_content, cards = per_slot_dcrrm_loop(
                        actor=actor,
                        use_case_name=use_case_name,
                        slot=slot,
                        filled_slots=uc.slots,
                        prior_evidence_cards=(
                            uc.all_evidence_cards if self.reuse_prior_evidence else []
                        ),
                        file_names=self.file_names,
                        topk=self.topk,
                        retrieval_fn=self.retrieval_fn,
                        provider=self.provider,
                        max_rounds=self.max_slot_rounds,
                        total_doc_pages=self.total_doc_pages,
                        overlap_rate_max=self.overlap_rate_max,
                        query_retry_max=self.query_retry_max,
                        answer_convergence_repeat=self.answer_convergence_repeat,
                        max_pages_ratio=self.max_pages_ratio,
                        min_novel_pages_per_round=self.min_novel_pages_per_round,
                        inconsistency_hint=cohesion_hint,
                        single_page_only=self.single_page_only,
                        enforce_evidence_refs=self.enforce_evidence_refs,
                    )

            uc.set_slot(slot, slot_content)
            uc.add_evidence_cards(cards)

            # P0.1: 收集 answer_generator 内部产生的 evidence_invalid flag
            for f in slot_content.quality_flags:
                uc.add_flag(f)
            slot_content.quality_flags = []

            # P0.4: GOAL slot 跑完后检查输入合法性
            goal_validation_cards = uc.all_evidence_cards if slot == CockburnSlot.GOAL else cards
            if slot == CockburnSlot.GOAL and not self._goal_is_resolvable(
                slot_content, goal_validation_cards
            ):
                uc.add_flag(QualityFlag(
                    flag_type="unresolvable_use_case",
                    slot=slot.value,
                    detail=(
                        f"Use case '{use_case_name}' could not be resolved against the document. "
                        f"Goal slot evidence_cards={len(goal_validation_cards)} (threshold={self.goal_min_evidence_cards}), "
                        f"median_confidence={_median_confidence(goal_validation_cards):.2f} "
                        f"(threshold={self.goal_min_confidence}). "
                        f"Aborting elaboration before subsequent slots."
                    ),
                ))
                logger.warning(
                    f"[goal] Unresolvable use case — aborting before downstream slots."
                )
                self._build_evidence_summary(uc)
                self._finalize_runtime_stats(uc, started, model_stats_before)
                return uc

            if slot_content.is_empty():
                uc.add_flag(QualityFlag(
                    flag_type="no_evidence",
                    slot=slot.value,
                    detail=f"No evidence found for slot '{slot.value}'",
                ))
                logger.warning(f"Slot '{slot.value}' has no evidence.")

            uc.add_revision(RevisionRecord(
                round_num=0,
                action="initial_fill",
                revised_slots=[slot.value],
            ))

        # ============================================================
        # Final Phase: Cross-Slot Consistency
        # ============================================================
        if self.dra_max_rounds > 0:
            logger.info("Final Phase: Cross-Slot Consistency...")
            uc = cross_slot_revision(
                actor=actor,
                use_case_name=use_case_name,
                uc=uc,
                file_names=self.file_names,
                topk=self.topk,
                retrieval_fn=self.retrieval_fn,
                provider=self.provider,
                max_rounds=self.dra_max_rounds,
            )
        else:
            logger.info("Final Phase disabled (dra_max_rounds=0).")

        # 汇总 evidence
        self._build_evidence_summary(uc)
        self._finalize_runtime_stats(uc, started, model_stats_before)

        logger.info(f"Elaboration complete. Flags: {len(uc.quality_flags)}, "
                     f"Revisions: {len(uc.revision_history)}")
        return uc

    def _finalize_runtime_stats(
        self, uc: UseCase, started: float, model_stats_before: Dict[str, object],
    ) -> None:
        model_delta = self.provider.stats_delta(
            model_stats_before, self.provider.stats_snapshot()
        )
        referenced_ids = {
            ref
            for content in uc.slots.values()
            for claim in content.claims
            for ref in claim.evidence_refs
        }
        uc.runtime_stats = {
            "elapsed_seconds": round(time.perf_counter() - started, 3),
            **model_delta,
            "cards_collected": len(uc.all_evidence_cards),
            "cards_referenced": len(referenced_ids),
        }

    def _build_evidence_summary(self, uc: UseCase):
        """构建证据摘要。"""
        cross_page = sum(1 for c in uc.all_evidence_cards if c.is_cross_page)
        region_types: Dict[str, int] = {}
        for c in uc.all_evidence_cards:
            rt = c.region_type or "text"
            region_types[rt] = region_types.get(rt, 0) + 1

        uc.evidence_summary = {
            "total_cards": len(uc.all_evidence_cards),
            "cross_page_cards": cross_page,
            "modalities": region_types,
        }

    def _goal_is_resolvable(self, slot_content: SlotContent, cards: List["EvidenceCard"]) -> bool:
        """P0.4 + Fix 7.2: 判断 GOAL slot 的产出是否说明 use case 在文档里能找到。

        除原有的"卡数/中位 confidence"检查外，加入证据 cohesion 检查：
        如果 GOAL slot 引用的证据卡的源页号跨度（最大页 - 最小页）超过文档总页数的
        cohesion_ratio，判定为"goal 卡片散落在不相关章节"，认为是多 use case 污染，
        不可解析。这是为了拦截"page 13 (bid) + page 130 (checkout) + page 133 (checkout)"
        这种把多个 use case 混进 Goal 的情况。
        """
        # Tests and ablations may explicitly set both thresholds to zero to disable
        # the fail-fast gate.  In that mode an empty Goal is allowed downstream.
        if self.goal_min_evidence_cards <= 0 and self.goal_min_confidence <= 0:
            return True
        if slot_content.is_empty():
            return False
        if len(cards) < self.goal_min_evidence_cards:
            return False
        if _median_confidence(cards) < self.goal_min_confidence:
            return False

        # Fix 7.2: cohesion 检查 — 只看 slot 实际引用的卡，不看整 cards 池
        if not self._goal_evidence_is_cohesive(slot_content, cards):
            return False
        return True

    def _goal_evidence_is_cohesive(
        self, slot_content: SlotContent, cards: List["EvidenceCard"],
    ) -> bool:
        """Spatial distance triggers semantic review; it is not a hard rejection rule."""
        referenced_ids = set(slot_content.evidence_card_ids or [])
        if slot_content.claims:
            for c in slot_content.claims:
                referenced_ids.update(c.evidence_refs or [])
        if len(referenced_ids) < 2:
            return True  # 单卡或零卡无法判定 cohesion，认为通过
        referenced_cards = [c for c in cards if c.card_id in referenced_ids]
        if len(referenced_cards) < 2:
            return True
        all_pages: List[int] = []
        for c in referenced_cards:
            if c.source_pages:
                all_pages.extend(c.source_pages)
        if len(all_pages) < 2:
            return True
        page_span = max(all_pages) - min(all_pages)
        cohesion_threshold = max(int(self.total_doc_pages * self.goal_cohesion_max_span_ratio), 10)
        if page_span <= cohesion_threshold:
            return True

        signature = (
            slot_content.raw_text,
            tuple(claim.content for claim in slot_content.claims),
            tuple(sorted(referenced_ids)),
        )
        if signature in self._goal_cohesion_cache:
            return self._goal_cohesion_cache[signature]

        logger.warning(
            f"[goal] Spatial dispersion: referenced cards span pages "
            f"{min(all_pages)}–{max(all_pages)} (span={page_span}) > "
            f"threshold {cohesion_threshold}; running semantic cohesion review."
        )
        goal_text = slot_content.raw_text or " | ".join(
            claim.content for claim in slot_content.claims if claim.content
        )
        evidence = [
            {
                "card_id": card.card_id,
                "content": card.content,
                "source_pages": card.source_pages,
            }
            for card in referenced_cards
        ]
        prompt = (
            "You are validating whether spatially dispersed evidence supports one literal "
            "user goal or conflates different use cases. Page distance alone is not a "
            "failure: navigation diagrams, detailed flows, and appendices may repeat the "
            "same operation. Return JSON only with keys same_user_goal (boolean), "
            "conflicting_actions (array), and reason (string). Set same_user_goal=false "
            "only when the cards require mutually different actor objectives or adjacent "
            "processes to form the Goal.\n"
            f"Use case name: {self._current_use_case_name}\n"
            f"Generated Goal: {goal_text}\n"
            f"Referenced evidence: {json.dumps(evidence, ensure_ascii=False)}"
        )
        try:
            raw = self.provider.llm(prompt)
            result = json.loads(repair_json(raw))
            if not isinstance(result, dict) or "same_user_goal" not in result:
                raise ValueError("semantic cohesion response missing same_user_goal")
            value = result.get("same_user_goal")
            if isinstance(value, str):
                cohesive = value.strip().lower() == "true"
            else:
                cohesive = bool(value)
            logger.info(
                f"[goal] Semantic cohesion={cohesive}: {result.get('reason', '')}"
            )
        except Exception as exc:
            # A detector-format failure must not silently turn a well-supported valid UC
            # into a hard abort. Quantity/confidence checks remain active.
            logger.warning(
                f"[goal] Semantic cohesion review failed ({exc}); allowing Goal and "
                "recording the diagnostic in logs."
            )
            cohesive = True

        self._goal_cohesion_cache[signature] = cohesive
        return cohesive


def _median_confidence(cards: List["EvidenceCard"]) -> float:
    if not cards:
        return 0.0
    vals = sorted(c.confidence for c in cards)
    n = len(vals)
    if n % 2:
        return vals[n // 2]
    return (vals[n // 2 - 1] + vals[n // 2]) / 2
