# V2 Kickoff — Labor Market Layer

## Project state

| | |
|---|---|
| **v1 folder** | `~/Library/Mobile Documents/com~apple~CloudDocs/Work/Strategic Planning Analyst/Strategic Planning Analyst/Claude Code IPEDS Lookup Tool/` (iCloud Drive) |
| **v1 status** | Working. CLI tool + Streamlit app. Processes IPEDS Completions → Excel report (CAGR, program growth, market view). **Do not modify.** |
| **v2 folder** | `~/Library/Mobile Documents/com~apple~CloudDocs/Work/Strategic Planning Analyst/Strategic Planning Analyst/Claude Code IPEDS Lookup Tool v2/` (this folder, copy of v1, iCloud Drive) |
| **v2 goal** | Add labor market layer joined via SOC codes. For any CIP, surface BLS OEWS wages (national + state), BLS 10-yr Employment Projections, CA EDD LMI, Census population context. Job postings deferred to v3. |

## Architectural decision (resolved)

**Orchestrator pattern.** Modular package with shared `core/`, independent `completions/` and `labor/` modules, and a `reports/` orchestrator that produces both standalone and combined Excel outputs. Rationale and target layout in `docs/ARCHITECTURE.md`.

Rejected alternatives:
- **Monolithic extend** — couples mismatched refresh cadences; harder to test labor module in isolation.
- **Sibling tool** — duplicates crosswalk logic; forces manual merge for combined output.

## Open design decisions

| Decision | Status | Where resolved |
|---|---|---|
| CIP→SOC aggregation method | **Resolved** — implement three view modes; default to employment-weighted | `docs/CIP_SOC_AGGREGATION.md` |
| State coverage | **Resolved** — load all 50; CA+national is default view, not data scope | `docs/ARCHITECTURE.md` |
| Job postings inclusion | **Resolved** — defer to v3 | `docs/PHASE_PLAN.md` |
| Refresh cadence automation | **Resolved** — manual annual via `scripts/refresh_labor.py` checklist; no scheduler | `docs/DATA_SOURCES.md` |
| Backward compat with v1 CLI | **Hard constraint** — v1 command + Excel output unchanged | `CLAUDE.md` |

## Discrepancies to verify in Phase 0

| Item | Assumed | Verify |
|---|---|---|
| App type | CLI → Excel | Confirm by running `python -m <entry> --help` or equivalent |
| Crosswalk file | `data/dictionary/cip_soc_crosswalk.csv` | Confirm filename, location, format |
| BLS source naming | OEWS (renamed from OES in 2021) | Use OEWS in all new code/docs |

## Sourcing standard (carry through all work)

- Confirmed vs. inferred — label both.
- Primary sources only — BLS bulletins, NCES files, Census API, EDD publications. No secondary blog paraphrases.
- Every methodology choice documented in code comment AND in report footer.
- When uncertain, say so. Don't paper over gaps.

## First prompt to give Claude Code in the new chat

> Read `CLAUDE.md` and all files in `docs/`. Then perform the Phase 0 crosswalk inspection per `docs/CROSSWALK_INSPECTION.md` and report findings as a structured summary. Do not modify any existing v1 code. After the inspection, surface any new design questions and wait for my decisions before proposing module structure changes.
