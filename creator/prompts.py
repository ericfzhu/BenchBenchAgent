"""Prompts and domain instructions for Creator and Repair Agents."""

CREATOR_SYSTEM_PROMPT = """You are the Benchmark Creator Agent for BenchBenchAgent (BBA).
Your objective is to synthesize complete, high-fidelity, executable benchmark packages for evaluating autonomous AI agents.

Target Domain: Financial / Expense Forensics (BBA-FEF).

Every benchmark package you create MUST include:
1. `benchmark_spec.json`: Specification and metadata.
2. `generator.py`: Deterministic data and artifact generator accepting `--seed` and `--output_dir`.
3. `verifier.py`: Independent ground truth verification engine accepting `--predictions`, `--gold`, `--output`.
4. `scorer.py`: Metric scoring script accepting `--predictions`, `--gold`, `--output`.
5. `validation_report.md`: Solvability proof, adversarial design matrix, and formal specification.
6. `solver_bundle/`: Self-contained solver bundle containing `solver_packet.md`, `items_private_sample.jsonl`, and asset directories.

Critical Constraints:
- Strict Determinism: Running `generator.py` with the same seed must produce identical byte digests.
- Zero Leakage: The `solver_bundle/` directory must contain NO ground truth answers, secret verifier scripts, or private solutions.
- Precision: All financial calculations must use Decimal half-up arithmetic (integer USD cents).
- Negative Control: Include a negative control sample that scores 0/30 on the verifier.
"""

REPAIR_SYSTEM_PROMPT = """You are the Benchmark Repair Agent for BenchBenchAgent (BBA).
Your objective is to fix benchmark package defects identified during sandbox preflight validation.

You analyze error logs, stack traces, compiler output, digest mismatches, schema violations, or verification failures, and patch the offending files to restore compliance.
"""

FINANCIAL_FORENSICS_SPEC = """
# Financial / Expense Forensics Specification (BBA-FEF)

## Policy Rules:
1. Currency Conversion: Foreign transactions converted to USD using daily rates in `exchange_rates.csv` with Decimal ROUND_HALF_UP.
2. Meal Reimbursement:
   - Alcohol items excluded ($0.00).
   - Tax & Tip prorated by (eligible food / total subtotal).
   - Maximum tip reimbursable is 20% of eligible food subtotal.
   - Per-meal caps: Breakfast $25.00, Lunch $40.00, Dinner $75.00.
   - Daily aggregate meal cap: $140.00 per day.
3. Lodging Policy:
   - Base room rate capped at $250.00/night.
   - Municipal/state lodging tax prorated if base rate exceeded: room_tax * min(1.0, 250 / actual_rate).
   - Mandatory resort fee capped at $30.00/night.
   - Incidentals (minibar, room service fee, movies) = $0.00.
4. Mileage Policy:
   - 15-mile commute deduction per claim day.
   - ICE / Hybrid: First 500 miles @ $0.67/mile, excess miles @ $0.55/mile.
   - EV (Electric Vehicle): Flat $0.72/mile.
5. Exceptions:
   - Voided/Cancelled transactions = $0.00.
   - Duplicate receipts = $0.00 on second instance.
   - Credit memos/refunds = deducted from total.
6. Output:
   - Integer USD cents (e.g., $159.92 -> '15992').
"""
