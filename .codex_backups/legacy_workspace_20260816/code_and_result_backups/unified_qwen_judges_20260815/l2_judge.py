"""
L2 — 跨槽位一致性 R1-R7 裁判。

论文要求:
  - L2 规则判定由 GPT-5.4 完成（与生成模型家族/厂商均不重叠，温度 0）
  - 对每份 Detail-UC 独立判定，不将不同方法的输出并置比较
  - 输出结构化违规记录（违规规则编号、所涉槽位、陈述索引与简短理由）
  - 响应解析失败的情形单独记账，不计入"无违规"，以避免将裁判的静默失败误读为一致性良好

规则定义（与论文表 1 / eval.compute_metrics.CONSISTENCY_RULES 一致）:
  R1 trigger_preconditions_coverage
  R2 mainflow_trigger_continuity
  R3 mainflow_postconditions_reachability
  R4 actor_mainflow_presence
  R5 goal_mainflow_alignment
  R6 altflow_diverge_points
  R7 exceptionflow_exit_coverage

输入: 一份 Detail-UC 的 slots dict（与 main.json 中 slots 字段同结构）
输出: {
  "violations": {rule_id: [Violation, ...]},
  "parse_failures": [rule_id, ...],  # 解析失败但被判为"无违规"的不计入此项
  "summary": {rule_id: count, ...}
}
"""

import json
import logging
import os
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

# 确保可以 import config.settings
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config.settings import L2_JUDGE_ENDPOINT, ModelEndpoint, chat_completion
from eval.compute_metrics import CONSISTENCY_RULES

logger = logging.getLogger("l2_judge")


# ---------------------------------------------------------------------------
# 提示词模板（与论文第 7 条规则的判定语义一致）
# ---------------------------------------------------------------------------

_VERTICAL_SYSTEM_PROMPT = """\
You are a strict consistency judge for Cockburn-style Detail-UC.
You check the VERTICAL flow continuity: preconditions → trigger → main_flow → postconditions.

For each violation found, output a JSON object with fields:
- rule_violated: one of ["trigger_preconditions_coverage", "mainflow_trigger_continuity", "mainflow_postconditions_reachability"]
- offending_slot: the slot whose content should be revised (prefer LATER slot in Cockburn order:
  preconditions < trigger < main_flow < postconditions; the LATER slot is the one to fix
  because it should align to upstream content)
- offending_claim_index: 0-based claim index inside offending_slot; use -1 only for slot-wide problems
- rationale: one short sentence explaining the violation

Rules:
1. trigger_preconditions_coverage — Every implicit prerequisite of the Trigger must have a corresponding entry in Preconditions.
2. mainflow_trigger_continuity — Main Flow step 1 must pick up where the Trigger left off.
3. mainflow_postconditions_reachability — The last step of Main Flow must reasonably lead to the Postconditions state.

Be conservative: only flag violations that are explicit in the slot text. Empty slots do NOT count as violations (they are tracked by ESC, not L2).

Return ONLY a JSON array. Empty array means no violations found in this group.
"""

_LATERAL_SYSTEM_PROMPT = """\
You are a strict consistency judge for Cockburn-style Detail-UC.
You check the LATERAL alignment around main_flow: actor presence, goal alignment,
alternative-flow branch points, and exception-flow exit coverage.

For each violation found, output a JSON object with fields:
- rule_violated: one of ["actor_mainflow_presence", "goal_mainflow_alignment",
  "altflow_diverge_points", "exceptionflow_exit_coverage"]
- offending_slot: the slot whose content should be revised
- offending_claim_index: 0-based claim index inside offending_slot; use -1 only for slot-wide problems
- rationale: one short sentence explaining the violation

Rules:
1. actor_mainflow_presence — The Primary Actor must appear (by name or pronoun) in Main Flow.
   A step where the actor appears as object or via possessive counts as actor present.
2. goal_mainflow_alignment — The Goal must be consistent with the behavior described in Main Flow.
   Ignore goal phrases describing long-term / asynchronous / system-side ongoing behavior.
3. altflow_diverge_points — Every divergence point referenced in Alternative Flow must exist
   as a step in Main Flow (matched by step_number or by wording 'diverges from step N').
4. exceptionflow_exit_coverage — Every branch in Exception Flow must have a clear exit
   (return to a Main Flow step, terminate the use case, or hand off to Alternative Flow).

Be conservative: only flag violations that are explicit in the slot text. Empty slots do NOT count as violations.

Return ONLY a JSON array. Empty array means no violations found in this group.
"""


def _format_slot_for_prompt(slot_name: str, slot_content: Optional[Dict[str, Any]]) -> str:
    """把一个 slot 的内容格式化为 prompt 友好的字符串。"""
    if not slot_content:
        return "(empty)"
    claims = slot_content.get("claims") or []
    raw_text = slot_content.get("raw_text") or ""
    if claims:
        lines = []
        for i, c in enumerate(claims):
            step = c.get("step_number")
            content = c.get("content", "")
            if step is not None:
                lines.append(f"  [{i}] (step {step}) {content}")
            else:
                lines.append(f"  [{i}] {content}")
        return "\n".join(lines)
    if raw_text:
        return f"  (raw_text) {raw_text}"
    return "(empty)"


def _build_vertical_prompt(use_case_name: str, actor: str, slots: Dict[str, Any]) -> str:
    return f"""\
Use Case: {use_case_name}
Primary Actor: {actor}

=== SLOTS UNDER REVIEW ===

preconditions:
{_format_slot_for_prompt("preconditions", slots.get("preconditions"))}

trigger:
{_format_slot_for_prompt("trigger", slots.get("trigger"))}

main_flow:
{_format_slot_for_prompt("main_flow", slots.get("main_flow"))}

postconditions:
{_format_slot_for_prompt("postconditions", slots.get("postconditions"))}

=== BACKGROUND SLOTS (read-only context) ===

goal:
{_format_slot_for_prompt("goal", slots.get("goal"))}

primary_actor:
{_format_slot_for_prompt("primary_actor", slots.get("primary_actor"))}

Return ONLY a JSON array of violations. Empty array if no violations."""


def _build_lateral_prompt(use_case_name: str, actor: str, slots: Dict[str, Any]) -> str:
    return f"""\
Use Case: {use_case_name}
Primary Actor: {actor}

=== ANCHOR SLOT ===

main_flow:
{_format_slot_for_prompt("main_flow", slots.get("main_flow"))}

=== SLOTS COMPARED AGAINST main_flow ===

goal:
{_format_slot_for_prompt("goal", slots.get("goal"))}

primary_actor:
{_format_slot_for_prompt("primary_actor", slots.get("primary_actor"))}

alternative_flow:
{_format_slot_for_prompt("alternative_flow", slots.get("alternative_flow"))}

exception_flow:
{_format_slot_for_prompt("exception_flow", slots.get("exception_flow"))}

Return ONLY a JSON array of violations. Empty array if no violations."""


# ---------------------------------------------------------------------------
# 违规记录
# ---------------------------------------------------------------------------

@dataclass
class Violation:
    rule_id: str           # "R1" .. "R7"
    rule_name: str         # CONSISTENCY_RULES[rule_id]
    offending_slot: str
    offending_claim_index: int  # -1 表示 slot-wide
    rationale: str

    def to_dict(self) -> dict:
        return {
            "rule_id": self.rule_id,
            "rule_name": self.rule_name,
            "offending_slot": self.offending_slot,
            "offending_claim_index": self.offending_claim_index,
            "rationale": self.rationale,
        }


# ---------------------------------------------------------------------------
# 解析辅助
# ---------------------------------------------------------------------------

_VALID_RULE_NAMES = set(CONSISTENCY_RULES.values())
_VALID_RULE_BY_NAME = {v: k for k, v in CONSISTENCY_RULES.items()}


def _coerce_to_list(parsed: Any) -> Optional[List[Any]]:
    """宽容处理 LLM 输出形状（与 agents.py._coerce_to_list 同形）。"""
    if parsed is None:
        return None
    if isinstance(parsed, list):
        return parsed
    if isinstance(parsed, dict):
        for key in ("violations", "issues", "items", "list", "results"):
            v = parsed.get(key)
            if isinstance(v, list):
                return v
        if "rule_violated" in parsed:
            return [parsed]
        return None
    return None


def _validate_issue(issue: Any) -> Optional[Violation]:
    """校验单条 issue，返回 Violation 或 None（不合法的丢弃）。"""
    if not isinstance(issue, dict):
        return None
    rule_name = issue.get("rule_violated")
    if rule_name not in _VALID_RULE_NAMES:
        return None
    rule_id = _VALID_RULE_BY_NAME[rule_name]
    slot = issue.get("offending_slot", "")
    if not isinstance(slot, str):
        slot = str(slot)
    idx = issue.get("offending_claim_index", -1)
    if isinstance(idx, bool):
        idx = -1
    try:
        idx = int(idx)
    except (TypeError, ValueError):
        idx = -1
    if idx < -1:
        idx = -1
    rationale = issue.get("rationale", "")
    if not isinstance(rationale, str):
        rationale = str(rationale)
    return Violation(
        rule_id=rule_id,
        rule_name=rule_name,
        offending_slot=slot,
        offending_claim_index=idx,
        rationale=rationale.strip(),
    )


def _location_is_valid(violation: Violation, slots: Dict[str, Any]) -> bool:
    if violation.offending_slot not in slots:
        return False
    if violation.offending_claim_index == -1:
        return True
    claims = (slots.get(violation.offending_slot) or {}).get("claims") or []
    return 0 <= violation.offending_claim_index < len(claims)


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------

class L2Judge:
    """R1-R7 跨槽位一致性裁判。

    默认 endpoint: config.settings.L2_JUDGE_ENDPOINT（论文: GPT-5.4）。
    """

    def __init__(self, model_fn=None, endpoint: Optional[ModelEndpoint] = None):
        """
        Args:
            model_fn:  callable(prompt_str) -> str。覆盖默认 endpoint，便于离线/测试。
            endpoint:  ModelEndpoint，默认 L2_JUDGE_ENDPOINT（GPT-5.4，温度 0）
        """
        self._model_fn = model_fn
        self._endpoint = endpoint or L2_JUDGE_ENDPOINT

    def _call_model(self, system_prompt: str, user_prompt: str) -> Optional[str]:
        if self._model_fn is not None:
            return self._model_fn(user_prompt)
        try:
            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ]
            return chat_completion(self._endpoint, messages)
        except Exception as e:
            logger.error(f"L2 judge call failed: {e}")
            return None

    @staticmethod
    def _parse_response(raw: Optional[str]) -> Optional[List[Any]]:
        """解析 + 容错（与 agents.py._parse_json 同形，但只解析不重试）。"""
        if not isinstance(raw, str):
            return None
        try:
            from json_repair import repair_json
            return json.loads(repair_json(raw))
        except Exception:
            return None

    def judge(
        self,
        use_case_name: str,
        actor: str,
        slots: Dict[str, Any],
    ) -> Dict[str, Any]:
        """对一份 Detail-UC 跑 7 条规则判定。

        Returns:
          {
            "violations": {rule_id: [Violation, ...]},
            "parse_failures": [{"group": "vertical"|"lateral", "raw": ...}, ...],
            "summary": {rule_id: count, ...}
          }
        """
        violations_by_rule: Dict[str, List[Violation]] = defaultdict(list)
        parse_failures: List[Dict[str, str]] = []

        # ---- Vertical 组 ----
        v_prompt = _build_vertical_prompt(use_case_name, actor, slots)
        raw_v = self._call_model(_VERTICAL_SYSTEM_PROMPT, v_prompt)
        parsed_v = _coerce_to_list(self._parse_response(raw_v))
        if parsed_v is None:
            logger.warning("[L2-vertical] parse failed or non-list output")
            parse_failures.append({"group": "vertical", "raw": (raw_v or "")[:300]})
        else:
            invalid = []
            for it in parsed_v:
                v = _validate_issue(it)
                if v and _location_is_valid(v, slots):
                    violations_by_rule[v.rule_id].append(v)
                else:
                    invalid.append(it)
            if invalid:
                parse_failures.append({
                    "group": "vertical", "raw": (raw_v or "")[:300],
                    "error": "invalid violation schema or claim location",
                })

        # ---- Lateral 组 ----
        l_prompt = _build_lateral_prompt(use_case_name, actor, slots)
        raw_l = self._call_model(_LATERAL_SYSTEM_PROMPT, l_prompt)
        parsed_l = _coerce_to_list(self._parse_response(raw_l))
        if parsed_l is None:
            logger.warning("[L2-lateral] parse failed or non-list output")
            parse_failures.append({"group": "lateral", "raw": (raw_l or "")[:300]})
        else:
            invalid = []
            for it in parsed_l:
                v = _validate_issue(it)
                if v and _location_is_valid(v, slots):
                    violations_by_rule[v.rule_id].append(v)
                else:
                    invalid.append(it)
            if invalid:
                parse_failures.append({
                    "group": "lateral", "raw": (raw_l or "")[:300],
                    "error": "invalid violation schema or claim location",
                })

        # 排序规则键 R1..R7
        for rule_id, violations in list(violations_by_rule.items()):
            unique = {}
            for violation in violations:
                key = (
                    violation.rule_id, violation.offending_slot,
                    violation.offending_claim_index, violation.rationale,
                )
                unique[key] = violation
            violations_by_rule[rule_id] = list(unique.values())
        summary = {rid: len(violations_by_rule.get(rid, [])) for rid in CONSISTENCY_RULES}
        return {
            "violations": dict(violations_by_rule),
            "parse_failures": parse_failures,
            "summary": summary,
        }


# ---------------------------------------------------------------------------
# 便捷函数：对单份 Detail-UC 落盘结果跑裁判
# ---------------------------------------------------------------------------

def judge_use_case(uc_data: Dict[str, Any], judge: Optional[L2Judge] = None) -> Dict[str, Any]:
    """对单份 UC JSON 跑 L2 裁判。返回结构与 L2Judge.judge 一致。"""
    judge = judge or L2Judge()
    return judge.judge(
        use_case_name=uc_data.get("use_case_name", ""),
        actor=uc_data.get("actor", ""),
        slots=uc_data.get("slots", {}),
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description="L2 R1-R7 consistency judge")
    ap.add_argument("--uc", required=True, help="Path to a use-case JSON (main output)")
    ap.add_argument("--out", default=None, help="Optional path to dump the JSON verdict")
    args = ap.parse_args()

    with open(args.uc, "r", encoding="utf-8") as f:
        uc_data = json.load(f)

    result = judge_use_case(uc_data)
    result_serializable = {
        "violations": {
            rid: [v.to_dict() for v in vs]
            for rid, vs in result["violations"].items()
        },
        "parse_failures": result["parse_failures"],
        "summary": result["summary"],
    }
    print(json.dumps(result_serializable, ensure_ascii=False, indent=2))
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(result_serializable, f, ensure_ascii=False, indent=2)
