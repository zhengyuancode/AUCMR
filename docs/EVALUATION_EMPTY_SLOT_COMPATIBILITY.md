# Empty-slot and early-refusal compatibility

This change fixes interpretation of the existing SlotContent.to_dict format.
It does not change the L2, L3, or L4 rubric, model prompts, frozen inputs, or
generation code. No model calls are needed for acceptance or preflight.

## Evidence and behavior

The server at fe98845 reported 66 files with 80 empty slot objects. Six objects
contained only `slot`; 74 also contained `coverage_state`. None contained final
claims or raw text. The serializer deliberately omits an empty claims list.
All reported source hashes matched the frozen input manifest.

The shared `eval/serialized_slots.py` recognizes that representation. A matching
slot marker and known metadata are required. Explicit null/non-list claims,
nonempty unparsed text, unknown fields on a sparse object, and mismatched slot
names remain errors. Metadata never becomes final claims or judge evidence.

Two additional outputs had an explicit unresolvable_use_case flag, an empty goal,
and no later generated slots. All three readers now distinguish this from a
malformed artifact. L4 preserves physical omissions in its manifest while using
the established no-content scoring rule: empty PCS slots are zero, PRC is null,
and no model request is made. Nonempty refusal outputs retain their text.
Rules depend on structure and explicit flags, not the method name. The former L3
test that rejected an identical refusal solely for an external method is updated.
Previously supported markerless empty goal objects are still accepted only with
an explicit refusal marker. Missing goal, invalid fields, and unparsed text are
not excused by that marker.

The shared reader is included in the L2/L3/L4 implementation fingerprints.
Use new preflight/binding directories after this change; do not resume old
evaluation runs or replace their manifests. Original batch configuration and
output bytes must remain unchanged.

## Offline verification

Run `tests/test_serialized_slot_compatibility.py` separately, followed by the
existing L2, L3, L4, legacy-entrypoint, and runtime/batch suites in separate
processes. Import guards in these suites intentionally require process isolation.
The new tests check all-method parity, sparse/explicit empty equivalence, final
text preservation, malformed-field rejection, refusal with and without content,
PCS/PRC empty behavior, and inclusion in implementation fingerprints.

Initial verification: 243 tests and 26 subtests passed with socket calls blocked.
The 80 original slot objects and two refusal excerpts supplied in the server log
also passed replay in synthetic envelopes. This replay is not a full replay of
the server's 651 final artifacts and does not produce research scores.

## Server preflight

Keep `config/evaluation_batches/unified93-v1` unchanged. Create a new L2 binding
from its l2.json, a new L3 inventory directory (for example
`outputs/agent_evaluation/unified93-preflight-v2`), and a new L4 inventory using
its l4.json. Do not run prepare_evaluation_batch again or overwrite preflight-v1.

Check the complete 651-file matrix, unchanged input hashes and claim sequences,
and the state transitions. Expected, pending actual server verification:

| Layer | Nonempty | Empty | Explicit refusal | Structure error |
| --- | ---: | ---: | ---: | ---: |
| L3 | 646 PRESENT | 3 VALID_EMPTY | 2 ABSTAINED | 0 |
| L4 | 646 PRESENT | 3 EMPTY_OUTPUT | 2 REFUSED | 0 |

L3 should still contain 11199 final assertions. Verify L2 independently as well;
successful input binding alone is not a structure-state acceptance result.
Unexpected states must be diagnosed rather than coerced to these counts.
All preflight semantic statuses remain NOT_RUN. L0 is outside this repair.
