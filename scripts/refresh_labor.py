"""
scripts/refresh_labor.py — print-only annual-refresh checklist for labor data.

This script does NOT download anything. The labor data sources (BLS OEWS, BLS
Projections, CA EDD, Census API) require human-driven choices at fetch time:
which release year is current, which state files to pull, which projection
window to use. Automating that would silently break aggregation when a source
changes its naming convention — which they all do, regularly.

What it does:
1. Print each labor source: URL, expected filename pattern, target subdir.
2. Check whether the expected file already exists locally.
3. Show the date the user last marked the source as fetched.

What you do:
1. Visit each URL and download the current-vintage file.
2. Save it into the printed target subdirectory with a filename matching
   the printed pattern.
3. Run `python3 scripts/refresh_labor.py --mark-fetched <source>` once
   verified.
4. When all sources are marked current, you're ready for Phase 3 (loaders).

Usage:
    python3 scripts/refresh_labor.py                   # print checklist
    python3 scripts/refresh_labor.py --mark-fetched oews
    python3 scripts/refresh_labor.py --reset oews      # forget a fetch
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

PROJECT_ROOT = Path(__file__).resolve().parent.parent
RAW_LABOR = PROJECT_ROOT / 'data' / 'raw_labor'
REFRESH_LOG = RAW_LABOR / '.refresh_log.json'


@dataclass
class LaborSource:
    key: str
    name: str
    subdir: str
    url: str
    filename_hint: str
    cadence: str
    notes: str

    @property
    def target_dir(self) -> Path:
        return RAW_LABOR / self.subdir


SOURCES: List[LaborSource] = [
    LaborSource(
        key='oews',
        name='BLS OEWS — Occupational Employment and Wage Statistics',
        subdir='oews',
        url='https://www.bls.gov/oes/tables.htm',
        filename_hint='national_<YYYY>.xlsx + state_<YYYY>.xlsx (one combined or 50 per-state)',
        cadence='Annual; May reference period, released the following spring',
        notes=(
            'Need BOTH national and state files. National provides tot_emp used '
            'as weights; state provides geographic wage variation. Verify the '
            'release year — confirm at https://www.bls.gov/oes/release.htm. '
            'No API key required.'
        ),
    ),
    LaborSource(
        key='projections',
        name='BLS Employment Projections (national, 10-year)',
        subdir='projections',
        url='https://www.bls.gov/emp/tables/occupational-projections-and-characteristics.htm',
        filename_hint='occupation_<PROJ_WINDOW>.xlsx (e.g., occupation_2023-2033.xlsx)',
        cadence='Biennial; current projection window must be verified at fetch',
        notes=(
            'Look for the Occupational Projections table. National only — CA '
            'projections come from EDD. No API key.'
        ),
    ),
    LaborSource(
        key='edd',
        name='CA EDD — Long-term Occupational Projections',
        subdir='edd',
        url='https://labormarketinfo.edd.ca.gov/data/employment-projections.html',
        filename_hint='ca_longterm_<WINDOW>.xlsx',
        cadence='Biennial (long-term ~10yr)',
        notes=(
            'Uses SOC codes. Cross-check SOC vintage against BLS — EDD '
            'occasionally lags by one SOC revision. Short-term (2yr) is '
            'optional; long-term is required for v2.'
        ),
    ),
    LaborSource(
        key='census',
        name='U.S. Census Bureau — Population API (ACS)',
        subdir='census',
        url='https://api.census.gov/data/key_signup.html',
        filename_hint='(no file — API queried at runtime; key in .env)',
        cadence='ACS 1-year and 5-year released annually',
        notes=(
            'No file to download. Get a free API key at the URL above, store '
            'it in .env as CENSUS_API_KEY (see .env.example). The labor '
            'loader queries the API live and caches responses under '
            'data/raw_labor/census/.'
        ),
    ),
]


# ---------------------------------------------------------------------------
# Refresh log I/O
# ---------------------------------------------------------------------------

def _load_log() -> dict:
    if not REFRESH_LOG.exists():
        return {'sources': {}}
    try:
        return json.loads(REFRESH_LOG.read_text())
    except json.JSONDecodeError:
        print(f'WARNING: {REFRESH_LOG} is malformed; treating as empty', file=sys.stderr)
        return {'sources': {}}


def _save_log(log: dict) -> None:
    REFRESH_LOG.write_text(json.dumps(log, indent=2, sort_keys=True) + '\n')


def _today() -> str:
    return datetime.now(timezone.utc).date().isoformat()


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

def _local_status(src: LaborSource) -> str:
    """One-line summary of what's actually on disk for this source."""
    d = src.target_dir
    if not d.exists():
        return 'directory missing'
    files = sorted(
        f for f in d.iterdir()
        if f.is_file() and not f.name.startswith('.')
    )
    if not files:
        return 'no files yet'
    sizes = sum(f.stat().st_size for f in files)
    return f'{len(files)} file(s), {sizes / 1024 / 1024:.1f} MB total'


def _fetched_status(src: LaborSource, log: dict) -> str:
    entry = log.get('sources', {}).get(src.key)
    if not entry:
        return 'never marked fetched'
    return f"last marked fetched: {entry.get('date', '?')}"


def print_checklist() -> None:
    log = _load_log()
    print('=' * 78)
    print('  Labor data refresh checklist')
    print('=' * 78)
    print()
    print('No source is downloaded automatically. Visit each URL, save the file')
    print('to the indicated target directory, then run:')
    print('  python3 scripts/refresh_labor.py --mark-fetched <key>')
    print()

    for i, src in enumerate(SOURCES, 1):
        print(f'[{i}] {src.name}    (key: {src.key})')
        print(f'    URL:           {src.url}')
        print(f'    Target dir:    {src.target_dir.relative_to(PROJECT_ROOT)}/')
        print(f'    Filename hint: {src.filename_hint}')
        print(f'    Cadence:       {src.cadence}')
        print(f'    Local files:   {_local_status(src)}')
        print(f'    Status:        {_fetched_status(src, log)}')
        for line in _wrap(src.notes, indent='        '):
            print(line)
        print()

    # Census API key check — special since there's no file.
    env_path = PROJECT_ROOT / '.env'
    if env_path.exists():
        has_key = any(
            line.startswith('CENSUS_API_KEY=') and line.strip() != 'CENSUS_API_KEY='
            for line in env_path.read_text().splitlines()
        )
        print(f'    .env present:   {env_path.relative_to(PROJECT_ROOT)} '
              f'({"CENSUS_API_KEY appears set" if has_key else "CENSUS_API_KEY blank"})')
    else:
        print(f'    .env missing:   copy .env.example → .env and fill in CENSUS_API_KEY')
    print()
    print('=' * 78)


def _wrap(text: str, indent: str = '', width: int = 70) -> List[str]:
    """Simple word-wrap for the notes field."""
    words = text.split()
    lines: List[str] = []
    cur = indent
    for w in words:
        if len(cur) + 1 + len(w) > width and cur != indent:
            lines.append(cur)
            cur = indent + w
        else:
            cur = (cur + ' ' + w) if cur != indent else (indent + w)
    if cur != indent:
        lines.append(cur)
    return lines


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def cmd_mark_fetched(key: str, when: Optional[str] = None) -> int:
    if key not in {s.key for s in SOURCES}:
        print(f'unknown source key: {key!r}', file=sys.stderr)
        print(f'valid keys: {[s.key for s in SOURCES]}', file=sys.stderr)
        return 2
    log = _load_log()
    log.setdefault('sources', {})[key] = {'date': when or _today()}
    _save_log(log)
    print(f'marked {key} fetched on {log["sources"][key]["date"]}')
    return 0


def cmd_reset(key: str) -> int:
    log = _load_log()
    if key in log.get('sources', {}):
        del log['sources'][key]
        _save_log(log)
        print(f'cleared {key} from refresh log')
        return 0
    print(f'{key} was not in the refresh log; nothing to do')
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(prog='refresh_labor.py')
    g = p.add_mutually_exclusive_group()
    g.add_argument('--mark-fetched', metavar='KEY',
                   help='Record that this source has been freshly downloaded.')
    g.add_argument('--reset', metavar='KEY',
                   help='Clear this source from the refresh log.')
    p.add_argument('--date', metavar='YYYY-MM-DD',
                   help='Date to record (default: today, UTC).')
    args = p.parse_args(argv)

    if args.mark_fetched:
        return cmd_mark_fetched(args.mark_fetched, when=args.date)
    if args.reset:
        return cmd_reset(args.reset)
    print_checklist()
    return 0


if __name__ == '__main__':
    sys.exit(main())
