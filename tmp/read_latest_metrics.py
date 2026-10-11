import json
from pathlib import Path

for filename in ("aggregated_metrics_l1.json", "aggregated_metrics_l2.json"):
    path = Path("outputs") / filename
    print("FILE", filename, "mtime_ns", path.stat().st_mtime_ns)
    data = json.loads(path.read_text(encoding="utf-8"))
    for method, values in data.items():
        print(method, {
            key: values.get(key)
            for key in (
                "n_ucs", "l1_n_all", "l1_n_cite", "l1_n_ent", "l1_citcov",
                "l1_nli_g", "l1_chg", "l1_esc_per_uc", "l2_total_per_uc",
                "l2_all_8_rate", "l2_valid",
            )
        })
