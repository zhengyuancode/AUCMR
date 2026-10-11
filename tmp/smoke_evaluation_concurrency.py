import json
import tempfile
import threading
import time
from pathlib import Path

from eval.compute_metrics import UCMetrics
from eval.run_experiments import ExperimentRunner


class FakeComputer:
    def __init__(self):
        self.lock = threading.Lock()
        self.active = 0
        self.max_active = 0

    def compute_all(self, uc_path, ev_path, run_nli=False, run_l2=False):
        with self.lock:
            self.active += 1
            self.max_active = max(self.max_active, self.active)
        try:
            time.sleep(0.08)
            data = json.loads(Path(uc_path).read_text(encoding="utf-8"))
            return UCMetrics(data["use_case_name"], data["actor"])
        finally:
            with self.lock:
                self.active -= 1


with tempfile.TemporaryDirectory() as directory:
    root = Path(directory)
    result_map = {}
    for index in range(8):
        uc = root / f"uc-{index}.json"
        ev = root / f"ev-{index}.json"
        uc.write_text(json.dumps({
            "method": "AUCMR (CREST full)",
            "use_case_name": f"UC-{index}",
            "actor": "Actor",
        }), encoding="utf-8")
        ev.write_text("{}", encoding="utf-8")
        result_map[("AUCMR (CREST full)", "p", f"UC-{index}", "Actor")] = (
            str(uc), str(ev)
        )

    runner = ExperimentRunner.__new__(ExperimentRunner)
    runner.metrics_computer = FakeComputer()
    metrics = runner.compute_all_metrics(
        result_map, run_nli=True, run_l2=False, max_workers=4,
    )

assert runner.metrics_computer.max_active == 4
assert [item.use_case_name for item in metrics["AUCMR (CREST full)"]] == [
    f"UC-{index}" for index in range(8)
]
print("EVALUATION_CONCURRENCY_SMOKE_PASS")
