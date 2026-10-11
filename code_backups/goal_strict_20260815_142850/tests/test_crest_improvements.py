import json

import unittest


try:
    from AUCMR.crest.agents import (
        ModelProvider,
        _apply_patch,
        _enforce_branch_slot_ownership,
        _select_active_cards,
        answer_generator,
        query_designer,
    )
    from AUCMR.crest.orchestrator import CRESTOrchestrator
    from AUCMR.crest.schemas import CockburnSlot, EvidenceCard, Claim, SlotContent, UseCase
except ImportError:  # server runs tests from the AUCMR repository root
    from crest.agents import (
        ModelProvider,
        _apply_patch,
        _enforce_branch_slot_ownership,
        _select_active_cards,
        answer_generator,
        query_designer,
    )
    from crest.orchestrator import CRESTOrchestrator
    from crest.schemas import CockburnSlot, EvidenceCard, Claim, SlotContent, UseCase


def _provider(llm_responses):
    responses = iter(llm_responses)

    def llm_call(_prompt, _images, _assistant, _overlap):
        return next(responses)

    def lvlm_call(_prompt, _images, _assistant, _overlap):
        return json.dumps({"is_complete": True, "claims": [], "paging_actions": []})

    return ModelProvider(llm_call=llm_call, lvlm_call=lvlm_call)


def _card(card_id, text, page, confidence=0.9):
    return EvidenceCard(
        card_id=card_id,
        content=text,
        source_pages=[page],
        source_locations=[],
        confidence=confidence,
        source_doc="doc",
    )


def test_structured_query_plan_can_stop_with_reuse():
    provider = _provider([
        json.dumps({
            "reuse_card_ids": ["EC-G-001"],
            "missing_aspects": [],
            "queries": [],
            "stop": True,
            "reason": "existing goal evidence covers the trigger",
        })
    ])
    goal = SlotContent(
        slot="goal",
        claims=[Claim("Customer places a bid", ["EC-G-001"])],
        evidence_card_ids=["EC-G-001"],
    )
    plan = query_designer(
        actor="Customer",
        use_case_name="Place Bid on Auction Item",
        slot=CockburnSlot.TRIGGER,
        filled_slots={"goal": goal},
        current_slot_content=SlotContent(slot="trigger"),
        history_queries=[],
        history_page_paths=set(),
        inconsistency_hint="",
        retrieval_fn=lambda *_args: (_ for _ in ()).throw(AssertionError("retrieval called")),
        file_names=["doc"],
        topk=5,
        provider=provider,
        prior_evidence_cards=[_card("EC-G-001", "Click Place Bid to start bidding", 10)],
    )
    assert plan.should_stop is True
    assert plan.reuse_card_ids == ["EC-G-001"]
    assert plan.queries == []


def test_retrieval_failure_aborts_instead_of_becoming_goal_fast_fail():
    provider = _provider([json.dumps({
        "reuse_card_ids": [],
        "missing_aspects": ["goal evidence"],
        "queries": ["place bid auction item"],
        "stop": False,
        "reason": "retrieve supporting pages",
    })])
    try:
        query_designer(
            actor="Customer",
            use_case_name="Place Bid on Auction Item",
            slot=CockburnSlot.GOAL,
            filled_slots={},
            current_slot_content=SlotContent(slot="goal"),
            history_queries=[],
            history_page_paths=set(),
            inconsistency_hint="",
            retrieval_fn=lambda *_args: (_ for _ in ()).throw(KeyError("dim")),
            file_names=["doc"],
            topk=5,
            provider=provider,
            prior_evidence_cards=[],
        )
    except RuntimeError as exc:
        assert "retrieval failed" in str(exc)
    else:
        raise AssertionError("retrieval infrastructure failure was silently swallowed")


def test_active_card_selection_limits_and_deduplicates():
    own = [
        _card(f"EC-MF-{index:03d}", "Customer clicks the Place Bid button", index)
        for index in range(8)
    ]
    own.append(_card("EC-MF-999", "Customer confirms the bid amount", 20))
    selected = _select_active_cards(
        slot=CockburnSlot.MAIN_FLOW,
        own_cards=own,
        prior_cards=[],
        filled_slots={},
        use_case_name="Place Bid on Auction Item",
        budget=3,
    )
    assert len(selected) <= 3
    assert len([card for card in selected if "Place Bid button" in card.content]) == 1
    assert any("confirms the bid amount" in card.content for card in selected)


def test_goal_answer_captures_scope_and_gap_state():
    provider = _provider([
        json.dumps({
            "raw_text": "The customer places a bid on an auction item.",
            "evidence_refs": ["EC-G-001"],
            "covered_aspect_ids": ["goal"],
            "missing_aspects": [],
            "scope_contract": {
                "goal_statement": "Place a bid on one auction item",
                "canonical_success_scenario": "Submit and confirm one bid",
                "in_scope_actions": ["enter bid", "confirm bid"],
                "excluded_adjacent_actions": ["checkout won item"],
                "variant_dimensions": ["QuickBid or SmartBid"],
                "evidence_refs": ["EC-G-001"],
            },
        })
    ])
    result = answer_generator(
        actor="Customer",
        use_case_name="Place Bid on Auction Item",
        slot=CockburnSlot.GOAL,
        evidence_cards=[_card("EC-G-001", "The customer can place a bid", 10)],
        filled_slots={},
        current_slot_content=SlotContent(slot="goal"),
        provider=provider,
    )
    assert result.scope_contract["goal_statement"] == "Place a bid on one auction item"
    assert result.aspect_status_known is True
    assert result.missing_aspects == []
    assert result.evidence_card_ids == ["EC-G-001"]


def test_spatially_dispersed_same_goal_is_not_rejected():
    provider = _provider([
        json.dumps({
            "same_user_goal": True,
            "conflicting_actions": [],
            "reason": "All cards describe scheduling an auction event.",
        })
    ])
    orchestrator = CRESTOrchestrator(
        retrieval_fn=lambda *_args: [],
        file_names=["doc"],
        topk=5,
        provider=provider,
        total_doc_pages=145,
    )
    orchestrator._current_use_case_name = "Schedule Auction Event"
    content = SlotContent(
        slot="goal",
        claims=[Claim("Auctioneer schedules an auction event", ["EC-G-001", "EC-G-002"])],
        evidence_card_ids=["EC-G-001", "EC-G-002"],
    )
    cards = [
        _card("EC-G-001", "Create a new scheduled auction event", 14),
        _card("EC-G-002", "Edit and schedule the auction event", 127),
    ]
    assert orchestrator._goal_evidence_is_cohesive(content, cards) is True


def test_spatially_dispersed_conflicting_goals_trigger_refocus():
    provider = _provider([
        json.dumps({
            "same_user_goal": False,
            "conflicting_actions": ["place bid", "complete checkout"],
            "reason": "The cards support adjacent but distinct objectives.",
        })
    ])
    orchestrator = CRESTOrchestrator(
        retrieval_fn=lambda *_args: [],
        file_names=["doc"],
        topk=5,
        provider=provider,
        total_doc_pages=145,
    )
    orchestrator._current_use_case_name = "Place Bid on Auction Item"
    content = SlotContent(
        slot="goal",
        claims=[Claim("Customer bids and checks out", ["EC-G-001", "EC-G-002"])],
        evidence_card_ids=["EC-G-001", "EC-G-002"],
    )
    cards = [
        _card("EC-G-001", "Customer places a bid", 13),
        _card("EC-G-002", "Customer pays for a won item", 130),
    ]
    assert orchestrator._goal_evidence_is_cohesive(content, cards) is False


def test_model_provider_records_calls():
    provider = _provider(["{}"])
    provider.llm("test")
    stats = provider.stats_snapshot()
    assert stats["llm_calls"] == 1
    assert stats["lvlm_calls"] == 0
    assert stats["llm_seconds"] >= 0


def test_branch_slot_ownership_removes_only_preserved_exception_duplicates():
    uc = UseCase(use_case_name="Schedule Auction Event", actor="Auctioneer")
    uc.slots["alternative_flow"] = SlotContent(
        slot="alternative_flow",
        claims=[
            Claim(
                "Diverges from Step 5: apply a predefined event profile instead of entering dates manually.",
                ["EC-AF-001"],
                step_number=1,
            ),
            Claim(
                "Diverges from Step 6: identical starting and ending times display an error and highlight the data.",
                ["EC-AF-002"],
                step_number=2,
            ),
            Claim(
                "Diverges from Step 4: the system warns that a selected item has no image.",
                ["EC-AF-003"],
                step_number=3,
            ),
        ],
        evidence_card_ids=["EC-AF-001", "EC-AF-002", "EC-AF-003"],
        covered_aspect_ids=["AF-profile", "AF-date-error", "AF-image-warning"],
        missing_aspects=[],
        aspect_status_known=True,
    )
    uc.slots["exception_flow"] = SlotContent(
        slot="exception_flow",
        claims=[
            Claim(
                "At Step 6, identical starting and ending times display an error, highlight the data, and return to Step 5.",
                ["EC-EF-001"],
                step_number=1,
            )
        ],
        evidence_card_ids=["EC-EF-001"],
    )

    revised, hints = _enforce_branch_slot_ownership(uc)

    alternative = uc.slots["alternative_flow"]
    assert revised == ["alternative_flow"]
    assert len(hints) == 1
    assert [claim.step_number for claim in alternative.claims] == [1, 2]
    assert [claim.evidence_refs for claim in alternative.claims] == [
        ["EC-AF-001"],
        ["EC-AF-003"],
    ]
    assert alternative.evidence_card_ids == ["EC-AF-001", "EC-AF-003"]
    assert alternative.aspect_status_known is False
    assert alternative.covered_aspect_ids == []


def test_atomic_goal_patch_invalidates_stale_scope_metadata():
    goal = SlotContent(
        slot="goal",
        claims=[Claim("Configure product type as an event parameter", ["EC-G-001"])],
        raw_text="Configure product type as an event parameter",
        evidence_card_ids=["EC-G-001"],
        scope_contract={"goal_statement": "Configure product type"},
        covered_aspect_ids=["old-goal"],
        missing_aspects=[],
        aspect_status_known=True,
    )
    cards = {"EC-G-001": _card(
        "EC-G-001", "Product type is a search criterion", 10,
    )}
    success, reason = _apply_patch(
        goal,
        {
            "action": "modify",
            "target_claim_index": 0,
            "new_claim": {
                "content": "Use product type as a search criterion",
                "evidence_refs": ["EC-G-001"],
            },
        },
        detector_target_idx=0,
        card_map=cards,
    )
    assert success is True, reason
    assert goal.raw_text == "Use product type as a search criterion"
    assert goal.scope_contract == {}
    assert goal.aspect_status_known is False
    assert goal.covered_aspect_ids == []


class TestCrestImprovements(unittest.TestCase):
    """unittest wrappers keep the server test path dependency-free."""

    def test_structured_query_plan_can_stop_with_reuse(self):
        test_structured_query_plan_can_stop_with_reuse()

    def test_active_card_selection_limits_and_deduplicates(self):
        test_active_card_selection_limits_and_deduplicates()

    def test_goal_answer_captures_scope_and_gap_state(self):
        test_goal_answer_captures_scope_and_gap_state()

    def test_spatially_dispersed_same_goal_is_not_rejected(self):
        test_spatially_dispersed_same_goal_is_not_rejected()

    def test_spatially_dispersed_conflicting_goals_trigger_refocus(self):
        test_spatially_dispersed_conflicting_goals_trigger_refocus()

    def test_model_provider_records_calls(self):
        test_model_provider_records_calls()

    def test_branch_slot_ownership_removes_only_preserved_exception_duplicates(self):
        test_branch_slot_ownership_removes_only_preserved_exception_duplicates()

    def test_atomic_goal_patch_invalidates_stale_scope_metadata(self):
        test_atomic_goal_patch_invalidates_stale_scope_metadata()
