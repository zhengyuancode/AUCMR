import json
from pathlib import Path

EXPECTED = "crest-monotonic-atomic-dra-v5-balanced"
errors = []
counts = []
for method_dir in ("aucmr_full", "crest_without_dra"):
    mains = [
        path for path in (Path("outputs") / method_dir).rglob("*.json")
        if not path.name.endswith("-evidence.json")
    ]
    counts.append((method_dir, len(mains)))
    for main_path in mains:
        evidence_path = main_path.with_name(f"{main_path.stem}-evidence.json")
        try:
            main = json.loads(main_path.read_text(encoding="utf-8"))
            evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
        except Exception as exc:
            errors.append((str(main_path), str(exc)))
            continue
        if (
            main.get("generation_version") != EXPECTED
            or evidence.get("generation_version") != EXPECTED
            or main.get("artifact_revision_id") != evidence.get("artifact_revision_id")
        ):
            errors.append((str(main_path), "version/revision mismatch"))

assert counts == [("aucmr_full", 14), ("crest_without_dra", 14)], counts
assert not errors, errors
print("V5_PAIR_VALIDATION_PASS", counts)
