"""
tests/test_core_crosswalk.py — unit tests for src/core/crosswalk.py.

Run standalone:
    python3 tests/test_core_crosswalk.py

Or under pytest:
    python3 -m pytest tests/test_core_crosswalk.py -v
"""
from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / 'src'))

import pandas as pd  # noqa: E402
from core import crosswalk  # noqa: E402

REAL_DICT_DIR = PROJECT_ROOT / 'data' / 'dictionary'


# ---------------------------------------------------------------------------
# Real-file tests (depend on the bundled cip_soc_crosswalk.xlsx)
# ---------------------------------------------------------------------------

def test_load_with_sentinels_matches_inspection_findings() -> None:
    """drop_sentinels=False should reproduce the raw NCES sheet shape."""
    df = crosswalk.load_crosswalk(REAL_DICT_DIR, drop_sentinels=False)
    assert not df.empty, 'crosswalk should load from the bundled file'
    assert len(df) == 6097, f'expected 6,097 raw rows; got {len(df)}'
    assert set(df.columns) == {'CIPCODE', 'CIPTitle', 'SOCCode', 'SOCTitle'}, (
        f'unexpected columns: {df.columns.tolist()}'
    )
    # Sentinels present (matches CROSSWALK_INSPECTION_FINDINGS.md §1):
    assert (df['CIPCODE'] == crosswalk.SENTINEL_CIP).sum() == 180
    assert (df['SOCCode'] == crosswalk.SENTINEL_SOC).sum() == 194


def test_load_with_drop_sentinels_filters_both_sentinels() -> None:
    """Default drop_sentinels=True should remove both NO MATCH sides."""
    df = crosswalk.load_crosswalk(REAL_DICT_DIR)  # default True
    assert (df['CIPCODE'] == crosswalk.SENTINEL_CIP).sum() == 0
    assert (df['SOCCode'] == crosswalk.SENTINEL_SOC).sum() == 0
    # 6097 - 180 - 194 = 5723 real (CIP, SOC) pairs.
    assert len(df) == 5723, f'expected 5,723 real pairs; got {len(df)}'


def test_canonical_column_names() -> None:
    """The renamer must produce CIPCODE / CIPTitle / SOCCode / SOCTitle."""
    df = crosswalk.load_crosswalk(REAL_DICT_DIR)
    for col in ('CIPCODE', 'CIPTitle', 'SOCCode', 'SOCTitle'):
        assert col in df.columns, f'missing canonical column: {col}'


def test_codes_are_strings_and_stripped() -> None:
    """Leading zeros and dash-formatted SOCs must survive as strings."""
    df = crosswalk.load_crosswalk(REAL_DICT_DIR)
    assert df['CIPCODE'].dtype == object
    assert df['SOCCode'].dtype == object
    # Spot-check leading-zero preservation.
    assert (df['CIPCODE'].str.startswith('01.')).any(), 'leading-zero CIPs lost'
    # SOC format \d{2}-\d{4} for every real row.
    assert df['SOCCode'].str.match(r'^\d{2}-\d{4}$').all(), (
        'non-conforming SOC codes found'
    )


def test_unmatched_cips_helper() -> None:
    """unmatched_cips() should return the 194 NCES-says-no-SOC codes."""
    raw = crosswalk.load_crosswalk(REAL_DICT_DIR, drop_sentinels=False)
    unmatched = crosswalk.unmatched_cips(raw)
    assert len(unmatched) == 194, (
        f'expected 194 unmatched CIPs; got {len(unmatched)}'
    )
    # Spot-check a known-unmatched code from the inspection findings:
    assert '01.0508' in unmatched, 'Taxidermy/Taxidermist should be unmatched'


def test_unmatched_cips_empty_on_filtered_frame() -> None:
    """If sentinels are already dropped, unmatched_cips returns []."""
    filtered = crosswalk.load_crosswalk(REAL_DICT_DIR)  # drop_sentinels=True
    assert crosswalk.unmatched_cips(filtered) == []


# ---------------------------------------------------------------------------
# Graceful-failure tests (no real file required)
# ---------------------------------------------------------------------------

def test_missing_directory_returns_empty_df(tmp_path: Path = None) -> None:
    """A nonexistent dict_dir must return an empty DataFrame, not raise."""
    import tempfile
    with tempfile.TemporaryDirectory() as t:
        empty_dir = Path(t)
        df = crosswalk.load_crosswalk(empty_dir)
        assert df.empty


def test_find_crosswalk_path_returns_real_file() -> None:
    """The public path-finder should locate the bundled xlsx."""
    p = crosswalk.find_crosswalk_path(REAL_DICT_DIR)
    assert p is not None and p.name == 'cip_soc_crosswalk.xlsx'


# ---------------------------------------------------------------------------
# Standalone runner
# ---------------------------------------------------------------------------

def _run_all() -> int:
    """Run every test_* function in this module. Returns exit code."""
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
