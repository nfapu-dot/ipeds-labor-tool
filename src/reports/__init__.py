"""
reports — v2 orchestrator that joins v1 completions output with v2 labor data.

`combine.build_combined_dataset` calls v1's existing pipeline (loader, joiner,
aggregator, reporter) AND v2's labor pipeline (core.crosswalk, labor.loaders,
labor.aggregator), then produces a per-(CIP, geography) joined frame plus
the full v1 + v2 source data ready for Excel export.

`writer.write_combined_workbook` writes the combined Excel — starts from v1's
build_workbook output (preserves byte-identical v1 sheets) and appends new
sheets for labor and the combined view.

See docs/PHASE_PLAN.md §Phase 5 for the design.
"""
