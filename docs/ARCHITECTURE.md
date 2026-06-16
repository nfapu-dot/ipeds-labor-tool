# Architecture — v2

## Decision

**Orchestrator pattern.** Single repo, modular package, shared core, independent loaders, top-level orchestrator producing combined output. Not a monolithic extension. Not sibling tools.

## Rationale

| Factor | Why orchestrator wins |
|---|---|
| Mismatched refresh cadences (IPEDS annual / OEWS annual / Projections biennial / postings live) | Each module refreshes independently; no forced recomputation |
| Output flexibility | Both standalone labor reports and combined reports supported by design |
| Shared CIP-SOC logic | Lives in one `core/` module, imported by both completions and labor |
| Future UI wrapper | Sits cleanly on the orchestrator as a single data layer |
| Module testability | Each module isolated; can mock the others |

## Target layout

```
Claude Code IPEDS Lookup Tool v2/
├── CLAUDE.md
├── docs/
│   ├── V2_KICKOFF.md
│   ├── ARCHITECTURE.md
│   ├── DATA_SOURCES.md
│   ├── CIP_SOC_AGGREGATION.md
│   ├── PHASE_PLAN.md
│   └── CROSSWALK_INSPECTION.md
├── data/
│   ├── dictionary/
│   │   └── cip_soc_crosswalk.xlsx        # existing (NCES workbook; data on "CIP-SOC" sheet)
│   ├── raw_completions/                   # existing IPEDS data
│   └── raw_labor/                         # NEW — BLS, EDD, Census downloads
├── src/
│   ├── core/                              # NEW — shared
│   │   ├── crosswalk.py                   # CIP↔SOC loader + helpers
│   │   ├── cip_utils.py                   # CIP normalization (2010/2020)
│   │   └── io_helpers.py                  # Excel writer, paths, config
│   ├── completions/                       # MIGRATED from existing v1 modules (preserve behavior)
│   │   └── (existing pipeline)
│   ├── labor/                             # NEW
│   │   ├── loaders/
│   │   │   ├── oews.py                    # BLS OEWS national + state
│   │   │   ├── projections.py             # BLS 10-yr Employment Projections
│   │   │   ├── edd.py                     # CA EDD LMI
│   │   │   └── census.py                  # Census Population API (ACS)
│   │   └── aggregator.py                  # CIP → SOC rollup, 3 view modes
│   └── reports/                           # NEW
│       └── combine.py                     # orchestrator → combined Excel
├── scripts/
│   └── refresh_labor.py                   # NEW — checklist runner for annual refresh
├── tests/
└── cli.py                                 # entry point with subcommands
```

## CLI design

| Subcommand | Behavior | Backward compat |
|---|---|---|
| `completions` | v1 behavior, unchanged output | **Hard requirement** |
| `labor` | Labor-only Excel report | New |
| `combined` | Joined completions + labor Excel | New |

v1's existing CLI invocation (whatever form it takes) must continue to produce identical output when run in v2 folder.

## State coverage

- **Data load:** all 50 states from OEWS state files; all 50 from Projections where available; CA-specific from EDD.
- **Default view:** national + CA in summary panels.
- **User control:** multi-select state filter at report-generation time.

## Refresh cadence philosophy

- **Manual annual** refresh via `scripts/refresh_labor.py` checklist.
- Script prints source URLs, expected file shapes, last-fetched dates from a local `data/raw_labor/.refresh_log.json`.
- **No scheduler, no auto-fetch.** Annual cadence + sourcing rigor make manual review safer.

## Backward compatibility (hard constraint)

The existing IPEDS pipeline must not change observable behavior. If migration to `src/completions/` requires path changes, preserve old entry points as thin shims pointing to new locations. Add tests that lock v1 output byte-for-byte (or row-for-row at minimum) before refactoring.
