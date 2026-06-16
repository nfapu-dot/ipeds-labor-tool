"""
tests/test_combine.py — end-to-end integration test for the v2 orchestrator.

Runs `python3 src/main_v2.py --state CA --cip 51.3801 52.0201 11.0701` to a
temp directory and asserts the combined workbook has both the v1 sheets and
the new v2 sheets, plus a sanity-check on the joined data.

Run standalone:
    python3 tests/test_combine.py
"""
from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SRC = PROJECT_ROOT / 'src'

# Same CIP set used in the Phase 5 smoke-test. Small enough to run fast,
# representative enough to exercise the labor join across SOC counts.
V2_ARGS = ['--state', 'CA', '--cip', '51.3801', '52.0201', '11.0701']


def _run_v2(output_dir: Path) -> Path:
    import os
    cmd = [sys.executable, str(SRC / 'main_v2.py'), *V2_ARGS, '--output', str(output_dir)]
    env = {
        'PYTHONPATH': str(SRC),
        'PATH': os.environ.get('PATH', '/usr/local/bin:/usr/bin:/bin'),
    }
    result = subprocess.run(
        cmd, input='y\n', capture_output=True, text=True,
        env=env, timeout=600, check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f'main_v2 exited {result.returncode}\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}'
        )
    produced = sorted(output_dir.glob('*.xlsx'))
    assert len(produced) == 1, f'expected 1 xlsx; got {produced}'
    return produced[0]


def test_combined_workbook_has_all_expected_sheets() -> None:
    with tempfile.TemporaryDirectory(prefix='ipeds_v2_test_') as tmp:
        out = _run_v2(Path(tmp))
        xl = pd.ExcelFile(out)
        # v1 sheets (preserved verbatim) + v2 sheets (Phase 5 refactor for
        # long-format + plain-English headers).
        expected = {
            # v1
            'Institutions', 'Completions_2020', 'Completions_2021',
            'Completions_2022', 'Completions_2023', 'Completions_2024',
            'CAGR_by_Institution', 'Market_View', 'Definitions',
            # v2
            'Labor_View_Long', 'Labor_Detail_by_State',
            'Combined_Wide_Drilldown', 'Labor_Flat_SOC_Level',
            'Saturation_by_CIP', 'Unmatched_CIPs', 'Disclosure',
        }
        actual = set(xl.sheet_names)
        missing = expected - actual
        assert not missing, f'missing sheets: {missing}'


def test_labor_view_long_has_plain_english_headers() -> None:
    """The primary labor sheet should have human-readable columns."""
    with tempfile.TemporaryDirectory(prefix='ipeds_v2_test_') as tmp:
        out = _run_v2(Path(tmp))
        lvl = pd.read_excel(out, sheet_name='Labor_View_Long',
                            dtype={'CIP Code': str})
        # Plain-English column names — no raw `wage_a_median` etc.
        for col in ('CIP Code', 'CIP Title', 'Geography', 'Source', 'Metric', 'Value'):
            assert col in lvl.columns, f'missing column: {col}'
        # Long-format check: each row is one (CIP × Geography × Source × Metric) fact.
        assert len(lvl) > 0
        # Multiple Source values present (OEWS + Projections + EDD).
        sources = set(lvl['Source'].dropna())
        assert 'BLS OEWS' in sources
        assert 'BLS Projections' in sources
        # CA EDD only appears when CA is the primary state.
        assert 'CA EDD' in sources, 'CA EDD rows should appear (primary state is CA)'


def test_labor_long_data_sanity() -> None:
    """Joined numbers triangulate — CA nursing wage > national, CA openings 5-30% of national."""
    with tempfile.TemporaryDirectory(prefix='ipeds_v2_test_') as tmp:
        out = _run_v2(Path(tmp))
        lvl = pd.read_excel(out, sheet_name='Labor_View_Long',
                            dtype={'CIP Code': str})

        # CA nursing wage > national nursing wage (from OEWS rows)
        rn_wage_ca = lvl[
            (lvl['CIP Code'] == '51.3801')
            & (lvl['Geography'] == 'CA')
            & (lvl['Metric'] == 'Median Annual Wage')
            & (lvl['Source'] == 'BLS OEWS')
        ]
        rn_wage_us = lvl[
            (lvl['CIP Code'] == '51.3801')
            & (lvl['Geography'] == 'U.S.')
            & (lvl['Metric'] == 'Median Annual Wage')
            & (lvl['Source'] == 'BLS OEWS')
        ]
        assert len(rn_wage_ca) == 1 and len(rn_wage_us) == 1
        assert rn_wage_ca.iloc[0]['Value'] > rn_wage_us.iloc[0]['Value'], (
            f'CA RN wage should exceed national; got CA={rn_wage_ca.iloc[0]["Value"]}, '
            f'US={rn_wage_us.iloc[0]["Value"]}'
        )

        # CA EDD openings vs BLS national openings — CA should be a sensible fraction.
        # Both are now stored as absolute annual counts (BLS normalized from thousands
        # in build_labor_long_view), so no conversion needed.
        rn_open_ca = lvl[
            (lvl['CIP Code'] == '51.3801')
            & (lvl['Source'] == 'CA EDD')
            & (lvl['Metric'] == 'Annual Openings')
        ]
        rn_open_us = lvl[
            (lvl['CIP Code'] == '51.3801')
            & (lvl['Source'] == 'BLS Projections')
            & (lvl['Metric'] == 'Annual Openings')
        ]
        assert len(rn_open_ca) == 1 and len(rn_open_us) == 1
        ca_abs = rn_open_ca.iloc[0]['Value']
        us_abs = rn_open_us.iloc[0]['Value']
        # Sanity: BLS national openings should be far larger than CA's.
        assert us_abs > 50_000, f'BLS national RN openings look too small: {us_abs}'
        ratio = ca_abs / us_abs
        assert 0.05 < ratio < 0.30, (
            f'CA share of US openings should be 5-30%; got {ratio:.1%}'
        )


def test_market_view_has_reported_program_rows() -> None:
    """
    Market_View sheet should carry BOTH program counts: degree-conferring
    (CTOTALT>0) and all-reported (incl. 0-graduate). Reported >= degree-
    conferring for every program (reported is a superset).
    """
    with tempfile.TemporaryDirectory(prefix='ipeds_v2_test_') as tmp:
        out = _run_v2(Path(tmp))
        mv = pd.read_excel(out, sheet_name='Market_View', dtype={'CIPCODE': str})
        metrics = set(mv['Metric'].dropna())
        assert 'Programs (degree-conferring)' in metrics, (
            f'missing degree-conferring metric; got {metrics}'
        )
        assert 'Programs (all reported, incl. 0 graduates)' in metrics, (
            f'missing all-reported metric; got {metrics}'
        )
        # For Nursing (51.3801) National in 2024: reported >= degree-conferring.
        deg = mv[(mv['CIPCODE'] == '51.3801') & (mv['Geography'] == 'National')
                 & (mv['Metric'] == 'Programs (degree-conferring)')]
        rep = mv[(mv['CIPCODE'] == '51.3801') & (mv['Geography'] == 'National')
                 & (mv['Metric'] == 'Programs (all reported, incl. 0 graduates)')]
        assert len(deg) >= 1 and len(rep) >= 1
        # Sum the latest-year column across award levels (multiple AWLEVEL rows).
        deg_2024 = pd.to_numeric(deg['2024'], errors='coerce').sum()
        rep_2024 = pd.to_numeric(rep['2024'], errors='coerce').sum()
        assert rep_2024 >= deg_2024, (
            f'reported ({rep_2024}) should be >= degree-conferring ({deg_2024})'
        )


def test_saturation_sheet_present_and_sane() -> None:
    """Saturation sheet: per-CIP completions/openings, long format, both geographies."""
    with tempfile.TemporaryDirectory(prefix='ipeds_v2_test_') as tmp:
        out = _run_v2(Path(tmp))
        sat = pd.read_excel(out, sheet_name='Saturation_by_CIP', dtype={'CIP Code': str})
        # Plain-English columns
        for col in ('CIP Code', 'CIP Title', 'Geography', 'Annual Completions',
                    'Annual Openings', 'Completions per Opening', 'Reading'):
            assert col in sat.columns, f'missing saturation column: {col}'
        # Both geographies present (CA primary state → California + U.S. rows)
        geos = set(sat['Geography'].dropna())
        assert any('California' in g for g in geos), f'expected a California row; got {geos}'
        assert any('national' in g.lower() for g in geos), f'expected a national row; got {geos}'

        # Nursing national: ratio should be a positive finite number.
        rn_nat = sat[(sat['CIP Code'] == '51.3801') & (sat['Geography'] == 'U.S. (national)')]
        assert len(rn_nat) == 1
        ratio = rn_nat.iloc[0]['Completions per Opening']
        assert ratio > 0, f'nursing national saturation should be positive; got {ratio}'
        # Sanity: national nursing completions/openings is in a plausible band (0.1–10).
        assert 0.1 < ratio < 10, f'nursing saturation implausible: {ratio}'


def test_saturation_caveat_in_disclosure() -> None:
    """The saturation caveat must appear on the Disclosure sheet."""
    with tempfile.TemporaryDirectory(prefix='ipeds_v2_test_') as tmp:
        out = _run_v2(Path(tmp))
        disc = pd.read_excel(out, sheet_name='Disclosure', header=None)
        text = disc.astype(str).agg(' '.join, axis=1).str.cat(sep=' ').lower()
        for token in ('saturation ratio', 'directional signal', 'many-to-many',
                      'graduates move'):
            assert token in text, f'disclosure missing saturation caveat token: {token!r}'


def test_disclosure_sheet_populated() -> None:
    with tempfile.TemporaryDirectory(prefix='ipeds_v2_test_') as tmp:
        out = _run_v2(Path(tmp))
        # Read with no header — Disclosure is a key-value layout.
        disc = pd.read_excel(out, sheet_name='Disclosure', header=None)
        text = disc.astype(str).agg(' '.join, axis=1).str.cat(sep=' ')
        for token in ('OEWS', 'BLS Projections', 'EDD', 'Census',
                      'employment_weighted', 'CIP-SOC', 'many-to-many'):
            assert token in text, f'disclosure missing token: {token!r}'


def test_unmatched_cips_sheet_filtered_to_user_selection() -> None:
    """
    Per [[feedback-v2-ux-must-match-v1]], unmatched sheet must reflect the
    user's CIP filter. Our test CIPs (51.3801, 52.0201, 11.0701) are all
    matched, so the sheet should show 0 rows.
    """
    with tempfile.TemporaryDirectory(prefix='ipeds_v2_test_') as tmp:
        out = _run_v2(Path(tmp))
        um = pd.read_excel(out, sheet_name='Unmatched_CIPs', dtype={'CIP Code': str})
        # All three test CIPs have SOC mappings → sheet should be empty.
        assert len(um) == 0, (
            f'expected 0 unmatched rows (all test CIPs are mapped); got {len(um)}'
        )


def test_unmatched_cips_sheet_populated_when_user_picks_unmatched_cip() -> None:
    """If the user explicitly selects an unmatched CIP (e.g. Taxidermy), it shows."""
    import subprocess, os, sys
    with tempfile.TemporaryDirectory(prefix='ipeds_v2_test_unmatched_') as tmp:
        cmd = [
            sys.executable, str(SRC / 'main_v2.py'),
            '--state', 'CA',
            '--cip', '01.0508',  # Taxidermy — unmatched in crosswalk
            '--output', tmp,
        ]
        result = subprocess.run(
            cmd, input='y\n', capture_output=True, text=True,
            env={'PYTHONPATH': str(SRC),
                 'PATH': os.environ.get('PATH', '/usr/local/bin:/usr/bin:/bin')},
            timeout=600, check=False,
        )
        if result.returncode != 0:
            raise RuntimeError(f'main_v2 exited {result.returncode}\nstderr:\n{result.stderr}')
        out = sorted(Path(tmp).glob('*.xlsx'))[0]
        um = pd.read_excel(out, sheet_name='Unmatched_CIPs', dtype={'CIP Code': str})
        assert '01.0508' in set(um['CIP Code']), (
            f'expected 01.0508 in unmatched; got {set(um["CIP Code"])}'
        )


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
