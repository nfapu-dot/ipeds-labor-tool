"""
labor.loaders.projections — BLS Employment Projections (national, 10-year).

Loads the 'Occupational projections' table from the BLS workbook downloaded
from https://www.bls.gov/emp/. The currently-bundled vintage is
**2024–2034** (released August 2025).

Schema returned (long format, one row per detailed SOC):

    Column                       Type    Description
    ------                       ----    -----------
    SOCCode                      str     6-digit SOC formatted XX-XXXX
    SOCTitle                     str     BLS occupation title
    employment_base              float   Employment at base year (thousands)
    employment_target            float   Employment at target year (thousands)
    employment_change_numeric    float   Numeric change (thousands)
    employment_change_pct        float   Percent change AS DECIMAL (0.061 = 6.1%);
                                         see docs/LABOR_SOURCES_INSPECTION.md §3
    employment_distribution_base    float   % of all occupations, base year (decimal)
    employment_distribution_target  float   % of all occupations, target year (decimal)
    openings_annual_avg          float   Occupational openings per year (thousands)
    median_annual_wage_base      float   Median wage at base year (dollars)
    education_entry              str     Typical entry education category
    work_experience              str     Typical work experience required
    on_the_job_training          str     Typical OJT category
    percent_self_employed_base   float   % self-employed at base year (decimal)
    base_year                    int     e.g., 2024
    target_year                  int     e.g., 2034
    vintage                      str     'BLS Projections 2024-2034'

Notes on the source format:
- Sheet 'Table 1.2' is the primary; the first row is a title (skip via skiprows=1).
- 'Occupation type' distinguishes 'Line item' (detailed) from 'Summary' (parent SOCs).
  Loader filters to 'Line item' only.
- Percent columns in the source are integers (e.g., 6.1 for 6.1%). Loader divides
  by 100 to normalize to decimal across all labor sources. EDD uses decimal natively;
  consistency across sources prevents silent 100x errors downstream.
- Employment is in thousands in the source; loader preserves thousands. Multiply by
  1000 downstream if absolute counts are needed.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

import pandas as pd

DEFAULT_SHEET = 'Table 1.2'

# Source-column → canonical-column rename. Source column names are very long;
# this also drops the [footnote] suffixes.
_RENAMES = {
    '2024 National Employment Matrix code': 'SOCCode',
    '2024 National Employment Matrix title': 'SOCTitle',
    'Employment, 2024': 'employment_base',
    'Employment, 2034': 'employment_target',
    'Employment change, numeric, 2024–34': 'employment_change_numeric',
    'Employment change, percent, 2024–34': 'employment_change_pct',
    'Employment distribution, percent, 2024': 'employment_distribution_base',
    'Employment distribution, percent, 2034': 'employment_distribution_target',
    'Occupational openings, 2024–34 annual average': 'openings_annual_avg',
    'Median annual wage, dollars, 2024[1]': 'median_annual_wage_base',
    'Typical education needed for entry': 'education_entry',
    'Work experience in a related occupation': 'work_experience',
    'Typical on-the-job training needed to attain competency in the occupation': 'on_the_job_training',
    'Percent self employed, 2024': 'percent_self_employed_base',
}

# Numeric columns that need string→float coercion + percent normalization.
_PCT_COLS = (
    'employment_change_pct',
    'employment_distribution_base',
    'employment_distribution_target',
    'percent_self_employed_base',
)

_FLOAT_COLS = (
    'employment_base', 'employment_target', 'employment_change_numeric',
    'employment_change_pct', 'employment_distribution_base',
    'employment_distribution_target', 'percent_self_employed_base',
    'openings_annual_avg', 'median_annual_wage_base',
)


def _coerce_float(value: object) -> float:
    if isinstance(value, str):
        s = value.strip().replace(',', '')
        if not s or s in ('—', '-'):
            return float('nan')
        try:
            return float(s)
        except ValueError:
            return float('nan')
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return float('nan')


def _detect_year_range(path: Path) -> tuple[int, int]:
    """Pull base/target years from the source's column headers."""
    # Read just the column row to inspect.
    df = pd.read_excel(path, sheet_name=DEFAULT_SHEET, dtype=str, skiprows=1, nrows=0)
    cols = ' '.join(str(c) for c in df.columns)
    # Look for 'Employment, YYYY' and 'YYYY–YY' patterns.
    base_match = re.search(r'Employment,\s*(\d{4})', cols)
    span_match = re.search(r'(\d{4})\s*[–-]\s*(\d{2,4})', cols)
    base_year = int(base_match.group(1)) if base_match else 2024
    if span_match:
        b = int(span_match.group(1))
        t = span_match.group(2)
        target_year = int(t) if len(t) == 4 else (b // 100) * 100 + int(t)
    else:
        target_year = base_year + 10
    return base_year, target_year


def load_projections(
    path: Optional[Path] = None,
    *,
    sheet: str = DEFAULT_SHEET,
    vintage: Optional[str] = None,
) -> pd.DataFrame:
    """
    Load BLS national projections from the supplied workbook.

    Returns a long-format DataFrame with one row per detailed (Line item) SOC.
    For the 2024-2034 vintage that's typically 832 rows.
    """
    if path is None:
        raise ValueError('path is required')

    base_year, target_year = _detect_year_range(path)

    df = pd.read_excel(path, sheet_name=sheet, dtype=str, skiprows=1)
    detailed = df[df.get('Occupation type') == 'Line item'].copy()

    # Rename only columns that exist (defensive in case BLS adds/removes columns).
    rename_map = {k: v for k, v in _RENAMES.items() if k in detailed.columns}
    detailed = detailed.rename(columns=rename_map)

    # Coerce numerics.
    for col in _FLOAT_COLS:
        if col in detailed.columns:
            detailed[col] = detailed[col].map(_coerce_float)

    # Normalize percent columns to decimal so the labor sources match EDD's
    # native convention. See docs/LABOR_SOURCES_INSPECTION.md §3.
    for col in _PCT_COLS:
        if col in detailed.columns:
            detailed[col] = detailed[col] / 100.0

    detailed['base_year'] = base_year
    detailed['target_year'] = target_year
    detailed['vintage'] = vintage or f'BLS Projections {base_year}-{target_year}'

    canonical_cols = [
        'SOCCode', 'SOCTitle',
        'employment_base', 'employment_target',
        'employment_change_numeric', 'employment_change_pct',
        'employment_distribution_base', 'employment_distribution_target',
        'openings_annual_avg', 'median_annual_wage_base',
        'education_entry', 'work_experience', 'on_the_job_training',
        'percent_self_employed_base',
        'base_year', 'target_year', 'vintage',
    ]
    keep = [c for c in canonical_cols if c in detailed.columns]
    return detailed[keep].reset_index(drop=True)
