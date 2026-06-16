"""
tests/test_census_loader.py — unit tests for src/labor/loaders/census.py.

Hits the live Census API on first run; subsequent runs use the on-disk
cache under data/raw_labor/census/. To force-refresh, delete that folder's
JSON files.
"""
from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / 'src'))

from labor.loaders import census  # noqa: E402

CACHE_DIR = PROJECT_ROOT / 'data' / 'raw_labor' / 'census'


def test_loads_all_50_states_plus_dc() -> None:
    df = census.load_census()
    # 50 states + DC (PR may be filtered if API doesn't return it for this vintage).
    n = df['state_abbr'].nunique()
    assert n >= 51, f'expected at least 51 jurisdictions; got {n}'


def test_ca_population_in_expected_range() -> None:
    df = census.load_census(states=['CA'])
    assert len(df) == 1
    pop = df.iloc[0]['pop_total']
    # CA ~39M in recent ACS releases. Wide band to survive next-year refresh.
    assert 35_000_000 < pop < 45_000_000, f'CA pop out of range: {pop:,}'


def test_18_24_cohort_smaller_than_total() -> None:
    df = census.load_census(states=['CA'])
    row = df.iloc[0]
    assert 0 < row['pop_18_24'] < row['pop_total']
    # CA 18-24 cohort is typically ~3.5M.
    assert 2_000_000 < row['pop_18_24'] < 5_000_000


def test_bachelors_pct_is_decimal_and_reasonable() -> None:
    df = census.load_census(states=['CA', 'WV'])
    # Both states' bachelor's+ share should be between 10% and 60%.
    for _, row in df.iterrows():
        pct = row['bachelors_or_higher_pct']
        assert 0.10 < pct < 0.60, (
            f'{row["state_abbr"]} bachelor\'s share out of range: {pct}'
        )


def test_vintage_string_populated() -> None:
    df = census.load_census()
    assert df['vintage'].unique().tolist() == ['Census ACS 5-year 2023']


def test_state_filter_works() -> None:
    df = census.load_census(states=['CA', 'NY', 'TX'])
    assert set(df['state_abbr'].unique()) == {'CA', 'NY', 'TX'}


def test_cache_files_written() -> None:
    """After at least one load, a cache file should exist under data/raw_labor/census/."""
    census.load_census()
    cache_files = list(CACHE_DIR.glob('*.json'))
    assert len(cache_files) > 0, 'expected at least one cached response'


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
