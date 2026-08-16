# Vertex quota governor

BBA reads the effective Vertex AI quotas assigned to the active Google Cloud
project and uses them to pace individual model requests. The governor sits below
creator and solver jobs: every ADK model turn acquires quota capacity before the
provider request is sent.

## Why this exists

A single creator or solver invocation can make several LLM calls while using
BBA tools. Limiting only the number of concurrent solver jobs does not protect a
project-level QPM or TPM quota. The governor therefore controls the actual model
calls made inside each ADK session.

## Quota discovery

BBA reads `aiplatform.googleapis.com` consumer quota metrics through the Service
Usage v1beta1 API with `view=FULL`. The operator's ADC principal needs the
`serviceusage.quotas.get` permission. `roles/serviceusage.serviceUsageViewer` or
`roles/servicemanagement.quotaViewer` are suitable read-only roles.

Service Usage quota resources use the numeric project resource. BBA resolves the
configured project ID through Cloud Resource Manager before querying quotas.
That lookup requires `resourcemanager.projects.get`. Operators who already know
the project number can set `BBA_GCP_PROJECT_NUMBER` or
`GOOGLE_CLOUD_PROJECT_NUMBER` instead.

BBA reads the effective bucket values, including project overrides. For fixed
quota routes, missing or zero QPM/input-TPM/output-TPM is a preflight failure.
The quota snapshot is also saved as append-only epoch evidence under
`quota-snapshots/`.

## Fixed and adaptive modes

BBA currently treats xAI Grok and Anthropic Claude routes as fixed-quota routes.
Google Gemini routes use Standard PayGo and are handled in adaptive mode.

Fixed mode enforces all three rolling limits when they exist:

- requests per minute;
- input tokens per minute;
- output tokens per minute.

Adaptive mode does not invent a fixed rate for Standard PayGo. It retains the
existing bounded scheduler and adds a persisted cooldown after quota-related
`429` / `RESOURCE_EXHAUSTED` provider errors.

## Shared Claude lineage buckets

Newer Claude versions can share one quota lineage. BBA maps the current catalog
accordingly:

- `claude-opus-5` and `claude-opus-4-8` -> `anthropic-claude-opus`;
- `claude-sonnet-5` -> `anthropic-claude-sonnet`;
- `claude-fable-5` -> `anthropic-claude-fable`.

Older catalog routes continue to use their per-model base-model dimensions, for
example `anthropic-claude-opus-4-7`.

All local jobs using the same evidence root share the same SQLite quota ledger,
so two Claude versions in one lineage consume one local bucket rather than two
independent allowances.

## Safety target

By default BBA uses two thirds of every fixed provider limit. Set
`BBA_QUOTA_UTILIZATION` to a value from `0.10` through `0.95` to change this.
The default leaves capacity for timing jitter and other project traffic.

For a project with Grok 4.3 quotas of:

- 6 QPM;
- 40,000 input TPM;
- 12,000 output TPM;

BBA targets:

- 4 requests per minute;
- 26,666 input tokens per minute;
- 8,000 output tokens per minute;
- at least 15 seconds between Grok requests.

## Per-request output grants

Output capacity is granted atomically when a model call is admitted. BBA does
not reserve the whole rolling output-TPM allowance for each call.

For a fixed bucket, the governor divides the output headroom remaining in the
rolling minute by the request slots still available in that minute. With the
Grok limits above, the first of four available calls receives a normal cap of
2,000 output tokens:

```text
8,000 output tokens / 4 request slots = 2,000 tokens
```

If that call actually uses all 2,000 tokens, each of the next three calls can
still receive 2,000 tokens at 15-second intervals. If it uses only 500 tokens,
the unused 1,500-token headroom is available to later calls; the next call can
receive a larger grant without exceeding the rolling 8,000-token target.

The quota lease returned to the ADK hook contains the exact granted output cap.
The hook applies that cap to `max_output_tokens` before sending the provider
request. Input usage is estimated conservatively before admission and replaced
with provider-reported usage after the response.

## Rolling ledger

`.bba/quota-governor.sqlite3` stores:

- the most recent project quota snapshot;
- in-flight and recently completed request leases;
- reserved and actual input/output tokens;
- temporary quota-error cooldowns.

Only counts and identifiers are stored. Prompts, responses, tool arguments, and
tool results are not stored in the quota database.

The rolling window is 60 seconds. An in-flight call counts its conservative
reservation until usage metadata arrives. On success, BBA replaces the
reservation with the actual token counts. Stale request events expire after one
minute, so a process crash cannot permanently consume local quota capacity.

## Refresh and failure behavior

Effective quotas are refreshed every five minutes by default. Set
`BBA_QUOTA_REFRESH_SECONDS` to change the refresh interval. If a refresh fails,
BBA can temporarily use a recent cached snapshot; fixed-quota execution fails
closed once the cache is too old.

A quota-related provider error creates a shared bucket cooldown. BBA honors a
numeric `Retry-After` header when available; otherwise the cooldown grows from
5 to 10 to 20 to 40 seconds, capped at 60 seconds. The next controller retry
therefore waits instead of immediately repeating the same quota failure.

Cloud Monitoring quota usage is intentionally not the primary rate limiter:
quota usage metrics are sampled and can be delayed. The local SQLite ledger is
the real-time source for BBA's own calls, while Service Usage is the source of
truth for effective project limits.

## Portal

The development portal adds **Effective Vertex quotas** to workspace readiness
and an **Inspect effective model quotas** diagnostic. The diagnostic refreshes
quota values and prints provider limits, BBA targets, bucket names, rolling local
usage, minimum request spacing, and the nominal output grant per request for
every frozen catalog model.
