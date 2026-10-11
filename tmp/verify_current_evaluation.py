import json
from pathlib import Path

for method, root in (
    ("AUCMR (CREST full)", Path("outputs/aucmr_full")),
    ("CREST w/o cross-slot", Path("outputs/crest_without_dra")),
):
    files = [path for path in root.rglob("*.json") if not path.name.endswith("-evidence.json")]
    versions = {json.loads(path.read_text(encoding="utf-8")).get("generation_version") for path in files}
    print("V5_SOURCE", method, len(files), sorted(versions))

for stage in ("l1", "l2"):
    with open(f"outputs/evaluation_audit_{stage}.json", encoding="utf-8") as handle:
        audit = json.load(handle)
    print("AUDIT_ROWS", stage, {method: len(rows) for method, rows in audit.items()})
