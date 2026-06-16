"""
core — shared utilities used by both the v1 completions pipeline and the v2
labor market layer.

Only modules in here can be safely imported from either v1 (src/loader.py et al.)
or v2 (src/labor/*, src/app_v2.py). Anything specific to one app belongs in
that app's own module.
"""
