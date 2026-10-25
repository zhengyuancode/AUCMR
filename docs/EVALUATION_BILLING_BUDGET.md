# Evaluation billing and request bounds

This repair changes billing infrastructure only. L1-L4 semantic protocols,
prompts, scenario/reference rules, methods, seeds, frozen batches and generated
outputs are unchanged. All release verification uses constructed responses with
socket access denied. L0 is separate.

## Cost meanings

* `per_request_upper_bound` reserves campaign money before each request. It is
  not a billed amount. Images included in prompt tokens are reserved in input.
* `cost_cny` is the accounting value calculated from returned usage. Consult
  `cost_basis`. `USAGE_LIST_PRICE` uses a bound tariff and reported cache usage.
  `USAGE_RATE_UPPER_BOUND` uses the larger cached/uncached rate when cache usage
  is missing. `LEGACY_RATE_ESTIMATE` exists only for historical offline fixtures.
* `list_price_cost_cny` is null unless the configured usage dimensions are known.
  `invoice_cost_cny` is always null until a separate account reconciliation.
  Usage-based list price does not include account credits or discounts.
* Invalid/missing mandatory usage gives null cost, retains the reservation and
  stops further requests. An observed charge above reservation, or a provider
  token count above its verified request bound, also stops the campaign.
* Receipts retain raw usage, tariff version/source/applied tier and billing hash.
  L3 schema-closed call records keep their existing fields; the full billing
  receipt is in `request_trace.jsonl` and durable request progress. L2 exports
  cost basis; L4 summary/export cost is labelled as accounting, not invoice.

The existing SQLite schema is preserved. Its historical `actual` column stores
accounting cost rather than an independently verified invoice. Replaying a paid
request does not reserve, send or settle it twice. Unknown charges cannot be
resolved by silently rerunning a task.

## Explicit binding fields

Live bindings need `billing.tariff` with `currency: CNY`, a version/source and
contiguous half-open `tiers`. Each tier has `min_input_tokens`, an exclusive
`max_input_tokens_exclusive` (null only for the final unbounded tier), and
`input_cny_per_million`, `output_cny_per_million`,
`cached_input_cny_per_million`. The tier is selected from the total prompt
token count, before subtracting cached tokens. Convert published K units to
exact integer thresholds only with supporting provider evidence.

`billing.usage_contract` requires `verified`, `evidence`,
`cached_input_path` as a list of JSON keys, `missing_cache` (`unknown` or
`zero_documented`), and `image_tokens_in_prompt: true`. A missing cache field
must not silently imply zero unless the contract documents that convention.
`reasoning_in_output: true` prevents reasoning double charging. Otherwise a
separate reasoning rate, returned reasoning count and bound are required.
This version supports standard token-billed chat requests, not explicit cache
creation/storage, batch discounts, audio/video or separately priced tools.
Bindings for those must remain incomplete until those dimensions are implemented.

`request_limits` requires `verified`, `evidence`, `max_input_tokens`,
`context_tokens`, `max_request_bytes`, `text_tokens_per_utf8_byte`,
`message_overhead_tokens`, and `request_overhead_tokens`. The multiplier is at
least one. UTF-8 bytes plus documented expansion/overhead are a conservative
bound, not the provider tokenizer's measured output. Unknown tokenizer or chat
framing bounds cannot be certified by simply setting `verified` to true.

Visual calls additionally require `max_images`, `max_image_pixels`,
`max_image_bytes`, `max_image_base64_bytes` and `image_tokens_by_detail` for each
used detail level (`auto` when absent). Tools currently render PNG, so live
budget checking accepts inline PNG and checks its dimensions. No evidence is
resized, compressed, deleted or truncated by this repair. Repeated images and
all prior tool/assistant turns count on every request. Separate reasoning
billing also requires `max_reasoning_tokens` backed by an enforced model limit.

The complete serialized JSON must fit the byte cap. Bounded input plus output
must fit context. The five reservation components must cover the applicable
largest tariff, without assuming a cache discount. The checks run before
reservation in L2/L4 and before TaskRecovery reservation in the L3 executor.
Provider counts that contradict the bound stop subsequent requests and retain
the known calculated cost for reconciliation.

## Server verification

`scripts/check_evaluation_budget.py --offline-tests` runs the selected L1-L4
regression suite with socket networking disabled. It creates isolated test
fixtures, never changes the frozen campaign or original outputs.

`scripts/check_evaluation_budget.py --draft PATH` reads `l2.json`, `l3.json` and
`l4.json`, prints missing fields and draft hashes without writing any files or
constructing a ledger/runtime. It reports key presence only, never key values.
An audit completing successfully does not mean model bindings or live access
are ready. No configuration is automatically bound or authorized.

Use a new preparation/run directory after implementation fingerprints change;
do not update hashes inside a frozen run or reuse its stale acceptance files.
Existing selection-only profiles lacking tariff, usage or request evidence will
remain blocked until those fields are established in a new derived draft.
