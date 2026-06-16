"""
labor.loaders.census — U.S. Census Bureau Population API (ACS).

API-driven, not file-driven. Pulls state-level demographic context to enrich
the labor view: total population, 18-24 cohort, educational attainment.

Schema returned (long format, one row per state):

    Column                Type    Description
    ------                ----    -----------
    state_fips            str     2-digit FIPS code (e.g., '06' for CA)
    state_name            str     'California', 'New York', etc.
    state_abbr            str     2-letter postal abbreviation
    pop_total             int     Total population
    pop_18_24             int     Population aged 18-24 (M + W summed)
    pop_25plus            int     Population 25+ (denominator for attainment %)
    bachelors_or_higher   int     Population 25+ with bachelor's degree or higher
    bachelors_or_higher_pct  float   Share of 25+ with bachelor's+ (decimal)
    survey                str     'acs5'
    year                  int     Survey vintage year
    vintage               str     e.g., 'Census ACS 5-year 2023'

Caching: every API call's JSON response is saved under
`data/raw_labor/census/<query_hash>.json` so subsequent runs of the same
query do not re-hit the API. Delete the file to force a refresh.

API key: read from .env at the project root (CENSUS_API_KEY=...). The .env
file is gitignored — never committed.
"""
from __future__ import annotations

import hashlib
import json
import os
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Optional

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_CACHE_DIR = PROJECT_ROOT / 'data' / 'raw_labor' / 'census'
DEFAULT_ENV_FILE = PROJECT_ROOT / '.env'
DEFAULT_YEAR = 2023
DEFAULT_SURVEY = 'acs5'

# FIPS → postal abbreviation. Includes 50 states + DC + PR.
_FIPS_TO_ABBR = {
    '01': 'AL', '02': 'AK', '04': 'AZ', '05': 'AR', '06': 'CA', '08': 'CO',
    '09': 'CT', '10': 'DE', '11': 'DC', '12': 'FL', '13': 'GA', '15': 'HI',
    '16': 'ID', '17': 'IL', '18': 'IN', '19': 'IA', '20': 'KS', '21': 'KY',
    '22': 'LA', '23': 'ME', '24': 'MD', '25': 'MA', '26': 'MI', '27': 'MN',
    '28': 'MS', '29': 'MO', '30': 'MT', '31': 'NE', '32': 'NV', '33': 'NH',
    '34': 'NJ', '35': 'NM', '36': 'NY', '37': 'NC', '38': 'ND', '39': 'OH',
    '40': 'OK', '41': 'OR', '42': 'PA', '44': 'RI', '45': 'SC', '46': 'SD',
    '47': 'TN', '48': 'TX', '49': 'UT', '50': 'VT', '51': 'VA', '53': 'WA',
    '54': 'WV', '55': 'WI', '56': 'WY', '72': 'PR',
}

# ACS variable IDs (verified against api.census.gov/data/<year>/acs/acs5/variables.html):
# - B01001_001E: total population (all ages, all sexes)
# - 18-24: M = B01001_007E..B01001_010E; W = B01001_031E..B01001_034E
#   Subgroups: 18-19, 20, 21, 22-24 (last is single bracket "22 to 24 years")
# - B15003_001E: total 25+ (universe for attainment)
# - Bachelor's or higher: B15003_022E (Bachelor's), B15003_023E (Master's),
#   B15003_024E (Professional degree), B15003_025E (Doctorate)
_VARS_TOTAL_POP = ['B01001_001E']
_VARS_18_24_M = ['B01001_007E', 'B01001_008E', 'B01001_009E', 'B01001_010E']
_VARS_18_24_W = ['B01001_031E', 'B01001_032E', 'B01001_033E', 'B01001_034E']
_VARS_25PLUS_TOTAL = ['B15003_001E']
_VARS_BACHELORS_PLUS = ['B15003_022E', 'B15003_023E', 'B15003_024E', 'B15003_025E']

_ALL_VARS = (
    _VARS_TOTAL_POP
    + _VARS_18_24_M
    + _VARS_18_24_W
    + _VARS_25PLUS_TOTAL
    + _VARS_BACHELORS_PLUS
)


def _read_api_key(env_file: Path = DEFAULT_ENV_FILE) -> str:
    """Read CENSUS_API_KEY from .env, raising a clear error if missing."""
    key = os.environ.get('CENSUS_API_KEY')
    if key:
        return key
    if not env_file.exists():
        raise RuntimeError(
            f'CENSUS_API_KEY not set and {env_file} not found. '
            f'Copy .env.example to .env and fill in your API key.'
        )
    for line in env_file.read_text().splitlines():
        if line.strip().startswith('CENSUS_API_KEY='):
            value = line.split('=', 1)[1].strip()
            if value:
                return value
    raise RuntimeError(
        f'CENSUS_API_KEY is blank in {env_file}. '
        f'Get a free key at https://api.census.gov/data/key_signup.html'
    )


def _query_url(year: int, survey: str, variables: list[str], geo: str, key: Optional[str] = None) -> str:
    params = {
        'get': 'NAME,' + ','.join(variables),
        'for': geo,
    }
    if key:
        params['key'] = key
    return f'https://api.census.gov/data/{year}/acs/{survey}?{urllib.parse.urlencode(params)}'


def _cache_path(url: str, cache_dir: Path) -> Path:
    """Stable cache filename derived from URL (excluding the key)."""
    # Strip the key from the URL before hashing, so the cache survives key rotation.
    no_key = url.split('&key=')[0]
    digest = hashlib.sha256(no_key.encode()).hexdigest()[:16]
    return cache_dir / f'{digest}.json'


def _fetch(url: str, cache_dir: Path) -> list[list[str]]:
    """Fetch a Census API URL, using on-disk cache when available."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_file = _cache_path(url, cache_dir)
    if cache_file.exists():
        return json.loads(cache_file.read_text())
    with urllib.request.urlopen(url, timeout=30) as resp:
        data = json.loads(resp.read())
    cache_file.write_text(json.dumps(data))
    return data


def load_census(
    *,
    year: int = DEFAULT_YEAR,
    survey: str = DEFAULT_SURVEY,
    states: Optional[list[str]] = None,
    cache_dir: Path = DEFAULT_CACHE_DIR,
    env_file: Path = DEFAULT_ENV_FILE,
) -> pd.DataFrame:
    """
    Load Census ACS demographic data for all states (or a filtered subset).

    Args:
        year: ACS vintage year (default 2023).
        survey: 'acs5' (default; 5-year ACS) or 'acs1' (1-year, larger geos only).
        states: Optional list of 2-letter state codes to filter (e.g., ['CA', 'NY']).
                If None, returns all 50 states + DC + PR.
        cache_dir: Where to cache API responses.
        env_file: Where to find CENSUS_API_KEY.

    Returns the schema documented in the module docstring.
    """
    # Serve from the on-disk cache first. The cache filename excludes the API
    # key (see _cache_path), so a committed cache hit needs no key at all —
    # only a cache MISS triggers a live call that requires _read_api_key().
    # This lets the app run where the key is unset but the default-query cache
    # ships with the repo (e.g., Streamlit Community Cloud).
    cache_file = _cache_path(
        _query_url(year=year, survey=survey, variables=_ALL_VARS, geo='state:*'),
        cache_dir,
    )
    if cache_file.exists():
        data = json.loads(cache_file.read_text())
    else:
        key = _read_api_key(env_file)
        url = _query_url(
            year=year, survey=survey,
            variables=_ALL_VARS, geo='state:*', key=key,
        )
        data = _fetch(url, cache_dir)

    headers, *rows = data
    df = pd.DataFrame(rows, columns=headers)

    # Map FIPS → abbr / name. The API returns 'NAME' = state name and 'state' = FIPS.
    df['state_fips'] = df['state']
    df['state_name'] = df['NAME']
    df['state_abbr'] = df['state_fips'].map(_FIPS_TO_ABBR).fillna('')

    # Cast counts to int (missing/negative codes in ACS appear as small negatives).
    for col in _ALL_VARS:
        df[col] = pd.to_numeric(df[col], errors='coerce').fillna(0).astype(int)
        # Census uses negative codes for "not available"; treat as 0 for sums.
        df.loc[df[col] < 0, col] = 0

    df['pop_total'] = df[_VARS_TOTAL_POP].sum(axis=1)
    df['pop_18_24'] = df[_VARS_18_24_M + _VARS_18_24_W].sum(axis=1)
    df['pop_25plus'] = df[_VARS_25PLUS_TOTAL].sum(axis=1)
    df['bachelors_or_higher'] = df[_VARS_BACHELORS_PLUS].sum(axis=1)
    df['bachelors_or_higher_pct'] = df.apply(
        lambda r: (r['bachelors_or_higher'] / r['pop_25plus']) if r['pop_25plus'] else float('nan'),
        axis=1,
    )

    df['survey'] = survey
    df['year'] = year
    df['vintage'] = f'Census ACS 5-year {year}' if survey == 'acs5' else f'Census ACS 1-year {year}'

    keep = [
        'state_fips', 'state_name', 'state_abbr',
        'pop_total', 'pop_18_24', 'pop_25plus',
        'bachelors_or_higher', 'bachelors_or_higher_pct',
        'survey', 'year', 'vintage',
    ]
    out = df[keep].copy()

    if states:
        wanted = {s.upper() for s in states}
        out = out[out['state_abbr'].isin(wanted)]

    # Drop territories with no postal abbr if user didn't ask for them
    out = out[out['state_abbr'] != '']
    return out.reset_index(drop=True).sort_values('state_abbr').reset_index(drop=True)
