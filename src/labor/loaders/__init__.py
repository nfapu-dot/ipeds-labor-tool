"""
labor.loaders — one module per external labor data source.

Each module exposes a public `load_<source>()` function that returns a
long-format DataFrame with a documented schema. See module docstrings
for column lists. Suppressed / sentinel values are normalized to NaN.

Sources (vintages as of 2026-05-28):
- oews        BLS OEWS May 2025 (wages + employment by SOC × state)
- projections BLS Employment Projections 2024-2034 (national)
- edd         CA EDD Long-term Occupational Projections 2023-2033
- census      Census ACS via API (state demographics)

See docs/LABOR_SOURCES_INSPECTION.md for the source structural details
that drove these contracts.
"""
