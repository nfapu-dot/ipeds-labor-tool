"""
tests/test_aggregator.py — unit tests for src/labor/aggregator.py.

Covers Phase 4 acceptance criteria:
  - a CIP with one SOC
  - a CIP with many SOCs
  - a CIP with suppressed cells
  - an unmapped CIP

Plus mode equivalences, vintage metadata shape, and CA-EDD masking.
"""
from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / 'src'))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from core import crosswalk as core_cw  # noqa: E402
from labor.loaders import oews, projections, edd, census  # noqa: E402
from labor import aggregator  # noqa: E402

# Cache the heavy frames at module level — pytest re-imports per test otherwise.
_CACHE: dict = {}


def _data() -> dict:
    if not _CACHE:
        root = PROJECT_ROOT
        _CACHE['cw'] = core_cw.load_crosswalk(root / 'data' / 'dictionary')
        _CACHE['oews'] = oews.load_oews(
            oews.OEWSPaths.from_dir(root / 'data' / 'raw_labor')
        )
        _CACHE['proj'] = projections.load_projections(
            root / 'data' / 'raw_labor' / 'projections' / 'occupation_2024-2034.xlsx'
        )
        _CACHE['edd'] = edd.load_edd(
            root / 'data' / 'raw_labor' / 'edd'
            / 'edd_long_term_occ_projections_2023-2033.xlsx'
        )
        _CACHE['cens'] = census.load_census()
    return _CACHE


def _agg(mode='employment_weighted', states=('US', 'CA')):
    d = _data()
    return aggregator.aggregate_cip_labor(
        d['cw'], d['oews'], d['proj'], d['edd'], d['cens'],
        mode=mode, states=states,
    )


# ---------------------------------------------------------------------------
# Phase 4 acceptance — required scenarios
# ---------------------------------------------------------------------------

def test_cip_with_one_soc() -> None:
    """CIP 26.1501 (Neuroscience) maps to 1 SOC. Aggregated row should pass through cleanly."""
    df, _ = _agg(mode='employment_weighted')
    target = df[df['CIPCODE'] == '26.1501']
    assert len(target) > 0, 'expected at least one row for 26.1501'
    us = target[target['PRIM_STATE'] == 'US'].iloc[0]
    assert us['soc_count'] >= 1
    # With single-SOC aggregation, weighted == that SOC's value; both modes equivalent.
    df_m, _ = _agg(mode='median')
    us_m = df_m[(df_m['CIPCODE'] == '26.1501') & (df_m['PRIM_STATE'] == 'US')].iloc[0]
    # If exactly one SOC, both modes produce identical wage figures.
    if us['soc_count'] == 1:
        assert abs(us['wage_a_median'] - us_m['wage_a_median']) < 0.01, (
            f'single-SOC CIP should agree across modes; weighted={us["wage_a_median"]}, median={us_m["wage_a_median"]}'
        )


def test_cip_with_many_socs_business_admin() -> None:
    """CIP 52.0201 (Business Admin) maps to 23 SOCs — the max in the crosswalk."""
    df, _ = _agg(mode='employment_weighted')
    ba = df[(df['CIPCODE'] == '52.0201') & (df['PRIM_STATE'] == 'US')]
    assert len(ba) == 1
    row = ba.iloc[0]
    assert row['soc_count'] == 23, f'expected 23 SOCs for 52.0201; got {row["soc_count"]}'
    # Wage should be a reasonable management-tier value.
    assert 50_000 < row['wage_a_median'] < 200_000, (
        f'business admin wage_a_median out of range: {row["wage_a_median"]}'
    )
    # Total employment is the sum across 23 SOCs — should be in the millions.
    assert row['tot_emp'] > 1_000_000


def test_cip_with_suppressed_cells() -> None:
    """
    Some state-level OEWS rows have suppressed wage cells. The aggregator
    must NOT silently propagate NaN — it should exclude only the suppressed
    cell from that metric's weighted denominator.
    """
    df, _ = _agg(mode='employment_weighted', states=('CA',))
    # Any CIP with at least one suppressed wage cell, in CA.
    suppressed = df[df['n_suppressed_wage'] > 0]
    assert len(suppressed) > 0, (
        'CA aggregation must produce at least some rows with suppressed cells'
    )
    # For those rows, wage_a_median should still be populated where any SOC
    # had a non-NaN value.
    row = suppressed.iloc[0]
    # When there are multiple SOCs, even with some suppression, the aggregate
    # should be non-NaN as long as ≥1 SOC contributes.
    if row['soc_count'] > 1:
        assert not pd.isna(row['wage_a_median']), (
            f'aggregate wage should not be NaN when some SOCs are present; '
            f'soc_count={row["soc_count"]}, n_suppressed_wage={row["n_suppressed_wage"]}'
        )


def test_unmapped_cip_excluded_from_matched_frame() -> None:
    """
    Unmatched CIPs (e.g., 01.0508 Taxidermy) have no SOC mapping in the
    crosswalk after sentinel-drop. They should be absent from the aggregated
    frame. The orchestrator surfaces them separately via unmatched_cips.
    """
    df, _ = _agg()
    assert '01.0508' not in df['CIPCODE'].values, (
        'Taxidermy (no SOC mapping) must not appear in the aggregated frame'
    )

    # The reporting helper should surface it from the with-sentinels frame.
    raw_cw = core_cw.load_crosswalk(PROJECT_ROOT / 'data' / 'dictionary', drop_sentinels=False)
    unmatched = aggregator.unmatched_cips_in_crosswalk(raw_cw)
    assert '01.0508' in unmatched


# ---------------------------------------------------------------------------
# Mode-level checks
# ---------------------------------------------------------------------------

def test_modes_recognized() -> None:
    for mode in ('flat', 'median', 'employment_weighted'):
        df, meta = _agg(mode=mode)
        assert not df.empty, f'mode {mode!r} produced empty frame'
        assert meta['aggregation_mode'] == mode


def test_invalid_mode_raises() -> None:
    try:
        _agg(mode='bogus')
    except ValueError as e:
        assert 'mode' in str(e).lower()
    else:
        raise AssertionError('expected ValueError for invalid mode')


def test_flat_mode_preserves_one_row_per_soc() -> None:
    """Flat = one row per (CIP, SOC, state). No collapse."""
    df, _ = _agg(mode='flat', states=('CA',))
    # CIP 51.3801 has 2 SOCs in CA → 2 rows.
    rn = df[df['CIPCODE'] == '51.3801']
    assert len(rn) == 2, f'flat-mode CIP 51.3801 in CA should have 2 rows; got {len(rn)}'


def test_weighted_vs_median_differ_for_wage_skewed_cip() -> None:
    """
    CIP 51.1201 (Medicine MD) maps to 17 SOCs spanning specialties with very
    different wages (Family Medicine $244K, Radiologists $421K, Pediatricians
    $210K, etc.) and very different employment counts. Weighted vs unweighted
    aggregates differ by ≥5% in either direction.

    Business Admin (52.0201) does NOT exhibit this — its 23 management SOCs
    have wages clustered tightly around $100K-$150K so weights barely move
    the answer. Pick a CIP with real wage dispersion to exercise the
    methodology choice.
    """
    df_w, _ = _agg(mode='employment_weighted')
    df_m, _ = _agg(mode='median')
    w = df_w[(df_w['CIPCODE'] == '51.1201') & (df_w['PRIM_STATE'] == 'US')]
    m = df_m[(df_m['CIPCODE'] == '51.1201') & (df_m['PRIM_STATE'] == 'US')]
    assert len(w) == 1 and len(m) == 1
    diff_pct = abs(w.iloc[0]['wage_a_median'] - m.iloc[0]['wage_a_median']) / m.iloc[0]['wage_a_median']
    assert diff_pct > 0.05, (
        f'weighted vs median should differ noticeably for wage-skewed CIP 51.1201; got {diff_pct:.1%}'
    )


# ---------------------------------------------------------------------------
# CA-EDD masking
# ---------------------------------------------------------------------------

def test_ca_edd_columns_populated_only_in_ca() -> None:
    df, _ = _agg(states=('US', 'CA'))
    us_ca_cols = ['ca_median_annual_wage', 'ca_employment_change_pct', 'ca_openings_annual_avg']
    # US rows must have all CA-EDD columns NaN.
    us = df[df['PRIM_STATE'] == 'US']
    for col in us_ca_cols:
        assert us[col].isna().all(), f'{col} must be NaN for US rows; found values'
    # CA rows should have ca_median_annual_wage populated for at least some CIPs.
    ca = df[df['PRIM_STATE'] == 'CA']
    populated = ca['ca_median_annual_wage'].notna().sum()
    assert populated > 100, f'CA rows should have many populated ca_median_annual_wage; got {populated}'


# ---------------------------------------------------------------------------
# Vintage / disclosure metadata
# ---------------------------------------------------------------------------

def test_vintage_metadata_complete() -> None:
    _, meta = _agg()
    for required in ('oews', 'projections', 'edd', 'census', 'crosswalk', 'aggregation_mode', 'disclosure'):
        assert required in meta, f'missing vintage key: {required}'
        assert meta[required], f'empty vintage value for: {required}'
    # Disclosure must mention each vintage anchor.
    disc = meta['disclosure']
    for token in ('OEWS', 'BLS Projections', 'EDD', 'Census', 'Crosswalk', 'many-to-many'):
        assert token in disc, f'disclosure missing token: {token!r}'


# ---------------------------------------------------------------------------
# Census state context
# ---------------------------------------------------------------------------

def test_census_context_attached_to_ca_rows() -> None:
    df, _ = _agg(states=('CA',))
    ca = df[df['PRIM_STATE'] == 'CA']
    # All CA rows should share the same state_pop_total.
    pops = ca['state_pop_total'].dropna().unique()
    assert len(pops) == 1, f'CA rows should share one state_pop_total; got {pops}'
    assert pops[0] > 35_000_000, f'CA pop suspiciously low: {pops[0]}'


# ---------------------------------------------------------------------------
# Standalone runner
# ---------------------------------------------------------------------------

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
