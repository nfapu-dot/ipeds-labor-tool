# Crosswalk Inspection — Phase 0

Run this before proposing any module structure or aggregation code. Findings determine downstream design.

## Inspect

Target file: `data/dictionary/cip_soc_crosswalk.xlsx` (Excel workbook; main data on sheet `CIP-SOC`)

Run a read-only inspection script and report:

| Question | What to capture |
|---|---|
| File format confirmed? | CSV vs XLSX; encoding; delimiter |
| Total row count | Integer |
| Column names + dtypes | Full list |
| Distinct CIP codes | Count |
| Distinct SOC codes | Count |
| CIP vintage | 2010 / 2020 / other; how identified (column? filename? doc?) |
| SOC vintage | 2010 / 2018 / 2018 revised / other |
| CIPs per SOC distribution | min, median, p90, max |
| SOCs per CIP distribution | min, median, p90, max |
| CIPs with zero SOC mappings | Count + sample |
| SOCs with zero CIP mappings | Count + sample |
| Any "primary" or weight column? | Yes/no; column name if yes |
| Any flags or notes columns? | List |
| Sample rows | First 5, last 5, plus 5 random |
| Encoding/whitespace anomalies | CIP codes with leading zeros stripped? SOC codes formatted as `XX-XXXX` consistently? |

## Output

Write findings to `docs/CROSSWALK_INSPECTION_FINDINGS.md` with these sections:

1. **Confirmed facts** — what the file actually contains.
2. **Surprises** — anything unexpected vs. assumptions in `CIP_SOC_AGGREGATION.md` or `DATA_SOURCES.md`.
3. **Design implications** — does the aggregation methodology need adjustment? Any new design questions?
4. **Recommended next step** — proposed Phase 1 structure given findings.

## Hard rules

- **Read-only.** Do not write to, rename, or move the crosswalk file.
- **No code restructuring yet.** Inspection only.
- **Wait for user approval** before advancing to Phase 1.

## If the file is missing or malformed

Stop. Report to user. Do not attempt to download a replacement without explicit instruction — vintage matters and the wrong crosswalk silently breaks aggregation.
