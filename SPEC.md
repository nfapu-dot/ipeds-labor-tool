# IPEDS Completions + Labor Market Tool — Specification (v2)

## Purpose
Join IPEDS Completions (degrees conferred by CIP × award level) with labor-market data
keyed to the same programs via the NCES CIP-SOC crosswalk: BLS OEWS wages, BLS Employment
Projections, CA EDD long-term projections, and Census ACS demographics. Produce a combined
Excel workbook and a Streamlit web app that present student demand and labor demand side by
side, including a saturation ratio (completions ÷ openings).

This is **v2**. Canonical **v1** (completions-only) lives in the sibling folder
`../Claude Code IPEDS Lookup Tool/`. This folder is v2-only; the original v1 standalone app
was removed here during cleanup (see `CLAUDE.md`).

---

## Architecture

**Orchestrator pattern.** A shared completions engine, independent labor loaders, and a
top-level orchestrator that joins them. See `docs/ARCHITECTURE.md` for rationale.

```
src/
├── loader.py joiner.py aggregator.py reporter.py resolver.py   Shared completions engine
├── core/crosswalk.py        CIP-SOC loader (sentinel-aware), shared by v1 path + labor
├── labor/
│   ├── loaders/oews.py        BLS OEWS national + state (wages, employment)
│   ├── loaders/projections.py BLS 10-yr national projections
│   ├── loaders/edd.py         CA EDD long-term projections
│   ├── loaders/census.py      Census ACS via API (cached)
│   └── aggregator.py          CIP→SOC rollup, three view modes
├── reports/
│   ├── combine.py             Orchestrator: runs completions + labor, joins, derives saturation
│   ├── writer.py              Combined Excel writer (reuses v1 reporter for completions sheets)
│   └── labels.py              Technical-name → plain-English column map (shared by app + Excel)
├── app_v2.py                  Streamlit web app
├── main_v2.py                 CLI
└── main.py                    Regression-test entry point only (not a user app)
```

**Dependency rule.** The labor/reports/app layer imports the shared engine; the shared engine
never imports the labor layer. Editing a shared-engine module risks v1's output — guarded by
the regression test (below).

**Regression guard.** `src/main.py` + `tests/test_v1_regression.py` run the completions
pipeline and compare the workbook against `tests/fixtures/v1_baseline.xlsx`
(`--state CA`, all CIPs, 2020–2024). Must stay byte-identical unless a change is explicitly
intended. Re-run after any shared-engine edit.

---

## Data Inputs

### Completions surveys (`data/raw/`)
| Survey | Pattern | Key columns |
|---|---|---|
| Institutional Characteristics (HD) | `hd{year}.csv` | UNITID, INSTNM, STABBR, CONTROL, ICLEVEL, CARNEGIE (C21BASIC), CITY |
| Completions A (C_A) | `c{year}_a.csv` | UNITID, CIPCODE, MAJORNUM, AWLEVEL, CTOTALT, CTOTALM, CTOTALW |

Default years 2020–2024 (`config/years.yaml`). **MAJORNUM == 1 filter is applied on load** —
including second majors double-counts completions. CIPCODE kept as string (leading zeros).
Suppressed cells (<3) stay NaN, never imputed. Non-6-digit CIP rollups (e.g. `99`) excluded
by default via the `IS_CIP_6DIGIT` flag.

**Award levels:** 1–11 standard (5=Bachelor's, 7=Master's, 9/10=Doctorate) plus legacy
17–20 seen in real data. Full table in the workbook's Definitions sheet.

### CIP-SOC crosswalk (`data/dictionary/cip_soc_crosswalk.xlsx`)
NCES workbook, **CIP 2020 → SOC 2018**, sheet `CIP-SOC`. Many-to-many (~2.7 SOCs per CIP).
Unmatched codes use sentinel rows (`CIPCODE 99.9999` / `SOCCode 99-9999`). `core/crosswalk.py`
normalizes columns to `CIPCODE / CIPTitle / SOCCode / SOCTitle` and filters sentinels (the v1
path keeps them harmlessly; the labor path drops them). Full structure in
`docs/CROSSWALK_INSPECTION_FINDINGS.md`.

### Labor sources (`data/raw_labor/`) — vintages differ; see Vintage Discipline
| Source | File / access | Provides | Vintage |
|---|---|---|---|
| BLS OEWS | `oews/national_M2025_dl.xlsx`, `state_M2025_dl.xlsx` | Wages + employment by SOC, national + 50 states | May 2025 |
| BLS Projections | `projections/occupation_2024-2034.xlsx` (Table 1.2) | 10-yr growth %, annual openings, median wage | 2024–2034 |
| CA EDD | `edd/edd_long_term_occ_projections_2023-2033.xlsx` | CA growth, openings, wages | 2023–2033 |
| Census ACS | API (key in `.env`), cached to `census/` | State population, 18–24 cohort, attainment | ACS 5-yr 2023 |

Each loader returns a documented long-format schema, handles suppression flags
(OEWS `*` / `**` / `#` → NaN) and source quirks (EDD newline column names, sentinel row;
BLS thousands units; EDD openings are a 10-yr total → divided to annual). Details and the
quirks list in `docs/LABOR_SOURCES_INSPECTION.md`.

---

## CIP→SOC Aggregation (three modes)

The crosswalk publishes no weights, so combining a CIP's linked SOCs into one number is a
methodology choice (`docs/CIP_SOC_AGGREGATION.md`):

| Mode | Behavior |
|---|---|
| **employment_weighted** (default) | Weight SOC metrics by national OEWS employment. Reflects realistic graduate destinations. |
| **median** | Unweighted median across linked SOCs. |
| **flat** | No aggregation; one row per (CIP, SOC, state). |

Suppressed cells are excluded from a weighted aggregate per-cell (one missing value doesn't
void the row). National OEWS employment is the weight source for every metric, including
projections, for consistency.

---

## Calculations

### 5-year CAGR
```
CAGR = (end / start) ** (1 / (end_year - start_year)) - 1     # exponent 1/4 for 2020→2024
```
Flags: `OK` (both endpoints >0) · `New Program` (start=0, N/A) · `Program Ended`
(start>0, end=0 → −100%) · `Missing Data` (endpoint NaN, N/A).

### Market view — two program counts
Per (CIPCODE × AWLEVEL), Selected and National:
- **Completions** — sum of CTOTALT per year (suppressed excluded via `min_count=1`).
- **Programs (degree-conferring)** — distinct UNITIDs with CTOTALT > 0 that year.
- **Programs (all reported, incl. 0 graduates)** — distinct UNITIDs filing any record that
  year (includes new programs pre-first-cohort). Counted per year, so closed programs drop
  out. *(v2 addition; computed in `reports/combine.py`, not the v1 engine.)*

Both counts and Completions get a 5-yr CAGR. Rendered long format, six rows per program.

### Labor metrics (per CIP × geography)
Aggregated from the linked SOCs per the chosen mode: median/mean annual wage and percentiles,
total employment (OEWS); 10-yr growth % and annual openings (BLS national; CA EDD for
California); plus state population / 18–24 cohort / bachelor's-attainment (Census).

### Saturation ratio
```
saturation = annual completions / annual openings
```
Per CIP × geography (California uses CA completions ÷ CA EDD openings; national uses national
completions ÷ BLS openings). **Directional only** — caveats: completions reflect the award
filter while openings span all degree levels; graduates relocate; CIP-SOC overlap means
openings are shared across CIPs; vintages differ. Heuristic reading bands accompany the
number. Full caveat text in `reports/combine.py::SATURATION_CAVEAT` and the Disclosure sheet.

---

## Vintage Discipline
OEWS May 2025, BLS Projections 2024–2034, CA EDD 2023–2033, Census ACS 5-yr 2023 do **not**
share a base year. "Median wage 2024" (BLS Projections) ≠ OEWS May 2025 wage; carry both with
labels. Every combined report's Disclosure sheet states the mismatch and that cross-source
comparisons require normalization. Source-of-truth for vintage strings is each loader's
output, surfaced through `labor/aggregator.py`'s vintage dict — not hard-coded.

---

## Output Workbook
`output/reports/IPEDS_Completions_COMBINED_{label}_{timestamp}.xlsx` — 16 sheets.

**Completions:** `Institutions`; `Completions_2020`…`_2024`; `CAGR_by_Institution`;
`Market_View` (six rows/program, long).
**Labor:** `Labor_View_Long`; `Labor_Detail_by_State`; `Saturation_by_CIP`;
`Combined_Wide_Drilldown` (all columns, one wide row per CIP × award level);
`Labor_Flat_SOC_Level`; `Unmatched_CIPs`; `Disclosure`.

Formatting: bold gray header, frozen top row, integer/percent/currency formats per column,
auto-fit widths. Code/title columns (CIPCODE, SOC, etc.) written as text so leading zeros
survive. v2 sheets use plain-English column names from `reports/labels.py`.

---

## Institution Selection
CLI `src/main_v2.py` flags (same as v1 plus `--labor-mode`):

| Flag | Purpose |
|---|---|
| `--search NAME` | Fuzzy name search; interactive picker |
| `--state ABBR` | All institutions in state (confirms if >200) |
| `--control {1,2,3}` / `--iclevel {1,2,3}` | Sub-filters for `--state` |
| `--unitids ID …` | Specific UNITIDs |
| `--cip CODE …` / `--awlevel N …` | Override `cip_filter.yaml` (quote CIPs to keep leading zeros) |
| `--include-residual` | Keep CIP-99 rollups |
| `--labor-mode {employment_weighted,median,flat}` | CIP→SOC aggregation (default employment_weighted) |
| `--output DIR` / `--verbose` | Output path / per-step logging |

The web app adds an "All institutions nationally" mode (auto-selects institutions with
completions in the chosen CIPs). Labor projections are CA-only (EDD); other states get
national openings or an "unavailable" flag.

---

## Data Quality Rules
1. Filter `MAJORNUM == 1` before any aggregation — no exceptions.
2. CIPCODE as string; SOC as `XX-XXXX` string. Leading zeros preserved in Excel.
3. Suppressed cells stay NaN; never imputed. Suppression counts surfaced in labor detail.
4. Validate CIPCODE `\d{2}\.\d{4}`; non-6-digit rollups excluded unless `--include-residual`.
5. Mixed file formats: `.xlsx` via `read_excel`, `.csv` via `read_csv(encoding='latin-1')`.
6. Unmatched CIPs (no SOC mapping) surfaced explicitly, never silently dropped.
7. Reported-program counts are per-year (closed programs excluded structurally).
8. Every combined report discloses source vintages and the saturation caveat.

---

## Tech Stack
`pandas`, `openpyxl`, `pyyaml`, `rich`, `rapidfuzz`, `streamlit`, `matplotlib`. Census API
queried via stdlib `urllib` (no extra dependency). Python 3.9+.

---

## Status
Feature-complete (Phases 0–6 per `docs/PHASE_PLAN.md`). Deferred to a future v3: job-postings
volume (CareerOneStop), scheduled auto-refresh, and non-CA state projections.
