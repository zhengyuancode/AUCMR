"""
L1–L3 自动指标计算模块。

L1 — Evidence Grounding:
  - SCG (Self-Cited Grounding Rate): % of claims with non-empty evidence_refs
  - NLI-G (NLI-Verified Grounding Rate): % of claims where evidence entails the claim
  - Abstention F1: F1 of abstain-or-fill decision
  - Cross-Page Cards %: % of evidence cards spanning multiple pages
  - Avg Cards / UC: average number of evidence cards per use case

L2 — Structural Completeness:
  - Per-slot fill rate: % of UCs where a slot has ≥1 substantive claim
  - All-8 rate: % of UCs with all 8 slots filled

L3 — Internal Consistency:
  - 7-rule violation counts per UC (pre-repair and post-repair)
  - DRA repair rate

Usage:
  from eval.compute_metrics import MetricsComputer
  mc = MetricsComputer(nli_judge=None)  # uses Qwen for NLI
  results = mc.compute_all(use_case_json_path, evidence_json_path)
"""

import json
import logging
import os
import re
import sys
import threading
from collections import defaultdict
from concurrent.futures import Future
from dataclasses import dataclass, field
from typing import Any, Optional

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("eval")

# Cockburn slots in dependency order. ``primary_actor`` is an external input,
# not a generated assertion, so paper L1/ESC evaluates the remaining 7 slots.
COCKBURN_SLOTS = [
    "goal", "primary_actor", "preconditions", "trigger",
    "main_flow", "alternative_flow", "exception_flow", "postconditions"
]
EVALUATED_SLOTS = [slot for slot in COCKBURN_SLOTS if slot != "primary_actor"]

# 7 cross-slot consistency rules
CONSISTENCY_RULES = {
    "R1": "trigger_preconditions_coverage",
    "R2": "mainflow_trigger_continuity",
    "R3": "mainflow_postconditions_reachability",
    "R4": "actor_mainflow_presence",
    "R5": "goal_mainflow_alignment",
    "R6": "altflow_diverge_points",
    "R7": "exceptionflow_exit_coverage",
}


@dataclass
class SlotMetrics:
    slot_name: str
    claim_count: int = 0
    cited_claim_count: int = 0
    evidence_ref_count: int = 0
    is_filled: bool = False
    quality_flags: list = field(default_factory=list)


@dataclass
class UCMetrics:
    use_case_name: str
    actor: str
    scg: float = 0.0  # Self-Cited Grounding Rate
    nli_g: float = 0.0  # NLI-Verified Grounding Rate
    abstention_f1: float = 0.0
    cross_page_card_pct: float = 0.0
    avg_cards: int = 0
    total_claims: int = 0
    total_cited_claims: int = 0
    total_entailed_claims: int = 0
    # Backward-compatible aliases for the single L1 claim population captured
    # from the model-authored structured output.
    pre_constraint_claim_count: int = 0
    pre_constraint_cited_claim_count: int = 0
    empty_slot_count: int = 0
    total_evidence_cards: int = 0
    cross_page_cards: int = 0
    referenced_cards: int = 0
    all_8_filled: bool = False
    slot_metrics: dict[str, SlotMetrics] = field(default_factory=dict)
    l3_violations: dict[str, int] = field(default_factory=dict)
    l3_violations_pre_repair: dict[str, int] = field(default_factory=dict)
    # L2 经独立 L2Judge（GPT-5.4）逐 UC 判定的 R1-R7 违规数；run_l2=False 时为 None
    l2_violations: Optional[dict[str, int]] = None
    l2_parse_failures: Optional[list] = None
    l2_details: Optional[dict] = None
    quality_flags: list = field(default_factory=list)
    nli_details: list = field(default_factory=list)
    nli_parse_failures: list = field(default_factory=list)
    nli_evaluated: bool = False
    l2_evaluated: bool = False


class NLIJudge:
    """NLI judge for verifying whether evidence entails a claim.

    Text and multimodal endpoints are selected by ``config.settings``.
    """

    SYSTEM_PROMPT = """You are an NLI (Natural Language Inference) judge for requirements engineering.
Your task is to determine whether a claim is entailed by the given evidence.

Output a JSON object with exactly two fields:
- "entailment": one of "entailment", "contradiction", or "neutral"
- "rationale": a one-sentence justification

Rules:
- "entailment": every MATERIAL proposition in the claim is directly supported
  or reasonably implied by one or more cited evidence units collectively
- "contradiction": the evidence directly contradicts the claim
- "neutral": at least one MATERIAL behavioral, precondition, exception, or
  outcome proposition lacks support and is not contradicted
- The evidence may contain multiple cited units. Judge their UNION: a claim is
  entailed when every material part is supported by one or more cited units
  collectively. No single unit is required to support the whole claim.
- Treat faithful paraphrases, terminology normalization, active/passive voice,
  and combining complementary facts from several cited units as entailment.
- Do not require verbatim overlap, support from one sentence/page, or one cited
  unit to contain the complete compound claim.
- Do not penalize purely organizational wording used to express a requirement
  as a use-case step when it does not add new behavior or conditions.
- A harmless difference in wording or granularity is not missing evidence.
  Identify a concrete unsupported material proposition before choosing neutral.

Remain evidence-bound: do not excuse invented actors, conditions, actions,
exceptions, ordering constraints, or outcomes.
Do NOT use world knowledge — only judge based on the provided evidence text."""

    def __init__(
        self, model_fn=None, endpoint=None, vision_endpoint=None, max_attempts: int = 3,
    ):
        """
        Args:
            model_fn:  callable(prompt, images=None) -> str。
                       若非 None，则覆盖默认 endpoint（便于测试）
            endpoint:  ModelEndpoint，默认 NLI_JUDGE_ENDPOINT（GPT-5.4）
        """
        self._model_fn = model_fn
        self._max_attempts = max(1, int(max_attempts))
        # A shared judge instance is used by concurrent per-UC evaluation.
        # Identical claim/evidence inputs must receive one identical judgment
        # regardless of method or task scheduling; otherwise evaluator noise
        # can create artificial differences between AUCMR and its ablation.
        self._judgment_lock = threading.Lock()
        self._judgment_futures: dict[tuple, Future] = {}
        if endpoint is None:
            sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
            from config.settings import NLI_JUDGE_ENDPOINT, NLI_VISION_JUDGE_ENDPOINT
            self._endpoint = NLI_JUDGE_ENDPOINT
            self._vision_endpoint = NLI_VISION_JUDGE_ENDPOINT
        else:
            self._endpoint = endpoint
            self._vision_endpoint = vision_endpoint or endpoint

    def _call_model(self, prompt: str) -> dict:
        """Call the LLM for NLI judgment. Override for different backends."""
        if self._model_fn is not None:
            response = self._model_fn(prompt)
        else:
            response = self._default_nli_call(prompt)
        parsed = self._parse_response(response)
        if parsed is None:
            raise RuntimeError("NLI judge returned an unparseable response")
        return parsed

    def _default_nli_call(self, prompt: str, image_paths: Optional[list[str]] = None) -> str:
        """Route text evidence to DeepSeek and page evidence to the vision judge."""
        try:
            from config.settings import chat_completion
            endpoint = self._vision_endpoint if image_paths else self._endpoint
            messages = [
                {"role": "system", "content": self.SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ]
            return chat_completion(endpoint, messages, image_paths=image_paths)
        except Exception as e:
            raise RuntimeError(f"NLI judge call failed: {e}") from e

    def _parse_response(self, response: str) -> Optional[dict]:
        """Parse the LLM response into a structured judgment."""
        try:
            # Try to extract JSON from the response
            import json_repair
            parsed = json_repair.loads(response)
            label = parsed.get("entailment")
            if label not in {"entailment", "contradiction", "neutral"}:
                return None
            return {"entailment": label, "rationale": str(parsed.get("rationale", ""))}
        except Exception:
            return None

    def judge(
        self, claim: str, evidence_texts: list[str], image_paths: Optional[list[str]] = None,
    ) -> dict:
        """Judge whether the evidence entails the claim.

        Args:
            claim: the claim text to verify
            evidence_texts: list of evidence card content strings

        Returns:
            {"entailment": str, "rationale": str}
        """
        image_paths = [p for p in (image_paths or []) if os.path.isfile(p)]
        if not evidence_texts and not image_paths:
            return {"entailment": "neutral", "rationale": "no evidence provided"}

        combined_evidence = "\n\n---\n\n".join(evidence_texts)
        prompt = f"""Claim: {claim}

Evidence:
{combined_evidence}

Judge whether the evidence entails the claim. Output JSON."""
        image_signature = tuple(
            (
                os.path.realpath(path),
                os.path.getsize(path),
                os.stat(path).st_mtime_ns,
            )
            for path in image_paths
        )
        cache_key = (claim, tuple(evidence_texts), image_signature)
        with self._judgment_lock:
            shared_future = self._judgment_futures.get(cache_key)
            owns_judgment = shared_future is None
            if owns_judgment:
                shared_future = Future()
                self._judgment_futures[cache_key] = shared_future
        if not owns_judgment:
            cached = dict(shared_future.result())
            cached["judge_cache_hit"] = True
            return cached

        last_error: Optional[Exception] = None
        try:
            for attempt in range(1, self._max_attempts + 1):
                try:
                    if self._model_fn is not None:
                        response = self._model_fn(prompt)
                        judge_model = "injected-test-judge"
                    else:
                        response = self._default_nli_call(prompt, image_paths=image_paths)
                        endpoint = self._vision_endpoint if image_paths else self._endpoint
                        judge_model = endpoint.model_name
                    parsed = self._parse_response(response)
                    if parsed is None:
                        raise RuntimeError("NLI judge returned an unparseable response")
                    parsed["judge_model"] = judge_model
                    parsed["judge_attempts"] = attempt
                    parsed["judge_cache_hit"] = False
                    shared_future.set_result(dict(parsed))
                    return parsed
                except Exception as exc:
                    last_error = exc
                    if attempt < self._max_attempts:
                        logger.warning(
                            "NLI judge attempt %d/%d failed; retrying: %s",
                            attempt, self._max_attempts, exc,
                        )
            error = RuntimeError(
                f"NLI judge failed after {self._max_attempts} attempts: {last_error}"
            )
            shared_future.set_exception(error)
            raise error from last_error
        except BaseException:
            if not shared_future.done():
                shared_future.set_exception(last_error or RuntimeError("NLI judge failed"))
            with self._judgment_lock:
                self._judgment_futures.pop(cache_key, None)
            raise


class MetricsComputer:
    """Compute L1, L2, L3 metrics from CREST output JSON files."""

    def __init__(self, nli_judge: Optional[NLIJudge] = None):
        self.nli_judge = nli_judge or NLIJudge()

    def load_use_case(self, uc_path: str) -> dict:
        with open(uc_path, "r", encoding="utf-8") as f:
            return json.load(f)

    def load_evidence(self, ev_path: str) -> dict:
        with open(ev_path, "r", encoding="utf-8") as f:
            return json.load(f)

    @staticmethod
    def _card_resolvable(card: Optional[dict]) -> bool:
        if not isinstance(card, dict):
            return False
        if str(card.get("content", "")).strip():
            return True
        return any(os.path.isfile(str(path)) for path in (card.get("image_paths") or []))

    def _pre_constraint_claims(self, uc_data: dict) -> list[dict]:
        """Read raw author candidates captured before deterministic ref filtering.

        Older archived runs have no audit block; for those, fall back to final
        claims so the metric collector remains backward compatible.
        """
        audit_slots = (
            uc_data.get("citation_audit", {})
            .get("pre_constraint_slots", {})
        )
        if isinstance(audit_slots, dict) and audit_slots:
            claims: list[dict] = []
            for slot_name in EVALUATED_SLOTS:
                draft = audit_slots.get(slot_name, {})
                if not isinstance(draft, dict):
                    continue
                exposed = draft.get("exposed_evidence_refs", [])
                exposed = exposed if isinstance(exposed, list) else []
                for claim in draft.get("claims", []) or []:
                    if not isinstance(claim, dict) or not str(claim.get("content", "")).strip():
                        continue
                    item = dict(claim)
                    item["_slot_name"] = slot_name
                    item["_exposed_evidence_refs"] = exposed
                    item["_requires_exposed_ref"] = True
                    claims.append(item)
            return claims

        claims = []
        for slot_name in EVALUATED_SLOTS:
            slot = uc_data.get("slots", {}).get(slot_name, {})
            for claim in slot.get("claims", []):
                if isinstance(claim, dict):
                    item = dict(claim)
                    item["_slot_name"] = slot_name
                    claims.append(item)
        return claims

    def compute_l1_scg(self, uc_data: dict, ev_data: dict) -> tuple[float, int, int]:
        """Compute CitCov from raw candidates, before the hard evidence gate."""
        cards = {str(card.get("card_id")): card for card in ev_data.get("evidence_cards", [])}
        total = 0
        cited = 0
        for claim in self._pre_constraint_claims(uc_data):
            total += 1
            refs = claim.get("evidence_refs", []) or []
            exposed = claim.get("_exposed_evidence_refs", []) or []
            requires_exposed = bool(claim.get("_requires_exposed_ref"))
            if any(
                isinstance(ref, str)
                and (not requires_exposed or ref in exposed)
                and self._card_resolvable(cards.get(ref))
                for ref in refs
            ):
                cited += 1
        scg = (cited / total * 100) if total > 0 else 0.0
        return scg, cited, total

    @staticmethod
    def _final_claim_count(uc_data: dict) -> int:
        return sum(
            len(uc_data.get("slots", {}).get(slot_name, {}).get("claims", []) or [])
            for slot_name in EVALUATED_SLOTS
        )

    def compute_l1_nli_g(
        self, uc_data: dict, ev_data: dict
    ) -> tuple[int, list[dict], list[dict]]:
        """Compute NLI-Verified Grounding Rate.

        For each claim, checks whether the cited evidence cards entail the claim.
        Uses the NLI judge (GPT-4o or Qwen fallback).
        """
        # Build evidence card lookup
        cards_by_id = {}
        for card in ev_data.get("evidence_cards", []):
            cards_by_id[card["card_id"]] = card

        entailed = 0
        details = []
        failures = []

        # CitCov, NLI-G and CHG must share one claim population.  Use the
        # captured model-authored candidates before deterministic whitelist
        # filtering for all three counts; otherwise the hard gate would make
        # CitCov tautologically 100% and N_ent <= N_cite <= N_all could fail.
        for claim in self._pre_constraint_claims(uc_data):
            slot_name = str(claim.get("_slot_name", "unknown"))
            raw_refs = claim.get("evidence_refs", []) or []
            exposed = claim.get("_exposed_evidence_refs", []) or []
            requires_exposed = bool(claim.get("_requires_exposed_ref"))
            refs = list(dict.fromkeys(
                ref for ref in raw_refs
                if isinstance(ref, str)
                and (not requires_exposed or ref in exposed)
                and self._card_resolvable(cards_by_id.get(ref))
            ))
            if not refs:
                details.append({
                    "slot": slot_name,
                    "claim": claim.get("content", "")[:100],
                    "entailment": "neutral",
                    "rationale": "no valid, exposed evidence cited",
                    "evidence_refs": raw_refs,
                })
                continue

            # A Detail-UC claim may intentionally compose facts from several
            # cited cards.  Score the union once; requiring one card to entail
            # the whole claim would silently impose an atomic-claim assumption
            # that the task and paper do not make.
            evidence_texts = []
            image_paths = []
            evidence_modalities = {}
            for ref_id in refs:
                card = cards_by_id[ref_id]
                images = list(card.get("image_paths", []) or [])
                valid_images = [path for path in images if os.path.isfile(str(path))]
                first_image = len(image_paths) + 1
                image_paths.extend(valid_images)
                parts = [f"Evidence [{ref_id}]:"]
                if card.get("content"):
                    parts.append(str(card["content"]))
                if valid_images:
                    last_image = len(image_paths)
                    image_label = (
                        f"Attached Image {first_image}" if first_image == last_image
                        else f"Attached Images {first_image}-{last_image}"
                    )
                    parts.append(f"{image_label} belong to Evidence [{ref_id}].")
                evidence_texts.append("\n".join(parts))
                evidence_modalities[ref_id] = "image" if valid_images else "text"

            try:
                judgment = self.nli_judge.judge(
                    claim.get("content", ""), evidence_texts, image_paths=image_paths
                )
            except Exception as exc:
                failure = {
                    "slot": slot_name,
                    "claim": claim.get("content", ""),
                    "evidence_refs": refs,
                    "error": str(exc),
                }
                failures.append(failure)
                details.append({
                    **failure, "entailment": "judge_failure",
                    "rationale": failure["error"],
                    "judgment_mode": "joint_cited_evidence",
                })
                continue

            if judgment["entailment"] == "entailment":
                entailed += 1

            details.append({
                "slot": slot_name,
                "claim": claim.get("content", "")[:100],
                "entailment": judgment["entailment"],
                "rationale": judgment["rationale"],
                "evidence_refs": refs,
                "evidence_modalities": evidence_modalities,
                "judgment_mode": "joint_cited_evidence",
                "judge_model": judgment.get("judge_model"),
                "judge_attempts": judgment.get("judge_attempts", 1),
                "judge_cache_hit": judgment.get("judge_cache_hit", False),
            })

        return entailed, details, failures

    def compute_l1_cross_page(self, ev_data: dict) -> tuple[float, int, int, int]:
        """Compute cross-page card statistics."""
        cards = ev_data.get("evidence_cards", [])
        total = len(cards)
        cross = sum(1 for c in cards if c.get("is_cross_page"))
        referenced = len(ev_data.get("referenced_card_ids", []))
        pct = (cross / total * 100) if total > 0 else 0.0
        return pct, cross, total, referenced

    def compute_l2_completeness(self, uc_data: dict) -> dict[str, SlotMetrics]:
        """Compute per-slot fill rates."""
        slot_metrics = {}
        for slot_name in COCKBURN_SLOTS:
            slot = uc_data.get("slots", {}).get(slot_name, {})
            claims = slot.get("claims", [])
            sm = SlotMetrics(slot_name=slot_name)
            sm.claim_count = len(claims)
            sm.cited_claim_count = sum(
                1 for c in claims if c.get("evidence_refs")
            )
            sm.evidence_ref_count = sum(
                len(c.get("evidence_refs", [])) for c in claims
            )
            # A slot is "filled" if it has ≥1 substantive claim
            # Primary Actor is always filled (short-circuited)
            if slot_name == "primary_actor":
                sm.is_filled = True
            else:
                sm.is_filled = len(claims) > 0
            sm.quality_flags = slot.get("quality_flags", [])
            slot_metrics[slot_name] = sm
        return slot_metrics

    def compute_l3_consistency(
        self, uc_data: dict
    ) -> tuple[dict[str, int], dict[str, int]]:
        """Extract L3 consistency violations from quality flags.

        Returns:
            (post_repair_violations, pre_repair_violations)
            Pre-repair violations are estimated from revision history.

        注: 这是 AUCMR 内部 quick-path，只从 quality_flags 反推。
        论文要的"GPT-5.4 跑每份 Detail-UC 判定 R1-R7 违规"由
        eval.l2_judge.judge_use_case 完成，需要传 --run-l2 才能激活。
        """
        post = defaultdict(int)
        pre = defaultdict(int)

        # Post-repair: count from quality_flags
        for flag in uc_data.get("quality_flags", []):
            detail = flag.get("detail", "")
            flag_type = flag.get("flag_type", "")
            if flag_type == "unresolved_inconsistency":
                # Map detail to rule
                for rule_id, rule_name in CONSISTENCY_RULES.items():
                    if rule_name in detail:
                        post[rule_id] += 1
                        break
                else:
                    post["R_unknown"] += 1

        # Pre-repair: estimate from revision history
        # The consistency_revision action records what was repaired
        for rev in uc_data.get("revision_history", []):
            if rev.get("action") == "consistency_revision":
                # Each consistency revision round may have repaired some issues
                pass  # We can't easily extract pre-repair counts from history alone

        # If no pre-repair data, estimate pre = post (conservative)
        # In practice, pre-repair data comes from running CREST w/o cross-slot
        for rule_id in CONSISTENCY_RULES:
            if rule_id not in pre:
                pre[rule_id] = post.get(rule_id, 0)  # will be overridden by actual CREST w/o cross-slot data

        return dict(post), dict(pre)

    def compute_l2_via_judge(
        self,
        uc_data: dict,
        l2_judge: Optional["L2Judge"] = None,
    ) -> dict:
        """论文规定的 L2 路径：用独立 L2Judge（GPT-5.4）对每份 Detail-UC 跑 R1-R7。

        与 compute_l3_consistency（从 quality_flags 反推）不同，本函数真实读
        slot 内容判定违规。

        Args:
            uc_data:  任意方法输出的 Detail-UC JSON（CREST / baseline 都可）
            l2_judge: 已构造的 L2Judge 实例（None 则用默认 GPT-5.4 endpoint）

        Returns:
            dict with keys: violations / parse_failures / summary
        """
        from eval.l2_judge import L2Judge, judge_use_case
        return judge_use_case(uc_data, l2_judge or L2Judge())

    def compute_all(
        self, uc_path: str, ev_path: str, run_nli: bool = True,
        run_l2: bool = False, l2_judge: Optional["L2Judge"] = None,
    ) -> UCMetrics:
        """Compute all L1-L3 metrics for a single use case."""
        uc_data = self.load_use_case(uc_path)
        ev_data = self.load_evidence(ev_path)

        metrics = UCMetrics(
            use_case_name=uc_data.get("use_case_name", "unknown"),
            actor=uc_data.get("actor", "unknown"),
        )

        # L1 uses one model-authored claim population for N_all, N_cite and
        # N_ent.  The audit capture preserves claims that the deterministic
        # evidence whitelist may later remove from the delivered artifact.
        scg, cited, total = self.compute_l1_scg(uc_data, ev_data)
        metrics.scg = scg
        metrics.pre_constraint_claim_count = total
        metrics.pre_constraint_cited_claim_count = cited
        metrics.total_claims = total
        metrics.total_cited_claims = cited

        # L1: NLI-G
        if run_nli:
            metrics.nli_evaluated = True
            entailed, nli_details, nli_failures = self.compute_l1_nli_g(uc_data, ev_data)
            metrics.total_entailed_claims = entailed
            metrics.nli_g = (entailed / metrics.total_claims * 100) if metrics.total_claims else 0.0
            metrics.nli_details = nli_details
            metrics.nli_parse_failures = nli_failures

        # L1: Cross-page cards
        cross_pct, cross, total_cards, ref_cards = self.compute_l1_cross_page(ev_data)
        metrics.cross_page_card_pct = cross_pct
        metrics.total_evidence_cards = total_cards
        metrics.cross_page_cards = cross
        metrics.referenced_cards = ref_cards
        metrics.avg_cards = ref_cards  # referenced cards per UC

        # L2: Structural completeness
        slot_metrics = self.compute_l2_completeness(uc_data)
        metrics.slot_metrics = slot_metrics
        metrics.all_8_filled = all(
            sm.is_filled for sm in slot_metrics.values()
        )
        metrics.empty_slot_count = sum(
            1 for slot in EVALUATED_SLOTS if not slot_metrics[slot].is_filled
        )

        # L3: Internal consistency
        post_violations, pre_violations = self.compute_l3_consistency(uc_data)
        metrics.l3_violations = post_violations
        metrics.l3_violations_pre_repair = pre_violations
        metrics.quality_flags = uc_data.get("quality_flags", [])

        # L2: 通过独立 L2Judge（GPT-5.4）对每份 Detail-UC 真实判定 R1-R7 违规。
        # 仅当显式 run_l2=True 时激活（避免默认跑 GPT-5.4 拉高指标计算开销）。
        if run_l2:
            metrics.l2_evaluated = True
            l2 = self.compute_l2_via_judge(uc_data, l2_judge=l2_judge)
            metrics.l2_violations = l2["summary"]
            metrics.l2_parse_failures = l2["parse_failures"]
            metrics.l2_details = {
                "violations": {
                    rule_id: [item.to_dict() for item in items]
                    for rule_id, items in l2["violations"].items()
                },
                "parse_failures": l2["parse_failures"],
                "summary": l2["summary"],
            }
        else:
            metrics.l2_violations = None
            metrics.l2_parse_failures = None
            metrics.l2_details = None

        return metrics

    def compute_aggregate(
        self, all_metrics: list[UCMetrics], method_name: str = ""
    ) -> dict[str, Any]:
        """Aggregate metrics across multiple UCs for paper tables."""
        n = len(all_metrics)
        if n == 0:
            return {}

        # L1
        n_all = sum(m.total_claims for m in all_metrics)
        n_cite = sum(m.total_cited_claims for m in all_metrics)
        n_ent = sum(m.total_entailed_claims for m in all_metrics)
        citcov = (n_cite / n_all * 100) if n_all else 0.0
        nli_g = (n_ent / n_all * 100) if n_all else 0.0
        chg = ((n_cite - n_ent) / n_cite * 100) if n_cite else None
        empty_slots = sum(m.empty_slot_count for m in all_metrics)
        nli_failures = sum(len(m.nli_parse_failures) for m in all_metrics)
        nli_evaluated = all(m.nli_evaluated for m in all_metrics)
        avg_cross_pct = sum(m.cross_page_card_pct for m in all_metrics) / n
        avg_cards = sum(m.avg_cards for m in all_metrics) / n

        # L2
        slot_fill_rates = {}
        for slot_name in COCKBURN_SLOTS:
            filled = sum(1 for m in all_metrics if m.slot_metrics.get(slot_name, SlotMetrics(slot_name=slot_name)).is_filled)
            slot_fill_rates[slot_name] = (filled / n * 100) if n > 0 else 0.0
        all_8_rate = sum(1 for m in all_metrics if m.all_8_filled) / n * 100

        # L3
        judged = [m for m in all_metrics if m.l2_violations is not None]
        if judged:
            consistency_metrics = [m for m in judged if not m.l2_parse_failures]
        else:
            consistency_metrics = all_metrics
        total_violations = defaultdict(float)
        for m in consistency_metrics:
            source = m.l2_violations if judged else m.l3_violations
            for rule_id, count in source.items():
                total_violations[rule_id] += count
        consistency_n = len(consistency_metrics)
        avg_violations = {
            k: v / consistency_n for k, v in total_violations.items()
        } if consistency_n else {}
        total_per_uc = sum(avg_violations.values())

        return {
            "method": method_name,
            "n_ucs": n,
            "l1_n_all": n_all,
            "l1_n_cite": n_cite,
            "l1_n_ent": n_ent,
            "l1_citcov": round(citcov, 1),
            "l1_nli_g": round(nli_g, 1),
            "l1_chg": round(chg, 1) if chg is not None else None,
            "l1_empty_slots": empty_slots,
            "l1_esc_per_uc": round(empty_slots / n, 2),
            "l1_judge_failures": nli_failures,
            "l1_evaluated": nli_evaluated,
            "l1_valid": nli_evaluated and nli_failures == 0,
            "l1_cross_page_pct": round(avg_cross_pct, 1),
            "l1_avg_cards": round(avg_cards, 1),
            "l2_slot_fill_rates": {k: round(v, 1) for k, v in slot_fill_rates.items()},
            "l2_all_8_rate": round(all_8_rate, 1),
            "l2_violations_per_uc": {k: round(v, 2) for k, v in avg_violations.items()},
            "l2_total_per_uc": round(total_per_uc, 2),
            # Backward-compatible aliases for older analysis scripts.
            "l3_violations_per_uc": {k: round(v, 2) for k, v in avg_violations.items()},
            "l3_total_per_uc": round(total_per_uc, 2),
            "consistency_judged_ucs": consistency_n,
            "consistency_parse_failures": len(judged) - consistency_n if judged else 0,
            "l2_evaluated": bool(judged),
            "l2_valid": bool(judged) and consistency_n == n,
        }

    def print_report(self, metrics: UCMetrics):
        """Print a human-readable metrics report for a single UC."""
        print(f"\n{'='*60}")
        print(f"Use Case: {metrics.use_case_name} ({metrics.actor})")
        print(f"{'='*60}")

        print(f"\n--- L1: Evidence Grounding ---")
        print(
            f"  CitCov: {metrics.scg:.1f}% "
            f"({metrics.total_cited_claims}/{metrics.total_claims} generated assertions cited)"
        )
        print(f"  NLI-G: {metrics.nli_g:.1f}% (same assertion population, NLI-verified)")
        print(f"  Cross-Page Cards: {metrics.cross_page_card_pct:.1f}% ({metrics.cross_page_cards}/{metrics.total_evidence_cards})")
        print(f"  Referenced Cards: {metrics.referenced_cards} / {metrics.total_evidence_cards} total")

        print(f"\n--- L2: Structural Completeness ---")
        for slot_name in COCKBURN_SLOTS:
            sm = metrics.slot_metrics.get(slot_name)
            if sm:
                status = "✓" if sm.is_filled else "✗"
                print(f"  [{status}] {slot_name}: {sm.claim_count} claims, {sm.cited_claim_count} cited")
        print(f"  All-8 filled: {'✓' if metrics.all_8_filled else '✗'}")

        print(f"\n--- L3: Internal Consistency ---")
        total_v = sum(metrics.l3_violations.values())
        print(f"  Total violations: {total_v}")
        for rule_id in sorted(metrics.l3_violations.keys()):
            count = metrics.l3_violations[rule_id]
            if count > 0:
                rule_name = CONSISTENCY_RULES.get(rule_id, rule_id)
                print(f"    {rule_id} ({rule_name}): {count}")

        if metrics.quality_flags:
            print(f"\n  Quality flags ({len(metrics.quality_flags)}):")
            for flag in metrics.quality_flags:
                print(f"    [{flag.get('flag_type', '?')}] {flag.get('detail', '')[:120]}")


# ============================================================
# Command-line interface
# ============================================================

if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description="Compute L1-L3 metrics from CREST output")
    ap.add_argument("--uc", required=True, help="Path to use-case JSON file")
    ap.add_argument("--evidence", required=True, help="Path to evidence JSON file")
    ap.add_argument("--no-nli", action="store_true", help="Skip NLI verification (faster)")
    ap.add_argument("--max-nli-claims", type=int, default=50, help="Max claims for NLI check")
    args = ap.parse_args()

    mc = MetricsComputer()
    metrics = mc.compute_all(args.uc, args.evidence, run_nli=not args.no_nli)
    mc.print_report(metrics)
