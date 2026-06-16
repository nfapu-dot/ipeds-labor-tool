"""
resolver.py — Resolves user institution selection into a concrete UNITID list.

Three modes (may combine; main.py unions their UNITID outputs):
- Mode 1 (`--search "<name>"`): substring + fuzzy match against HD INSTNM,
  interactive numbered pick.
- Mode 2 (`--state <ABBR>`): all UNITIDs in state. Optional --control / --iclevel
  sub-filters. Warn (and require confirmation) when count > 200.
- Mode 3 (`--unitids ...` or config/institutions.csv): fixed UNITID list.

Authoritative HD = the most recent year in the loaded HD dict. Older HD files
are scanned only to surface UNITIDs that appear to have closed/merged.
"""
from __future__ import annotations

from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

import pandas as pd
from rich.console import Console
from rich.prompt import Confirm, Prompt
from rich.table import Table

try:
    from rapidfuzz import fuzz, process
    _HAS_RAPIDFUZZ = True
except ImportError:  # pragma: no cover
    _HAS_RAPIDFUZZ = False

console = Console()

CONTROL_LABELS: Dict[int, str] = {
    1: 'Public', 2: 'Private nonprofit', 3: 'Private for-profit',
}
ICLEVEL_LABELS: Dict[int, str] = {
    1: '4-year', 2: '2-year', 3: 'Less-than-2-year',
}
LARGE_RESULT_THRESHOLD = 200


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _latest_hd(hd_dict: Dict[int, pd.DataFrame]) -> Tuple[int, pd.DataFrame]:
    year = max(hd_dict.keys())
    return year, hd_dict[year]


def _label_codes(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    if 'CONTROL' in df.columns:
        df['CONTROL_LABEL'] = df['CONTROL'].map(CONTROL_LABELS)
    if 'ICLEVEL' in df.columns:
        df['ICLEVEL_LABEL'] = df['ICLEVEL'].map(ICLEVEL_LABELS)
    return df


# ---------------------------------------------------------------------------
# Mode 1 — name search
# ---------------------------------------------------------------------------

def search_by_name(
    query: str,
    hd_dict: Dict[int, pd.DataFrame],
    top_n: int = 25,
    fuzzy_cutoff: int = 60,
) -> pd.DataFrame:
    """
    Two-pass search against the latest HD INSTNM column:
      1. Case-insensitive substring matches first (score=100).
      2. RapidFuzz WRatio backfill from the remaining rows.
    Returns ≤ top_n rows with display labels and a `score` column.
    """
    year, hd = _latest_hd(hd_dict)
    if 'INSTNM' not in hd.columns:
        raise ValueError(f'HD {year} missing INSTNM')

    q = query.strip()
    q_lc = q.lower()

    instnm = hd['INSTNM'].astype(str)
    mask = instnm.str.lower().str.contains(q_lc, na=False, regex=False)
    partial = hd[mask].copy()
    partial['score'] = 100.0

    if len(partial) >= top_n or not _HAS_RAPIDFUZZ:
        results = partial.head(top_n)
    else:
        rest = hd[~mask]
        choices = rest['INSTNM'].astype(str).tolist()
        fuzzy = process.extract(
            q, choices, scorer=fuzz.WRatio,
            limit=top_n - len(partial), score_cutoff=fuzzy_cutoff,
        )
        if fuzzy:
            fuzzy_idx = [rest.index[m[2]] for m in fuzzy]
            fuzzy_df = hd.loc[fuzzy_idx].copy()
            fuzzy_df['score'] = [m[1] for m in fuzzy]
            results = pd.concat([partial, fuzzy_df]).head(top_n)
        else:
            results = partial

    return _label_codes(results)


def interactive_pick(matches: pd.DataFrame) -> List[int]:
    """
    Prompt user to choose rows from a printed match table.
    Accepts '1', '1,3,5', '1-5', '1,3-7'. Returns selected UNITIDs.
    Returns [] if the user submits a blank line.
    """
    if matches.empty:
        return []
    raw = Prompt.ask(
        'Select rows (e.g. "1", "1,3,5", "1-5"; blank to skip)',
        default='',
        show_default=False,
    ).strip()
    if not raw:
        return []
    picks: set = set()
    for token in raw.split(','):
        token = token.strip()
        if not token:
            continue
        if '-' in token:
            a, b = token.split('-', 1)
            picks.update(range(int(a), int(b) + 1))
        else:
            picks.add(int(token))
    valid_picks = [p for p in sorted(picks) if 1 <= p <= len(matches)]
    if not valid_picks:
        return []
    rows = matches.iloc[[p - 1 for p in valid_picks]]
    return [int(u) for u in rows['UNITID'].dropna().tolist()]


# ---------------------------------------------------------------------------
# Mode 2 — state filter
# ---------------------------------------------------------------------------

def filter_by_state(
    state: str,
    hd_dict: Dict[int, pd.DataFrame],
    control: Optional[int] = None,
    iclevel: Optional[int] = None,
) -> pd.DataFrame:
    """All institutions in `state`, with optional CONTROL and ICLEVEL sub-filters."""
    _, hd = _latest_hd(hd_dict)
    state_norm = state.upper().strip()
    df = hd[hd['STABBR'] == state_norm]
    if control is not None:
        df = df[df['CONTROL'] == control]
    if iclevel is not None:
        df = df[df['ICLEVEL'] == iclevel]
    return _label_codes(df)


def confirm_large_result(count: int, threshold: int = LARGE_RESULT_THRESHOLD) -> bool:
    """Prompt for confirmation when count exceeds threshold."""
    if count <= threshold:
        return True
    return Confirm.ask(
        f'[yellow]{count} institutions matched (>{threshold}). Continue?[/]',
        default=False,
    )


# ---------------------------------------------------------------------------
# Mode 3 — fixed UNITID list
# ---------------------------------------------------------------------------

def resolve_fixed_list(
    unitids_arg: Optional[Iterable[int]],
    institutions_csv: Optional[Path],
    hd_dict: Dict[int, pd.DataFrame],
) -> Tuple[pd.DataFrame, List[int]]:
    """
    Resolve --unitids (preferred) or fall back to config/institutions.csv.
    Returns (resolved_df, unmatched_unitids).
    """
    _, hd = _latest_hd(hd_dict)

    unitids: List[int] = []
    if unitids_arg:
        unitids = [int(u) for u in unitids_arg]
    elif institutions_csv and institutions_csv.exists():
        cfg = pd.read_csv(institutions_csv)
        if 'UNITID' in cfg.columns:
            unitids = [int(u) for u in cfg['UNITID'].dropna().tolist()]

    if not unitids:
        return pd.DataFrame(), []

    resolved = hd[hd['UNITID'].isin(unitids)]
    matched_ids = set(resolved['UNITID'].dropna().astype(int).tolist())
    unmatched = sorted(set(unitids) - matched_ids)
    return _label_codes(resolved), unmatched


# ---------------------------------------------------------------------------
# Closure / merger cross-reference
# ---------------------------------------------------------------------------

def cross_reference_closures(
    unitids: Iterable[int],
    hd_dict: Dict[int, pd.DataFrame],
) -> pd.DataFrame:
    """
    For UNITIDs missing from the latest HD, scan older HD files for the most
    recent appearance — these are likely closures or mergers.
    Returns: UNITID, last_seen_year, INSTNM, STABBR.
    """
    latest_year, _ = _latest_hd(hd_dict)
    rows = []
    for year in sorted(hd_dict.keys()):
        if year == latest_year:
            continue
        hd = hd_dict[year]
        found = hd[hd['UNITID'].isin(list(unitids))]
        for _, row in found.iterrows():
            rows.append({
                'UNITID': int(row['UNITID']),
                'last_seen_year': year,
                'INSTNM': row.get('INSTNM'),
                'STABBR': row.get('STABBR'),
            })
    if not rows:
        return pd.DataFrame(columns=['UNITID', 'last_seen_year', 'INSTNM', 'STABBR'])
    df = pd.DataFrame(rows).sort_values('last_seen_year').drop_duplicates(
        'UNITID', keep='last'
    )
    return df.reset_index(drop=True)


# ---------------------------------------------------------------------------
# Display helpers
# ---------------------------------------------------------------------------

def print_institution_table(
    df: pd.DataFrame,
    title: str = 'Institutions',
    show_score: bool = False,
    limit: Optional[int] = None,
) -> None:
    if df.empty:
        console.print(f'[yellow]{title}: no rows[/]')
        return
    rows = df if limit is None else df.head(limit)
    table = Table(title=title)
    table.add_column('#', justify='right', style='dim')
    table.add_column('UNITID', justify='right')
    table.add_column('Institution', style='cyan', max_width=45)
    table.add_column('State')
    table.add_column('Control')
    table.add_column('Level')
    if show_score and 'score' in df.columns:
        table.add_column('Score', justify='right')

    for i, (_, row) in enumerate(rows.iterrows(), start=1):
        cells = [
            str(i),
            str(row.get('UNITID') or ''),
            str(row.get('INSTNM') or ''),
            str(row.get('STABBR') or ''),
            str(row.get('CONTROL_LABEL') or ''),
            str(row.get('ICLEVEL_LABEL') or ''),
        ]
        if show_score and 'score' in df.columns:
            cells.append(f'{row["score"]:.0f}')
        table.add_row(*cells)

    suffix = f'  (showing {len(rows)} of {len(df)})' if limit and limit < len(df) else ''
    console.print(table)
    if suffix:
        console.print(f'[dim]{suffix.strip()}[/]')


def print_closure_table(df: pd.DataFrame, title: str = 'Likely closures/mergers') -> None:
    if df.empty:
        console.print(f'[dim]{title}: none[/]')
        return
    table = Table(title=title)
    table.add_column('UNITID', justify='right')
    table.add_column('Last seen', justify='right')
    table.add_column('Institution', style='cyan', max_width=45)
    table.add_column('State')
    for _, row in df.iterrows():
        table.add_row(
            str(row['UNITID']),
            str(row['last_seen_year']),
            str(row.get('INSTNM') or ''),
            str(row.get('STABBR') or ''),
        )
    console.print(table)


# ---------------------------------------------------------------------------
# Smoke test
# ---------------------------------------------------------------------------

if __name__ == '__main__':
    from loader import load_all, load_years_config

    project_root = Path(__file__).resolve().parent.parent
    years_cfg = load_years_config(project_root / 'config' / 'years.yaml')

    console.rule('[bold]resolver — smoke test[/]')
    console.print('[dim]loading HD files (silently)…[/]')
    loaded = load_all(
        years_cfg,
        raw_dir=project_root / 'data' / 'raw',
        dict_dir=project_root / 'data' / 'dictionary',
    )
    hd = loaded['hd']
    latest_year, latest_hd = _latest_hd(hd)
    console.print(f'[dim]authoritative HD = hd{latest_year} '
                  f'({len(latest_hd):,} institutions)[/]')

    # ── 1. name search ──
    console.rule('1. name search: "Pacific University"')
    matches = search_by_name('Pacific University', hd, top_n=10)
    print_institution_table(matches, title='Top matches', show_score=True)

    # ── 2. small state ──
    console.rule('2. state filter: HI (no sub-filters)')
    hi = filter_by_state('HI', hd)
    print_institution_table(hi, title=f'HI · {len(hi)} institutions', limit=15)

    # ── 3. state + control + iclevel ──
    console.rule('3. state filter: CA · private nonprofit · 4-year')
    ca_pn_4y = filter_by_state('CA', hd, control=2, iclevel=1)
    print_institution_table(
        ca_pn_4y, title=f'CA · CONTROL=2 · ICLEVEL=1 · {len(ca_pn_4y)} institutions',
        limit=10,
    )

    # ── 4. large-result threshold preview ──
    console.rule('4. large-result threshold preview')
    all_ca = filter_by_state('CA', hd)
    console.print(f'all CA = {len(all_ca):,} institutions (threshold={LARGE_RESULT_THRESHOLD})')
    console.print('[dim](confirm prompt skipped in non-interactive smoke test)[/]')

    # ── 5. fixed-list resolution ──
    console.rule('5. fixed UNITID list')
    sample_ids = [110644, 110635, 110662, 99999999]  # last one is intentionally bogus
    resolved, unmatched = resolve_fixed_list(sample_ids, None, hd)
    print_institution_table(resolved, title=f'resolved {len(resolved)} of {len(sample_ids)}')
    console.print(f'[yellow]unmatched UNITIDs: {unmatched}[/]')

    # ── 6. closure cross-reference ──
    console.rule('6. closure / merger cross-reference')
    in_oldest = set(hd[min(hd.keys())]['UNITID'].dropna().astype(int))
    in_latest = set(hd[latest_year]['UNITID'].dropna().astype(int))
    likely_closed = sorted(in_oldest - in_latest)[:5]
    console.print(f'testing {len(likely_closed)} UNITIDs present in hd{min(hd.keys())} '
                  f'but missing from hd{latest_year}: {likely_closed}')
    closures = cross_reference_closures(likely_closed, hd)
    print_closure_table(closures)
