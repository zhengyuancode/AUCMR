# CREST component ablations (current AUCMR)

This isolated runner is pinned to the generation and evaluation versions
declared by the current `run.py`. It writes only to
`outputs_ablation/crest_components_v4_latest/`.

```bash
conda activate aucmr
cd /home/dzy/code/TOSEM2026/AUCMR

# Read-only version, task-matrix, conflict, and production-control audit.
python ablation/crest_ablation.py plan

# Bring the official full/no-DRA controls to the same current generation.
# First re-author the 14 valid paper-cohort cases from their validated frozen
# v4 card pools, then freshly collect evidence for the four fast-failed cases.
python run.py generate --resume --paper-cohort --reuse-evidence \
  --generation-workers 18 \
  --methods "AUCMR (CREST full)" "CREST w/o cross-slot"
python run.py generate --resume --generation-workers 18 \
  --methods "AUCMR (CREST full)" "CREST w/o cross-slot"

# All 144 non-control (use-case, ablation-run) tasks. The current config uses
# 144 cloud-call workers and one correctness-preserving shared retrieval lock.
python ablation/crest_ablation.py generate-threaded --resume

# Copy full/no-DRA only after run.py has produced all 18 current-version pairs.
python ablation/crest_ablation.py sync-core --resume

# Evaluate the shared full-AUCMR cohort with the current L1 and L2 judges.
# The ceiling is 180 workers; a 14-case valid cohort has 140 actual judge jobs,
# so every job can be submitted at once.
python ablation/crest_ablation.py evaluate --resume
```

The full and no-DRA controls are never inferred again by the component runner.
They must have the exact current `CREST_GENERATION_VERSION` and matching
main/evidence revision IDs. All stages share one output lock, all result pairs
are committed atomically, and evaluation caches include both judge versions
and the source artifact revision ID.

For parity with the current production path, random order, no cross-slot card
reuse, and no mandatory evidence constraint re-author from the same validated
frozen v4 pool whenever one exists (14 cases). The four fast-failed candidates
fall back to fresh collection, matching their production controls. The
single-page variant always recollects evidence because collection itself is the
ablated mechanism.

`experiment.json` also pins SHA-256 hashes for the production CREST sources and
the L1/L2 evaluator sources. If any of those files changes again, every command
fails before model loading until the isolated runtime is rebuilt.

Final artifacts include `aggregated_by_run.json`, `ablation_summary.json`,
`evaluation_cohort.json`, `evaluation_manifest.json`, and the paper-ready
`table_ablation_crest_rows.tex`. The random-order row is the mean plus sample
standard deviation over five fixed distinct permutations; DRA is a separate
row.
