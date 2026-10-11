# CREST component ablations

This directory is intentionally isolated from the normal experiment entry
point.  It imports `ablation_runtime/` and writes only beneath
`outputs_ablation/crest_components_v2/`.

The runner refuses to start while CREST generation, `run.py revise`, or
another ablation generation is active.  Baseline-only generation (for example
`ViDoRAG-Elaborate`) is reported by `plan` as non-blocking because it uses a
different output tree, although both processes may still contend for compute
and external API quotas.

```bash
conda activate aucmr
cd /home/dzy/code/TOSEM2026/AUCMR

# Read-only: show variants, five seeded slot orders, output root, and conflicts.
python ablation/crest_ablation.py plan

# Run all generation variants. Safe to resume after interruption.
python ablation/crest_ablation.py generate --resume

# Evaluate the full-run cohort with the configured NLI and L2 judges.
# Per-use-case judge results are cached, so this is also resumable.
python ablation/crest_ablation.py evaluate --resume
```

Generated artifacts:

- `manifest.json`: frozen variants, seeds, slot orders, and use cases;
- `evaluation_cohort.json`: the shared cohort defined by full AUCMR;
- `aggregated_by_run.json`: one aggregate for every deterministic run/seed;
- `ablation_summary.json`: random-order mean and sample standard deviation;
- `table_ablation_crest_rows.tex`: paper-ready table rows;
- `evaluation_cache/`: resumable per-use-case judge results.

The full and no-DRA variants share the same pre-DRA generated artifact.  The
five random-order runs use fixed, distinct permutations and are summarized as
mean plus/minus sample standard deviation.

By default, the full and no-DRA controls are validated and copied from the
complete paired production artifacts in `outputs/aucmr_full/` and
`outputs/crest_without_dra/`; they are not inferred again.  Missing or
mismatched production pairs cause an immediate error before model loading.
