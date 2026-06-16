"""
tests/test_edd_loader.py — unit tests for src/labor/loaders/edd.py.
"""
from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / 'src'))

from labor.loaders import edd  # noqa: E402

PATH = (
    PROJECT_ROOT / 'data' / 'raw_labor' / 'edd'
    / 'edd_long_term_occ_projections_2023-2033.xlsx'
)


def test_loads_676_detailed_rows() -> None:
    df = edd.load_edd(PATH)
    assert len(df) == 676, f'expected 676 detailed rows; got {len(df)}'


def test_no_sentinel_row() -> None:
    df = edd.load_edd(PATH)
    # 'End of worksheet.' must not appear anywhere.
    assert not (df['SOCCode'] == 'End of worksheet.').any()
    # Every SOC code is well-formed.
    assert df['SOCCode'].str.match(r'^\d{2}-\d{4}$').all()


def test_year_range_detected() -> None:
    df = edd.load_edd(PATH)
    assert df['base_year'].unique().tolist() == [2023]
    assert df['target_year'].unique().tolist() == [2033]


def test_percent_change_is_decimal() -> None:
    """EDD's native format is decimal — verify by sanity-checking against known values."""
    df = edd.load_edd(PATH)
    # Most growth rates fall within ±0.5 (i.e., ±50%) over a 10-year window.
    in_range = df['employment_change_pct'].between(-0.5, 0.6)
    assert in_range.mean() > 0.95, (
        'too many percent-change values outside ±50%; format may be wrong'
    )


def test_ca_rn_row_present() -> None:
    df = edd.load_edd(PATH)
    rn = df[df['SOCCode'] == '29-1141']
    assert len(rn) == 1
    assert rn.iloc[0]['employment_base'] > 250_000, (
        f'CA RN base employment off: {rn.iloc[0]["employment_base"]}'
    )
    assert rn.iloc[0]['median_annual_wage'] > 100_000


def test_openings_column_populated() -> None:
    df = edd.load_edd(PATH)
    pct = df['openings_annual_avg'].notna().mean()
    assert pct > 0.95, f'too many NaN openings: {pct:.1%} populated'


def test_vintage_string_set() -> None:
    df = edd.load_edd(PATH)
    assert df['vintage'].unique().tolist() == ['CA EDD Long-term 2023-2033']


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
