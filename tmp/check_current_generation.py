import json
import time
from pathlib import Path

import run


root = Path("outputs")
cohort = json.loads((root / "evaluation_cohort_l1.json").read_text(encoding="utf-8"))
identities = {
    (str(row["project"]), str(row["use_case"]), str(row["actor"]))
    for row in cohort["valid_use_cases"]
}
directories = {
    run.FULL: root / "aucmr_full",
    run.WITHOUT_DRA: root / "crest_without_dra",
}
summary = {}
for method, directory in directories.items():
    completed = []
    for path in directory.rglob("*.json"):
        if path.name.endswith("-evidence.json"):
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        identity = (
            path.parent.name,
            str(data.get("use_case_name", "")),
            str(data.get("actor", "")),
        )
        evidence_path = path.with_name(path.stem + "-evidence.json")
        if (
            identity in identities
            and data.get("method") == method
            and data.get("generation_version") == run.CREST_GENERATION_VERSION
            and evidence_path.is_file()
            and run._result_pair_is_valid(path, evidence_path)
        ):
            completed.append((identity, path.stat().st_mtime))
    completed_ids = {identity for identity, _mtime in completed}
    latest = max((mtime for _identity, mtime in completed), default=None)
    summary[method] = {
        "complete": len(completed_ids),
        "remaining": len(identities - completed_ids),
        "latest_age_minutes": round((time.time() - latest) / 60, 1) if latest else None,
        "completed": [identity[1] for identity in sorted(completed_ids)],
        "remaining_items": [identity[1] for identity in sorted(identities - completed_ids)],
    }

print(json.dumps(summary, ensure_ascii=False, indent=2))
