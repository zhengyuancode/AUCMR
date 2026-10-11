# AUCMR output history and final rerun scope

This file records the server-side output cleanup performed on 2026-08-14. The
active output root is `/home/dzy/code/TOSEM2026/AUCMR/outputs`. Only these
directories are treated as current generation results:

- `outputs/aucmr_full`
- `outputs/crest_without_dra`
- `outputs/baselines` (excluding `_composites` and `_watermarked`)

`run.py guide`, `run.py generate --resume`, result freshness checks, and
`run.py evaluate` no longer scan `outputs/history` or development folders.

## History layout

### `history/legacy_pre_v5_20260813`

The original 9-use-case AUCMR and CREST-without-DRA run. It predates the final
v5 scope contract, active-card selection, gap-driven stopping, semantic goal
cohesion review, strict slot semantics, and DRA ownership/deduplication changes.
Three cases also contain the old false goal-fast-fail outputs. This directory is
for provenance only and must not be used in the paper's final comparison.

### `history/baselines_pre_flexible_normalizer_20260814`

Baseline artifacts produced before the flexible output normalizer was fixed.
The archive contains:

| Method | Complete cases | Empty slots across saved cases | Saved claims |
|---|---:|---:|---:|
| LVLM-Direct | 9/9 | 41 | 112 |
| TextRAG-Elaborate | 9/9 | 20 | 144 |
| VDocRAG-Elaborate | 9/9 | 47 | 45 |
| ReAct-Elaborate | 9/9 | 49 | 51 |
| ViDoRAG-Elaborate | 1/9 | 1 | 23 |

The model's raw responses were not saved separately, so content discarded by
the old normalizer cannot be recovered by rerunning evaluation. These files are
not final paper results.

### `history/gbea_v5_development_20260814`

Single-use-case AUCMR v1/v3/v5 development outputs, copied baseline cohorts,
judge audits, logs, the 401 error report, and the pre-change code backup. These
are diagnostic artifacts, not the 9-use-case paper cohort.

### `history/cmab_v2_adapter_failure_20260814_1921`

The 9 paired AUCMR/w/o artifacts created while the runtime still used the old
11-D feature builder with the new 14-D CMAB/PCA assets. Retrieval raised an
asset-schema error that the old Query Designer incorrectly converted into a
normal stop, so every Goal had zero evidence cards and was falsely rejected.
These artifacts are retained only as a failure audit and must never be used for
evaluation.

## Reusable preprocessing kept active

For JTVauctions, the cleanup deliberately does not move or rebuild:

- MinerU output and LVLM caption cache under `data`
- Milvus `{project}` image and `{project}_text` collections
- Jina ColBERT-style text vectors
- VDocRAG page-vector cache
- `outputs/baselines/_composites`
- `outputs/baselines/_watermarked`

## Final rerun scope

All seven methods must be regenerated for the final 18-use-case cohort. The
cohort is defined by `data/use-case-selection.md`: 9 JTV candidates and 9
MIL-STD-6016B candidates, with 14 valid and 4 intentionally unresolvable cases.
The new 6016B project requires first-time page-image, MinerU/text, image-index,
and VDocRAG preprocessing; `python run.py guide` directs these stages.

1. Run AUCMR and CREST without cross-slot revision together so they share the
   same pre-DRA artifact:

   ```bash
   python run.py generate --resume --methods "AUCMR (CREST full)" "CREST w/o cross-slot"
   ```

2. Regenerate each external baseline with the fixed normalizer. `guide` chooses
   the next incomplete method, so it is safest to follow it after each method:

   ```bash
   python run.py guide
   ```

3. After all methods show 18/18, rerun automatic L1/L2 evaluation:

   ```bash
   python run.py evaluate
   ```

4. Build the paper's L3/L4 human/expert results only from this final active
   cohort.

Future active results can be archived without touching reusable caches:

```bash
python run.py archive --label <descriptive-version>
```

The command refuses to overwrite an existing history label and writes an
`archive_manifest.json` plus a copy of the experiment configuration.
