# BBA prompt caching and cost accounting

Prompt caching is an opportunistic latency and billing optimization. It is not a
correctness dependency and it is not used to justify BBA's hard USD limit.

## Runtime behavior

Before an ADK model turn, BBA attaches a `ContextCacheConfig` when the installed
ADK request type supports it. In Google ADK 2.6.3, the explicit cache lifecycle
is implemented by the Gemini adapter. Other providers may apply their own
implicit prefix caching, but BBA does not assume that they do.

After each response, BBA records provider-reported prompt, output, total, and
cached-token metadata when those fields are available. Content is never copied
into the observability records.

Cache creation may be skipped when:

- the stable prefix is below the provider's minimum;
- the call is the first turn in a session;
- the provider or adapter does not implement the cache configuration;
- cache creation expires or fails;
- the conversation prefix changes.

The model call continues without an active cache in those cases.

## Hard cost policy

The frozen price catalog contains ordinary input and output token rates for each
model route. BBA applies those uncached rates to every reservation and every
reconciled invocation, then applies the catalog's safety multiplier.

This means:

- the hard runtime ledger never depends on a cache hit;
- cached tokens do not make additional budget capacity appear;
- a provider cache failure cannot cause the epoch to exceed its frozen USD
  ceiling merely because preflight assumed a discount;
- observed cache savings can be reported separately from the safety-adjusted
  ledger.

Each production reservation is attributed to the exact model identity embedded
in its immutable work or attempt ID. Removed or unrelated routes in the
historical price catalog therefore cannot inflate the cost of an active Grok,
Gemini, or Claude invocation. Unattributed legacy operations continue to use the
highest frozen rates and therefore fail safe.

## Planning estimates

`PriceCatalog.estimate()` publishes four different views:

1. **Planning provider cost** — likely uncached token usage at ordinary rates.
2. **Planning budgeted cost** — the same estimate after the safety multiplier.
3. **Stress estimate** — high but plausible uncached usage plus solver retry
   overhead; this is the paid-preflight gate.
4. **Maximum envelopes** — absolute first-attempt and complete-retry token
   ceilings. These are diagnostic bounds, not the preflight gate, because the
   model-specific runtime ledger admits and reconciles work incrementally.

The planning and stress assumptions are versioned in
`bba/data/price-catalog.json` and are included in the evaluator identity.

## Current default profiles

For the default 9-model, 3-round, 3-repetition epoch:

| Profile | Creator session | Solver session | Solver retry allowance |
|---|---:|---:|---:|
| Planning | 70,000 input / 10,000 output | 20,000 / 3,500 | 5% |
| Stress | 110,000 input / 14,000 output | 45,000 / 7,000 | 10% |

Neither profile assumes any cached-input discount. Actual work remains bounded
by the per-session token contract, the epoch token ceilings, the quota governor,
and the `$500` safety-adjusted runtime ledger.
