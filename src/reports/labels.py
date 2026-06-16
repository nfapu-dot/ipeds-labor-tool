"""
reports.labels — central rename dicts for user-visible columns.

Used by BOTH the Streamlit app (`src/app_v2.py`) AND the Excel writer
(`src/reports/writer.py`) so a column gets the same plain-English label
everywhere the user sees it. v1's column naming conventions take precedence
where they overlap.

Per [[feedback-v2-ux-must-match-v1]]: every user-visible column must have a
reader-friendly label. No raw `wage_a_median` / `PRIM_STATE` in the UI.
"""
from __future__ import annotations

import pandas as pd

# v1 already uses these names; reproduced here for completeness.
V1_COLUMN_LABELS = {
    'CIPCODE': 'CIP Code',
    'CIPTitle': 'CIP Title',
    'AWLEVEL': 'Award Level',
    'INSTNM': 'Institution',
    'UNITID': 'UNITID',
    'STABBR': 'State',
    'CITY': 'City',
    'CONTROL': 'Control',
    'ICLEVEL': 'Level',
    'CARNEGIE': 'Carnegie Code',
    'HD_SOURCE_YEAR': 'HD Source Year',
    'CTOTALT': 'Total Completions',
    'CTOTALM': 'Completions (men)',
    'CTOTALW': 'Completions (women)',
    'MAJORNUM': 'Major Number',
    'IS_CIP_6DIGIT': '6-digit CIP?',
    'CAGR': 'CAGR',
    'Flag': 'Flag',
    'Metric': 'Metric',
    'Geography': 'Geography',
}

# Labor-aggregator and combined-view technical column names → plain English.
LABOR_COLUMN_LABELS = {
    'SOCCode': 'SOC Code',
    'SOCTitle': 'SOC Title',
    'PRIM_STATE': 'State',
    'AREA_TITLE': 'Area',
    'area_kind': 'Area Type',
    'soc_count': '# of Linked SOC Occupations',
    'linked_socs': 'Linked SOC Codes',
    'crosswalk_status': 'Crosswalk Status',
    'tot_emp': 'Total Employment',
    'wage_a_mean': 'Annual Mean Wage',
    'wage_a_median': 'Annual Median Wage',
    'wage_a_pct10': 'Wage — 10th Percentile',
    'wage_a_pct25': 'Wage — 25th Percentile',
    'wage_a_pct75': 'Wage — 75th Percentile',
    'wage_a_pct90': 'Wage — 90th Percentile',
    'wage_h_median': 'Hourly Median Wage',
    'n_suppressed_wage': '# Suppressed Wage Cells',
    'n_suppressed_emp': '# Suppressed Employment Cells',
    'bls_employment_change_pct': 'Projected 10-yr Growth % (BLS)',
    'bls_openings_annual_avg': 'Annual Openings (BLS national)',
    'bls_median_annual_wage_base': 'Median Annual Wage (BLS Projections, 2024)',
    'ca_employment_change_pct': 'Projected 10-yr Growth % (CA)',
    'ca_openings_annual_avg': 'Annual Openings (CA)',
    'ca_median_annual_wage': 'Median Annual Wage (CA)',
    'state_pop_total': 'State Population',
    'state_pop_18_24': 'State Population 18–24',
    'state_bachelors_or_higher_pct': "State Adults 25+ w/ Bachelor's+",
    'aggregation_mode': 'Aggregation Mode',
    'vintage': 'Source Vintage',
    'suppression_flag': 'Suppression Flag',
    # Combined-view wide columns
    'LABOR_WAGE_MEDIAN_SEL': 'Median Annual Wage — Selected',
    'LABOR_WAGE_MEAN_SEL': 'Mean Annual Wage — Selected',
    'LABOR_WAGE_P10_SEL': 'Wage 10th %ile — Selected',
    'LABOR_WAGE_P90_SEL': 'Wage 90th %ile — Selected',
    'LABOR_TOT_EMP_SEL': 'Total Employment — Selected',
    'LABOR_SOC_COUNT_SEL': '# Linked SOCs — Selected',
    'LABOR_LINKED_SOCS_SEL': 'Linked SOCs — Selected',
    'LABOR_WAGE_MEDIAN_NAT': 'Median Annual Wage — National',
    'LABOR_WAGE_MEAN_NAT': 'Mean Annual Wage — National',
    'LABOR_WAGE_P10_NAT': 'Wage 10th %ile — National',
    'LABOR_WAGE_P90_NAT': 'Wage 90th %ile — National',
    'LABOR_TOT_EMP_NAT': 'Total Employment — National',
    'LABOR_SOC_COUNT_NAT': '# Linked SOCs — National',
    'LABOR_LINKED_SOCS_NAT': 'Linked SOCs — National',
    'LABOR_BLS_GROWTH_PCT': 'Projected 10-yr Growth % (BLS national)',
    'LABOR_BLS_OPENINGS_ANNUAL': 'Annual Openings (BLS national)',
    'LABOR_CA_GROWTH_PCT': 'Projected 10-yr Growth % (CA)',
    'LABOR_CA_OPENINGS_ANNUAL': 'Annual Openings (CA)',
    'LABOR_CA_MEDIAN_WAGE': 'Median Annual Wage (CA)',
    'STATE_POP_TOTAL_SEL': 'Selected State Population',
    'STATE_POP_18_24_SEL': 'Selected State Pop 18–24',
    'STATE_BACHELORS_PCT_SEL': "Selected State Adults 25+ w/ Bachelor's+",
    'LABOR_PRIMARY_STATE': 'Primary State',
}

# Combined master rename dict. v1 labels first so labor doesn't clobber v1's.
ALL_LABELS = {**LABOR_COLUMN_LABELS, **V1_COLUMN_LABELS}


def rename_for_display(df: pd.DataFrame) -> pd.DataFrame:
    """Return a copy of df with technical column names renamed to plain English."""
    if df is None or df.empty:
        return df
    rename_map = {c: ALL_LABELS[c] for c in df.columns if c in ALL_LABELS}
    return df.rename(columns=rename_map) if rename_map else df.copy()


def display_label(column: str) -> str:
    """Return the plain-English label for a column, or the column itself if unmapped."""
    return ALL_LABELS.get(column, column)
