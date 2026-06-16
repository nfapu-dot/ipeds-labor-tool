# Crosswalk Inspection — Findings (Phase 0)

Inspection of `data/dictionary/cip_soc_crosswalk.xlsx`. Read-only; no files modified outside `docs/`. v1 pipeline (CLI + Streamlit) untouched.

---

## 1. Confirmed facts

### File

| Property | Value |
|---|---|
| Path | `data/dictionary/cip_soc_crosswalk.xlsx` |
| Format | **XLSX**, not CSV (docs reference `.csv` — see §2) |
| Size | 428,901 bytes |
| Sheets (8) | `File Guide`, `CIP-SOC`, `SOC-CIP`, `New CIP`, `New SOC`, `Added Matches`, `Unmatched CIP Codes`, `Unmatched SOC Codes` |

### Vintage (confirmed from File Guide sheet)

> "This file crosswalks **2020 CIP Codes** to **2018 SOC Codes** in ascending order by CIP Code."

- CIP vintage: **2020**
- SOC vintage: **2018**
- Matches assumption in `CIP_SOC_AGGREGATION.md`. No surprise here.

### Main sheet (`CIP-SOC`)

| | |
|---|---|
| Rows | **6,097** |
| Columns (4) | `CIP2020Code`, `CIP2020Title`, `SOC2018Code`, `SOC2018Title` |
| Dtypes | all `object` (strings) |
| Nulls | **zero** in every column |
| Duplicate (CIP, SOC) pairs | **zero** |
| Whitespace anomalies | **none** (no leading/trailing whitespace in any column) |
| CIP format | `^\d{2}\.\d{4}$` — **100% conforming**; leading zeros preserved (553 rows begin with "0") |
| SOC format | `^\d{2}-\d{4}$` — **100% conforming** |
| Cosmetic note | 5,916 of 6,097 CIP titles end with a trailing `.` (e.g., `"Agriculture, General."`). Harmless for joins. |

### Distinct counts

| | Count |
|---|---|
| Distinct CIP codes (incl. sentinel) | 2,143 |
| Distinct CIP codes (real, ex-sentinel) | **2,142** |
| Distinct SOC codes (incl. sentinel) | 868 |
| Distinct SOC codes (real, ex-sentinel) | **687** |

### Distribution — SOCs per CIP (real mappings only)

| min | median | p90 | p99 | max |
|---|---|---|---|---|
| 1 | 2 | 5 | 9 | **23** |

Top max: `52.0201 — Business Administration and Management, General` → 23 SOCs.

### Distribution — CIPs per SOC (real mappings only)

| min | median | p90 | p99 | max |
|---|---|---|---|---|
| 1 | 3 | 17 | 87 | **337** |

Top max: `25-1071 — Health Specialties Teachers, Postsecondary` → 337 CIPs.

### Primary / weight / flag columns

**None.** The crosswalk has exactly 4 columns. No "is_primary" flag, no employment weight, no relationship-strength score, no notes column.

This confirms the `CIP_SOC_AGGREGATION.md` premise: any rollup is a methodology choice the tool owns, not something NCES tells us.

### Sentinel-row convention (not noted in any v1 doc)

The publisher uses **sentinel rows**, not nulls, to encode "no match":

- `SOC2018Code = '99-9999'` with `SOC2018Title = 'NO MATCH'` → this CIP has no SOC mapping. **194 distinct CIPs** appear only on sentinel rows.
- `CIP2020Code = '99.9999'` with `CIP2020Title = 'NO MATCH'` → this SOC has no CIP mapping. **180 distinct SOCs** appear only on sentinel rows.
- No CIP has *both* a real SOC and a sentinel SOC row (clean partition — verified).

### Auxiliary sheets

| Sheet | Rows | Purpose |
|---|---|---|
| `SOC-CIP` | (not loaded; mirror of main with reversed sort) | Redundant for our needs |
| `New CIP` | 1,076 | NEW 2020 CIPs (not present in 2010 vintage) and their SOC mappings |
| `New SOC` | 415 | NEW 2018 SOCs (not present in 2010 vintage) and their CIP mappings |
| `Added Matches` | 1,007 | Pairs added on review of the 2010 crosswalk (provenance audit trail) |
| `Unmatched CIP Codes` | 194 | The 194 unmatched CIPs (also appear in `CIP-SOC` as sentinel rows) |
| `Unmatched SOC Codes` | 180 | The 180 unmatched SOCs (ditto) |

Useful for audit, not as a primary signal.

### Coverage vs. real IPEDS C_A data

Cross-check against `c2024_a.csv` (most recent IPEDS Completions in the repo):

| | Count |
|---|---|
| Distinct CIPs in C_A 2024 | 1,608 |
| Distinct CIPs in crosswalk (real) | 2,142 |
| C_A 2024 CIPs **not in** crosswalk | **2** |
| Crosswalk CIPs not in C_A 2024 | 536 (niche programs no one in 2024 produced) |

The 2 missing CIPs are:
- `99` — IPEDS residual rollup; v1 already excludes via `--include-residual` flag. Expected.
- `49.0109` — real CIP, growing each year (0 → 6 → 13 → 26 → 32 rows from 2020 → 2024). **Genuinely missing from the 2020 crosswalk.** Likely added to CIP after the 2020 vintage was published. Worth surfacing in the labor module as "no SOC mapping available."

---

## 2. Surprises

1. **Filename extension.** Docs (`DATA_SOURCES.md`, `ARCHITECTURE.md`, `CROSSWALK_INSPECTION.md`) reference `cip_soc_crosswalk.csv`. The file is `.xlsx`. v1's `loader.load_crosswalk` already handles both via prefix-based `_find_file('cip_soc_crosswalk')`, so the pipeline is unaffected — **but the docs are stale.**

2. **Sentinel rows ≠ nulls.** Unmatched CIPs/SOCs are encoded as literal `99.9999` / `99-9999` rows with title `'NO MATCH'`. Any aggregator that doesn't explicitly filter these will (a) double-count "unmatched" as a real SOC, (b) treat `99-9999` as a valid weight target. Must be filtered at the core loader.

3. **Extreme skew on the SOC side.** A handful of SOCs are linked to a huge number of CIPs (top: `25-1071` Health Specialties Teachers Postsecondary → 337 CIPs; `19-1042` Medical Scientists → 210 CIPs). Postsecondary-teaching SOCs in particular are linked to almost every related discipline. This actually **strengthens** the case for employment-weighted aggregation as default — a simple-mean approach would let teaching-postsecondary wages dominate any health, science, or business CIP.

4. **CIP `49.0109` exists in IPEDS 2021+ but not in the 2020 crosswalk.** CIP vintage drift. Not catastrophic — 32 rows in 2024 c_a — but the labor module needs to surface unmatched CIPs explicitly, not silently drop them.

5. **No primary/weight column anywhere.** This confirms (rather than surprises) the `CIP_SOC_AGGREGATION.md` premise. Worth restating because every analyst who picks up this file asks the same question.

---

## 3. Design implications

### Methodology — no change

`CIP_SOC_AGGREGATION.md`'s three-mode design (Flat / Median-of-medians / Employment-weighted default) stands. Inspection found nothing to override.

### Core loader contract (Phase 1)

`src/core/crosswalk.py::load_crosswalk()` should:

- Read `data/dictionary/cip_soc_crosswalk.xlsx`, sheet `CIP-SOC`, all-string dtype.
- Rename columns to canonical names already used by v1's `joiner.py`: `CIP2020Code → CIPCODE`, `CIP2020Title → CIPTitle`, `SOC2018Code → SOCCode`, `SOC2018Title → SOCTitle`. (v1 already does this in `loader._CROSSWALK_COLUMN_RENAMES` — reuse the same map for byte-identical behavior.)
- Strip whitespace defensively on code columns (no-op for this file, but cheap insurance against future drift).
- Provide a `drop_sentinels: bool = True` parameter. Default filters rows where `CIPCODE == '99.9999'` OR `SOCCode == '99-9999'`. Opt-in keeps them for audit views.
- Return a dataframe; do not cache to disk.
- Expose a small helper: `unmatched_cips(df) → list[str]` so the labor module can surface drop-outs.

### v1 backward compatibility

v1's `joiner.build_cip_title_lookup` only uses `CIPCODE` + `CIPTitle` from the loaded frame. The sentinel rows currently flow through; v1 dedupes by CIPCODE first-wins, so `99.9999/NO MATCH` exists as a row but never matches a real CIPCODE in `c_a`. **v1 behavior is unaffected** whether we filter sentinels in `core.crosswalk` or not, because v1 only reads two columns and never joins on SOC. Phase 1 can drop the sentinels without risk.

### Doc fixes (cheap, no code touched)

Update three places to say `.xlsx` (or "Excel workbook"):
- `docs/DATA_SOURCES.md` line 51
- `docs/ARCHITECTURE.md` line 31
- `docs/CROSSWALK_INSPECTION.md` line 7

Recommend doing this in the same commit that lands Phase 1.

### Labor module — explicit unmatched handling

When the aggregator hits a CIP with no real SOC mapping:

- **Flat mode** — emit one row with `SOCCode = NULL`, `SOCTitle = "(no SOC mapping in 2020 CIP × 2018 SOC crosswalk)"`. Don't silently drop.
- **Median / Weighted modes** — emit one row with all metrics NULL and a `crosswalk_status` column = `"unmatched"`.

This makes the 194 unmatched CIPs visible rather than mysteriously absent. Footer disclosure should report the count.

### Crosswalk vintage drift handling

For CIPs present in C_A but missing from the crosswalk entirely (e.g., `49.0109`): same treatment as unmatched, but with `crosswalk_status = "post-vintage CIP"`. This separates "NCES says no SOC" from "NCES hasn't published a mapping yet."

---

## 4. Recommended next step

Phase 1 as written in `PHASE_PLAN.md` is unchanged. Concrete first commit:

1. Create `src/core/__init__.py` and `src/core/crosswalk.py` with the contract above.
2. Add a thin shim in `src/loader.py::load_crosswalk` that delegates to `core.crosswalk.load_crosswalk(drop_sentinels=False)` to **exactly preserve v1's "sentinels in, no SOC join" behavior**. Decision: keep sentinels for v1's frame (zero behavior change); filter in the labor module's own load path.
3. Add a single regression test that loads the crosswalk via v1's old path and via the new shim and asserts identical frames (shape + content). Locks v1 in place.
4. Apply the three-line doc-extension fix above.
5. **Stop and check in with user** before Phase 2 (data acquisition) — the labor download workflow is what touches the network and disk most.

Hard line preserved: v1 CLI (`python -m main`) and Streamlit app (`streamlit run src/app.py`) remain insulated. The new module is additive; no existing files moved or renamed.

---

## v1 code structure summary (per Phase 0 acceptance)

- **Two entry points** — `src/main.py` (CLI) and `src/app.py` (Streamlit). Both feed the same downstream modules.
- **Pipeline** — `loader → resolver → joiner → aggregator → reporter`. CLI runs the full chain; Streamlit imports the same modules and feeds them from sidebar widget state.
- **Crosswalk loaded once** in `loader.load_crosswalk()`, sheet `CIP-SOC`, dtype-string, with a canonical column rename map (`CIP2020Code → CIPCODE` etc.). Returned in the `loaded` dict alongside HD, C_A, and varlist.
- **Crosswalk consumed once** in `joiner.build_cip_title_lookup()`, which deduplicates to one CIP per CIPCODE and discards all SOC columns before joining onto C_A. (Comment at `joiner.py:13-16` explicitly notes "SOC columns are retained on the loaded crosswalk DataFrame for v2 labor market integration but are NOT joined to C_A.")
- **SOC columns are dead weight in v1.** They survive the loader, are dropped before the join, and never reach the workbook. v2 can pick them up at the loader level without touching v1 behavior.
- **Flat package layout** — every module lives directly under `src/`. No subpackages today.
- **Project root resolved relative to each file** (`Path(__file__).resolve().parent.parent`). Both CLI and Streamlit are CWD-tolerant.
- **Imports are flat** (`from aggregator import ...`, no `src.` prefix). Streamlit puts `src/` on `sys.path` at startup; CLI must be run with `PYTHONPATH=src` or `cd src` to resolve imports.
- **Output is timestamped Excel** — `IPEDS_Completions_{label}_{YYYYMMDD_HHMMSS}.xlsx`. 9 sheets: Institutions, Completions_2020–2024, CAGR_by_Institution, Market_View, Definitions.
- **Configs read from `config/`** — `years.yaml`, `cip_filter.yaml`, and optional `institutions.csv`. The CLI requires *some* selection mode (`--state`, `--search`, `--unitids`, or a populated institutions.csv); refuses to run otherwise.
- **No tests today.** No `tests/` directory existed before this Phase 0 step.
- **Implication for Phase 1** — adding `src/core/crosswalk.py` and pointing v1's `load_crosswalk` at it is trivially safe: v1 already only reads two columns from the returned frame, and any sentinel-filtering decision is invisible to v1.

---

## v1 regression baseline (captured this session)

| | |
|---|---|
| Path | `tests/fixtures/v1_baseline.xlsx` |
| Generated by | `PYTHONPATH=src python3 src/main.py --state CA --output tests/fixtures` (CA, all CIPs, all award levels, 2020–2024) |
| Size | 9,915,618 bytes |
| SHA-256 | `d89aa27616b3aff6382079444753ce14505b371f4fe1b8eb0019583489dc1375` |
| Sheets | 9: Institutions (638), Completions_2020–2024 (~24K–27K each), CAGR_by_Institution (28,985), Market_View (40,552), Definitions (93) |

This file is the regression-lock for Phase 1. Any change that causes v1's CLI to produce a different workbook will be caught by the Phase 1 test.

---

## Open questions for user

1. **Doc-extension fix** — do you want it bundled with Phase 1 or done now as a docs-only commit?
2. **`49.0109` handling** — is the "post-vintage CIP" status label useful, or would you rather collapse it under generic "unmatched"?
3. **`src/core/` placement** — `PHASE_PLAN.md` puts it at `src/core/`. v1's existing modules sit flat in `src/` (no package nesting). Confirm you're OK introducing the `src/core/` subdir; if so, do the existing flat modules stay in place (parallel to `src/core/`), or do they eventually move into `src/completions/`? (Phase 1 doesn't force that decision; just want to confirm direction.)
