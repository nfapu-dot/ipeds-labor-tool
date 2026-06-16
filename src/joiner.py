"""
joiner.py — Joins Completions A (C_A) rows to HD institutional metadata.

Responsibilities:
- Build an authoritative per-UNITID metadata table by walking HD files newest →
  oldest and taking each UNITID's most recent appearance. Active institutions
  resolve to the latest HD; closed/merged institutions resolve to the last HD
  year they appeared in. The source year is recorded in HD_SOURCE_YEAR.
- Left-join C_A rows on UNITID → metadata (INSTNM, STABBR, CITY, CONTROL,
  ICLEVEL, CARNEGIE, HD_SOURCE_YEAR).
- Carnegie: prefer C21BASIC, fall back to C00CARNEGIE. Neither column exists in
  hd2020; affected UNITIDs receive NaN.
- Left-join CIPCODE → CIPTitle (deduped 1:1 from the CIP-SOC crosswalk).
  SOC columns are retained on the loaded crosswalk DataFrame for v2 labor
  market integration but are NOT joined to C_A — multiple SOCs per CIP would
  cause row explosion. (See SPEC §CIP-SOC Crosswalk.)
- Match CIP codes at the same digit level (6-digit ↔ 6-digit). Aggregate
  rollup rows (e.g., bare '99', '01') won't match the 6-digit crosswalk;
  their CIPTitle stays NaN and IS_CIP_6DIGIT remains False.
- Log unmatched UNITIDs as [NOT IN HD — POSSIBLE CLOSURE] before/after the join.
"""
from __future__ import annotations

from pathlib import Path
from typing import Dict, Iterable, List, Optional

import pandas as pd
from rich.console import Console
from rich.table import Table

console = Console()

METADATA_COLS = [
    'UNITID', 'INSTNM', 'STABBR', 'CITY',
    'CONTROL', 'ICLEVEL', 'CARNEGIE', 'HD_SOURCE_YEAR',
]


# ---------------------------------------------------------------------------
# Metadata builder
# ---------------------------------------------------------------------------

def build_institution_metadata(hd_dict: Dict[int, pd.DataFrame]) -> pd.DataFrame:
    """
    Authoritative per-UNITID metadata, walking newest → oldest HD and keeping
    the most recent appearance of each UNITID. HD_SOURCE_YEAR records which
    HD the row came from (useful for diagnosing closures).
    """
    frames: List[pd.DataFrame] = []
    for year in sorted(hd_dict.keys(), reverse=True):
        hd = hd_dict[year]
        keep = [c for c in ('UNITID', 'INSTNM', 'STABBR', 'CITY', 'CONTROL', 'ICLEVEL')
                if c in hd.columns]
        df = hd[keep].copy()

        # Carnegie: C21BASIC preferred, C00CARNEGIE fallback.
        if 'C21BASIC' in hd.columns:
            df['CARNEGIE'] = hd['C21BASIC']
            if 'C00CARNEGIE' in hd.columns:
                df['CARNEGIE'] = df['CARNEGIE'].fillna(hd['C00CARNEGIE'])
        elif 'C00CARNEGIE' in hd.columns:
            df['CARNEGIE'] = hd['C00CARNEGIE']
        else:
            df['CARNEGIE'] = pd.NA

        df['HD_SOURCE_YEAR'] = year
        frames.append(df)

    combined = pd.concat(frames, ignore_index=True)
    combined = (
        combined
        .dropna(subset=['UNITID'])
        .drop_duplicates('UNITID', keep='first')  # 'first' = newest year (frames sorted reverse)
        .reset_index(drop=True)
    )
    # Ensure all expected columns exist even if some HD years lacked them.
    for c in METADATA_COLS:
        if c not in combined.columns:
            combined[c] = pd.NA
    return combined[METADATA_COLS]


# ---------------------------------------------------------------------------
# CIP title lookup
# ---------------------------------------------------------------------------

def build_cip_title_lookup(crosswalk_df: pd.DataFrame) -> pd.DataFrame:
    """
    Deduplicated CIPCODE → CIPTitle map from the crosswalk.
    Multiple SOC rows per CIP are collapsed; the first CIPTitle per code wins.
    """
    if crosswalk_df.empty or 'CIPCODE' not in crosswalk_df.columns:
        return pd.DataFrame(columns=['CIPCODE', 'CIPTitle'])
    keep_cols = ['CIPCODE'] + (['CIPTitle'] if 'CIPTitle' in crosswalk_df.columns else [])
    titles = (
        crosswalk_df[keep_cols]
        .dropna(subset=['CIPCODE'])
        .drop_duplicates('CIPCODE', keep='first')
        .reset_index(drop=True)
    )
    if 'CIPTitle' not in titles.columns:
        titles['CIPTitle'] = pd.NA
    titles['CIPCODE'] = titles['CIPCODE'].astype(str).str.strip()
    return titles


# ---------------------------------------------------------------------------
# Join
# ---------------------------------------------------------------------------

def join_completions(
    ca_df: pd.DataFrame,
    metadata_df: pd.DataFrame,
    cip_titles_df: pd.DataFrame,
    year: int,
    quiet: bool = False,
) -> pd.DataFrame:
    """
    Enrich one year's C_A with institution metadata and CIPTitle.

    The input ca_df MUST already be MAJORNUM == 1 filtered (loader handles
    this). Left joins only — no rows dropped. Unmatched UNITIDs receive
    NaN metadata and are logged.
    """
    n_input = len(ca_df)
    unique_unitids = ca_df['UNITID'].dropna().unique()
    n_unitids = len(unique_unitids)

    # UNITID → metadata
    merged = ca_df.merge(metadata_df, on='UNITID', how='left', suffixes=('', '_hd'))

    # CIPCODE → CIPTitle (left, deduped)
    if not cip_titles_df.empty:
        merged = merged.merge(cip_titles_df, on='CIPCODE', how='left')
    else:
        merged['CIPTitle'] = pd.NA

    unmatched_uids = sorted({
        int(u) for u in unique_unitids
        if u not in set(metadata_df['UNITID'].dropna().astype(int))
    })

    if not quiet:
        console.log(
            f'[cyan]join {year}[/]  rows: {n_input:,}  UNITIDs: {n_unitids:,}  '
            f'unmatched UNITIDs: {len(unmatched_uids):,}'
        )
        if unmatched_uids:
            preview = unmatched_uids[:5]
            console.log(
                f'  [yellow][NOT IN HD — POSSIBLE CLOSURE][/] '
                f'sample: {preview}{" …" if len(unmatched_uids) > 5 else ""}'
            )

    return merged


def join_all_years(
    ca_dict: Dict[int, pd.DataFrame],
    metadata_df: pd.DataFrame,
    cip_titles_df: pd.DataFrame,
    selected_unitids: Optional[Iterable[int]] = None,
    quiet: bool = False,
) -> Dict[int, pd.DataFrame]:
    """
    Filter each year's C_A to selected_unitids (if provided) and enrich.
    Returns {year: enriched_df}. Logs before/after row counts per year.
    """
    selected = set(int(u) for u in selected_unitids) if selected_unitids else None
    out: Dict[int, pd.DataFrame] = {}
    for year in sorted(ca_dict.keys()):
        df = ca_dict[year]
        n_before = len(df)
        if selected is not None:
            df = df[df['UNITID'].isin(selected)].copy()
        n_after = len(df)
        if not quiet:
            scope = (
                f'filtered: {n_before:,} → {n_after:,}  (kept {len(selected):,} UNITIDs)'
                if selected is not None else f'rows: {n_before:,}  (no UNITID filter)'
            )
            console.log(f'[cyan]C_A {year}[/]  {scope}')
        out[year] = join_completions(df, metadata_df, cip_titles_df, year, quiet=quiet)
    return out


# ---------------------------------------------------------------------------
# Display helpers
# ---------------------------------------------------------------------------

def print_metadata_summary(metadata_df: pd.DataFrame) -> None:
    counts = metadata_df['HD_SOURCE_YEAR'].value_counts().sort_index(ascending=False)
    carnegie_pct = 100 * metadata_df['CARNEGIE'].notna().sum() / max(len(metadata_df), 1)
    table = Table(title=f'Authoritative metadata · {len(metadata_df):,} UNITIDs')
    table.add_column('HD_SOURCE_YEAR', justify='right')
    table.add_column('Count', justify='right')
    table.add_column('Share', justify='right')
    for year, n in counts.items():
        share = 100 * n / len(metadata_df)
        table.add_row(str(year), f'{n:,}', f'{share:.1f}%')
    console.print(table)
    console.print(f'[dim]Carnegie populated: {carnegie_pct:.1f}% of UNITIDs[/]')


def print_joined_sample(
    joined_by_year: Dict[int, pd.DataFrame],
    year: int,
    unitids: Optional[Iterable[int]] = None,
    limit: int = 12,
) -> None:
    df = joined_by_year[year]
    if unitids is not None:
        df = df[df['UNITID'].isin(list(unitids))]
    df = df.head(limit)
    table = Table(title=f'C_A {year} · joined sample ({len(df)} rows)')
    table.add_column('UNITID', justify='right')
    table.add_column('Institution', style='cyan', max_width=30)
    table.add_column('CIPCODE')
    table.add_column('CIPTitle', max_width=35)
    table.add_column('AWLEVEL', justify='right')
    table.add_column('CTOTALT', justify='right')
    table.add_column('Carnegie', justify='right')
    for _, row in df.iterrows():
        ctot = row.get('CTOTALT')
        ctot_str = f'{int(ctot):,}' if pd.notna(ctot) else '·'
        carn = row.get('CARNEGIE')
        carn_str = '' if pd.isna(carn) else str(int(carn)) if isinstance(carn, (int, float)) else str(carn)
        table.add_row(
            str(row.get('UNITID') or ''),
            str(row.get('INSTNM') or ''),
            str(row.get('CIPCODE') or ''),
            str(row.get('CIPTitle') or ''),
            str(row.get('AWLEVEL') or ''),
            ctot_str,
            carn_str,
        )
    console.print(table)


# ---------------------------------------------------------------------------
# Smoke test
# ---------------------------------------------------------------------------

if __name__ == '__main__':
    from loader import load_all, load_years_config

    project_root = Path(__file__).resolve().parent.parent
    years_cfg = load_years_config(project_root / 'config' / 'years.yaml')

    console.rule('[bold]joiner — smoke test[/]')
    console.print('[dim]loading…[/]')
    loaded = load_all(
        years_cfg,
        raw_dir=project_root / 'data' / 'raw',
        dict_dir=project_root / 'data' / 'dictionary',
    )

    # 1. Metadata coverage
    console.rule('1. authoritative metadata')
    metadata = build_institution_metadata(loaded['hd'])
    print_metadata_summary(metadata)

    # 2. CIP title lookup
    console.rule('2. CIP title lookup')
    cip_titles = build_cip_title_lookup(loaded['crosswalk'])
    console.print(f'unique CIPs with title: {len(cip_titles):,}')
    sample_cips = ['51.3801', '52.0201', '11.0701', '14.0901']
    sample = cip_titles[cip_titles['CIPCODE'].isin(sample_cips)]
    table = Table(title='Sample CIP titles')
    table.add_column('CIPCODE'); table.add_column('CIPTitle', style='cyan')
    for _, r in sample.iterrows():
        table.add_row(r['CIPCODE'], str(r['CIPTitle']))
    console.print(table)

    # 3. One-year join on a small selected set
    console.rule('3. join 2024 · UC Berkeley + Pacific U (OR) + Judson College (closed)')
    selected = [110635, 209612, 101541]  # UCB, Pacific U OR, Judson (closed)
    joined = join_all_years(
        loaded['ca'], metadata, cip_titles,
        selected_unitids=selected,
    )

    # 4. Sample joined rows for 2024
    console.rule('4. sample joined rows · 2024')
    print_joined_sample(joined, 2024, unitids=selected, limit=12)

    # 5. Sample joined rows for 2020 (Judson should still appear here)
    console.rule('5. sample joined rows · 2020 (closed school present)')
    print_joined_sample(joined, 2020, unitids=selected, limit=12)

    # 6. Cross-year row counts
    console.rule('6. per-year row counts (selected institutions)')
    counts_table = Table()
    counts_table.add_column('Year', justify='right')
    counts_table.add_column('Joined rows', justify='right')
    counts_table.add_column('Unique UNITIDs', justify='right')
    counts_table.add_column('CIPTitle hit-rate', justify='right')
    for y, df in joined.items():
        hit = df['CIPTitle'].notna().sum()
        rate = 100 * hit / max(len(df), 1)
        counts_table.add_row(
            str(y), f'{len(df):,}',
            f'{df["UNITID"].nunique():,}', f'{rate:.1f}%',
        )
    console.print(counts_table)
