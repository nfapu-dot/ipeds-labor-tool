# Data Sources

All sources free and ToS-clean. Verify exact endpoints at fetch time — URLs below are starting points and may change.

## Summary table

| Source | Provides | Format | Cadence | API key | Confidence |
|---|---|---|---|---|---|
| BLS OEWS | Wages + employment by SOC, national + state | XLSX / CSV bulk download | Annual (May reference, spring release) | No | High |
| BLS Employment Projections | 10-yr occupational outlook by SOC | XLSX | Biennial | No | High |
| CA EDD LMI | CA long-term and short-term occupation projections | XLSX / CSV | Biennial (long-term) | No | High |
| Census Population API | State demographics, 18-24 cohort, educational attainment | JSON API | ACS 1-yr + 5-yr (annual) | **Yes (free)** | High |
| CareerOneStop (v3 only) | BLS wrapper + limited job posting volume | REST JSON | Real-time | **Yes (free)** | Medium |
| NCES CIP-SOC Crosswalk | CIP ↔ SOC mapping | XLSX | Updated with each CIP vintage (CIP 2020 current) | No | High |

## Source detail

### BLS OEWS (formerly OES — renamed 2021)
- **Site:** bls.gov/oes/ (URL path retained after rename)
- **What:** Occupational Employment and Wage Statistics. Mean/median wages, employment counts by SOC.
- **Files needed:** National (`oesm[YR]nat.zip`), state (`oesm[YR]st.zip`). Filenames change year to year — verify at fetch.
- **Notes:** Includes the `tot_emp` field used as weight in the employment-weighted aggregation. Confidentiality suppressions appear in some SOC × state cells — code must handle missing values gracefully.

### BLS Employment Projections
- **Site:** bls.gov/emp/
- **What:** 10-year projections by occupation. Current cycle to be verified at fetch — Claude Code should not assume which projection window is current.
- **Files needed:** Occupation projections table (typically `occupation.xlsx` or similar).
- **Notes:** National only. State-level projections come from EDD (for CA) or state agencies.

### CA EDD LMI (Employment Development Department, Labor Market Information)
- **Site:** labormarketinfo.edd.ca.gov
- **What:** California occupational projections (long-term, ~10 yr; short-term, ~2 yr). Wage data by occupation.
- **Files needed:** Long-term projection workbook. Short-term optional.
- **Notes:** Uses SOC. Cross-check SOC vintage against BLS (occasional lag).

### Census Population API
- **Site:** api.census.gov
- **Key signup:** api.census.gov/data/key_signup.html (free, instant)
- **What:** ACS 1-year and 5-year estimates. State population, age cohorts (especially 18-24), educational attainment.
- **Notes:** Store key in environment variable `CENSUS_API_KEY` or local `.env` (gitignored). Never commit.

### CareerOneStop (deferred to v3)
- **Site:** careeronestop.org/Developers
- **Key signup:** free DOL signup
- **What:** Wraps BLS data; provides limited job posting volume.
- **Posture:** Defer. Posting depth is shallow vs. paid sources (Lightcast, etc.). Reassess after v2 ships.

### NCES CIP-SOC Crosswalk
- **Site:** nces.ed.gov/ipeds/cipcode/
- **What:** Official CIP ↔ SOC mapping. Current vintage CIP 2020.
- **Already in repo:** `data/dictionary/cip_soc_crosswalk.xlsx` (Excel workbook; data on sheet `CIP-SOC`)
- **Critical property:** Many-to-many with NO published weights. See `CIP_SOC_AGGREGATION.md`.

## Storage convention

```
data/raw_labor/
├── oews/
│   ├── national_<YYYY>.xlsx
│   └── state_<YYYY>.xlsx
├── projections/
│   └── occupation_<PROJ_WINDOW>.xlsx
├── edd/
│   └── ca_longterm_<WINDOW>.xlsx
├── census/                          # API cache only; no bulk file
│   └── (cached JSON, optional)
└── .refresh_log.json                # last-fetched dates per source
```

## Footer disclosure (every Excel and console output)

Every report must include:
- Each data source name + vintage/release year used
- Date downloaded
- Crosswalk vintage (NCES year)
- Aggregation mode used (see `CIP_SOC_AGGREGATION.md`)
- One-line caveat about crosswalk many-to-many limitation
