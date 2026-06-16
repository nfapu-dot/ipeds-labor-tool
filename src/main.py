"""
main.py — CLI entry point for the IPEDS Completions analysis tool.

Pipeline:
  loader  → resolver  → joiner  → aggregator  → reporter

Modes (may combine — selected UNITIDs are unioned across modes):
  --search "<name>"          Interactive name search
  --state <ABBR>             All institutions in state
  --control 1|2|3            Sub-filter for --state
  --iclevel 1|2|3            Sub-filter for --state
  --unitids <id> [<id> ...]  Fixed list (overrides config/institutions.csv)

Runtime overrides:
  --cip <code> [<code> ...]  Overrides cip_filter.yaml cip_codes
  --awlevel <n> [<n> ...]    Overrides cip_filter.yaml award_levels
  --include-residual         Include CIP 99 aggregate-rollup rows

Output:
  --output <dir>             Overrides output/reports/
  --verbose                  Verbose module-level logging

Filename: IPEDS_Completions_{label}_{YYYYMMDD_HHMMSS}.xlsx
  label = state code (if --state only) else 'custom'
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import List, Optional, Tuple

from rich.console import Console

CIP_6DIGIT_PATTERN = re.compile(r'^\d{2}\.\d{4}$')

from aggregator import (
    apply_filters,
    compute_cagr_table,
    compute_market_view,
    compute_national_market_view,
    compute_program_growth,  # noqa: F401 — retained for back-compat with older scripts
    market_view_to_long,
    merge_selected_and_national_market_view,
    warn_zero_completion_unitids,
)
from joiner import (
    build_cip_title_lookup,
    build_institution_metadata,
    join_all_years,
)
from loader import load_all, load_cip_filter_config, load_years_config
from reporter import build_workbook
from resolver import (
    confirm_large_result,
    cross_reference_closures,
    filter_by_state,
    interactive_pick,
    print_closure_table,
    print_institution_table,
    resolve_fixed_list,
    search_by_name,
)

console = Console()


# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------

def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog='ipeds-tool',
        description=(
            'IPEDS Completions analysis — joins HD + C_A surveys across years, '
            'computes CAGR / program-count growth / market view, and writes an Excel workbook.'
        ),
    )
    # Selection modes
    p.add_argument('--search', metavar='NAME',
                   help='Interactive name search against HD INSTNM.')
    p.add_argument('--state', metavar='ABBR',
                   help='Filter by state abbreviation (e.g., CA).')
    p.add_argument('--control', type=int, choices=[1, 2, 3],
                   help='Sub-filter: 1=Public, 2=Private nonprofit, 3=Private for-profit.')
    p.add_argument('--iclevel', type=int, choices=[1, 2, 3],
                   help='Sub-filter: 1=4-year, 2=2-year, 3=<2-year.')
    p.add_argument('--unitids', nargs='+', type=int, metavar='UNITID',
                   help='Specific UNITIDs (overrides config/institutions.csv).')

    # Filters
    p.add_argument('--cip', nargs='+', metavar='CODE',
                   help='Override cip_filter.yaml cip_codes (use quotes to preserve leading zeros).')
    p.add_argument('--awlevel', nargs='+', type=int, metavar='N',
                   help='Override cip_filter.yaml award_levels.')
    p.add_argument('--include-residual', action='store_true',
                   help='Include CIP 99 aggregate-rollup rows (excluded by default).')

    # Output
    p.add_argument('--output', metavar='DIR',
                   help='Output directory (default: output/reports/).')
    p.add_argument('--verbose', action='store_true',
                   help='Verbose module-level logging.')
    return p


# ---------------------------------------------------------------------------
# Institution resolution (combines modes)
# ---------------------------------------------------------------------------

def _resolve_unitids(
    args: argparse.Namespace,
    hd_dict: dict,
    project_root: Path,
) -> Tuple[List[int], str]:
    """
    Run any combination of --search / --state / --unitids, plus fall back to
    config/institutions.csv when nothing else is set. Returns (sorted unique
    UNITID list, filename label).
    """
    unitids: set = set()

    # ── Mode 1: name search ──
    if args.search:
        matches = search_by_name(args.search, hd_dict, top_n=25)
        print_institution_table(
            matches, title=f'Matches for "{args.search}"', show_score=True,
        )
        picks = interactive_pick(matches)
        if picks:
            unitids.update(picks)
            console.print(f'[green]added {len(picks)} from search[/]')
        else:
            console.print('[yellow]no selection from search[/]')

    # ── Mode 2: state filter ──
    if args.state:
        state_df = filter_by_state(
            args.state, hd_dict,
            control=args.control, iclevel=args.iclevel,
        )
        n = len(state_df)
        scope = f'{args.state.upper()}'
        if args.control:
            scope += f' · CONTROL={args.control}'
        if args.iclevel:
            scope += f' · ICLEVEL={args.iclevel}'
        console.print(f'[cyan]state filter[/] {scope} → {n} institutions')
        if n == 0:
            console.print(f'[yellow]no institutions match {scope}[/]')
        elif confirm_large_result(n):
            unitids.update(int(u) for u in state_df['UNITID'].dropna())
            console.print(f'[green]added {n} from state filter[/]')
        else:
            console.print('[yellow]declined large state result; skipping state mode[/]')

    # ── Mode 3: explicit UNITIDs (overrides CSV) ──
    if args.unitids:
        resolved, unmatched = resolve_fixed_list(args.unitids, None, hd_dict)
        if not resolved.empty:
            unitids.update(int(u) for u in resolved['UNITID'].dropna())
            console.print(f'[green]added {len(resolved)} from --unitids[/]')
        if unmatched:
            console.print(f'[yellow]--unitids unmatched in latest HD: {unmatched}[/]')
            # See if they exist in older HDs (closures)
            closure_df = cross_reference_closures(unmatched, hd_dict)
            if not closure_df.empty:
                print_closure_table(closure_df)
                unitids.update(int(u) for u in closure_df['UNITID'])
                console.print(
                    f'[green]added {len(closure_df)} closed/merged UNITIDs from older HD files[/]'
                )

    # ── Fallback: config/institutions.csv ──
    if not unitids and not (args.search or args.state or args.unitids):
        institutions_csv = project_root / 'config' / 'institutions.csv'
        resolved, unmatched = resolve_fixed_list(None, institutions_csv, hd_dict)
        if not resolved.empty:
            unitids.update(int(u) for u in resolved['UNITID'].dropna())
            console.print(f'[green]added {len(resolved)} from config/institutions.csv[/]')
        if unmatched:
            console.print(f'[yellow]institutions.csv UNITIDs not in latest HD: {unmatched}[/]')

    # ── Label for filename ──
    if args.state and not args.search and not args.unitids:
        label = args.state.upper()
        if args.control:
            label += f'_ctrl{args.control}'
        if args.iclevel:
            label += f'_lvl{args.iclevel}'
    elif args.search and not args.state and not args.unitids:
        label = 'search_' + ''.join(c if c.isalnum() else '_' for c in args.search)[:30]
    else:
        label = 'custom'

    return sorted(unitids), label


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main(argv: Optional[List[str]] = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)
    verbose = bool(args.verbose)
    quiet = not verbose

    project_root = Path(__file__).resolve().parent.parent

    # ── Configs ──
    console.rule('[bold]config[/]')
    try:
        years_cfg = load_years_config(project_root / 'config' / 'years.yaml')
        cip_cfg = load_cip_filter_config(project_root / 'config' / 'cip_filter.yaml')
    except (FileNotFoundError, ValueError) as e:
        console.print(f'[red]config error:[/] {e}')
        return 2
    console.print(
        f'years: {years_cfg["years"]}  '
        f'CAGR: {years_cfg["cagr_start_year"]} → {years_cfg["cagr_end_year"]}'
    )
    cip_codes = [str(c).strip() for c in (args.cip or cip_cfg['cip_codes'])]
    award_levels = list(args.awlevel) if args.awlevel else list(cip_cfg['award_levels'])

    # Validate user-provided CIP format unless they explicitly want residuals.
    # Spec §Data Quality #6: codes should match ^\d{2}\.\d{4}$. If not, warn but
    # don't drop — the user might be intentionally matching bare '99' rollups
    # via --include-residual.
    bad_cips = [c for c in cip_codes if not CIP_6DIGIT_PATTERN.match(c)]
    if bad_cips:
        if args.include_residual:
            console.print(
                f'[yellow]note:[/] non-6-digit CIP codes {bad_cips} accepted '
                f'(--include-residual is on; intended for aggregate rollups).'
            )
        else:
            console.print(
                f'[yellow]warning:[/] CIP codes {bad_cips} do not match the '
                f'6-digit format (\\d{{2}}\\.\\d{{4}}). They will produce zero rows.'
            )
            console.print(
                '[yellow]hint:[/] pass --include-residual if you intended to match '
                "rollup codes like '99' or '01'."
            )

    console.print(
        f'CIP filter: {cip_codes or "[all]"}  '
        f'AWLEVEL filter: {award_levels or "[all]"}  '
        f'include-residual: {args.include_residual}'
    )

    # ── Load data ──
    console.rule('[bold]load[/]')
    try:
        loaded = load_all(
            years_cfg,
            raw_dir=project_root / 'data' / 'raw',
            dict_dir=project_root / 'data' / 'dictionary',
        )
    except FileNotFoundError as e:
        console.print(f'[red]data file missing:[/] {e}')
        console.print(
            '[yellow]hint:[/] check that every year listed in config/years.yaml '
            'has a matching hd{year} and c{year}_a file in data/raw/.'
        )
        return 3
    except ValueError as e:
        console.print(f'[red]data validation error:[/] {e}')
        return 3

    # ── Build authoritative metadata + CIP titles ──
    metadata = build_institution_metadata(loaded['hd'])
    cip_titles = build_cip_title_lookup(loaded['crosswalk'])

    # ── Resolve institutions ──
    console.rule('[bold]resolve institutions[/]')
    unitids, label = _resolve_unitids(args, loaded['hd'], project_root)
    if not unitids:
        console.print(
            '[red]No institutions selected. '
            'Provide --search, --state, --unitids, or populate config/institutions.csv.[/]'
        )
        return 1
    console.print(f'[bold]final selection: {len(unitids)} UNITIDs[/]  label={label!r}')

    # ── Join + filter ──
    console.rule('[bold]join + filter[/]')
    joined = join_all_years(
        loaded['ca'], metadata, cip_titles,
        selected_unitids=unitids, quiet=quiet,
    )
    filtered = apply_filters(
        joined,
        cip_codes=cip_codes, award_levels=award_levels,
        include_residual=args.include_residual,
        quiet=quiet,
    )
    warn_zero_completion_unitids(filtered, unitids, quiet=quiet)

    # ── Aggregations ──
    console.rule('[bold]aggregate[/]')
    cagr_df = compute_cagr_table(
        filtered,
        start_year=years_cfg['cagr_start_year'],
        end_year=years_cfg['cagr_end_year'],
    )
    selected_mv = compute_market_view(
        filtered,
        start_year=years_cfg['cagr_start_year'],
        end_year=years_cfg['cagr_end_year'],
    )
    national_mv = compute_national_market_view(
        loaded['ca'], cip_titles,
        cip_codes=cip_codes, award_levels=award_levels,
        include_residual=args.include_residual,
        start_year=years_cfg['cagr_start_year'],
        end_year=years_cfg['cagr_end_year'],
    )
    mv_wide = merge_selected_and_national_market_view(selected_mv, national_mv)
    mv_long = market_view_to_long(
        mv_wide,
        start_year=years_cfg['cagr_start_year'],
        end_year=years_cfg['cagr_end_year'],
    )
    console.print(
        f'CAGR rows: {len(cagr_df):,}   '
        f'Market View rows (long format, 4 per program): {len(mv_long):,}'
    )

    # ── Workbook ──
    console.rule('[bold]write workbook[/]')
    output_dir = Path(args.output) if args.output else project_root / 'output' / 'reports'

    # Institutions sheet only shows institutions with matching completions in
    # the filtered CIP × Award Level (across any year). Schools the user selected
    # that contributed no rows would be confusing in the output.
    unitids_with_data: set = set()
    for year_df in filtered.values():
        unitids_with_data.update(int(u) for u in year_df['UNITID'].dropna())
    institutions_view = metadata[metadata['UNITID'].isin(unitids_with_data)]
    n_no_data = len(unitids) - len(institutions_view)
    if n_no_data > 0:
        console.print(
            f'[dim]{n_no_data:,} of {len(unitids):,} selected institutions had no '
            f'matching completions and are omitted from the Institutions sheet.[/]'
        )
    out_path = build_workbook(
        output_dir=output_dir,
        label=label,
        institutions_df=institutions_view,
        completions_by_year=filtered,
        cagr_df=cagr_df,
        market_view_df=mv_long,
        varlist_df=loaded['varlist'],
        only_latest_completions=False,
    )

    console.rule('[bold green]done[/]')
    console.print(f'output: [cyan]{out_path}[/]')
    return 0


if __name__ == '__main__':
    sys.exit(main())
