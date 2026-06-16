"""
core.crosswalk — shared CIP-SOC crosswalk loader.

Used by:
- v1 (src/loader.py::load_crosswalk) via a thin shim that passes
  drop_sentinels=False to preserve byte-identical v1 behavior.
- v2 labor modules, which default to drop_sentinels=True so aggregation
  doesn't accidentally weight the NCES "NO MATCH" sentinel rows.

The crosswalk file is data/dictionary/cip_soc_crosswalk.xlsx — an NCES
workbook mapping CIP 2020 → SOC 2018. The publisher encodes "no match"
as a sentinel row (CIPCODE='99.9999' or SOCCode='99-9999') rather than
nulls; see docs/CROSSWALK_INSPECTION_FINDINGS.md for full structure.
"""
from __future__ import annotations

from pathlib import Path
from typing import List, Optional

import pandas as pd

# The NCES workbook ships with CIP2020Code/CIP2020Title/SOC2018Code/SOC2018Title.
# The rest of the codebase (v1 joiner.py, etc.) joins on the shorter names.
# Alternate headers below are defensive — seen in other distributions.
_COLUMN_RENAMES = {
    'CIP2020Code': 'CIPCODE',
    'CIP2020Title': 'CIPTitle',
    'SOC2018Code': 'SOCCode',
    'SOC2018Title': 'SOCTitle',
    'CIPCode': 'CIPCODE',
    'CIP Code': 'CIPCODE',
    'CIP Title': 'CIPTitle',
    'SOC Code': 'SOCCode',
    'SOC Title': 'SOCTitle',
}

# NCES sentinels for unmatched codes in the main CIP-SOC sheet.
SENTINEL_CIP = '99.9999'
SENTINEL_SOC = '99-9999'

DEFAULT_SHEET = 'CIP-SOC'


def _find_crosswalk_file(dict_dir: Path) -> Optional[Path]:
    """
    Find a crosswalk file by stem, regardless of extension.

    Matches the v1 loader._find_file behavior so v1 keeps finding the same
    file it always did: case-insensitive stem match on 'cip_soc_crosswalk'
    with extension in (.xlsx, .xls, .csv).
    """
    if not dict_dir.exists():
        return None
    target = 'cip_soc_crosswalk'
    for entry in dict_dir.iterdir():
        if not entry.is_file():
            continue
        if entry.stem.lower() == target and entry.suffix.lower() in ('.csv', '.xlsx', '.xls'):
            return entry
    return None


def _read_crosswalk_table(path: Path) -> pd.DataFrame:
    """Read the crosswalk file. For workbooks, prefer the 'CIP-SOC' sheet."""
    suffix = path.suffix.lower()
    if suffix in ('.xlsx', '.xls'):
        sheet: object = 0
        try:
            sheets = pd.ExcelFile(path).sheet_names
            if DEFAULT_SHEET in sheets:
                sheet = DEFAULT_SHEET
        except Exception:
            pass
        df = pd.read_excel(path, dtype=str, sheet_name=sheet)
    elif suffix == '.csv':
        df = pd.read_csv(path, encoding='latin-1', dtype=str, low_memory=False)
    else:
        raise ValueError(f'Unsupported crosswalk extension: {path.suffix}')
    # Strip BOM + whitespace from column names, mirroring v1's _clean_columns.
    df.columns = [
        str(c).replace('﻿', '').replace('ï»¿', '').strip()
        for c in df.columns
    ]
    return df


def load_crosswalk(
    dict_dir: Path,
    *,
    drop_sentinels: bool = True,
) -> pd.DataFrame:
    """
    Load and normalize the CIP-SOC crosswalk.

    Args:
        dict_dir: Directory containing cip_soc_crosswalk.{xlsx,csv}.
        drop_sentinels: If True (default), filter out NCES "NO MATCH" rows
            (CIPCODE == '99.9999' or SOCCode == '99-9999'). v2 aggregation
            paths want this off-by-default. v1's shim passes False so the
            v1 frame is byte-identical to its pre-refactor shape.

    Returns:
        A DataFrame with canonical columns (CIPCODE, CIPTitle, SOCCode,
        SOCTitle when present). Returns an empty DataFrame if the file
        is missing — callers must not assume the file exists.
    """
    path = _find_crosswalk_file(dict_dir)
    if path is None:
        return pd.DataFrame()

    df = _read_crosswalk_table(path)

    rename_map = {k: v for k, v in _COLUMN_RENAMES.items() if k in df.columns}
    if rename_map:
        df = df.rename(columns=rename_map)

    if 'CIPCODE' in df.columns:
        df['CIPCODE'] = df['CIPCODE'].astype(str).str.strip()
    if 'SOCCode' in df.columns:
        df['SOCCode'] = df['SOCCode'].astype(str).str.strip()

    if drop_sentinels:
        if 'CIPCODE' in df.columns:
            df = df[df['CIPCODE'] != SENTINEL_CIP]
        if 'SOCCode' in df.columns:
            df = df[df['SOCCode'] != SENTINEL_SOC]
        df = df.reset_index(drop=True)

    return df


def unmatched_cips(crosswalk_with_sentinels: pd.DataFrame) -> List[str]:
    """
    CIPCODEs whose only SOC mapping is the sentinel — i.e., NCES has no
    SOC link published for them.

    Pass a frame loaded with drop_sentinels=False, otherwise the answer
    is trivially empty.
    """
    if 'CIPCODE' not in crosswalk_with_sentinels.columns:
        return []
    if 'SOCCode' not in crosswalk_with_sentinels.columns:
        return []
    mask = crosswalk_with_sentinels['SOCCode'] == SENTINEL_SOC
    return sorted(crosswalk_with_sentinels.loc[mask, 'CIPCODE'].unique().tolist())


def find_crosswalk_path(dict_dir: Path) -> Optional[Path]:
    """Public wrapper around the file-finder. Lets v1's shim log the path."""
    return _find_crosswalk_file(dict_dir)
