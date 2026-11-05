from crest.agents import _validate_detector_issues
from crest.prompts import CONSISTENCY_DETECT_LATERAL
from crest.schemas import Claim, SlotContent, UseCase


def case(exception=None):
    slots = {"main_flow": SlotContent("main_flow", claims=[Claim("Review claim status.")])}
    if exception is not None:
        slots["exception_flow"] = exception
    return UseCase("Review", slots=slots)


def issue(rule="exceptionflow_exit_coverage", root="main_flow"):
    return {"rule_violated": rule, "root_slot": root, "target_claim_index": -1,
            "root_cause": "Branch lacks an outcome.", "fix_intent": "Clarify the outcome."}


def test_absent_exception_has_no_r7_even_when_reported_on_main_flow():
    assert _validate_detector_issues([issue()], case()) == []


def test_coverage_metadata_is_not_an_exception_branch():
    uc = case(SlotContent("exception_flow", aspect_status_known=True,
                          covered_aspect_ids=["biometric-failure"], missing_aspects=["recovery"]))
    assert _validate_detector_issues([issue(root="exception_flow")], uc) == []
    other = issue("mainflow_trigger_continuity")
    actual = _validate_detector_issues([issue(), other], uc)
    assert len(actual) == 1 and actual[0]["rule_violated"] == other["rule_violated"]


def test_authored_exception_still_requires_exit():
    for slot in (SlotContent("exception_flow", claims=[Claim("Verification fails.")]),
                 SlotContent("exception_flow", raw_text="Verification fails.")):
        actual = _validate_detector_issues([issue(root="exception_flow")], case(slot))
        assert len(actual) == 1 and actual[0]["root_cause"] == "Branch lacks an outcome."


def test_prompt_marks_only_empty_exception_not_applicable():
    text = CONSISTENCY_DETECT_LATERAL.template
    assert "no authored claims or raw_text" in text
    assert "Nonempty branches\n   still require" in text
