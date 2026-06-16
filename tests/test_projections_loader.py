"""
tests/test_projections_loader.py — unit tests for src/labor/loaders/projections.py.
"""
from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / 'src'))

import pandas as pd  # noqa: E402

from labor.loaders import projections  # noqa: E402

PATH = PROJECT_ROOT / 'data' / 'raw_labor' / 'projections' / 'occupation_2024-2034.xlsx'


def test_loads_832_detailed_rows() -> None:
    df = projections.load_projections(PATH)
    assert len(df) == 832, f'expected 832 detailed rows; got {len(df)}'


def test_all_socs_are_6_digit() -> None:
    df = projections.load_projections(PATH)
    assert df['SOCCode'].str.match(r'^\d{2}-\d{4}$').all()


def test_year_range_detected() -> None:
    df = projections.load_projections(PATH)
    assert df['base_year'].unique().tolist() == [2024]
    assert df['target_year'].unique().tolist() == [2034]


def test_percent_change_normalized_to_decimal() -> None:
    """
    Source stores percent change as integer-ish numbers (e.g., 6.1 for 6.1%).
    Loader divides by 100 → 0.061. Test by comparing against a known fast-grower.
    """
    df = projections.load_projections(PATH)
    # Wind turbine service technicians (49-9081) — fastest-growing occupation.
    wind = df[df['SOCCode'] == '49-9081']
    assert len(wind) == 1
    pct = wind.iloc[0]['employment_change_pct']
    # Source says ~49.9% growth; normalized → 0.499.
    assert 0.3 <= pct <= 0.6, f'expected decimal ~0.5; got {pct}'


def test_known_rn_row_present() -> None:
    df = projections.load_projections(PATH)
    rn = df[df['SOCCode'] == '29-1141']
    assert len(rn) == 1
    # ~3.39M nurses in 2024 (stored as 3391.0 thousand)
    assert 3000 <= rn.iloc[0]['employment_base'] <= 4000, (
        f'RN base employment off: {rn.iloc[0]["employment_base"]}'
    )
    assert rn.iloc[0]['median_annual_wage_base'] > 80_000


def test_openings_column_populated() -> None:
    df = projections.load_projections(PATH)
    # Most occupations should have at least some annual openings; few NaN.
    pct_with_openings = df['openings_annual_avg'].notna().mean()
    assert pct_with_openings > 0.95, (
        f'too many NaN openings: {pct_with_openings:.1%} populated'
    )


def test_vintage_string_set() -> None:
    df = projections.load_projections(PATH)
    assert df['vintage'].unique().tolist() == ['BLS Projections 2024-2034']


def _run_all() -> int:
    tests = [
        (n, f) for n, f in globals().items()
        if n.startswith('test_') and callable(f)
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
