"""
tests/test_oews_loader.py — unit tests for src/labor/loaders/oews.py.

Tests run against the real bundled OEWS May 2025 files since they're
under 8 MB total and reading takes < 5 seconds. No synthetic fixtures.

Run standalone:
    python3 tests/test_oews_loader.py

Or under pytest:
    python3 -m pytest tests/test_oews_loader.py -v
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / 'src'))

import pandas as pd  # noqa: E402

from labor.loaders import oews  # noqa: E402

RAW_LABOR = PROJECT_ROOT / 'data' / 'raw_labor'
PATHS = oews.OEWSPaths.from_dir(RAW_LABOR)


# ---------------------------------------------------------------------------
# National
# ---------------------------------------------------------------------------

def test_national_loads_detail_only() -> None:
    df = oews.load_national(PATHS.national)
    assert len(df) == 830, f'expected 830 detailed national rows; got {len(df)}'
    assert df['area_kind'].unique().tolist() == ['national']
    assert df['PRIM_STATE'].unique().tolist() == ['US']


def test_national_socs_are_6_digit() -> None:
    df = oews.load_national(PATHS.national)
    assert df['SOCCode'].str.match(r'^\d{2}-\d{4}$').all(), (
        'national OEWS should produce only 6-digit SOC codes after detail filter'
    )


def test_national_known_value_registered_nurses() -> None:
    """Spot-check: 29-1141 Registered Nurses should have tot_emp > 3 million."""
    df = oews.load_national(PATHS.national)
    rn = df[df['SOCCode'] == '29-1141']
    assert len(rn) == 1, 'should be exactly one RN row'
    assert rn.iloc[0]['tot_emp'] > 3_000_000, (
        f'RN tot_emp suspiciously low: {rn.iloc[0]["tot_emp"]}'
    )


def test_national_suppression_flag_column_exists() -> None:
    df = oews.load_national(PATHS.national)
    assert 'suppression_flag' in df.columns
    # National file has very few suppressed cells; just confirm column type.
    assert df['suppression_flag'].dtype == object


# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------

def test_state_loads_all_54_areas() -> None:
    df = oews.load_state(PATHS.state)
    n_areas = df['PRIM_STATE'].nunique()
    assert n_areas == 54, f'expected 54 areas (50 states + DC/PR/GU/VI); got {n_areas}'


def test_state_filters_to_detailed_only() -> None:
    df = oews.load_state(PATHS.state)
    # May 2025 state file has ~36,168 detailed rows.
    assert 30_000 <= len(df) <= 45_000, (
        f'state detail rows out of expected band: {len(df)}'
    )


def test_state_ca_present_with_rn_row() -> None:
    df = oews.load_state(PATHS.state)
    ca = df[df['PRIM_STATE'] == 'CA']
    assert len(ca) > 700, f'CA detail rows suspiciously low: {len(ca)}'
    rn_ca = ca[ca['SOCCode'] == '29-1141']
    assert len(rn_ca) == 1, 'CA must have exactly one RN row'
    assert rn_ca.iloc[0]['a_median'] > 100_000, (
        f'CA RN median wage suspiciously low: {rn_ca.iloc[0]["a_median"]}'
    )


def test_state_suppression_flags_captured() -> None:
    """** appears in TOT_EMP, * and # appear in wage columns. All should land in suppression_flag."""
    df = oews.load_state(PATHS.state)
    flag_counts = df['suppression_flag'].value_counts()
    # At least some rows should have suppression flags.
    total_with_flags = (df['suppression_flag'] != '').sum()
    assert total_with_flags > 100, (
        f'expected many suppressed rows in state file; got {total_with_flags}'
    )
    # All flag chars should be from the known set.
    all_flag_chars = set(','.join(df['suppression_flag'].unique()).replace(',', ''))
    allowed = set('*#')  # ** dedupes to *; commas are separators
    # The actual flag strings include literal '**' — verify any non-empty flag is
    # composed of '*' or '#' characters only.
    for flag in df['suppression_flag'].unique():
        if not flag:
            continue
        for token in flag.split(','):
            assert token in oews.SUPPRESSION_FLAGS, (
                f'unexpected suppression token: {token!r}'
            )


def test_state_suppressed_cells_become_nan() -> None:
    """When suppression_flag is set, the affected numeric column must be NaN."""
    df = oews.load_state(PATHS.state)
    # Find rows flagged with ** (TOT_EMP suppression).
    starstar = df[df['suppression_flag'].str.contains(r'\*\*', regex=True, na=False)]
    assert len(starstar) > 0, 'expected some ** rows in state file'
    # All those tot_emp values must be NaN.
    assert starstar['tot_emp'].isna().all(), (
        'tot_emp must be NaN where ** is flagged'
    )


# ---------------------------------------------------------------------------
# Combined
# ---------------------------------------------------------------------------

def test_load_oews_combines_national_and_state() -> None:
    combined = oews.load_oews(PATHS)
    kinds = set(combined['area_kind'].unique())
    assert kinds == {'national', 'state'}
    # National (~830) + state (~36K) = roughly 37K total.
    assert 30_000 <= len(combined) <= 45_000


# ---------------------------------------------------------------------------
# Standalone runner
# ---------------------------------------------------------------------------

def _run_all() -> int:
    tests = [
        (name, fn) for name, fn in globals().items()
        if name.startswith('test_') and callable(fn)
    ]
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print(f'  PASS  {name}')
        except AssertionError as e:
            failed += 1
            print(f'  FAIL  {name}: {e}', file=sys.stderr)
        except Exception as e:
            failed += 1
            print(f'  ERROR {name}: {type(e).__name__}: {e}', file=sys.stderr)
    print()
    if failed:
        print(f'{failed}/{len(tests)} tests failed', file=sys.stderr)
        return 1
    print(f'all {len(tests)} tests passed')
    return 0


if __name__ == '__main__':
    sys.exit(_run_all())
