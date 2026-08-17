# BBA Prompt Caching Architecture & Cost Optimization

This document outlines the prompt caching implementation in BenchBenchAgent (BBA), detailing the architectural design, provider-specific mechanisms, telemetry tracking, and the mathematical rationale for why it was implemented.

---

## 1. Executive Summary & Rationale

In BBA's autonomous two-sided adversarial benchmark co-evolution tournament:
1. **Shared Candidate Problem Space**: Each Creator agent generates a complex benchmark candidate containing rich scenario specifications, financial transaction ledgers, receipts, voided invoice data, and evaluation rubrics. This candidate dataset is evaluated across **9 solver models with 3 repetitions each ($9 \times 3 = 27$ solver runs per candidate)**.
2. **Multi-Turn Agent Trajectories**: Each solver operates inside a sandboxed Python REPL environment, taking multiple iterative tool turns (inspecting files, running analysis scripts, submitting predictions, debriefing). In multi-turn agent workflows, each subsequent turn re-transmits the full conversation prefix (problem preamble, tool definitions, and prior execution steps).

### The Cost Challenge
Without prompt caching:
* Input tokens account for **85% to 90%** of total token volume across an epoch.
* High-capability frontier models (such as `claude-opus-5`, `claude-opus-4-8`, `claude-opus-4-7`) cost \$15.00 / MTok for standard input tokens on Vertex AI.
* Across all tournament matrix cells, repetitive transmission of identical problem statements produced high worst-case cost estimates (>\$1,500).

### The Solution
Prompt caching enables model providers to persist pre-computed key-value (KV) attention states for identical token prefixes in GPU memory across turns and runs. By caching static problem definitions, tool contracts, and early conversation history:
* **Anthropic Claude (Opus & Sonnet)**: Cached input reads receive a **90% discount** (\$1.50 / MTok on Opus vs. \$15.00 / MTok; \$0.30 / MTok on Sonnet vs. \$3.00 / MTok).
* **Google Gemini (Flash, Pro, Lite)**: Vertex AI Context Caching provides up to a **75% to 90% discount** on cached prompt prefixes exceeding 1,024 tokens.
* **xAI Grok**: Vertex AI server-side context caching discounts repeated prefix lookups.

---

## 2. Runtime Architecture & Integration

Prompt caching is integrated directly into BBA's ADK lifecycle hooks in `bba/_adk_runtime.py` and `bba/adk_runtime.py`.

### 2.1 Request-Time Cache Configuration (`before_model_callback`)
Before each model invocation, the `_ObservabilityPlugin` attaches a `ContextCacheConfig` to `llm_request`:

```python
from google.adk.models.llm_request import ContextCacheConfig

if getattr(llm_request, "cache_config", None) is None:
    llm_request.cache_config = ContextCacheConfig(
        cache_intervals=1,
        ttl_seconds=3600,
        min_tokens=1024,
        create_http_options=None,
    )
```

### 2.2 Telemetry & Observability (`after_model_callback`)
After each turn, provider usage metadata is parsed to record both standard prompt tokens and cached token reads:

```python
usage = llm_response.usage_metadata
if usage is not None:
    self.prompt_tokens += int(getattr(usage, "prompt_token_count", 0) or 0)
    cached = (
        getattr(usage, "cached_content_token_count", 0)
        or getattr(usage, "cache_read_input_tokens", 0)
        or 0
    )
    self.cached_tokens += int(cached or 0)
    self.output_tokens += int(getattr(usage, "candidates_token_count", 0) or 0)
    self.total_tokens += int(getattr(usage, "total_token_count", 0) or 0)
```

The resulting telemetry is persisted in `.bba/epochs/<epoch-id>/observability/` for auditing and cost accounting.

---

## 3. Provider Mechanics

| Provider | Mechanism | Minimum Threshold | TTL / Persistence | Read Discount |
| :--- | :--- | :---: | :---: | :---: |
| **Anthropic Claude on Vertex AI** | Ephemeral Prompt Caching | 1,024 tokens | 5 min – 1 hr (refreshed on read) | **90% off** |
| **Google Gemini on Vertex AI** | Context Caching (Implicit & Explicit) | 1,024 tokens | 1 hour default | **75%–90% off** |
| **xAI Grok on Vertex AI** | Server-side Prefix Caching | 1,024 tokens | Session-scoped | **50%–75% off** |

---

## 4. Verification & Evaluation Integrity

* **Zero Impact on Determinism**: Prompt caching reuses exact attention states without altering temperature, sampling, or token generation probabilities.
* **Hermetic Sandbox Execution**: Solver tool interactions (reading candidate files, executing REPL code, submitting predictions) execute identically regardless of whether the prefix is served from cache or computed from scratch.
