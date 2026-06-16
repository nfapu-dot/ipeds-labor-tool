"""
main_v2.py — CLI entry point for the IPEDS + Labor combined report (v2).

This is a NEW, SEPARATE entry point. v1's `src/main.py` is unchanged and
continues to work — run it for the standard IPEDS completions report.

Pipeline:
  v1: loader → joiner → aggregator → reporter
  v2: core.crosswalk + labor.loaders → labor.aggregator → reports.combine → reports.writer

Modes (combine like v1):
  --search "<name>"          Interactive name search
  --state <ABBR>             All institutions in state (single state for v2;
                             primary geography for the labor view)
  --control 1|2|3            Sub-filter for --state
  --iclevel 1|2|3            Sub-filter for --state
  --unitids <id> [<id> ...]  Fixed list (overrides config/institutions.csv)

Runtime overrides:
  --cip <code> [<code> ...]  Override cip_filter.yaml cip_codes
  --awlevel <n> [<n> ...]    Override cip_filter.yaml award_levels
  --include-residual         Include CIP 99 aggregate-rollup rows

Labor controls:
  --labor-mode flat|median|employment_weighted   Aggregation method (default: employment_weighted)

Output:
  --output <dir>             Override output/reports/
  --verbose                  Verbose logging

Filename: IPEDS_Completions_COMBINED_{label}_{YYYYMMDD_HHMMSS}.xlsx
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import List, Optional, Tuple

from rich.console import Console

# Ensure src/ is on sys.path so v1 modules (flat layout) resolve when this
# file is run as `python3 src/main_v2.py`.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from labor import aggregator as labor_aggregator  # noqa: E402
from reports import combine as reports_combine  # noqa: E402
from reports import writer as reports_writer  # noqa: E402
from resolver import (  # type: ignore[import-not-found]  # noqa: E402
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


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog='ipeds-tool-v2',
        description=(
            'IPEDS Completions + Labor Market combined report (v2). '
            'Adds BLS OEWS wages, BLS Projections, CA EDD, and Census ACS '
            'context to v1\'s completions analysis.'
        ),
    )
    # Same selection flags as v1
    p.add_argument('--search', metavar='NAME')
    p.add_argument('--state', metavar='ABBR')
    p.add_argument('--control', type=int, choices=[1, 2, 3])
    p.add_argument('--iclevel', type=int, choices=[1, 2, 3])
    p.add_argument('--unitids', nargs='+', type=int, metavar='UNITID')

    # Filters
    p.add_argument('--cip', nargs='+', metavar='CODE')
    p.add_argument('--awlevel', nargs='+', type=int, metavar='N')
    p.add_argument('--include-residual', action='store_true')

    # v2-only
    p.add_argument(
        '--labor-mode',
        choices=list(labor_aggregator.ALL_MODES),
        default=labor_aggregator.MODE_WEIGHTED,
        help='Labor aggregation method (default: employment_weighted).',
    )

    # Output
    p.add_argument('--output', metavar='DIR')
    p.add_argument('--verbose', action='store_true')
    return p


def _resolve_unitids(
    args: argparse.Namespace, hd_dict: dict, project_root: Path,
) -> Tuple[List[int], str, Optional[str]]:
    """
    Same selection resolution as v1's main.py, but also returns the primary
    state code if --state was used (for the labor view).
    """
    unitids: set = set()
    primary_state: Optional[str] = None

    if args.search:
        matches = search_by_name(args.search, hd_dict, top_n=25)
        print_institution_table(
            matches, title=f'Matches for "{args.search}"', show_score=True,
        )
        picks = interactive_pick(matches)
        if picks:
            unitids.update(picks)
            console.print(f'[green]added {len(picks)} from search[/]')

    if args.state:
        primary_state = args.state.upper()
        state_df = filter_by_state(
            args.state, hd_dict,
            control=args.control, iclevel=args.iclevel,
        )
        n = len(state_df)
        scope = primary_state
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

    if args.unitids:
        resolved, unmatched = resolve_fixed_list(args.unitids, None, hd_dict)
        if not resolved.empty:
            unitids.update(int(u) for u in resolved['UNITID'].dropna())
        if unmatched:
            console.print(f'[yellow]--unitids unmatched in latest HD: {unmatched}[/]')
            closure_df = cross_reference_closures(unmatched, hd_dict)
            if not closure_df.empty:
                print_closure_table(closure_df)
                unitids.update(int(u) for u in closure_df['UNITID'])

    if not unitids and not (args.search or args.state or args.unitids):
        institutions_csv = project_root / 'config' / 'institutions.csv'
        resolved, unmatched = resolve_fixed_list(None, institutions_csv, hd_dict)
        if not resolved.empty:
            unitids.update(int(u) for u in resolved['UNITID'].dropna())
        if unmatched:
            console.print(f'[yellow]institutions.csv UNITIDs not in latest HD: {unmatched}[/]')

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

    return sorted(unitids), label, primary_state


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)
    verbose = bool(args.verbose)
    quiet = not verbose
    project_root = Path(__file__).resolve().parent.parent

    # Resolve institutions — same flow as v1.
    from loader import load_all, load_years_config  # type: ignore[import-not-found]  # noqa: E402

    console.rule('[bold]load[/]')
    years_cfg = load_years_config(project_root / 'config' / 'years.yaml')
    loaded = load_all(
        years_cfg,
        raw_dir=project_root / 'data' / 'raw',
        dict_dir=project_root / 'data' / 'dictionary',
    )

    console.rule('[bold]resolve institutions[/]')
    unitids, label, primary_state = _resolve_unitids(args, loaded['hd'], project_root)
    if not unitids:
        console.print(
            '[red]No institutions selected. '
            'Provide --search, --state, --unitids, or populate config/institutions.csv.[/]'
        )
        return 1
    console.print(f'[bold]final selection: {len(unitids)} UNITIDs[/]  label={label!r}')

    # Build the combined dataset.
    console.rule('[bold]combine v1 + v2[/]')
    cip_codes = [str(c).strip() for c in (args.cip or [])] or None
    award_levels = list(args.awlevel) if args.awlevel else None
    states = [primary_state] if primary_state else None

    dataset = reports_combine.build_combined_dataset(
        unitids=unitids,
        cip_codes=cip_codes,
        award_levels=award_levels,
        states=states,
        include_residual=args.include_residual,
        aggregation_mode=args.labor_mode,
        project_root=project_root,
        label=label,
        quiet=quiet,
    )

    console.print(
        f'CAGR rows: {len(dataset["completions"]["cagr_df"]):,}   '
        f'Combined view rows: {len(dataset["combined_market_view"]):,}   '
        f'Labor (per CIP×state): {len(dataset["labor"]["aggregated"]):,}'
    )

    # Write workbook.
    console.rule('[bold]write workbook[/]')
    output_dir = Path(args.output) if args.output else project_root / 'output' / 'reports'
    out_path = reports_writer.write_combined_workbook(dataset, output_dir)
    console.rule('[bold green]done[/]')
    console.print(f'output: [cyan]{out_path}[/]')
    return 0


if __name__ == '__main__':
    sys.exit(main())
