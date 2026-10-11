"""
CREST 的提示词模板。

所有 prompt 都设计为 JSON 输出 + 强制格式约束。
沿用原 AUCMR 的 string.Template 模式，方便参数化。
"""

from string import Template

# ============================================================
# 共享：Slot 级提取规则
# ============================================================

_SLOT_EXTRACTION_RULES = """\
Slot-specific extraction rules (extract only facts that DIRECTLY constrain this use-case
slot; preserve numbers, thresholds and boundary conditions when they materially affect
the interaction, but do not collect every fact merely because it is specific):
- goal: 1 claim maximum. The exact objective phrase.
- primary_actor: 1 claim maximum. The exact actor name or role.
- preconditions: Only states that MUST already hold before the trigger. UI availability,
  feature descriptions, downstream policies and general system capabilities are not preconditions.
  One minimal entry-context claim is valid when the trigger explicitly starts inside a
  role-restricted administration workspace and the evidence identifies that workspace.
- trigger: 1 claim maximum. The exact triggering event or action.
- main_flow: The canonical single-success scenario. Different categories/options are
  alternatives, not actions that the actor performs serially in one use-case instance.
- alternative_flow: One claim per still-successful variant. It must specify which main
  flow step it diverges from. Validation failures, warnings, rejected operations and
  paths that cannot complete successfully belong in exception_flow, not here.
- exception_flow: One claim per exception. Include the trigger condition and the
  evidence-backed system response. An explicit failed/prevented end state is a
  terminal exception outcome. Add retry, return, continuation, hand-off or other
  recovery behavior only when an evidence card supports it; otherwise report that
  recovery as a missing_aspect instead of inventing it.
- postconditions: Only immediate observable states after successful completion; exclude
  cancellation outcomes, exception results and remote downstream consequences.
"""

_PAGING_ACTIONS_SCHEMA = """\
"paging_actions": list of actions when content is incomplete. Each action MUST be an object:
  {"direction": "next" | "previous", "reason": "<why this direction>"}
- "next": content is cut off at the bottom of the page (mid-sentence, table missing footer rows, diagram cut at page edge)
- "previous": content at the top of the page seems to continue from a prior page (mid-sentence start, no header)
- If is_complete is true, paging_actions MUST be [].
- DO NOT request non-adjacent jumps (e.g., "see Figure 7" elsewhere in the doc); cross-reference resolution is out of scope. If a needed reference is on a non-adjacent page, treat the current region as the best available evidence and set is_complete=true.
- DO NOT output strings here — each item must be an object with "direction" and "reason" fields.
"""

# ============================================================
# Phase 1-N: Per-Slot DCRRM Loop
# ============================================================

# --- Query Designer ---

QUERY_DESIGNER = Template("""\
You are a Query Designer in a requirements elaboration system. Your job is to design
search queries that will find evidence to fill a Cockburn use case slot.

Primary Actor: ${actor}
Use Case: ${use_case_name}
Goal (the user objective this entire use case must achieve): ${goal_text}

Current Slot to Fill: ${slot_type} — ${slot_description}
Leading Question: ${leading_question}

Already Filled Slots:
${filled_slots_json}

Evidence Cards Already Available from Prior Slots (these MAY ALSO support the current
slot — Cockburn slots describe different facets of the SAME interaction, so reuse
is expected. Only design queries for NEW gaps not covered below):
${prior_evidence_summary}

Current Slot Content (may be partial):
${current_slot_json}

Previously Asked Queries (do NOT repeat these):
${history_queries_json}

${slot_specific_hint}

${inconsistency_hint}

Note on inconsistency hints (if present above):
The hints above come from a cross-slot consistency check that detected logical or
factual inconsistencies between the current slot and other already-filled slots.
Your queries should specifically target the gaps or contradictions described in
the hints, so that retrieved evidence can resolve them.

=== TASK ===
Identify the smallest set of uncovered aspects, then design 1-2 new search queries
that will find the evidence needed to fill or improve
the "${slot_type}" slot. Queries should be:
- Specific (target concrete facts: numbers, behaviors, conditions)
- Diverse in angle (cover different aspects of the slot)
- Aligned with the stated Goal — your queries should hunt for evidence describing the
  Goal-achieving interaction. If the Goal is "place bids", search for "QuickBid button",
  "bid confirmation", "submit bid" — NOT for adjacent processes like "view auction
  history" or "post-auction payment".
- Non-overlapping with previously asked queries

=== TERMINATION ===
Before designing queries, assess the current state:
- Is the current slot already well-supported by evidence (including reusable prior-slot cards)?
- Are there meaningful gaps that more retrieval can fill?
- Have prior queries exhausted the document's relevant content?

If reusable evidence already covers the slot, do not retrieve again.

Return ONLY this JSON object:
{
  "reuse_card_ids": ["<existing card id that directly supports this slot>"],
  "missing_aspects": ["<specific unanswered aspect>"],
  "queries": ["<one gap-specific query>", "<optional second query>"],
  "stop": false,
  "reason": "<brief coverage decision>"
}

Set stop=true and queries=[] only when the current answer plus reusable evidence already
covers every essential aspect, or when the document has been exhausted. Do not stop merely
because some related evidence exists. Never emit more than two queries.
""")

# --- Evidence Collector ---

EVIDENCE_COLLECTOR_SINGLE_PAGE = Template("""\
You are an Evidence Collector reading a page from a requirements document.
Your task: extract evidence specifically for filling the "${slot_type}" slot.

Primary Actor: ${actor}
Use Case: ${use_case_name}
Current Slot: ${slot_type} — ${slot_description}

Evidence aspects actively sought this round (may be empty when no prior gap is known):
${required_aspects_json}

Already Known from Prior Slots:
${filled_slots_json}

${previous_page_context}

Read the provided page image carefully.

=== STEP 1: RELEVANCE GATE ===
Decide if this page contains ANY information that helps fill "${slot_type}".
- If the page has NOTHING to do with this slot, return {"is_complete": true, "paging_actions": [], "claims": []}.
- If the page mentions something related but trivial/obvious, also return empty claims.
- Only proceed to Step 2 if the page contains CONCRETE, SPECIFIC evidence for this slot.

=== STEP 2: COMPLETENESS CHECK ===
Determine if the slot-relevant content is self-contained on this page:
- All sentences are whole (no mid-sentence breaks at page edges)
- Any table has both header and footer rows visible
- Any diagram is fully visible (not cut at page edges)
- Cross-references to non-adjacent locations (e.g., "see Figure X elsewhere in the doc") are NOT a reason to flag the page as incomplete; treat the current page as the best available evidence in such cases.

If incomplete due to ADJACENT page boundaries (the missing content is on the immediately previous or next page), set is_complete=false and provide paging_actions (see schema below).

=== STEP 3: SLOT-SPECIFIC EXTRACTION ===
${slot_extraction_rules}

=== STEP 4: SELF-VALIDATE BEFORE EMITTING A CARD ===
For each candidate card, silently re-check the input page. Keep it only if every
actor, condition, action, response, and outcome in its content is visible on
this page. Remove any unsupported qualifier rather than guessing. The input page
has index 0. A card must identify that index in supporting_page_indices.

Then decide coverage for the CURRENT slot: which essential aspects are supported
by the verified cards, and which essential aspects remain unavailable. Do not
call optional detail a missing essential aspect.

=== OUTPUT SCHEMA ===
Return ONLY valid JSON:
{
  "is_complete": true/false,
  "paging_actions": [],
  "claims": [
    {
      "content": "specific factual claim with concrete details",
      "supporting_page_indices": [0],
      "location": "where on the page (e.g., 'paragraph 3', 'table row 2', 'first diagram')",
      "relevance": "why this specific detail matters for the current slot",
      "region_type": "text" / "table" / "diagram"
    }
  ],
  "coverage": {
    "status": "sufficient" / "need_adjacent_page" / "need_retrieval" / "no_evidence",
    "covered_aspects": ["short aspect label"],
    "missing_essential_aspects": ["short unmet essential aspect"]
  }
}

${paging_actions_schema}

CRITICAL:
- If is_complete is false, claims MUST be [].
- If the page has NO specific evidence for this slot, claims MUST be [].
- Each claim must be a CONCRETE, self-contained fact, not a summary.
- supporting_page_indices is mandatory for every claim and may contain only 0.
- If is_complete is false, coverage.status MUST be "need_adjacent_page".
""")

EVIDENCE_COLLECTOR_REGION = Template("""\
You are an Evidence Collector reading a REGION (multiple consecutive pages) from a requirements document.
The region was assembled because content spans page boundaries OR multiple pages were retrieved together.
Your task: extract evidence specifically for filling the "${slot_type}" slot from the merged region.

Primary Actor: ${actor}
Use Case: ${use_case_name}
Current Slot: ${slot_type} — ${slot_description}

Already Known from Prior Slots:
${filled_slots_json}

Region Pages: ${region_pages}

Evidence aspects actively sought this round (may be empty when no prior gap is known):
${required_aspects_json}

=== STEP 1: RELEVANCE GATE ===
Decide if this region contains ANY information that helps fill "${slot_type}".
- If the region has NOTHING to do with this slot, return {"is_complete": true, "paging_actions": [], "claims": []}.
- Only proceed if the region contains CONCRETE, SPECIFIC evidence for this slot.

=== STEP 2: COMPLETENESS CHECK ===
Even though pages were merged, the slot-relevant content may still be incomplete:
- A sentence/table/diagram may extend beyond the region's outermost pages

If incomplete due to ADJACENT page boundaries (more content on the page immediately before/after the region), set is_complete=false and provide paging_actions to extend the region. Cross-references to non-adjacent locations are NOT a reason to flag the region as incomplete.

=== STEP 3: SLOT-SPECIFIC EXTRACTION ===
${slot_extraction_rules}

=== STEP 4: SELF-VALIDATE BEFORE EMITTING A CARD ===
For each candidate card, silently re-check every proposition against the input
region. Keep it only if all material facts are visible in the page(s) named by
supporting_page_indices. Use 0-based INPUT PAGE indices, not document page
numbers. Do not let a fact from one page make an unsupported claim on another.

Then decide coverage for the CURRENT slot: report verified covered aspects and
only essential remaining gaps. A gap that needs a neighbouring page must use
need_adjacent_page; a gap requiring another retrieval query uses need_retrieval.

=== OUTPUT SCHEMA ===
Return ONLY valid JSON:
{
  "is_complete": true/false,
  "paging_actions": [],
  "claims": [
    {
      "content": "specific factual claim with concrete details",
      "supporting_page_indices": [0],
      "location": "where in the region (e.g., 'first image paragraph 2', 'second image table top')",
      "relevance": "why this specific detail matters for the current slot",
      "region_type": "text" / "table" / "diagram" / "diagram_split"
    }
  ],
  "coverage": {
    "status": "sufficient" / "need_adjacent_page" / "need_retrieval" / "no_evidence",
    "covered_aspects": ["short aspect label"],
    "missing_essential_aspects": ["short unmet essential aspect"]
  }
}

${paging_actions_schema}

CRITICAL:
- If is_complete is false, claims MUST be [].
- If the region has NO specific evidence for this slot, claims MUST be [].
- Each claim must be a CONCRETE, self-contained fact, not a summary.
- supporting_page_indices is mandatory for every claim and must index an input page.
- If is_complete is false, coverage.status MUST be "need_adjacent_page".
""")

# --- Answer Generator ---

ANSWER_GENERATOR = Template("""\
You are a Use Case Author. Your task is to write the "${slot_type}" slot of a Cockburn
fully-dressed use case for ${actor}, based on the evidence cards provided.

Primary Actor: ${actor}
Use Case: ${use_case_name}
Goal (THIS use case must describe how ${actor} achieves this): ${goal_text}
Slot: ${slot_type} — ${slot_description}

Evidence Cards Available:
${evidence_cards_json}

Already Filled Slots (for context):
${filled_slots_json}

Current Slot Content (to revise/improve):
${current_slot_json}

Cockburn Template Guidance:
${slot_guidance}

${slot_specific_hint}

=== GOAL-ALIGNMENT GATE (read this BEFORE selecting evidence) ===
Step 0: identify which subset of the available evidence cards describes the CORE
interaction that achieves the stated Goal above.

Reject evidence cards that describe processes ADJACENT to but NOT PART OF the
Goal-achieving interaction.

Concrete examples:
- Goal "place bids": Cards describing "QuickBid button", "SmartBid confirmation",
  "bid history update" ARE main-flow evidence. Cards describing "view SKU history",
  "post-auction 2nd-chance offer", "OPL daily batch", "payment after winning" are
  ADJACENT processes — they belong to different use cases (admin lookup,
  2nd-chance auction, checkout) and should NOT be selected for the main flow of
  "place bids".
- Goal "checkout won items": Cards describing "select shipping", "choose payment
  method" ARE main-flow. Cards describing "place bid", "bid confirmation popup"
  are upstream and NOT part of this use case's main flow.

If after filtering you have NO evidence card describing the Goal-achieving
interaction (even though the pool contains many cards about ADJACENT processes),
return:
{"status": "goal_evidence_missing", "missing_aspects": ["<what's missing>"]}

Do NOT fabricate a flow from adjacent evidence just because the pool is large.

=== MINIMAL-SUFFICIENT AUTHORING ===
Write the smallest non-redundant set of atomic claims that covers the essential
Cockburn meaning of this slot. More evidence does NOT imply more claims.
- Keep a number, threshold, policy or downstream effect only when it directly changes
  this use-case's entry condition, actor-system interaction, branch, error handling,
  or immediate successful result.
- Do not turn every system capability or UI description into a precondition.
- Describe ONE canonical successful instance in main_flow. If the actor can choose
  among categories (for example address, payment method, or email preferences),
  model the choice once and place category-specific behavior in alternatives; never
  serialize all categories as if every user performs all of them in one instance.
- Postconditions contain immediate success states only, not cancellation outcomes,
  exception outcomes, later reports, customer-service escalation or remote side effects.
- If two cards support the same fact, write one claim with the best supporting refs.

=== EVIDENCE SELECTION STRATEGY ===
After the Goal-alignment gate, from the surviving cards:
1. Pick the MOST SPECIFIC card for each claim (concrete numbers/conditions over vague descriptions).
2. Prefer cards directly addressing this slot over cards loosely related.
3. If multiple cards say the same thing, pick the one with highest confidence (see card.confidence) or the most detailed content.
   NOTE: confidence differences between cross-page cards (0.85) and single-page cards (1.0) are bookkeeping artifacts — do NOT let them override Goal-alignment.
4. Discard cards whose content does not specifically support any claim you write.
5. A claim may reference MULTIPLE cards if they jointly support it (e.g., one for the action, one for the timing constraint).

=== CRITICAL RULES ===
1. Every claim you write MUST reference at least one evidence card by its card_id.
   A claim without evidence_refs is INVALID.
2. If no evidence card supports a claim, DO NOT write that claim.
3. If you have no evidence at all for this slot, return {"status": "no_evidence"}.
4. If the only available evidence is about adjacent processes (not the Goal),
   return {"status": "goal_evidence_missing", "missing_aspects": [...]}.
5. For sequential slots (main_flow, alternative_flow, exception_flow), use numbered steps.
6. Do NOT fabricate facts not present in any evidence card.
7. Return covered_aspect_ids and missing_aspects. Aspect IDs may be short semantic labels
   that you create for this slot (for example "MF-entry", "MF-core-action", "MF-success").
   missing_aspects must contain only ESSENTIAL unanswered information, not optional details.
8. For the goal slot, also return scope_contract. It is an internal planning boundary,
   not an additional factual claim. Derive it only from the use-case name, actor and cited
   Goal evidence. It must describe one canonical successful use-case instance.
9. In exception_flow, never manufacture a retry, return-to-step, continuation or
   termination action merely to make the branch look complete. A documented rejection,
   prevention, cancellation, warning or other explicit failure state may itself be the
   terminal branch outcome. If the subsequent recovery is undocumented, keep only the
   supported condition/response and list the recovery gap in missing_aspects.
10. Do NOT add consequences, terminations, downstream effects, or follow-on
    state changes that are NOT explicitly stated in the cited evidence card.
    The card supports the listed precondition / condition / action / response;
    it does NOT license claims about what happens NEXT, whether the use case
    terminates, or system state after the cited condition. If such effects
    matter, cite a separate evidence card that supports them; otherwise omit
    them. (Targeted at preventing NEU/contradiction in exception_flow and
    postconditions where the LLM tends to extend the claim beyond the card.)
11. Preserve evidence-supported DETAIL COVERAGE. Do not omit, merge away, or
    generalize an independent behavior merely to make the Detail-UC shorter.
    When distinct cited cards support different choices, branch conditions,
    outcomes, or observable results that belong to this slot, retain each as a
    separate minimal claim. Merge only genuine duplicates. In particular,
    alternative_flow must retain distinct supported alternatives rather than
    collapsing them into a generic "other options" statement.
12. Keep audit metadata out of business claims. Never write wording such as
    "not specified in evidence", "recovery gap", or "continuation unknown"
    inside a claim. State only the documented condition/response; record an
    undocumented essential continuation or exit solely in missing_aspects.

=== OUTPUT SCHEMA ===
Return ONLY valid JSON.

For simple slots (goal, primary_actor, trigger):
{
  "raw_text": "the slot content",
  "evidence_refs": ["EC-XX-001"],
  "covered_aspect_ids": ["goal"],
  "missing_aspects": [],
  "scope_contract": {
    "goal_statement": "...",
    "canonical_success_scenario": "...",
    "start_boundary": "...",
    "success_boundary": "...",
    "in_scope_actions": ["..."],
    "excluded_adjacent_actions": ["..."],
    "variant_dimensions": ["..."],
    "evidence_refs": ["EC-XX-001"]
  }
}

For list slots (preconditions, postconditions):
{
  "claims": [
    {"content": "...", "evidence_refs": ["EC-XX-001"]}
  ],
  "covered_aspect_ids": ["..."],
  "missing_aspects": []
}

For sequential slots (main_flow, alternative_flow, exception_flow):
{
  "claims": [
    {"step_number": 1, "content": "...", "evidence_refs": ["EC-XX-001"]}
  ],
  "covered_aspect_ids": ["..."],
  "missing_aspects": []
}
""")

# --- Convergence Check ---

CONVERGENCE_CHECK = Template("""\
You are comparing two consecutive versions of the "${slot_type}" slot for the same use case.
Version A is the PREVIOUS version. Version B is the NEW version (after one more round of evidence gathering).
Determine if Version B is SUBSTANTIALLY different from Version A.

The Goal of this use case is: ${goal_text}

Substantial differences:
- Different factual claims or data values
- Missing or added steps/conditions/actors
- Changed actor or system behavior
- Changed logical order of steps
- The new version's flow now achieves the stated Goal where the previous version did
  not, OR vice versa (Goal-alignment flipped) — this is ALWAYS substantial

NOT substantial differences:
- Rewording the same fact
- Different sentence structure with same meaning
- Minor grammatical changes
- Synonym substitutions

Version A (PREVIOUS):
${version_a_json}

Version B (NEW):
${version_b_json}

Return ONLY valid JSON:
{"substantially_changed": true/false, "summary": "brief description of the substantive change, or 'no change'"}
""")

# ============================================================
# DRA Pass: Detect (Vertical / Lateral) + Repair (Claim Patch)
# ============================================================
# Vertical 组：preconditions → trigger → main_flow → postconditions 时间线贯通（3 条规则）
# Lateral 组：以 main_flow 为锚的 4 条对照规则
# 每组一次 LLM 调用，让 LLM 在全局上下文里去重 + 排序

CONSISTENCY_DETECT_VERTICAL = Template("""\
You are reviewing a Cockburn use case for cross-slot consistency.
Focus: VERTICAL flow continuity (preconditions → trigger → main_flow → postconditions).

Use Case: ${use_case_name}
Primary Actor: ${actor}

=== SLOTS UNDER REVIEW ===
preconditions:
${preconditions_json}

trigger:
${trigger_json}

main_flow:
${main_flow_json}

postconditions:
${postconditions_json}

=== BACKGROUND SLOTS (read-only context) ===
goal:
${goal_json}

primary_actor:
${primary_actor_json}

=== RULES TO CHECK ===
1. trigger_preconditions_coverage — Every independent external state that MUST already
   hold before the actor can initiate the Trigger must have a corresponding entry in
   Preconditions. Generic UI availability and navigation embodied by the Trigger itself
   are NOT missing preconditions. However, when the Trigger explicitly starts from a
   role-restricted administration workspace/menu/module, preserve one minimal evidence-
   backed entry-context claim (for example, the actor is in or has access to that admin
   workspace). Do not expand it into separate availability claims for every named page.
   The incoming message, click, selection, detection or other event that CONSTITUTES the
   Trigger is not a missing precondition and need not be duplicated there.
2. mainflow_trigger_continuity — Main Flow step 1 must be logically compatible with and
   continue from the Trigger. A concise restatement of the initiating action for readable
   context is not itself a violation; report only a real gap, contradiction or restart of
   a different scenario.
3. mainflow_postconditions_reachability — The Main Flow must reasonably lead to each
   Postcondition. A reachable UI/display/result state is allowed; do not demand that every
   postcondition be persistent, and do not flag a reachable state merely because the last
   main-flow step already mentions it.

=== INSTRUCTIONS ===
- Look at all four review slots together. If a single root cause triggers more than one rule, report it as ONE issue with the most relevant rule_violated.
- Decide root_slot using the dependency direction: when in doubt, the LATER slot in (preconditions → trigger → main_flow → postconditions) is the one to fix, because it should be aligned to upstream content. Exceptions: if main_flow is clearly the authoritative behavior and trigger is sketchy, root_slot=trigger.
- target_claim_index points to the specific claim in root_slot that is broken (0-based). Use -1 only for slot-wide problems.
- evidence_hint describes what new evidence would resolve the issue. Set to null if existing claims/evidence already provide what's needed.
- Sort issues by severity (most severe first).
- **SELF-CHECK before output**: For each issue, read your own root_cause text. If your reasoning ends by concluding the rule actually PASSES (phrases like "this rule is likely PASS", "no issue here", "actor is present", "the rule is satisfied"), you MUST exclude that issue from the output array. Do NOT emit an issue whose root_cause refutes itself.

=== OUTPUT SCHEMA ===
Return ONLY a JSON array. Empty array means no issues found.

[
  {
    "rule_violated": "trigger_preconditions_coverage",
    "root_slot": "preconditions",
    "target_claim_index": -1,
    "root_cause": "Trigger assumes the Link16 terminal is powered on, but Preconditions does not mention this.",
    "fix_intent": "Add a precondition asserting that the Link16 terminal is powered on before authentication starts.",
    "evidence_hint": "documentation describing terminal startup or power-on requirements"
  }
]
""")


CONSISTENCY_DETECT_LATERAL = Template("""\
You are reviewing a Cockburn use case for cross-slot consistency.
Focus: LATERAL alignment around main_flow (actor presence, goal alignment, branch coverage).

Use Case: ${use_case_name}
Primary Actor: ${actor}

=== ANCHOR SLOT ===
main_flow:
${main_flow_json}

=== SLOTS COMPARED AGAINST main_flow ===
goal:
${goal_json}

primary_actor:
${primary_actor_json}

alternative_flow:
${alternative_flow_json}

exception_flow:
${exception_flow_json}

=== RULES TO CHECK ===
1. actor_mainflow_presence — The Primary Actor must appear (by name, exact role phrase,
   or pronoun) in Main Flow. A qualified phrase such as "receiving C2 JU" counts as an
   occurrence of actor "C2 JU".
2. goal_mainflow_alignment — The Goal must be consistent with the complete behavior described in Main Flow.
3. altflow_diverge_points — Every divergence point referenced in Alternative Flow must
   identify an existing step in Main Flow. The alternative's branch-specific condition
   need not already be stated in the main step; only flag a nonexistent/out-of-range or
   logically unrelated attachment point.
4. exceptionflow_exit_coverage — Every branch in Exception Flow must have a clear
   outcome: return to a Main Flow step, terminate or hand off, OR an explicit terminal
   failure/prevention state (for example operation rejected, bid not placed, order
   cancelled, access blocked, or an error state shown with no continuation claimed).
   Do not demand an invented retry/return action when the branch already ends in a clear
   documented failure state.

=== INSTRUCTIONS ===
- All four rules pivot on main_flow. Look at the four lateral slots together. If main_flow itself is the root cause for multiple rules, merge into ONE issue with root_slot=main_flow.
- Use the dependency direction for root_slot, with one IMPORTANT exception:
  * **Default rule**: actor / goal are upstream constraints, so when they conflict with main_flow, root_slot=main_flow (let downstream adapt).
  * **Exception (Fix 7.3)**: When the conflict is clearly caused by goal being TOO BROAD or conflating MULTIPLE use cases (e.g., goal mentions activities outside the use case name, or goal covers actions that span multiple distinct flows), root_slot=goal — the goal itself needs narrowing. Signs of an over-broad goal: it lists multiple "and" conjunctions of distinct actions; it mentions both pre-action and post-action processes; it references downstream use cases by name.
  * alt_flow / exception_flow are downstream branches, so when they conflict with main_flow, root_slot is the conflicting branch slot.
- target_claim_index points to the specific claim in root_slot that is broken (0-based). Use -1 only for slot-wide problems.
- evidence_hint describes what new evidence would resolve the issue. Set to null if existing claims/evidence already provide what's needed.
- Sort issues by severity (most severe first).
- **SELF-CHECK before output (Polish A)**: For each issue, read your own root_cause text. If your reasoning ends by concluding the rule actually PASSES (phrases like "this rule is likely PASS", "no issue here", "actor is present", "the rule is satisfied"), you MUST exclude that issue from the output array. Do NOT emit an issue whose root_cause refutes itself.
- **goal_mainflow_alignment scope (Polish B)**: When checking this rule, IGNORE goal phrases describing long-term / asynchronous / system-side ongoing behavior (e.g., "automatic incremental bidding", "system continuously monitors X", "recurring background process"). These describe behavior AFTER the main flow's terminal step and belong to separate use cases. Only flag goal_mainflow_alignment when main flow fails to cover an action the actor explicitly performs within a single use case invocation.
- **actor presence semantics (Polish A)**: The Primary Actor must be explicitly named in subject or active-voice form in at least one main_flow step. Subject form (e.g., "The Customer places a bid"), object form (e.g., "the system prompts the Customer"), possessive form (e.g., "the Customer's profile is verified") and pronoun reference (e.g., "they confirm") all count. EXCEPTION: passive constructions where the system is the grammatical subject and the actor is implicit only (e.g., "the system redirects to sign-in", "verification is performed", "an email is sent") do NOT count as actor presence — the actor must be named explicitly at least once. Do NOT flag actor_mainflow_presence if the actor appears by name, exact role phrase, pronoun, or possessive anywhere in the main flow.
=== OUTPUT SCHEMA ===
Return ONLY a JSON array. Empty array means no issues found.

[
  {
    "rule_violated": "altflow_diverge_points",
    "root_slot": "alternative_flow",
    "target_claim_index": 1,
    "root_cause": "Alternative flow claim 1 says 'diverges from step 7' but main_flow only has 5 steps.",
    "fix_intent": "Either rewrite the alt_flow claim to point to a real main_flow step, or remove it.",
    "evidence_hint": null
  }
]
""")


CLAIM_PATCH = Template("""\
You are repairing a single Cockburn slot to resolve cross-slot inconsistencies.

Use Case: ${use_case_name}
Primary Actor: ${actor}
Goal: ${goal_text}

=== SLOT TO REPAIR ===
slot: ${slot_type} — ${slot_description}

Current claims (0-indexed):
${slot_json}

=== AVAILABLE EVIDENCE CARDS ===
${evidence_cards_json}

=== ISSUES TO FIX (one or more on the same target claim) ===
${issues_block}

=== ALLOWED ACTIONS ===
1. modify — Change a single existing claim. Provide target_claim_index and the full new_claim object (content + evidence_refs). evidence_refs MUST come from the evidence cards above. step_number is preserved from the original claim and CANNOT be changed by modify (use remove+add to reorder).
2. remove — Delete a single existing claim. Provide target_claim_index. For ordered slots, subsequent step_numbers will be auto-decremented to keep the sequence contiguous.
3. add    — Add a new claim. Provide new_claim object (content + evidence_refs). evidence_refs MUST come from the evidence cards above.
   - For UNORDERED slots (preconditions, postconditions, no step_number on existing claims): the new claim is appended at the end.
   - For ORDERED slots (main_flow, alternative_flow, exception_flow — any claim has step_number): you MUST set new_claim.step_number to the position you want the new step at (1-based). If that step_number is already taken, all existing claims with step_number >= that value will be auto-incremented by 1 to make room.
4. need_evidence — None of the existing evidence cards support an atomic fix. Provide a single evidence_query string describing what to retrieve.

=== RULES ===
- Pick the action that resolves all listed issues with the MINIMUM disruption to the slot. Prefer modify over add over remove over need_evidence. Never rewrite the whole slot: claims not targeted by this issue must remain unchanged.
- Never fabricate facts not present in any evidence card.
- For ORDERED slots, always emit step_number on add (use the target position; the orchestrator will renumber subsequent claims if needed).
- evidence_refs must be a non-empty list of valid card_ids from the cards above (unless action=remove or action=need_evidence).
- If you must choose between leaving the slot inconsistent vs. removing a claim that has supporting evidence, prefer remove and let the orchestrator surface a quality flag.
- modify and remove are valid ONLY when target_claim_index is one concrete existing
  0-based claim index. Never use -1 for modify/remove. If the detector reported
  a slot-wide issue, choose a concrete claim to change, add a minimal missing
  claim, or request evidence; do not request a whole-slot rewrite.

=== ADDITIONAL CONSTRAINTS (anti-regression) ===
- When action=modify on a claim in main_flow, alternative_flow, or
  exception_flow, do NOT change the actor subject (the entity performing the
  action) from the original claim unless the cited evidence card also uses
  a different actor. Read both the original claim's actor and the new
  claim's actor: if they differ and the evidence card does not justify
  the change, keep the original actor. (Prevents actor cross-contamination
  like "user" -> "Auctioneer" when only "user" is in the evidence.)
- When action=modify or add to alternative_flow, the modified/new claim
  MUST reference a divergence point that actually exists in the current
  main_flow (a real step number, or a concrete diverging condition that
  matches a main flow action). Read the main_flow claims list first; do
  not invent "diverges from Step N" references that point to non-existent
  or out-of-range main flow steps. (Prevents R6 altflow_diverge_points
  regressions when DRA makes alt_flow claims more specific.)
- For R1 trigger/preconditions issues, prefer adding or minimally modifying a
  cited prerequisite. Do not copy the trigger event itself into Preconditions.
- For R7 exception-exit issues, modify/add an exit only when a cited card
  explicitly supports the outcome. Otherwise use need_evidence; never invent a
  return, retry, continuation, hand-off, or termination just to satisfy R7.

=== OUTPUT SCHEMA ===
Return ONLY one JSON object.

For modify:
{
  "action": "modify",
  "target_claim_index": 2,
  "new_claim": {"content": "...", "evidence_refs": ["EC-MF-003"]}
}
(step_number is preserved from the original claim and cannot be changed via modify.)

For remove:
{
  "action": "remove",
  "target_claim_index": 5
}

For add to UNORDERED slot (preconditions / postconditions):
{
  "action": "add",
  "new_claim": {"content": "...", "evidence_refs": ["EC-PC-001"]}
}

For add to ORDERED slot (main_flow / alternative_flow / exception_flow) — step_number REQUIRED:
{
  "action": "add",
  "new_claim": {"content": "...", "evidence_refs": ["EC-MF-007"], "step_number": 3}
}

For need_evidence:
{
  "action": "need_evidence",
  "evidence_query": "Link16 terminal startup procedure"
}
""")
