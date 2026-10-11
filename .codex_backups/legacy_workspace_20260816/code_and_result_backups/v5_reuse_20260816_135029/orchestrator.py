"""
CREST 编排器 — AUCMR 的 elaboration 模块入口。

Phase 1-N: Per-Slot DCRRM Loop (Cockburn dependency order)
  → Final Phase: Cross-Slot Consistency
"""

import json
import logging
import re
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
        self._goal_cohesion_cache: Dict[tuple, bool] = {}
        self._goal_scope_cache: Dict[tuple, tuple[bool, Dict[str, object]]] = {}
        self._goal_validation_diagnostic: Dict[str, object] = {}
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
        self._goal_validation_diagnostic = {}

        uc = UseCase(use_case_name=use_case_name, actor=actor)

        # ============================================================
        # Phase 1-N: Per-Slot DCRRM Loop (Cockburn dependency order)
        # ============================================================
        for slot in COCKBURN_DEPENDENCY_ORDER:
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
                prior_evidence_cards=uc.all_evidence_cards,  # P1: 跨 slot 复用
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
            )

            # Fix 6 (removed): Trigger 槽位的 Goal-alignment 软检查与重跑逻辑已删除。
            # 论文表 1（CREST 质量标志分类）只列 6 种质量标志：
            #   unresolvable_use_case / no_evidence / goal_evidence_missing /
            #   evidence_invalid / unresolved_inconsistency / consistency_check_failed
            # ——没有 trigger_goal_misalignment。若仍想做 Trigger/Goal 一致性防护，
            # 可由 DRA Pass 的 R2 规则（mainflow_trigger_continuity）统一处理。

            # The experiment inputs are user-specified analysis units.  Goal
            # validation may reject an unsupported/uncited Goal, but it must not
            # silently redefine those units by deciding that a compound title
            # should have been split into several use cases.  Semantic grounding
            # of the Goal claim itself is already enforced inside DCRRM.

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
                diagnostic = self._goal_validation_diagnostic
                diagnostic_detail = ""
                if diagnostic:
                    diagnostic_detail = (
                        f" Strict goal review category={diagnostic.get('category', 'unknown')}; "
                        f"reason={diagnostic.get('reason', 'not provided')}."
                    )
                uc.add_flag(QualityFlag(
                    flag_type="unresolvable_use_case",
                    slot=slot.value,
                    detail=(
                        f"Use case '{use_case_name}' could not be resolved against the document. "
                        f"Goal slot evidence_cards={len(goal_validation_cards)} (threshold={self.goal_min_evidence_cards}), "
                        f"median_confidence={_median_confidence(goal_validation_cards):.2f} "
                        f"(threshold={self.goal_min_confidence}). "
                        f"Aborting elaboration before subsequent slots."
                        f"{diagnostic_detail}"
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
        """Fail fast only when the generated Goal lacks usable document support.

        The configured use-case name and granularity are experimental inputs, not
        hypotheses for this gate to accept or reject.  Cohesion/atomicity review
        previously rejected legitimate compound analysis units and produced empty
        Detail-UCs, so it is intentionally not a fail-fast condition here.
        """
        self._goal_validation_diagnostic = {}
        # Tests and ablations may explicitly set both thresholds to zero to disable
        # the fail-fast gate.  In that mode an empty Goal is allowed downstream.
        if self.goal_min_evidence_cards <= 0 and self.goal_min_confidence <= 0:
            return True
        if slot_content.is_empty():
            self._goal_validation_diagnostic = {
                "category": "empty_goal",
                "reason": "The Goal slot is empty.",
            }
            return False
        if len(cards) < self.goal_min_evidence_cards:
            self._goal_validation_diagnostic = {
                "category": "insufficient_cards",
                "reason": "Too few Goal evidence cards were collected.",
            }
            return False
        if _median_confidence(cards) < self.goal_min_confidence:
            self._goal_validation_diagnostic = {
                "category": "low_confidence",
                "reason": "The median Goal evidence confidence is below the threshold.",
            }
            return False

        referenced_ids = set(slot_content.evidence_card_ids or [])
        for claim in slot_content.claims:
            referenced_ids.update(claim.evidence_refs or [])
        available_ids = {card.card_id for card in cards}
        if not (referenced_ids & available_ids):
            self._goal_validation_diagnostic = {
                "category": "uncited_goal",
                "reason": "The generated Goal does not cite a collected evidence card.",
            }
            return False
        return True

    def _goal_evidence_has_sufficient_scope(
        self, slot_content: SlotContent, cards: List["EvidenceCard"],
    ) -> bool:
        """Strict Goal gate for definition-only evidence and fragmented broad scopes."""
        referenced_ids = set(slot_content.evidence_card_ids or [])
        for claim in slot_content.claims:
            referenced_ids.update(claim.evidence_refs or [])
        referenced_cards = [card for card in cards if card.card_id in referenced_ids]

        if not referenced_cards:
            self._goal_validation_diagnostic = {
                "category": "uncited_goal",
                "reason": "The generated Goal does not cite a collected evidence card.",
            }
            return False

        # A term definition establishes vocabulary, not the entry/action/response/outcome
        # needed to justify elaborating a fully-dressed use case.
        definition_markers = (
            "glossary", "defined as", "definition of", "term definition",
            "means the", "is the term for",
        )
        definition_only = True
        for card in referenced_cards:
            location_text = " ".join(
                str(location.get("location", ""))
                for location in (card.source_locations or [])
                if isinstance(location, dict)
            )
            card_text = f"{card.content} {location_text}".lower()
            if not any(marker in card_text for marker in definition_markers):
                definition_only = False
                break
        if definition_only:
            self._goal_validation_diagnostic = {
                "category": "definition_only",
                "reason": (
                    "The cited evidence only defines the requested term; it does not "
                    "establish an executable actor-system process or success outcome."
                ),
            }
            logger.warning("[goal] Strict scope review rejected definition-only evidence.")
            return False

        # Retrieval candidates with the Goal prefix expose alternate interpretations of
        # the requested name.  A large span is only a trigger for semantic review, never
        # a rejection on its own.
        candidate_cards = [card for card in cards if card.card_id.startswith("EC-G-")]
        candidate_pages = [
            page for card in candidate_cards for page in (card.source_pages or [])
        ]
        page_span = (
            max(candidate_pages) - min(candidate_pages)
            if len(candidate_pages) >= 2 else 0
        )
        scope_threshold = max(
            int(self.total_doc_pages * self.goal_cohesion_max_span_ratio), 10,
        )
        # A broad compound/umbrella name needs atomicity review even when retrieval
        # returns only one overview card. Otherwise a heading that merely restates the
        # candidate name bypasses the very granularity gate intended to detect that it
        # combines independently triggered subgoals. The judge can still accept genuine
        # single-chain compounds such as "Checkout and Payment".
        compound_name = bool(re.search(
            r"\b(and|or)\b|[/;,]", self._current_use_case_name, flags=re.IGNORECASE,
        ))
        if not compound_name and page_span <= scope_threshold:
            return True

        signature = (
            self._current_use_case_name,
            slot_content.raw_text,
            tuple(sorted(referenced_ids)),
            tuple((card.card_id, card.content) for card in candidate_cards),
        )
        cached = self._goal_scope_cache.get(signature)
        if cached is not None:
            supported, diagnostic = cached
            self._goal_validation_diagnostic = dict(diagnostic)
            return supported

        scope_contract = slot_content.scope_contract or {}
        payload = [
            {
                "card_id": card.card_id,
                "selected_by_goal": card.card_id in referenced_ids,
                "content": card.content,
                "source_pages": card.source_pages,
                "source_locations": card.source_locations,
            }
            for card in candidate_cards
        ]
        prompt = (
            "You are a strict input-feasibility and GRANULARITY reviewer for fully-dressed "
            "use-case elaboration. Decide whether the FULL candidate name denotes ONE atomic "
            "use-case instance supported by the selected Goal evidence. Atomic means that the "
            "scope has (1) one primary initiation/trigger, (2) one cohesive actor-system "
            "interaction chain, and (3) one observable completion state. Object variants or "
            "encoding alternatives may remain together only when they are subordinate choices "
            "inside that same invocation. If components can be initiated, executed, or "
            "completed independently, they are separate use cases and the umbrella candidate "
            "must be rejected as scope_fragmented.\n"
            "A glossary definition is insufficient. A conjunction is not automatically a "
            "failure. Page distance alone is only a review trigger. Conversely, an overview "
            "or objective sentence that repeats an umbrella heading is NOT proof of atomicity. "
            "Inspect the detailed candidate cards and scope contract: reject when the evidence "
            "separately describes creation, reporting/encoding, subordinate geometry, filtering, "
            "administration, or other independently usable operations without a shared trigger "
            "and completion boundary. Also reject when the generated Goal becomes coherent only "
            "by silently narrowing the candidate name to one subtype or excluding a material "
            "title component. Unselected candidates can be noise, so identify concrete independent "
            "subgoals in the response. Return JSON only with: supported (boolean), category (one "
            "of supported, definition_only, scope_fragmented, scope_narrowing), reason (string), "
            "conflicting_actions (array of strings), shared_trigger (string or null), and "
            "shared_completion_state (string or null).\n"
            f"Use case name: {self._current_use_case_name}\n"
            f"Generated Goal: {slot_content.raw_text}\n"
            f"Scope contract: {json.dumps(scope_contract, ensure_ascii=False)}\n"
            f"Review trigger: compound_name={compound_name}, evidence_span={page_span} pages.\n"
            f"Goal candidate evidence: "
            f"{json.dumps(payload, ensure_ascii=False)}"
        )
        try:
            review = getattr(self.provider, "judge", None)
            raw = review(prompt) if callable(review) else self.provider.llm(prompt)
            result = json.loads(repair_json(raw))
            if not isinstance(result, dict) or "supported" not in result:
                raise ValueError("strict scope response missing supported")
            value = result.get("supported")
            supported = (
                value.strip().lower() == "true" if isinstance(value, str) else bool(value)
            )
            category = str(result.get("category") or (
                "supported" if supported else "scope_fragmented"
            ))
            diagnostic = {
                "category": category,
                "reason": str(result.get("reason") or "Strict Goal scope review failed."),
                "candidate_page_span": page_span,
                "conflicting_actions": result.get("conflicting_actions") or [],
            }
            logger.info(
                f"[goal] Strict scope supported={supported}, category={category}: "
                f"{diagnostic['reason']}"
            )
        except Exception as exc:
            # A service/format failure is not evidence that the use case is invalid.
            supported = True
            diagnostic = {
                "category": "review_unavailable",
                "reason": f"Strict semantic scope review was unavailable: {exc}",
                "candidate_page_span": page_span,
            }
            logger.warning(
                f"[goal] Strict semantic scope review failed ({exc}); allowing Goal."
            )

        self._goal_scope_cache[signature] = (supported, diagnostic)
        self._goal_validation_diagnostic = dict(diagnostic)
        return supported

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
            review = getattr(self.provider, "judge", None)
            raw = review(prompt) if callable(review) else self.provider.llm(prompt)
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
