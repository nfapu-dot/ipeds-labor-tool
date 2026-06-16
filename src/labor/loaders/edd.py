"""
labor.loaders.edd — CA EDD Long-term Occupational Projections.

Loads the statewide long-term projection workbook published by CA EDD's
Labor Market Information Division. The currently-bundled vintage is
**2023–2033** (published July 2025).

Schema returned (long format, one row per detailed SOC):

    Column                    Type    Description
    ------                    ----    -----------
    SOCCode                   str     6-digit SOC formatted XX-XXXX
    SOCTitle                  str     EDD occupation title
    employment_base           float   CA employment at base year
    employment_target         float   CA projected employment at target year
    employment_change_numeric float   Numeric change over the WHOLE projection window
    employment_change_pct     float   Percent change AS DECIMAL (native format)
    exits_total_period        float   Projected exits, TOTAL over the projection window
    transfers_total_period    float   Projected transfers, TOTAL over the projection window
    openings_total_period     float   Total openings over the projection window
                                       (= exits_total_period + transfers_total_period + employment_change_numeric)
    openings_annual_avg       float   DERIVED: openings_total_period / (target_year - base_year)
                                       Makes EDD comparable to BLS Projections, which
                                       reports openings_annual_avg natively.
    median_hourly_wage        float   CA hourly median (NaN where suppressed)
    median_annual_wage        float   CA annual median (NaN where suppressed)
    education_entry           str     Entry-level education category
    work_experience           str     Work-experience category
    on_the_job_training       str     OJT category
    base_year                 int     e.g., 2023
    target_year               int     e.g., 2033
    vintage                   str     'CA EDD Long-term 2023-2033'

Source format quirks (see docs/LABOR_SOURCES_INSPECTION.md §3):
- Sheet 'Occupational' has 3 metadata rows above the header row; skiprows=3.
- Last data row is the sentinel string 'End of worksheet.' in the SOC Level
  column. Loader drops it by filtering on missing SOC Code.
- Column names contain literal newlines (e.g., 'Median Annual Wages\\n[10]').
  Loader normalizes newlines to spaces before applying the rename map.
- SOC Level is 1=total / 2=major / 3=minor / 4=detailed.
- Percent-change is already a decimal in the source (matches our cross-source
  convention).
- EDD's "Total Job Openings" is a TEN-YEAR TOTAL, not an annual average —
  confirmed by triangulating against BLS Projections' annual avg × 10
  (within rounding). The loader exposes both `openings_total_period` (raw)
  and `openings_annual_avg` (derived) so downstream code can pick.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

import pandas as pd

DEFAULT_SHEET = 'Occupational'

# Source-column → canonical. Keys here are post-newline-normalization.
_RENAMES = {
    'SOC Code[2]': 'SOCCode',
    'Occupational Title[3]': 'SOCTitle',
    'Base Year Employment Estimate 2023[4][5]': 'employment_base',
    'Projected Year Employment Estimate 2033': 'employment_target',
    'Numeric Change 2023-2033[6]': 'employment_change_numeric',
    'Percent-age Change 2023-2033': 'employment_change_pct',
    'Exits [7]': 'exits_total_period',
    'Transfers [8]': 'transfers_total_period',
    'Total Job Openings [9]': 'openings_total_period',
    'Median Hourly Wages [10]': 'median_hourly_wage',
    'Median Annual Wages [10]': 'median_annual_wage',
    'Entry Level Education [11][12]': 'education_entry',
    'Work Experience [11][12]': 'work_experience',
    'On-the-Job Training [11][12]': 'on_the_job_training',
}

_FLOAT_COLS = (
    'employment_base', 'employment_target', 'employment_change_numeric',
    'employment_change_pct', 'exits_total_period', 'transfers_total_period',
    'openings_total_period', 'median_hourly_wage', 'median_annual_wage',
)


def _coerce_float(value: object) -> float:
    if isinstance(value, str):
        s = value.strip().replace(',', '').replace('$', '')
        if not s or s in ('—', '-', 'N/A', 'NA'):
            return float('nan')
        try:
            return float(s)
        except ValueError:
            return float('nan')
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return float('nan')


def _normalize_col(name: object) -> str:
    """Collapse newlines and surrounding whitespace in a column header."""
    s = str(name).replace('\n', ' ')
    return re.sub(r'\s+', ' ', s).strip()


def _detect_year_range(df: pd.DataFrame) -> tuple[int, int]:
    """Pull base / target years from a normalized column list."""
    cols = ' '.join(df.columns)
    base_match = re.search(r'Base Year[^,]*?(\d{4})', cols)
    target_match = re.search(r'Projected Year[^,]*?(\d{4})', cols)
    base = int(base_match.group(1)) if base_match else 2023
    target = int(target_match.group(1)) if target_match else base + 10
    return base, target


def load_edd(
    path: Optional[Path] = None,
    *,
    sheet: str = DEFAULT_SHEET,
    vintage: Optional[str] = None,
) -> pd.DataFrame:
    """
    Load CA EDD long-term projections from the supplied workbook.

    Returns long-format detail-only rows. For the 2023-2033 vintage that's
    ~676 rows.
    """
    if path is None:
        raise ValueError('path is required')

    df = pd.read_excel(path, sheet_name=sheet, dtype=str, skiprows=3)
    df.columns = [_normalize_col(c) for c in df.columns]

    base_year, target_year = _detect_year_range(df)

    soc_level_col = next(
        (c for c in df.columns if c.lower().startswith('soc level')),
        None,
    )
    if soc_level_col is None:
        raise ValueError(
            f'EDD: could not find SOC Level column. cols: {list(df.columns)}'
        )

    detailed = df[df[soc_level_col] == '4'].copy()

    rename_map = {k: v for k, v in _RENAMES.items() if k in detailed.columns}
    detailed = detailed.rename(columns=rename_map)

    # Drop trailing 'End of worksheet.' sentinel — it has no SOC code.
    if 'SOCCode' in detailed.columns:
        detailed = detailed[detailed['SOCCode'].notna()]
        detailed = detailed[detailed['SOCCode'].str.match(r'^\d{2}-\d{4}$', na=False)]

    for col in _FLOAT_COLS:
        if col in detailed.columns:
            detailed[col] = detailed[col].map(_coerce_float)

    detailed['base_year'] = base_year
    detailed['target_year'] = target_year
    detailed['vintage'] = vintage or f'CA EDD Long-term {base_year}-{target_year}'

    # Derived: annual-average openings for cross-source comparison with BLS Projections.
    period_years = target_year - base_year
    if 'openings_total_period' in detailed.columns and period_years > 0:
        detailed['openings_annual_avg'] = detailed['openings_total_period'] / period_years

    canonical_cols = [
        'SOCCode', 'SOCTitle',
        'employment_base', 'employment_target',
        'employment_change_numeric', 'employment_change_pct',
        'exits_total_period', 'transfers_total_period',
        'openings_total_period', 'openings_annual_avg',
        'median_hourly_wage', 'median_annual_wage',
        'education_entry', 'work_experience', 'on_the_job_training',
        'base_year', 'target_year', 'vintage',
    ]
    keep = [c for c in canonical_cols if c in detailed.columns]
    return detailed[keep].reset_index(drop=True)
