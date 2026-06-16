# Labor Sources Inspection — Phase 2 (Findings)

Inspection of the four labor data sources downloaded on 2026-05-28. Same rigor as Phase 0 crosswalk inspection (`CROSSWALK_INSPECTION_FINDINGS.md`). Read-only — no loader code yet. Findings determine Phase 3 loader designs.

---

## Vintage alignment — flagged for every report footer

| Source | Vintage | Released |
|---|---|---|
| BLS OEWS | **May 2025** | 2026-05-15 |
| BLS Employment Projections | **2024 → 2034** | August 2025 |
| CA EDD Long-term Projections | **2023 → 2033** | July 2025 |
| Census ACS | **2023 ACS5** (default) | annual |

**Vintages do not align.** Aggregator must surface this in every combined-report footer. Specifically:
- OEWS base year (2025) is one year after BLS Projections base (2024) and two years after EDD base (2023).
- BLS Projections target year (2034) is one year after EDD target (2033).
- Wages from OEWS will not equal "Median wage, 2024" from BLS Projections (which was set in a separate cycle).

**Disclosure language for report footers (drafted here so it carries through):**
> *Sources reflect the most recent release as of report date. Vintages: OEWS May 2025; BLS Projections 2024–2034; CA EDD Long-term 2023–2033; Census ACS 5-year 2023. Year-over-year comparisons across sources are not valid without explicit normalization.*

---

## 1. BLS OEWS — Occupational Employment and Wage Statistics

### Files

| Path | Size | Notes |
|---|---|---|
| `data/raw_labor/oews/oesm25nat.zip` | 280 KB | Original archive; kept for provenance |
| `data/raw_labor/oews/national_M2025_dl.xlsx` | 290 KB | Extracted by loader; **read this** |
| `data/raw_labor/oews/oesm25st.zip` | 7.6 MB | Original archive |
| `data/raw_labor/oews/state_M2025_dl.xlsx` | 7.6 MB | **One combined workbook for all 50 states + DC + PR + GU + VI.** Not per-state. |

### Structure (both files share the same 32-column schema)

| | National | State |
|---|---|---|
| Sheets | `national_M2025_dl`, Field Descriptions, UpdateTime, Filler | `state_M2025_dl`, Field Descriptions, UpdateTime, Filler |
| Data sheet rows | 1,401 | 37,408 |
| Distinct areas | 1 (`U.S.`) | 54 (50 states + DC, PR, GU, VI) |
| O_GROUP values | total / major / minor / broad / detailed | total / major / detailed |

The state file is **less granular** than the national file — only major-occupation and detailed-occupation rows, no minor/broad summaries. National has all 5 levels.

### Key columns (for the loader contract)

| Column | What it is | Loader use |
|---|---|---|
| `AREA_TITLE` | "U.S." (national) or state name | filter / label |
| `PRIM_STATE` | 2-letter state code (CA, NY, etc.) | join key |
| `OCC_CODE` | 6-digit SOC formatted `XX-XXXX` | **join to crosswalk SOCCode** |
| `OCC_TITLE` | Occupation title | display |
| `O_GROUP` | total / major / minor / broad / detailed | **filter to `detailed` for joins** |
| `TOT_EMP` | Employment count | **weight for aggregator** |
| `A_MEAN` | Annual mean wage | metric |
| `A_MEDIAN` | Annual median wage | metric (primary) |
| `H_MEAN`, `H_MEDIAN` | Hourly equivalents | secondary |
| `A_PCT10/25/75/90` | Annual wage percentiles | distribution context |

### Suppression flags (user-verified at download)

Confirmed in both files. Loader **must** convert to `NaN`, not numeric:

| Flag | Meaning | Where it appears |
|---|---|---|
| `**` | Estimate not released | TOT_EMP and others (1,318 cells in state file) |
| `*` | Estimate not available | A_MEAN, A_MEDIAN, etc. (625 cells in state file) |
| `#` | Wage ≥ \$115/hr or ≥ \$239,200/yr (top-coded) | A_MEAN, A_MEDIAN (92 cells in state file) |

### CA-specific spot-check

CA has 830 total rows in the state file; 807 at `O_GROUP=detailed`. Reasonable — matches the ~830 detailed-occupation universe seen in the national file.

### Loader design implications

- **Skip non-data sheets.** Read only the first sheet (named `{file_stem}` per file). The `Field Descriptions`, `UpdateTime`, and `Filler` sheets are metadata.
- **Filter `O_GROUP == 'detailed'`** before joining. Aggregation by parent SOCs (major / minor / broad) would double-count.
- **One state file, not 50.** Loader signature should be `load_oews_state(path)` not `load_oews_state(paths: list)`. Internally pivot on `PRIM_STATE`.
- **Suppression normalization is mandatory.** Pass `na_values=['**', '*', '#']` to `pd.read_excel` for the numeric columns. Or do a post-load string-pattern strip. Either way, ensure downstream code never sees `*` as a string in a numeric column.

---

## 2. BLS Employment Projections (2024 → 2034)

### File

`data/raw_labor/projections/occupation_2024-2034.xlsx` — 407 KB, 12 sheets (Index + Tables 1.1–1.12, skipping 1.7).

### Sheet usage decision

| Sheet | What | Use? |
|---|---|---|
| Index | Sheet contents | No (metadata) |
| Table 1.1 | Employment by major occupation group | No (summary only, no detail) |
| **Table 1.2** | Occupational projections — **the main data sheet** | **Yes** (16 columns; full per-occupation row) |
| Table 1.3 | Fastest growing | No (subset / derived) |
| Table 1.4 | Most numeric growth | No (subset / derived) |
| Table 1.5 | Fastest declining | No (subset) |
| Table 1.6 | Most numeric decline | No (subset) |
| Table 1.8 | Industry-occupation matrix index | No (metadata) |
| Table 1.9 | Industry-occupation matrix industries | No (industry breakdown — out of v2 scope) |
| Table 1.10 | Separations and openings | **Maybe** (richer separations detail; redundant with Table 1.2 for our use) |
| Table 1.11 | STEM-occupation roll-up | No (out of scope) |
| Table 1.12 | Factors affecting utilization | No (qualitative) |

**Recommend Table 1.2 as the primary source.** It has employment, change %, openings annual avg, median wage, plus education/training requirements — all in one row per occupation.

### Structure quirks

- **First row is the table title, not headers.** Skip row 0 (`skiprows=1` for headers in row 1, data in row 2+).
- 1,117 rows total: 832 detailed (`Occupation type == 'Line item'`), 285 summary (major/minor/broad rollups).
- All 832 detailed rows have well-formed 6-digit SOC codes — no surprises.

### Key columns

| Column | What |
|---|---|
| `2024 National Employment Matrix title` | Occupation title |
| `2024 National Employment Matrix code` | **6-digit SOC** (join key) |
| `Occupation type` | `Line item` (detailed) or `Summary` (parent) |
| `Employment, 2024` | Base employment (thousands) |
| `Employment, 2034` | Projected employment (thousands) |
| `Employment change, numeric, 2024–34` | Change (thousands) |
| `Employment change, percent, 2024–34` | Change % |
| `Occupational openings, 2024–34 annual average` | **Annual openings (thousands)** |
| `Median annual wage, dollars, 2024[1]` | Wage at projection base year |
| `Typical education needed for entry` | Categorical |
| `Work experience in a related occupation` | Categorical |
| `Typical on-the-job training needed to attain competency in the occupation` | Categorical |

**Note on wage:** This is "Median annual wage, 2024" — different vintage from the OEWS May 2025 wage. Don't blend them; carry both as separate columns.

### Loader design implications

- Read sheet `Table 1.2`, `skiprows=1` to land headers at row 0.
- Filter `Occupation type == 'Line item'` for the 832 detailed rows.
- Convert thousands-unit columns to absolute integers if downstream code expects raw counts (or keep as thousands and document — pick one convention).
- The footnote `[1]` in the wage column header is harmless in pandas; don't strip it from the source name.

---

## 3. CA EDD — Long-term Occupational Projections (2023 → 2033)

### File

`data/raw_labor/edd/edd_long_term_occ_projections_2023-2033.xlsx` — 128 KB, 2 sheets.

### Structure quirks

- Sheet 0 (`Occupational`) has **3 header rows of metadata** before the actual column names: title, geography (`California`), scope (`Statewide`). Column names sit on row 3 (0-indexed); data starts row 4. Use `skiprows=3`.
- 794 data rows including a literal **`End of worksheet.`** sentinel row in the last position (in the `SOC Level[1]` column). Loader must filter it out.
- Distinct SOC levels: 1 (total) / 2 (major) / 3 (minor) / 4 (detailed) — matches BLS Projections semantics but uses numeric labels instead of strings.
- 676 detailed rows, all 6-digit SOC codes.
- Column names contain literal `\n` newlines in several cases (`Median Annual Wages\n[10]`, `Total Job Openings\n[9]`). Don't strip the `\n` — match exactly when referencing them, OR normalize via `df.columns = df.columns.str.replace('\n', ' ').str.strip()` on load.
- Notes sheet has metadata only (publication date `July 2025`, footnote definitions).

### Key columns

| Column (verbatim) | What |
|---|---|
| `SOC Level[1]` | 1 / 2 / 3 / 4 |
| `SOC Code[2]` | 6-digit SOC |
| `Occupational Title[3]` | Title |
| `Base Year Employment Estimate 2023[4][5]` | Base employment |
| `Projected Year Employment Estimate 2033` | Target employment |
| `Numeric Change 2023-2033[6]` | Change count |
| `Percent-age Change 2023-2033` | Change % (stored as decimal in this file — e.g., 0.0826, not 8.26) |
| `Exits\n[7]` | Annual exits |
| `Transfers\n[8]` | Annual transfers |
| `Total Job Openings\n[9]` | **Annual openings** |
| `Median Hourly Wages\n[10]` | CA hourly median |
| `Median Annual Wages\n[10]` | CA annual median |
| `Entry Level Education\n[11][12]` | Categorical |
| `Work Experience\n[11][12]` | Categorical |
| `On-the-Job Training\n[11][12]` | Categorical |

**Important format difference:** EDD's `Percent-age Change` is stored as a **decimal** (`0.0826` = 8.26%). BLS Projections stores the same metric as a number (`6.1` = 6.1%). Loader must normalize one or the other to avoid silent 100× errors downstream.

### Loader design implications

- `skiprows=3` to land on the real header row.
- Filter `SOC Level[1] == '4'` for detailed-occupation rows.
- Drop the trailing `End of worksheet.` sentinel — easy: it has no SOC code, so dropping `NaN` SOC rows handles it.
- Normalize the percent-change column: multiply by 100 OR convert BLS Projections to decimal — pick one, document the choice. Recommend converting both to **decimal** since that's the more error-resistant convention for downstream math.
- Cross-source SOC vintage check at load time: EDD says "SOC system" but doesn't print the vintage. The publication date (July 2025) implies SOC 2018 still; if EDD ever publishes SOC 2018-revised or moves to a new vintage, the loader should warn.

---

## 4. U.S. Census Bureau API

### Setup confirmed

- `.env` exists at project root with `CENSUS_API_KEY=` set (40-character key, gitignored).
- Live API smoke-test query returned CA 2023 ACS5 total population: **39,242,785**. API key works.

### Loader design implications

- Loader is **API-driven**, not file-driven. No raw_labor file to read.
- Recommend caching responses to `data/raw_labor/census/<query_hash>.json` to avoid re-hitting the API on every report run.
- Required v2 fields per `DATA_SOURCES.md`: state population, 18–24 age cohort, educational attainment.
- Use ACS 5-year (more stable than 1-year for small geos) as the default. Allow 1-year override via parameter.
- Key variables to query (initial list — refine when writing the loader):
  - `B01001_001E` — total population
  - `B01001_007E` through `B01001_010E` (M) + `B01001_031E` through `B01001_034E` (W) — 18–24 cohort
  - `B15003_022E` through `B15003_025E` — bachelor's degree or higher (educational attainment)
- API tolerates parallel state queries; one round-trip per state is fine. Avoid per-variable round-trips.

---

## Phase 3 loader build order (recommended)

1. **`src/labor/loaders/oews.py`** — first. It defines the employment-weights universe that aggregation depends on, and it's the most schema-complex file. Get the suppression-handling and `O_GROUP` filtering right here once; the rest is easier.
2. **`src/labor/loaders/projections.py`** — second. Adds growth signals to the wage-only OEWS picture. Table 1.2 → single-sheet read with `skiprows=1`.
3. **`src/labor/loaders/edd.py`** — third. CA-only enrichment; the column-name `\n` quirk and decimal-percent convention are the only non-trivial bits.
4. **`src/labor/loaders/census.py`** — last. Lowest analytical priority for v2 (it's context, not core), and writing it benefits from already having the cache directory convention used by the others.

Each loader should:
- Live in `src/labor/loaders/<source>.py`
- Have a single public function `load_<source>(path)` (or `load_<source>(state)` for Census)
- Return a long-format DataFrame with documented schema in the module docstring
- Handle suppression / sentinel rows gracefully — fail loudly if the column structure changes
- Have a unit test in `tests/test_<source>_loader.py` with a small fixture (a few representative rows; not the full file)

---

## Open questions (none blocking)

1. **Percent-change normalization** — EDD decimal vs BLS Projections numeric. Recommend decimal everywhere. **Default unless you object.**
2. **OEWS state filtering** — load all 54 areas (50 states + DC/PR/GU/VI) and let downstream filter? Or load all-50-plus-DC only? Recommend load-all-then-filter for forward compatibility. **Default unless you object.**
3. **Census cache invalidation** — never? on a schedule? for now, never (manual delete is fine for v2). **Default unless you object.**

These are all "I'll proceed with the default unless you say otherwise" — no need to answer unless something feels wrong.
