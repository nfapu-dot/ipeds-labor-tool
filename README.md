# IPEDS Completions + Labor Market Tool (v2)

Strategic-planning tool that joins **IPEDS Completions** (degrees conferred, by program
and award level) with the **labor market** those programs feed into — wages, employment,
10-year occupational projections, and demographic context. For any CIP code it answers
two questions side by side:

- **Student demand** — how many graduates, growing or shrinking, here vs. nationally?
- **Labor demand** — what do those jobs pay, how fast are they growing, how many annual
  openings, and how does graduate output compare to openings (saturation)?

It produces a formatted Excel workbook and a point-and-click web app.

> **This is v2.** The original student-demand-only tool (**v1**) lives in the sibling
> folder `../Claude Code IPEDS Lookup Tool/` and still runs there independently. v2 = v1's
> completions analysis **plus** the labor market layer. The two are parallel; running v2
> does not affect v1.

---

## Launching

### Web app (recommended)
Double-click **`Launch IPEDS Tool v2.command`**. A Terminal window opens (leave it open —
it runs the app), and your browser opens to `http://localhost:8502`.

First load takes ~10 seconds (reads ~1.4M IPEDS rows). The first "Generate Combined Report"
of a session takes ~20 seconds while it loads and aggregates the labor data; every report
after that is ~2 seconds (data is cached for the session).

### CLI (scripted / power users)
```bash
python src/main_v2.py --state CA --cip 51.3801 52.0201 11.0701
```
Output: `output/reports/IPEDS_Completions_COMBINED_{label}_{timestamp}.xlsx`.

---

## Using the web app

**Sidebar — pick what to analyze:**
1. **Selection mode** — *All institutions nationally* · *By state(s)* · *By institution name*
   · *Specific UNITIDs* · *From institutions.csv*. (National mode auto-selects every
   institution with completions in your chosen CIPs.)
2. **Program filters** — CIP codes (searchable by name) and award levels. Leave empty for all.
3. **Labor settings** — CIP→SOC aggregation method (see [Key concepts](#key-concepts-for-interpretation)).
4. Click **Generate Combined Report** → download button + tabs appear.

**Tabs:** Institutions · CAGR by Institution · Market View · Labor View · Wage Detail ·
Growth & Openings · Saturation · Unmatched CIPs · Definitions · Disclosure.

The first three are v1's completions analysis (unchanged in behavior); the rest are the
labor layer. Labor tabs respect your CIP filter.

---

## What's in the workbook

Filename: `IPEDS_Completions_COMBINED_{label}_{timestamp}.xlsx`. Sixteen sheets:

**Completions (student demand)**
| Sheet | Contents |
|---|---|
| `Institutions` | Selected institutions with control / level / Carnegie labels. |
| `Completions_2020`…`_2024` | Per-year rows: institution × CIP × award level, completions (total/men/women). |
| `CAGR_by_Institution` | One row per institution × CIP × award level; year columns + 5-yr CAGR + flag, green/red highlighting. |
| `Market_View` | Long format, **six rows per program** (Selected + National × Completions, Programs (degree-conferring), Programs (all reported, incl. 0 graduates)). |

**Labor market**
| Sheet | Contents |
|---|---|
| `Labor_View_Long` | One row per CIP × geography × source × metric — wages, growth, openings. The primary labor view. |
| `Labor_Detail_by_State` | Per-CIP × state labor detail (wages, employment, suppression counts). |
| `Saturation_by_CIP` | Annual completions ÷ annual openings, per CIP × geography, with a plain-English reading. |
| `Combined_Wide_Drilldown` | Everything joined, one wide row per CIP × award level — for analysts who want all columns at once. |
| `Labor_Flat_SOC_Level` | Drill-down to the individual SOC occupations behind each CIP. |
| `Unmatched_CIPs` | CIPs in your selection with no SOC mapping (no labor signal available). |
| `Disclosure` | Source vintages, aggregation method, and the full footer disclosure + caveats. |

---

## Key concepts for interpretation

**CIP→SOC aggregation method.** Each CIP maps to several SOC occupations with no official
weights. You choose how to combine them:
- **Employment-weighted (default)** — weights occupations by national employment, so common
  jobs dominate. Best reflects where graduates land.
- **Median of medians** — unweighted; treats every linked occupation equally.
- **Flat** — no aggregation; one row per CIP × SOC for auditing.

**Two program counts.** The Market View shows both:
- **Programs (degree-conferring)** — institutions that conferred ≥1 award that year.
- **Programs (all reported, incl. 0 graduates)** — institutions that reported the program
  even if no one graduated yet. Counts *per year*, so closed programs drop out automatically.
  This is the convention tools like Hanover use; it runs ahead of degree-conferring in fast-
  growing fields (new programs exist before their first cohort graduates).

**Saturation ratio** = annual completions ÷ annual openings. A **directional signal only** —
completions reflect your award-level filter but openings span all degree levels; graduates
move across state lines; the CIP-SOC map is many-to-many. The Saturation tab and the
Disclosure sheet carry the full caveat. Don't read it as a precise supply/demand balance.

**Vintage mismatch (important).** The sources have different base years — OEWS May 2025,
BLS Projections 2024–2034, CA EDD 2023–2033, Census ACS 5-year 2023. Cross-source
comparisons aren't valid without normalization. Every report's Disclosure sheet states this.

**Geographic coverage.** State-level wages come from OEWS (all 50 states). State-level
*projections* come from CA EDD (**California only**) — for other states, saturation falls
back to national openings or is flagged unavailable.

---

## Annual data refresh

Labor data is downloaded manually (no scraping, no paid sources). Once a year:
```bash
python3 scripts/refresh_labor.py            # prints a checklist of sources, URLs, status
python3 scripts/refresh_labor.py --mark-fetched oews   # after downloading each source
```
Current vintages and source URLs are in [docs/DATA_SOURCES.md](docs/DATA_SOURCES.md) and
[docs/LABOR_SOURCES_INSPECTION.md](docs/LABOR_SOURCES_INSPECTION.md).

---

## Requirements & setup

- Python 3.9+
- `pip install -r requirements.txt`
- Census API key (free) in a `.env` file — see [.env.example](.env.example)
- IPEDS files in `data/raw/`, dictionary + crosswalk in `data/dictionary/`, labor files in
  `data/raw_labor/` (all already in place). Default year range 2020–2024, set in
  [config/years.yaml](config/years.yaml).

---

## Project layout

```
Claude Code IPEDS Lookup Tool v2/
├── CLAUDE.md                       Project guidance for AI sessions
├── README.md  SPEC.md             This file + the technical spec
├── Launch IPEDS Tool v2.command   ★ Double-click to start the web app (port 8502)
├── config/                        years.yaml · cip_filter.yaml · institutions.csv
├── data/
│   ├── raw/                       IPEDS HD + C_A survey files
│   ├── dictionary/                varlist + CIP-SOC crosswalk (.xlsx)
│   └── raw_labor/                 OEWS · BLS Projections · CA EDD · Census cache
├── docs/                          Methodology + inspection findings (see below)
├── scripts/refresh_labor.py       Annual labor-data refresh checklist
├── src/
│   ├── loader.py joiner.py aggregator.py reporter.py resolver.py   Shared completions engine
│   ├── core/crosswalk.py          CIP-SOC loader (shared)
│   ├── labor/                     loaders/ (oews, projections, edd, census) + aggregator.py
│   ├── reports/                   combine.py (orchestrator) · writer.py · labels.py
│   ├── app_v2.py                  Streamlit web app
│   ├── main_v2.py                 CLI
│   └── main.py                    Regression-test entry point only (not a user app)
├── tests/                         Unit + integration + v1-regression tests
└── output/reports/                Generated workbooks
```

**`main.py` note:** the only "v1" file left in this folder. It exists solely so
`tests/test_v1_regression.py` can prove the shared completions engine still produces correct
output after any edit. It is not a user-facing app — use `main_v2.py` / the web app.

---

## Read more

| Doc | Covers |
|---|---|
| [docs/DATA_SOURCES.md](docs/DATA_SOURCES.md) | Every external source, format, cadence, API keys |
| [docs/CIP_SOC_AGGREGATION.md](docs/CIP_SOC_AGGREGATION.md) | The three aggregation modes and why employment-weighted is default |
| [docs/CROSSWALK_INSPECTION_FINDINGS.md](docs/CROSSWALK_INSPECTION_FINDINGS.md) | Structure of the CIP-SOC crosswalk (vintages, sentinels, coverage) |
| [docs/LABOR_SOURCES_INSPECTION.md](docs/LABOR_SOURCES_INSPECTION.md) | Structure + quirks of each labor file; the vintage-misalignment disclosure |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | Orchestrator design |
| [SPEC.md](SPEC.md) | Full technical specification |

---

## Running the tests

```bash
python3 tests/test_v1_regression.py     # completions engine unchanged (byte-identical baseline)
python3 tests/test_combine.py           # end-to-end combined report
python3 tests/test_aggregator.py        # CIP→SOC aggregation, all three modes
# plus test_oews_loader / test_projections_loader / test_edd_loader /
#      test_census_loader / test_core_crosswalk
```
After editing any shared-engine module (`loader`, `joiner`, `aggregator`, `reporter`,
`resolver`), re-run the regression test — it must stay byte-identical to the baseline.
