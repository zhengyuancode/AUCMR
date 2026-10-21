"""Fixed synthetic materials. Mock expectations are plumbing data, NOT judge validation."""
import copy
import json

from .common import PROMPT_VERSION, PROTOCOL_VERSION, SLOTS
from .evaluation import MockClient
from .inputs import adapt_bytes

FIXTURE_VERSION = "whole_case_fixtures_1"
MOCK_MODEL = {"binding_status": "MOCK", "provider": "FIXTURE", "model_id": "fixed_responses",
              "model_revision": FIXTURE_VERSION, "parameters": {}}


def fixture_config():
    return {"protocol_version": PROTOCOL_VERSION, "prompt_version": PROMPT_VERSION,
            "model": copy.deepcopy(MOCK_MODEL), "live_authorized": False,
            "data_origin": "FIXTURE", "research_data_status": "NOT_RESEARCH_DATA",
            "input_batch_id": FIXTURE_VERSION, "methods": ["FIXTURE_METHOD"],
            "targets": [{"task_id": f"F{i:02d}", "source_project": "SYNTHETIC",
                         "name": name, "primary_actor": "Operator"}
                        for i, name in enumerate(("Contradiction", "Consistent sequence", "Legal branches",
                                                  "Insufficient information", "Valid empty", "Explicit refusal",
                                                  "Malformed final slots", "Bad JSON", "Missing file",
                                                  "Bad model JSON", "Invalid assertion locator",
                                                  "Partial with confirmed conflict", "Mock transport failure"), 1)]}


def _conflict():
    return {"refs": [{"slot": "main_flow", "assertion_id": "main_flow:0"},
                     {"slot": "postconditions", "assertion_id": "postconditions:0"}],
            "condition_or_path": "The successful completion of the same save operation",
            "incompatible_content": "The saved record both exists and does not exist after successful completion.",
            "rationale": "The same record has mutually exclusive final existence states on the same path."}


def _unresolved():
    return {"refs": [{"slot": "main_flow", "assertion_id": "main_flow:0"}],
            "condition_or_path": "The unspecified completion branch",
            "question": "Does completing the operation delete or retain the temporary record?",
            "reason": "The text does not establish which completion branch applies."}


def fixture_cases():
    cfg = fixture_config()
    complete = {"review_status": "COMPLETE", "conflicts": [], "unresolved": []}
    cases = []
    for i, target in enumerate(cfg["targets"], 1):
        data = {"use_case_name": target["name"], "actor": target["primary_actor"],
                "slots": {s: {"claims": []} for s in SLOTS}}
        def claims(slot, *texts):
            data["slots"][slot]["claims"] = [
                {"content": text, **({"step_number": n + 1} if slot.endswith("flow") else {})}
                for n, text in enumerate(texts)]
        claims("goal", "Save a record.")
        claims("main_flow", "On success the system saves record R, which exists after completion.")
        claims("postconditions", "After successful completion record R exists.")
        response = copy.deepcopy(complete)
        expected = "COMPLETE_NO_CONFLICTS"
        if i in (1, 12):
            claims("postconditions", "After successful completion record R does not exist.")
            response["conflicts"] = [_conflict()]
            expected = "COMPLETE_WITH_CONFLICTS"
        if i == 2:
            claims("preconditions", "Record R does not exist before saving.")
            claims("trigger", "The operator requests saving.")
        if i == 3:
            claims("main_flow", "If the operator confirms, save record R.")
            claims("alternative_flow", "If the operator cancels, leave R absent.")
            claims("postconditions", "On confirmation R exists; on cancellation R is absent.")
        if i == 4:
            claims("main_flow", "The system completes the operation using the selected branch.")
            claims("postconditions", "The temporary record may remain depending on the selected branch.")
            response.update(review_status="PARTIAL", unresolved=[_unresolved()])
            expected = "PARTIAL_UNRESOLVED"
        if i == 5:
            data["slots"] = {s: {"claims": []} for s in SLOTS}
            expected = "NO_EVALUABLE_CONTENT"
        if i == 6:
            data["slots"] = {"goal": {"raw_text": ""}}
            data["quality_flags"] = [{"flag_type": "unresolvable_use_case"}]
            expected = "NO_EVALUABLE_CONTENT"
        if i == 7:
            data["slots"]["main_flow"]["claims"] = "damaged"
            expected = "TECHNICAL_FAILURE"
        raw = json.dumps(data, ensure_ascii=False).encode("utf-8")
        if i == 8:
            raw = b"{broken"
            expected = "TECHNICAL_FAILURE"
        artifact = adapt_bytes(raw, target, "FIXTURE_METHOD", FIXTURE_VERSION, data_origin="FIXTURE")
        if i == 9:
            artifact.update(input_status="MISSING_FILE", input_hash=None, assertions=[], empty_slots=[])
            expected = "TECHNICAL_FAILURE"
        if i == 10:
            response = "{bad response"
            expected = "TECHNICAL_FAILURE"
        if i == 11:
            issue = _conflict()
            issue["refs"][0]["assertion_id"] = "main_flow:999"
            response["conflicts"] = [issue]
            expected = "TECHNICAL_FAILURE"
        if i == 12:
            response.update(review_status="PARTIAL", unresolved=[_unresolved()])
            expected = "PARTIAL_UNRESOLVED"
        if i == 13:
            response = RuntimeError("Synthetic transport failure, no connection attempted")
            expected = "TECHNICAL_FAILURE"
        cases.append({"artifact": artifact, "response": response, "expected_status": expected,
                      "semantic_expectation_status": "HUMAN_CONSTRUCTED_NOT_MODEL_VALIDATED"})
    return cfg, cases


def client_for(case, config):
    return MockClient(case["response"], config["model"])
