"""
labor.loaders.oews — BLS OEWS (Occupational Employment and Wage Statistics).

Loads the national and state workbooks released annually by BLS. Files come
from https://www.bls.gov/oes/tables.htm; both ship as .zip archives that
extract to a single .xlsx. The currently-bundled vintage is **May 2025**
(release date 2026-05-15).

Schema returned (long format, one row per SOC × area):

    Column            Type     Description
    --------          -----    -----------
    SOCCode           str      6-digit SOC code formatted XX-XXXX
    SOCTitle          str      BLS occupation title
    area_kind         str      'national' or 'state'
    PRIM_STATE        str      2-letter postal abbreviation; 'US' for national
    AREA_TITLE        str      Human-readable area name (e.g., 'California', 'U.S.')
    tot_emp           float    Total employment (NaN if suppressed)
    emp_prse          float    Relative standard error of tot_emp (%, NaN if suppressed)
    a_mean            float    Annual mean wage (NaN if suppressed or top-coded)
    a_median          float    Annual median wage (NaN if suppressed or top-coded)
    h_mean            float    Hourly mean wage (NaN if suppressed)
    h_median          float    Hourly median wage (NaN if suppressed)
    a_pct10, a_pct25, a_pct75, a_pct90   float  Annual wage percentiles
    h_pct10, h_pct25, h_pct75, h_pct90   float  Hourly wage percentiles
    suppression_flag  str      One of '**' (TOT_EMP not released), '*' (no estimate),
                               '#' (wage top-coded), or '' (clean cell). Joined-of any
                               flag encountered across columns for this row.
    vintage           str      'May 2025' (or whatever the source release tag was)

Suppression handling (per docs/LABOR_SOURCES_INSPECTION.md §1):
    - '**' marks TOT_EMP unreleased — tot_emp set to NaN.
    - '*'  marks any wage cell with no estimate — that wage column set to NaN.
    - '#'  marks top-coded wages (>= $115/hr or >= $239,200/yr) — that wage
           column set to NaN. The actual value is bounded but not knowable
           from the file; treating as missing is the safest default.
    The original flag is preserved in `suppression_flag` so downstream
    aggregator code can choose to report it.

This module never modifies the source files. Workbooks must already be
extracted from their .zip archives.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import pandas as pd

# Suppression flags used by BLS OEWS workbooks.
SUPPRESSION_FLAGS = ('**', '*', '#')

# Numeric columns that may contain suppression flags in the source file.
# These are read as strings, then coerced; flag values land in suppression_flag.
_NUMERIC_COLS = (
    'TOT_EMP', 'EMP_PRSE', 'JOBS_1000', 'LOC_QUOTIENT',
    'PCT_TOTAL', 'PCT_RPT', 'MEAN_PRSE',
    'H_MEAN', 'A_MEAN',
    'H_PCT10', 'H_PCT25', 'H_MEDIAN', 'H_PCT75', 'H_PCT90',
    'A_PCT10', 'A_PCT25', 'A_MEDIAN', 'A_PCT75', 'A_PCT90',
)

# Source-column → canonical-column rename map.
_RENAMES = {
    'OCC_CODE': 'SOCCode',
    'OCC_TITLE': 'SOCTitle',
    'TOT_EMP': 'tot_emp',
    'EMP_PRSE': 'emp_prse',
    'A_MEAN': 'a_mean',
    'A_MEDIAN': 'a_median',
    'H_MEAN': 'h_mean',
    'H_MEDIAN': 'h_median',
    'A_PCT10': 'a_pct10',
    'A_PCT25': 'a_pct25',
    'A_PCT75': 'a_pct75',
    'A_PCT90': 'a_pct90',
    'H_PCT10': 'h_pct10',
    'H_PCT25': 'h_pct25',
    'H_PCT75': 'h_pct75',
    'H_PCT90': 'h_pct90',
}


@dataclass(frozen=True)
class OEWSPaths:
    """Default file paths for a v2 install (relative to project root)."""
    national: Path
    state: Path

    @classmethod
    def from_dir(cls, raw_labor_dir: Path) -> 'OEWSPaths':
        return cls(
            national=raw_labor_dir / 'oews' / 'national_M2025_dl.xlsx',
            state=raw_labor_dir / 'oews' / 'state_M2025_dl.xlsx',
        )


def _coerce_numeric(value: object) -> tuple[float, str]:
    """
    Given one cell value, return (numeric, flag).

    Suppression-flag strings become NaN with the flag preserved.
    Real numbers come back as float with empty flag.
    Anything else (None/NaN) → (NaN, '').
    """
    if isinstance(value, str):
        stripped = value.strip()
        if stripped in SUPPRESSION_FLAGS:
            return (float('nan'), stripped)
        # Try numeric coercion; the source ships some cells as quoted numbers.
        try:
            return (float(stripped.replace(',', '')), '')
        except ValueError:
            return (float('nan'), '')
    try:
        return (float(value), '')  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return (float('nan'), '')


def _join_flags(*flags: str) -> str:
    """Concatenate unique non-empty flags in encounter order."""
    seen: list[str] = []
    for f in flags:
        if f and f not in seen:
            seen.append(f)
    return ','.join(seen)


def _read_oews_sheet(path: Path) -> pd.DataFrame:
    """Read the single data sheet from an OEWS workbook (named like file stem)."""
    sheets = pd.ExcelFile(path).sheet_names
    # The data sheet's name matches the file stem (e.g., 'national_M2025_dl').
    target = path.stem
    sheet = target if target in sheets else sheets[0]
    return pd.read_excel(path, sheet_name=sheet, dtype=str)


def _normalize_oews(df: pd.DataFrame, *, area_kind: str, vintage: str) -> pd.DataFrame:
    """
    Convert a raw OEWS dataframe to canonical schema.

    Filters O_GROUP=='detailed' (6-digit SOC) and rolls per-row suppression
    flags into a single column.
    """
    # Detail-only: aggregator joins on 6-digit SOC from the crosswalk.
    detailed = df[df['O_GROUP'] == 'detailed'].copy()

    # Per-row flag collection — capture which numeric cell(s) were suppressed.
    flag_lists: list[str] = []
    numeric_cols_present = [c for c in _NUMERIC_COLS if c in detailed.columns]
    for _, row in detailed[numeric_cols_present].iterrows():
        cell_flags = []
        for v in row:
            if isinstance(v, str) and v.strip() in SUPPRESSION_FLAGS:
                cell_flags.append(v.strip())
        flag_lists.append(_join_flags(*cell_flags))

    # Coerce numeric columns in-place.
    for col in numeric_cols_present:
        detailed[col] = detailed[col].map(lambda v: _coerce_numeric(v)[0])

    detailed = detailed.rename(columns=_RENAMES)
    detailed['area_kind'] = area_kind
    detailed['vintage'] = vintage
    detailed['suppression_flag'] = flag_lists

    # For the national file there is no PRIM_STATE — synthesize 'US'.
    if 'PRIM_STATE' not in detailed.columns or area_kind == 'national':
        detailed['PRIM_STATE'] = 'US'

    if 'AREA_TITLE' not in detailed.columns:
        detailed['AREA_TITLE'] = 'U.S.' if area_kind == 'national' else ''

    canonical_cols = [
        'SOCCode', 'SOCTitle', 'area_kind', 'PRIM_STATE', 'AREA_TITLE',
        'tot_emp', 'emp_prse',
        'a_mean', 'a_median', 'h_mean', 'h_median',
        'a_pct10', 'a_pct25', 'a_pct75', 'a_pct90',
        'h_pct10', 'h_pct25', 'h_pct75', 'h_pct90',
        'suppression_flag', 'vintage',
    ]
    # Keep any of the canonical columns that exist (defensive against future
    # source-column drops). Reset index so concatenation downstream is clean.
    keep = [c for c in canonical_cols if c in detailed.columns]
    return detailed[keep].reset_index(drop=True)


def load_national(path: Optional[Path] = None, *, vintage: str = 'May 2025') -> pd.DataFrame:
    """
    Load the OEWS national workbook.

    Returns long-format detail-only rows with `area_kind='national'` and
    `PRIM_STATE='US'`. Typically ~830 rows for the May 2025 vintage.
    """
    if path is None:
        raise ValueError('path is required (or pass an OEWSPaths.from_dir() result)')
    raw = _read_oews_sheet(path)
    return _normalize_oews(raw, area_kind='national', vintage=vintage)


def load_state(path: Optional[Path] = None, *, vintage: str = 'May 2025') -> pd.DataFrame:
    """
    Load the OEWS state workbook (one file containing all 50 states + DC/PR/GU/VI).

    Returns long-format detail-only rows. Typically ~36K rows for May 2025.
    Downstream code filters on `PRIM_STATE`.
    """
    if path is None:
        raise ValueError('path is required (or pass an OEWSPaths.from_dir() result)')
    raw = _read_oews_sheet(path)
    return _normalize_oews(raw, area_kind='state', vintage=vintage)


def load_oews(paths: Optional[OEWSPaths] = None, *, vintage: str = 'May 2025') -> pd.DataFrame:
    """
    Convenience: load both national and state, concatenate, return one frame.

    Aggregator and report code should call this once per session.
    """
    if paths is None:
        raise ValueError('paths is required')
    nat = load_national(paths.national, vintage=vintage)
    st = load_state(paths.state, vintage=vintage)
    return pd.concat([nat, st], ignore_index=True)
