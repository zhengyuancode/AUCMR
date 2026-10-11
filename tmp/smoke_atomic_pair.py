import json
import tempfile
from pathlib import Path

import run
from crest.schemas import Claim, EvidenceCard, SlotContent, UseCase


uc = UseCase(use_case_name="Atomic Pair", actor="Tester")
uc.slots["goal"] = SlotContent(
    slot="goal", claims=[Claim("Test the result pair.", ["E1"])],
)
uc.all_evidence_cards = [EvidenceCard("E1", "Test the result pair.", [1], [])]

with tempfile.TemporaryDirectory() as directory:
    root = Path(directory)
    main_path = Path(run.write_crest_result(uc, run.FULL, root, "project"))
    evidence_path = main_path.with_name(main_path.stem + "-evidence.json")
    assert run._result_pair_is_valid(main_path, evidence_path)
    main = json.loads(main_path.read_text(encoding="utf-8"))
    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    assert main["artifact_revision_id"] == evidence["artifact_revision_id"]
    evidence["artifact_revision_id"] = "half-committed"
    evidence_path.write_text(json.dumps(evidence), encoding="utf-8")
    assert not run._result_pair_is_valid(main_path, evidence_path)

print("ATOMIC_RESULT_PAIR_SMOKE_PASS")
