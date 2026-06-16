# Phase Plan

Each phase has acceptance criteria. Do not advance until criteria met. Do not work ahead.

## Phase 0 — Inspection & baseline (no code changes)

**Tasks:**
1. Run v1 end-to-end in v2 folder. Save its Excel output as `tests/fixtures/v1_baseline.xlsx`.
2. Crosswalk inspection per `docs/CROSSWALK_INSPECTION.md`.
3. Survey existing v1 code structure: entry point, module layout, where the crosswalk is loaded today, where SOC columns are retained but unused.

**Acceptance:**
- v1 baseline output saved.
- Crosswalk inspection report written to `docs/CROSSWALK_INSPECTION_FINDINGS.md`.
- v1 code structure summary written (5–15 bullets, no fluff).
- User approves Phase 1 layout before any directory restructuring.

## Phase 1 — Core module + lock v1 behavior

**Tasks:**
1. Create `src/core/crosswalk.py` with a `load_crosswalk()` returning a normalized dataframe.
2. Refactor v1's crosswalk loading (if any) to call `core.crosswalk` via a thin compatibility shim. v1 behavior unchanged.
3. Add a regression test that re-runs the v1 pipeline and diffs output against `v1_baseline.xlsx`.

**Acceptance:**
- v1 regression test passes (output identical, or differences explicitly approved).
- `core.crosswalk` covered by unit tests.
- No new dependencies in v1 code path.

## Phase 2 — Static data acquisition

**Tasks:**
1. Manually download BLS OEWS (national + all states, current release), BLS Projections, CA EDD long-term projections. Place under `data/raw_labor/` per layout in `DATA_SOURCES.md`.
2. Obtain Census API key, store in `.env` (gitignored). Add `.env.example`.
3. Write `scripts/refresh_labor.py` as a print-only checklist: source URLs, expected filenames, last-fetched dates.

**Acceptance:**
- Files present at expected paths.
- `refresh_labor.py` produces a checklist users can follow next year.
- `.env.example` documents required keys.

## Phase 3 — Loaders

**Tasks:**
1. `src/labor/loaders/oews.py` — read national + state OEWS, return normalized long-format dataframe (SOC, state, wage_median, wage_mean, tot_emp, year).
2. `src/labor/loaders/projections.py` — read BLS national projections, return (SOC, base_year, target_year, employment_change_pct, openings_annual_avg).
3. `src/labor/loaders/edd.py` — read CA EDD long-term, return (SOC, projection_window, employment_change_pct, openings_annual_avg).
4. `src/labor/loaders/census.py` — query Census API for state pop and 18-24 cohort by state; cache locally.

**Acceptance:**
- Each loader has unit tests with small fixture files.
- All loaders handle suppressed/missing values gracefully.
- Output schemas documented at top of each loader file.

## Phase 4 — Aggregator

**Tasks:**
1. `src/labor/aggregator.py` — implement all three CIP→SOC view modes per `CIP_SOC_AGGREGATION.md`.
2. Per-(CIP, state, year) rollup function returning all metrics.
3. Tests covering: a CIP with one SOC, a CIP with many SOCs, a CIP with suppressed cells, an unmapped CIP.

**Acceptance:**
- Three modes produce expected results on fixture data.
- Suppression handling tested.
- Footer disclosure metadata returned alongside data.

## Phase 5 — Orchestrator + combined Excel

**Tasks:**
1. `src/reports/combine.py` — join completions output with labor aggregator output on CIP.
2. New CLI subcommands: `labor`, `combined`. v1 subcommand unchanged.
3. Excel writer outputs per-CIP rows with completions + wage + projection + population context columns; footer disclosure populated.

**Acceptance:**
- `combined` subcommand produces a working Excel from real data.
- v1 subcommand still produces identical baseline output.
- Footer includes all disclosure fields per `DATA_SOURCES.md` + `CIP_SOC_AGGREGATION.md`.

## Phase 6 — Population context layer

**Tasks:**
1. Per-state metrics: total pop, 18-24 cohort size, attainment levels.
2. Saturation ratio: program completions ÷ projected annual openings (where applicable).

**Acceptance:**
- Saturation ratios appear in combined report.
- Caveat about ratio interpretation in footer (it's a rough signal, not a labor surplus measure).

## Deferred — v3

- Job postings volume (CareerOneStop first; reassess paid sources if needed).
- Optional UI wrapper (Streamlit or similar) sitting on the orchestrator.
- Auto-refresh / scheduled fetch.
