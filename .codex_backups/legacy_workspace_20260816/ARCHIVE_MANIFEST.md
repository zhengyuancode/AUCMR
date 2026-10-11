# AUCMR legacy archive — 2026-08-16

This archive contains superseded experiment outputs, evaluator snapshots, and
source backups.  It is retained for rollback only and must not be used as an
active generation or evaluation input.

## Active canonical state (not archived)

- `crest/`, `eval/`, `run.py`, and `experiment.json`: current AUCMR and judge code.
- `outputs/aucmr_full/`: current v5-balanced AUCMR full results, with the
  DRA audit-restoration fix.
- `outputs/crest_without_dra/`: current paired CREST w/o cross-slot results.
- `outputs/baselines/`: active external baseline results.
- `outputs/evaluation_audit_l1.json`, `outputs/evaluation_audit_l2.json`,
  `outputs/aggregated_metrics_l1.json`, `outputs/aggregated_metrics_l2.json`,
  and the two `table_*.tex` files: current L1/L2 evaluation outputs.

## Protected historical dependency (not archived)

- `outputs/history/prompt-v1-pre-rewrite/crest_without_dra/` remains in place
  because `run.py --reuse-evidence` loads it as the frozen paired v4 evidence
  seed.  It is an input dependency, not an active experimental result.

All other legacy paths were moved here to prevent accidental selection by
future analysis or experiment runs.
