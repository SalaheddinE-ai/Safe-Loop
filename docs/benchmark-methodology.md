# Benchmark methodology
Suite `tasks_v1.json` v1.0.0: 8 tasks (easy/medium/hard; basic, edge cases, interface, bug repair), acceptance tests
validated against repo-owned reference solutions (`reference_solutions_v1.json`, never shown to models).
Configs: **A** direct (no static gate, 1 attempt); **B** + deterministic verification (1 attempt); **C** Safe-Loop (≤N attempts).
All use the same real sandbox, tests, provider and limits. Repeat ≥3 times for nondeterministic models.

Definitions (denominator = all runs of that config unless stated):
- pass@1: SUCCESS with exactly 1 attempt. Final success: status SUCCESS.
- Regression rate: runs where a later executed attempt passes fewer tests than an earlier one; denominator = runs with ≥2 executed attempts.
- Repair attempts: attempts − 1. Cost: mean over runs with a known (reported/estimated) cost only; the count of such runs is printed;
  none known → "n/a (not measured)". Latency: wall time per run (median, p95).
- Failures are separated: functional, provider, infrastructure, policy block, budget, deadline.
Limitations: 8 small tasks do not establish general performance; results depend on model, hardware, and sandbox limits.
Harness self-tests with scripted providers validate arithmetic only and are labelled as such in reports.
No benchmark results are included in this repository.
