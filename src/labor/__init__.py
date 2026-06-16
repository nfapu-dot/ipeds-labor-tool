"""
labor — v2 labor-market data layer.

Independent of the v1 completions pipeline. Imports only from `core/`
and stdlib + pandas; never imports from `loader.py`, `joiner.py`,
`aggregator.py`, `reporter.py`, `app.py`, or `main.py`. See [[feedback-parallel-apps]].
"""
