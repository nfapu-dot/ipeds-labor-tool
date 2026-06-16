"""
aggregator.py — Computes CAGR, program count growth, and market-level totals.

Inputs: dict {year: enriched DataFrame} from joiner.py.

Pipeline:
1. apply_filters() — drops aggregate-rollup rows (non-6-digit CIPCODE) unless
   --include-residual, then applies user CIP and AWLEVEL filters. Logs row
   counts at each step (spec §Data Quality Rule #5).
2. compute_cagr_table() — one row per (UNITID, CIPCODE, AWLEVEL), wide on year,
   with CAGR and Flag columns.
3. compute_program_growth() — one row per UNITID, distinct (CIPCODE,AWLEVEL)
   count per year + delta + percent change. Measures breadth, not volume.
4. compute_market_view() — one row per (CIPCODE, AWLEVEL), sum of CTOTALT per
   year across all selected institutions + market CAGR + distinct institution
   count offering that program per year.

CAGR formula:  (end / start) ** (1 / (end_year - start_year)) - 1

Edge case flags (spec §Calculations):
  start == 0                          → flag 'New Program',    CAGR NaN
  start > 0 and end == 0              → flag 'Program Ended',  CAGR -1.0 (-100%)
  start NaN, OR start > 0 and end NaN → flag 'Missing Data',   CAGR NaN
  start > 0 and end > 0               → flag 'OK',             CAGR computed

NaN handling: suppressed cells (IPEDS <3) stay NaN — never imputed to zero.
"""
from __future__ import annotations

from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

import numpy as np
import pandas as pd
from rich.console import Console
from rich.table import Table

console = Console()

CAGR_FLAG_OK = 'OK'
CAGR_FLAG_NEW = 'New Program'
CAGR_FLAG_ENDED = 'Program Ended'
CAGR_FLAG_MISSING = 'Missing Data'


# ---------------------------------------------------------------------------
# Filter pipeline
# ---------------------------------------------------------------------------

def apply_filters(
    joined_by_year: Dict[int, pd.DataFrame],
    cip_codes: Optional[Iterable[str]] = None,
    award_levels: Optional[Iterable[int]] = None,
    include_residual: bool = False,
    quiet: bool = False,
) -> Dict[int, pd.DataFrame]:
    """
    Apply 6-digit / CIP / AWLEVEL filters per year. Logs row counts at each step.
    """
    cip_set = {str(c).strip() for c in (cip_codes or [])} or None
    aw_set = {int(a) for a in (award_levels or [])} or None

    out: Dict[int, pd.DataFrame] = {}
    for year in sorted(joined_by_year.keys()):
        df = joined_by_year[year]
        n_in = len(df)

        if not include_residual and 'IS_CIP_6DIGIT' in df.columns:
            df = df[df['IS_CIP_6DIGIT'] == True]  # noqa: E712
        n_after_residual = len(df)

        if cip_set is not None:
            df = df[df['CIPCODE'].isin(cip_set)]
        n_after_cip = len(df)

        if aw_set is not None:
            df = df[df['AWLEVEL'].isin(aw_set)]
        n_after_aw = len(df)

        if not quiet:
            residual_label = 'incl. residual' if include_residual else '6-digit only'
            console.log(
                f'[cyan]filter {year}[/]  in: {n_in:,}  '
                f'→ {residual_label}: {n_after_residual:,}  '
                f'→ CIP filter: {n_after_cip:,}  '
                f'→ AWLEVEL filter: {n_after_aw:,}'
            )
        out[year] = df.copy()

    # Spec §Data Quality #3: warn when a CIP code is requested but never
    # appears. Always surface — too important to be quieted by verbose flag.
    if cip_set:
        seen: set = set()
        for df in out.values():
            seen.update(df['CIPCODE'].dropna().unique().tolist())
        unused = sorted(cip_set - seen)
        if unused:
            console.log(
                f'[yellow]⚠ CIP filter requested but no rows across any year: {unused}[/]'
            )

    return out


def warn_zero_completion_unitids(
    filtered_by_year: Dict[int, pd.DataFrame],
    selected_unitids: Iterable[int],
    quiet: bool = False,  # retained for back-compat; warning always surfaces
) -> None:
    """
    Spec §Data Quality #2: warn when a selected UNITID has 0 rows in a year.
    Always logs regardless of quiet — these warnings are about *results*, not
    pipeline detail.
    """
    selected = sorted({int(u) for u in selected_unitids})
    if not selected:
        return
    for year in sorted(filtered_by_year.keys()):
        df = filtered_by_year[year]
        present = set(df['UNITID'].dropna().astype(int))
        missing = [u for u in selected if u not in present]
        if missing:
            preview = missing[:5]
            console.log(
                f'[yellow]⚠ {year}: {len(missing)} selected UNITIDs have zero rows '
                f'after filters; sample: {preview}{" …" if len(missing) > 5 else ""}[/]'
            )


# ---------------------------------------------------------------------------
# CAGR primitive
# ---------------------------------------------------------------------------

def vec_cagr(
    start: pd.Series,
    end: pd.Series,
    n_periods: int,
) -> Tuple[pd.Series, pd.Series]:
    """
    Vectorized CAGR. Returns (cagr_series, flag_series).

    n_periods = end_year - start_year (e.g. 4 for 2020→2024).
    cagr is a decimal (0.123 = 12.3%); -1.0 for Program Ended.
    """
    s = pd.to_numeric(start, errors='coerce').astype(float)
    e = pd.to_numeric(end, errors='coerce').astype(float)

    flag = pd.Series([CAGR_FLAG_OK] * len(s), index=s.index, dtype=object)
    cagr = pd.Series(np.nan, index=s.index, dtype=float)

    s_nan = s.isna()
    e_nan = e.isna()
    s_zero = (~s_nan) & (s == 0)
    s_pos = (~s_nan) & (s > 0)

    flag[s_nan] = CAGR_FLAG_MISSING
    flag[s_zero] = CAGR_FLAG_NEW
    flag[s_pos & e_nan] = CAGR_FLAG_MISSING
    flag[s_pos & (~e_nan) & (e == 0)] = CAGR_FLAG_ENDED

    ended_mask = flag == CAGR_FLAG_ENDED
    cagr[ended_mask] = -1.0

    ok_mask = (flag == CAGR_FLAG_OK) & s_pos & (~e_nan) & (e > 0)
    flag[ok_mask] = CAGR_FLAG_OK
    cagr[ok_mask] = (e[ok_mask] / s[ok_mask]) ** (1.0 / n_periods) - 1.0

    # Any remaining 'OK' that didn't satisfy ok_mask (e.g., start>0 & end>0 but
    # division produced inf/-inf) → mark Missing Data.
    leftover = (flag == CAGR_FLAG_OK) & ~ok_mask
    if leftover.any():
        flag[leftover] = CAGR_FLAG_MISSING
        cagr[leftover] = np.nan

    return cagr, flag


# ---------------------------------------------------------------------------
# CAGR table — one row per (UNITID, CIPCODE, AWLEVEL)
# ---------------------------------------------------------------------------

def compute_cagr_table(
    filtered_by_year: Dict[int, pd.DataFrame],
    start_year: int,
    end_year: int,
) -> pd.DataFrame:
    """
    Build the CAGR table in two passes to avoid duplicate-column coalesce on merge:
      1. Build a `dims` frame (one row per UNITID×CIPCODE×AWLEVEL with INSTNM/STABBR/CIPTitle).
      2. Left-merge per-year CTOTALT sums into `dims`.
    """
    years = sorted(filtered_by_year.keys())
    join_keys = ['UNITID', 'CIPCODE', 'AWLEVEL']
    label_cols = ['INSTNM', 'STABBR', 'CIPTitle']

    # ── Pass 1: dimension table — union across years, first non-null label wins ──
    dim_frames = []
    for y in years:
        df = filtered_by_year[y]
        cols = [c for c in (join_keys + label_cols) if c in df.columns]
        if not cols:
            continue
        dim_frames.append(df[cols].copy())
    if not dim_frames:
        return pd.DataFrame()

    dims = pd.concat(dim_frames, ignore_index=True)
    dims = dims.dropna(subset=join_keys).sort_values(label_cols, na_position='last')
    # Keeping the first non-null label per join key.
    dims = dims.drop_duplicates(join_keys, keep='first').reset_index(drop=True)

    # ── Pass 2: per-year CTOTALT sums ──
    result = dims
    for y in years:
        df = filtered_by_year[y]
        if 'CTOTALT' not in df.columns:
            result[f'CTOTALT_{y}'] = pd.NA
            continue
        sums = (
            df[join_keys + ['CTOTALT']]
            .groupby(join_keys, dropna=False, as_index=False)['CTOTALT']
            .sum(min_count=1)
            .rename(columns={'CTOTALT': f'CTOTALT_{y}'})
        )
        result = result.merge(sums, on=join_keys, how='left')

    # ── CAGR + Flag ──
    n_periods = end_year - start_year
    cagr, flag = vec_cagr(
        result[f'CTOTALT_{start_year}'], result[f'CTOTALT_{end_year}'], n_periods
    )
    result['CAGR'] = cagr
    result['Flag'] = flag

    leading = [c for c in ('UNITID', 'INSTNM', 'STABBR', 'CIPCODE', 'CIPTitle', 'AWLEVEL')
               if c in result.columns]
    year_cols = [f'CTOTALT_{y}' for y in years]
    return result[leading + year_cols + ['CAGR', 'Flag']].sort_values(
        ['UNITID', 'CIPCODE', 'AWLEVEL']
    ).reset_index(drop=True)


# ---------------------------------------------------------------------------
# Program growth — one row per UNITID
# ---------------------------------------------------------------------------

def compute_program_growth(
    filtered_by_year: Dict[int, pd.DataFrame],
    metadata_df: pd.DataFrame,
    start_year: int,
    end_year: int,
) -> pd.DataFrame:
    years = sorted(filtered_by_year.keys())

    # Per-year distinct (CIPCODE, AWLEVEL) counts per UNITID.
    counts_by_year: Dict[int, pd.Series] = {}
    for y in years:
        df = filtered_by_year[y]
        if df.empty:
            counts_by_year[y] = pd.Series(dtype='Int64')
            continue
        counts = (
            df.drop_duplicates(['UNITID', 'CIPCODE', 'AWLEVEL'])
              .groupby('UNITID').size()
              .astype('Int64')
        )
        counts_by_year[y] = counts

    all_uids = sorted(set().union(*[set(s.index.tolist()) for s in counts_by_year.values()]))
    if not all_uids:
        return pd.DataFrame()

    out = pd.DataFrame({'UNITID': all_uids})
    for y in years:
        out[f'PROGRAMS_{y}'] = out['UNITID'].map(counts_by_year[y]).fillna(0).astype('Int64')

    start_col = f'PROGRAMS_{start_year}'
    end_col = f'PROGRAMS_{end_year}'
    out['DELTA'] = (out[end_col].astype(int) - out[start_col].astype(int)).astype('Int64')

    s = out[start_col].astype(float)
    e = out[end_col].astype(float)
    pct = pd.Series(np.nan, index=out.index, dtype=float)
    pct[s > 0] = (e[s > 0] - s[s > 0]) / s[s > 0]
    out['PCT_CHANGE'] = pct

    # Join INSTNM / STABBR from metadata (sourced from each UNITID's most recent HD).
    if not metadata_df.empty:
        meta_cols = [c for c in ('UNITID', 'INSTNM', 'STABBR') if c in metadata_df.columns]
        out = out.merge(metadata_df[meta_cols], on='UNITID', how='left')

    leading = [c for c in ('UNITID', 'INSTNM', 'STABBR') if c in out.columns]
    program_cols = [f'PROGRAMS_{y}' for y in years]
    return out[leading + program_cols + ['DELTA', 'PCT_CHANGE']].sort_values(
        'UNITID'
    ).reset_index(drop=True)


# ---------------------------------------------------------------------------
# Market view — one row per (CIPCODE, AWLEVEL)
# ---------------------------------------------------------------------------

def compute_market_view(
    filtered_by_year: Dict[int, pd.DataFrame],
    start_year: int,
    end_year: int,
) -> pd.DataFrame:
    years = sorted(filtered_by_year.keys())
    key_cols = ['CIPCODE', 'AWLEVEL']

    # CIPTitle lookup (first non-null sighting across all years)
    title_map: Dict[str, str] = {}
    for y in years:
        df = filtered_by_year[y]
        if df.empty or 'CIPTitle' not in df.columns:
            continue
        for code, title in df[['CIPCODE', 'CIPTitle']].dropna().drop_duplicates('CIPCODE').itertuples(index=False):
            title_map.setdefault(code, title)

    frames = []
    for y in years:
        df = filtered_by_year[y]
        if df.empty:
            frames.append(pd.DataFrame(columns=key_cols + [f'SUM_CTOTALT_{y}', f'INST_COUNT_{y}']))
            continue
        # Sum of CTOTALT (skipna=True so suppressed cells are excluded from sum).
        sums = (
            df.groupby(key_cols, dropna=False)['CTOTALT']
              .sum(min_count=1)
              .reset_index()
              .rename(columns={'CTOTALT': f'SUM_CTOTALT_{y}'})
        )
        # Institutions offering (CTOTALT > 0) — suppressed cells don't count.
        positive = df[df['CTOTALT'].fillna(0) > 0]
        inst_counts = (
            positive.groupby(key_cols, dropna=False)['UNITID']
                    .nunique()
                    .reset_index()
                    .rename(columns={'UNITID': f'INST_COUNT_{y}'})
        )
        frames.append(sums.merge(inst_counts, on=key_cols, how='outer'))

    if not frames:
        return pd.DataFrame()

    out = frames[0]
    for f in frames[1:]:
        out = out.merge(f, on=key_cols, how='outer')

    n_periods = end_year - start_year
    # Completions CAGR — volume (CAGR of sum of CTOTALT)
    compl_cagr, compl_flag = vec_cagr(
        out[f'SUM_CTOTALT_{start_year}'], out[f'SUM_CTOTALT_{end_year}'], n_periods,
    )
    out['MARKET_CAGR'] = compl_cagr
    out['MARKET_FLAG'] = compl_flag
    # Programs CAGR — breadth (CAGR of distinct institutions offering = "number of programs")
    prog_cagr, prog_flag = vec_cagr(
        out[f'INST_COUNT_{start_year}'], out[f'INST_COUNT_{end_year}'], n_periods,
    )
    out['PROGRAMS_CAGR'] = prog_cagr
    out['PROGRAMS_FLAG'] = prog_flag
    out['CIPTitle'] = out['CIPCODE'].map(title_map)

    leading = ['CIPCODE', 'CIPTitle', 'AWLEVEL']
    sum_cols = [f'SUM_CTOTALT_{y}' for y in years]
    inst_cols = [f'INST_COUNT_{y}' for y in years]
    cagr_cols = ['MARKET_CAGR', 'MARKET_FLAG', 'PROGRAMS_CAGR', 'PROGRAMS_FLAG']
    return out[leading + sum_cols + inst_cols + cagr_cols].sort_values(
        ['CIPCODE', 'AWLEVEL']
    ).reset_index(drop=True)


# ---------------------------------------------------------------------------
# National market view (cross-reference for any selected-region market view)
# ---------------------------------------------------------------------------

def compute_national_market_view(
    ca_dict: Dict[int, pd.DataFrame],
    cip_titles_df: pd.DataFrame,
    cip_codes: Optional[Iterable[str]],
    award_levels: Optional[Iterable[int]],
    include_residual: bool,
    start_year: int,
    end_year: int,
    quiet: bool = True,
) -> pd.DataFrame:
    """
    Same metrics as compute_market_view, but aggregated across ALL UNITIDs
    (no institution filter). Used to show 'how do my selected institutions
    compare to the national picture?' alongside the selected-region table.

    Implementation note: skips the metadata join (national doesn't need
    INSTNM/STABBR per row). Builds a synthetic joined dict by attaching
    CIPTitle only, then reuses apply_filters + compute_market_view.
    """
    # Attach CIPTitle so the market view tab can show it; skip the rest.
    fake_joined: Dict[int, pd.DataFrame] = {}
    title_map = (
        cip_titles_df.set_index('CIPCODE')['CIPTitle'].to_dict()
        if not cip_titles_df.empty and 'CIPTitle' in cip_titles_df.columns
        else {}
    )
    for year, df in ca_dict.items():
        sub = df.copy()
        sub['CIPTitle'] = sub['CIPCODE'].map(title_map)
        fake_joined[year] = sub

    filtered = apply_filters(
        fake_joined,
        cip_codes=cip_codes,
        award_levels=award_levels,
        include_residual=include_residual,
        quiet=quiet,
    )
    return compute_market_view(filtered, start_year=start_year, end_year=end_year)


def market_view_to_long(
    wide_mv: pd.DataFrame,
    start_year: int,
    end_year: int,
) -> pd.DataFrame:
    """
    Reshape the merged-wide market view into long format:
      4 rows per (CIPCODE × AWLEVEL): Selected/Completions, Selected/Programs,
      National/Completions, National/Programs.

    Columns: CIPCODE, CIP Title, Award Level, Geography, Metric,
             {start_year}, …, {end_year}, CAGR, Flag

    "Programs" = number of distinct institutions offering this CIP×AWLEVEL
    (one institution offering a program = one program, per user terminology).
    """
    if wide_mv.empty:
        return pd.DataFrame()
    years = list(range(start_year, end_year + 1))

    # Each (geography prefix, geography label, metric column prefix, metric label,
    #  CAGR col, Flag col) tuple becomes one row per program.
    spec = [
        ('',     'Selected', 'SUM_CTOTALT_', 'Completions', 'MARKET_CAGR',    'MARKET_FLAG'),
        ('',     'Selected', 'INST_COUNT_',  'Programs',    'PROGRAMS_CAGR',  'PROGRAMS_FLAG'),
        ('NAT_', 'National', 'SUM_CTOTALT_', 'Completions', 'NAT_MARKET_CAGR','NAT_MARKET_FLAG'),
        ('NAT_', 'National', 'INST_COUNT_',  'Programs',    'NAT_PROGRAMS_CAGR','NAT_PROGRAMS_FLAG'),
    ]

    rows: List[dict] = []
    for _, r in wide_mv.iterrows():
        for geo_prefix, geo_label, metric_prefix, metric_label, cagr_key, flag_key in spec:
            rec: Dict[str, object] = {
                'CIPCODE': r.get('CIPCODE'),
                'CIP Title': r.get('CIPTitle'),
                'Award Level': r.get('AWLEVEL'),
                'Geography': geo_label,
                'Metric': metric_label,
            }
            for y in years:
                rec[str(y)] = r.get(f'{geo_prefix}{metric_prefix}{y}')
            rec['CAGR'] = r.get(cagr_key)
            rec['Flag'] = r.get(flag_key)
            rows.append(rec)
    return pd.DataFrame(rows)


def merge_selected_and_national_market_view(
    selected_mv: pd.DataFrame,
    national_mv: pd.DataFrame,
) -> pd.DataFrame:
    """
    Outer-merge selected and national market views on (CIPCODE, AWLEVEL).
    National metric columns get a 'NAT_' prefix; CIPTitle is coalesced.
    """
    if selected_mv.empty and national_mv.empty:
        return pd.DataFrame()

    nat_prefixed = national_mv.copy()
    rename: Dict[str, str] = {}
    for c in nat_prefixed.columns:
        if (c.startswith('SUM_CTOTALT_')
                or c.startswith('INST_COUNT_')
                or c in ('MARKET_CAGR', 'MARKET_FLAG',
                          'PROGRAMS_CAGR', 'PROGRAMS_FLAG')):
            rename[c] = f'NAT_{c}'
    nat_prefixed = nat_prefixed.rename(columns=rename)

    # Drop CIPTitle from national so the selected one wins on merge.
    drop_cols = [c for c in ('CIPTitle',) if c in nat_prefixed.columns]
    if drop_cols:
        nat_prefixed = nat_prefixed.drop(columns=drop_cols)

    merged = selected_mv.merge(nat_prefixed, on=['CIPCODE', 'AWLEVEL'], how='outer')

    # When a CIP×AWLEVEL exists only in national (no selected rows), CIPTitle
    # might be missing on the selected side — backfill from the national title_map.
    if 'CIPTitle' in merged.columns and merged['CIPTitle'].isna().any():
        if 'CIPTitle' in national_mv.columns:
            nat_titles = national_mv.set_index(['CIPCODE', 'AWLEVEL'])['CIPTitle'].to_dict()
            mask = merged['CIPTitle'].isna()
            merged.loc[mask, 'CIPTitle'] = [
                nat_titles.get((row['CIPCODE'], row['AWLEVEL']))
                for _, row in merged[mask].iterrows()
            ]

    return merged.sort_values(['CIPCODE', 'AWLEVEL']).reset_index(drop=True)


# ---------------------------------------------------------------------------
# Display helpers
# ---------------------------------------------------------------------------

def _fmt_pct(v) -> str:
    if v is None or pd.isna(v):
        return 'N/A'
    return f'{v * 100:.1f}%'


def _fmt_count(v) -> str:
    if v is None or pd.isna(v):
        return '·'
    try:
        return f'{int(v):,}'
    except (ValueError, TypeError):
        return str(v)


def print_cagr_sample(df: pd.DataFrame, limit: int = 12, title: str = 'CAGR sample') -> None:
    if df.empty:
        console.print(f'[yellow]{title}: empty[/]')
        return
    years = sorted(int(c.split('_')[-1]) for c in df.columns if c.startswith('CTOTALT_'))
    table = Table(title=title)
    table.add_column('UNITID', justify='right')
    table.add_column('Institution', style='cyan', max_width=22)
    table.add_column('CIPCODE')
    table.add_column('AW', justify='right')
    for y in years:
        table.add_column(str(y), justify='right')
    table.add_column('CAGR', justify='right')
    table.add_column('Flag', style='dim')

    for _, row in df.head(limit).iterrows():
        cells = [
            str(row.get('UNITID') or ''),
            str(row.get('INSTNM') or ''),
            str(row.get('CIPCODE') or ''),
            str(row.get('AWLEVEL') or ''),
        ]
        for y in years:
            cells.append(_fmt_count(row.get(f'CTOTALT_{y}')))
        cells.append(_fmt_pct(row.get('CAGR')))
        cells.append(str(row.get('Flag') or ''))
        table.add_row(*cells)
    console.print(table)


def print_program_growth(df: pd.DataFrame, limit: int = 10) -> None:
    if df.empty:
        console.print('[yellow]program growth: empty[/]')
        return
    years = sorted(int(c.split('_')[-1]) for c in df.columns if c.startswith('PROGRAMS_'))
    table = Table(title='Program count growth')
    table.add_column('UNITID', justify='right')
    table.add_column('Institution', style='cyan', max_width=30)
    for y in years:
        table.add_column(str(y), justify='right')
    table.add_column('Δ', justify='right')
    table.add_column('% chg', justify='right')
    for _, row in df.head(limit).iterrows():
        cells = [
            str(row.get('UNITID') or ''),
            str(row.get('INSTNM') or ''),
        ]
        for y in years:
            cells.append(_fmt_count(row.get(f'PROGRAMS_{y}')))
        cells.append(_fmt_count(row.get('DELTA')))
        cells.append(_fmt_pct(row.get('PCT_CHANGE')))
        table.add_row(*cells)
    console.print(table)


def print_market_view(df: pd.DataFrame, limit: int = 10) -> None:
    if df.empty:
        console.print('[yellow]market view: empty[/]')
        return
    years = sorted(int(c.split('_')[-1]) for c in df.columns if c.startswith('SUM_CTOTALT_'))
    table = Table(title='Market view')
    table.add_column('CIPCODE')
    table.add_column('Title', style='cyan', max_width=30)
    table.add_column('AW', justify='right')
    for y in years:
        table.add_column(f'Σ{y}', justify='right')
    table.add_column('Inst count (latest)', justify='right')
    table.add_column('Market CAGR', justify='right')
    table.add_column('Flag', style='dim')
    latest = years[-1]
    for _, row in df.head(limit).iterrows():
        cells = [
            str(row.get('CIPCODE') or ''),
            str(row.get('CIPTitle') or ''),
            str(row.get('AWLEVEL') or ''),
        ]
        for y in years:
            cells.append(_fmt_count(row.get(f'SUM_CTOTALT_{y}')))
        cells.append(_fmt_count(row.get(f'INST_COUNT_{latest}')))
        cells.append(_fmt_pct(row.get('MARKET_CAGR')))
        cells.append(str(row.get('MARKET_FLAG') or ''))
        table.add_row(*cells)
    console.print(table)


# ---------------------------------------------------------------------------
# Unit tests for the CAGR formula (spec calls out: "unit test each formula")
# ---------------------------------------------------------------------------

def _self_test_cagr() -> None:
    """Synthetic unit test for vec_cagr — runs at top of the smoke test."""
    start = pd.Series([100, 0, 50, pd.NA, 50, 200])
    end = pd.Series([200, 80, 0, 100, pd.NA, 200])
    cagr, flag = vec_cagr(start, end, n_periods=4)

    # Expected
    expected_flags = [
        CAGR_FLAG_OK,        # 100 → 200
        CAGR_FLAG_NEW,       # 0 → 80
        CAGR_FLAG_ENDED,     # 50 → 0
        CAGR_FLAG_MISSING,   # NaN → 100
        CAGR_FLAG_MISSING,   # 50 → NaN
        CAGR_FLAG_OK,        # 200 → 200 (flat)
    ]
    expected_cagr_ok_0 = (200 / 100) ** 0.25 - 1   # ≈ 0.1892
    expected_cagr_flat = 0.0

    table = Table(title='CAGR self-test')
    table.add_column('Case')
    table.add_column('start', justify='right')
    table.add_column('end', justify='right')
    table.add_column('CAGR', justify='right')
    table.add_column('Flag')
    table.add_column('Expected flag', style='dim')
    table.add_column('Pass', justify='center')

    for i, (s_val, e_val, exp_flag) in enumerate(zip(start, end, expected_flags)):
        ok = flag.iloc[i] == exp_flag
        table.add_row(
            str(i + 1),
            str(s_val), str(e_val),
            'NaN' if pd.isna(cagr.iloc[i]) else f'{cagr.iloc[i]:.4f}',
            flag.iloc[i],
            exp_flag,
            '[green]✓[/]' if ok else '[red]✗[/]',
        )
    console.print(table)

    # Numeric assertions
    assert abs(cagr.iloc[0] - expected_cagr_ok_0) < 1e-9, 'CAGR 100→200 over 4 yrs'
    assert cagr.iloc[2] == -1.0, 'Program Ended → CAGR = -1.0'
    assert abs(cagr.iloc[5] - expected_cagr_flat) < 1e-9, 'flat CAGR == 0'
    assert pd.isna(cagr.iloc[1]), 'New Program → CAGR NaN'
    assert pd.isna(cagr.iloc[3]) and pd.isna(cagr.iloc[4]), 'Missing → CAGR NaN'
    console.print('[green]✓ all CAGR assertions passed[/]')


# ---------------------------------------------------------------------------
# Smoke test
# ---------------------------------------------------------------------------

if __name__ == '__main__':
    from loader import load_all, load_years_config
    from joiner import build_institution_metadata, build_cip_title_lookup, join_all_years

    project_root = Path(__file__).resolve().parent.parent
    years_cfg = load_years_config(project_root / 'config' / 'years.yaml')

    console.rule('[bold]aggregator — smoke test[/]')

    # ── 1. CAGR formula self-test ──
    console.rule('1. CAGR formula self-test')
    _self_test_cagr()

    # ── 2. Real-data pipeline on a small CA private-nonprofit-4yr slice ──
    console.rule('2. real-data pipeline')
    console.print('[dim]loading…[/]')
    loaded = load_all(
        years_cfg,
        raw_dir=project_root / 'data' / 'raw',
        dict_dir=project_root / 'data' / 'dictionary',
    )
    metadata = build_institution_metadata(loaded['hd'])
    cip_titles = build_cip_title_lookup(loaded['crosswalk'])

    # Sample selection: 5 well-known institutions, several CIPs, a few AWLEVELs.
    selected_unitids = [
        110635,  # UC Berkeley
        110662,  # UCLA
        209612,  # Pacific University (OR)
        236577,  # Seattle Pacific
        101541,  # Judson College (closed mid-window)
    ]
    cip_codes = ['51.3801', '52.0201', '11.0701', '14.0901']  # Nursing/BizAdmin/CS/CompEng
    award_levels = [5, 7]  # Bachelor's, Master's

    console.print(f'institutions: {selected_unitids}')
    console.print(f'CIPs: {cip_codes}   AWLEVELs: {award_levels}')

    joined = join_all_years(
        loaded['ca'], metadata, cip_titles,
        selected_unitids=selected_unitids,
        quiet=True,
    )

    console.rule('3. filter pipeline (row counts per step)')
    filtered = apply_filters(
        joined, cip_codes=cip_codes, award_levels=award_levels,
        include_residual=False,
    )
    warn_zero_completion_unitids(filtered, selected_unitids)

    # ── CAGR table ──
    console.rule('4. CAGR by institution × CIP × AWLEVEL')
    cagr_df = compute_cagr_table(
        filtered,
        start_year=years_cfg['cagr_start_year'],
        end_year=years_cfg['cagr_end_year'],
    )
    print_cagr_sample(cagr_df, limit=20)
    console.print(
        f'  total rows: {len(cagr_df):,}   '
        f'flag counts: {cagr_df["Flag"].value_counts().to_dict()}'
    )

    # ── Program growth ──
    console.rule('5. Program count growth (breadth)')
    pg_df = compute_program_growth(
        filtered, metadata,
        start_year=years_cfg['cagr_start_year'],
        end_year=years_cfg['cagr_end_year'],
    )
    print_program_growth(pg_df, limit=10)

    # ── Market view ──
    console.rule('6. Market view (sum across selected institutions)')
    mv_df = compute_market_view(
        filtered,
        start_year=years_cfg['cagr_start_year'],
        end_year=years_cfg['cagr_end_year'],
    )
    print_market_view(mv_df, limit=10)
    console.print(
        f'  total rows: {len(mv_df):,}   '
        f'flag counts: {mv_df["MARKET_FLAG"].value_counts().to_dict()}'
    )
