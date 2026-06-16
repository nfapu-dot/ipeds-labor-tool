# Claude Code IPEDS Lookup Tool — v2 (Labor Market Layer)

This folder is **v2**. The canonical **v1** (student-demand-only) lives in the sibling folder
`../Claude Code IPEDS Lookup Tool/` — treat that as read-only reference. This folder is now
**v2-only**: the IPEDS completions engine plus the labor market layer. The standalone v1
CLI/Streamlit/launcher were removed from this folder during cleanup (2026-05-28); v1's app
still lives in its own folder.

**Status:** Feature-complete (Phases 0–6 done per `docs/PHASE_PLAN.md`).
Run the app via `Launch IPEDS Tool v2.command` (Streamlit, port 8502) or
`python src/main_v2.py` (CLI).

## Structure
- **Shared completions engine** — `src/loader.py`, `joiner.py`, `aggregator.py`,
  `reporter.py`, `resolver.py`. The original IPEDS pipeline; the v2 layer imports it.
  This is NOT redundant v1 — it's the foundation v2 runs on. Edit with care.
- **v2 layer** — `src/core/` (CIP-SOC crosswalk), `src/labor/` (loaders + aggregator),
  `src/reports/` (orchestrator `combine.py`, Excel `writer.py`, labels), `src/app_v2.py`
  (Streamlit), `src/main_v2.py` (CLI).
- **Regression guard** — `src/main.py` + `tests/test_v1_regression.py` lock the completions
  engine's output against `tests/fixtures/v1_baseline.xlsx`. `main.py` is retained ONLY as
  that test's entry point (there is no separate v1 app here anymore). Run the test after any
  edit to a shared-engine module.

## Start here
Orientation docs under `docs/`: `V2_KICKOFF`, `ARCHITECTURE`, `DATA_SOURCES`,
`CIP_SOC_AGGREGATION`, `PHASE_PLAN`, `CROSSWALK_INSPECTION_FINDINGS`,
`LABOR_SOURCES_INSPECTION`.

## Non-negotiables
- **Protect the completions engine.** After editing any shared-engine module, re-run
  `python tests/test_v1_regression.py` — output must stay byte-identical to the baseline
  unless a change is explicitly intended and approved.
- **Source rigor.** Distinguish confirmed facts from inferences. Cite primary sources
  (BLS, NCES, Census). Flag analytical gaps explicitly. Document every methodology choice
  in code AND in report footers.
- **Vintage discipline.** OEWS / BLS Projections / EDD / Census have different base years;
  every combined report must disclose the mismatch (see `docs/LABOR_SOURCES_INSPECTION.md`).
- **No web scraping or paid sources.** All data sources must be free and ToS-clean.

## User context
Strategic planning analyst at Azusa Pacific University. Executive-audience standard. Values
brevity, pushback on weak sourcing, plain language over engineering jargon, and explicit
acknowledgment of analytical limitations.
