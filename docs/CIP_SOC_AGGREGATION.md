# CIP → SOC Aggregation Methodology

## The problem

The NCES CIP-SOC crosswalk is **many-to-many**:
- One CIP can map to multiple SOCs (e.g., a business CIP → several management SOCs)
- One SOC can be linked from multiple CIPs

**NCES publishes no weights.** There is no official "primary SOC" per CIP. Any aggregation involves a methodology choice that must be disclosed.

## Three view modes (all implemented; user selectable)

| Mode | Behavior | When to use |
|---|---|---|
| **1. Flat all-SOCs** | Return one row per (CIP, SOC) pair. No aggregation. | Drill-down analysis, verifying source data |
| **2. Median-of-medians** | For each CIP, take median across linked SOCs of each metric. Unweighted. | Quick scan; treats every linked SOC as equally relevant |
| **3. Employment-weighted (DEFAULT)** | For each CIP, weight SOC metrics by national `tot_emp` from OEWS. | Reflects realistic labor market mix; recommended default |

## Why default to employment-weighted

A business administration CIP linked to "Chief Executives" (very low employment) and "General and Operations Managers" (very high employment) shouldn't average them equally. Employment weighting reflects where graduates actually land.

## Implementation notes

- Compute all three modes on data load; cache. User toggles at report time, not data load time.
- Handle missing values: if an OEWS cell is suppressed for a SOC × state, exclude that SOC from that state's weighted aggregate. Document exclusions in the report footer.
- For projections (which use national-level data), weights also come from OEWS national `tot_emp`. Consistent weight source across metrics.

## Required disclosure (every report)

Footer must display:
- **Aggregation mode used** (Flat / Median-of-medians / Employment-weighted)
- **Crosswalk vintage** (e.g., "CIP 2020 → SOC 2018")
- **OEWS release year** used for weights
- **Caveat:** "CIP-SOC crosswalk is many-to-many with no NCES-published weights. Aggregation method chosen by tool; alternative methods may yield different results."

## Open question for Phase 0 inspection

The actual crosswalk file may include additional columns beyond CIP and SOC (e.g., notes, primary-flag fields, vintage indicators). The inspection step (`CROSSWALK_INSPECTION.md`) will surface these. If a primary-flag or weight column exists, revisit this methodology before locking it in.
