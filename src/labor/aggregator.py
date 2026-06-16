"""
labor.aggregator — CIP → SOC labor-market rollup, three view modes.

Joins the CIP-SOC crosswalk to the four labor loaders (OEWS, BLS Projections,
CA EDD, Census) and aggregates to one row per (CIPCODE, state) in two of the
three modes. The flat mode preserves one row per (CIPCODE, SOCCode, state)
for drilldown.

View modes (per docs/CIP_SOC_AGGREGATION.md):

  flat                  One row per (CIP, SOC, state). No aggregation.
                        For audits and source-traceability.

  median                One row per (CIP, state). For each metric, take the
                        unweighted median across linked SOCs. Treats every
                        linked SOC as equally relevant.

  employment_weighted   DEFAULT. One row per (CIP, state). For each metric,
                        compute the employment-weighted mean across linked
                        SOCs, with weights = OEWS NATIONAL tot_emp (consistent
                        weight source across metrics). Suppressed cells are
                        excluded from both numerator and denominator for that
                        cell only — they don't poison the aggregate.

The aggregator returns (DataFrame, vintage_dict). The vintage dict carries
the source-release labels for footer disclosure (see
docs/LABOR_SOURCES_INSPECTION.md and [[reference-vintage-misalignment]]).

Schema (median / employment_weighted modes):

    CIPCODE                          str    6-digit CIP
    CIPTitle                         str    NCES title
    PRIM_STATE                       str    2-letter postal abbr or 'US'
    AREA_TITLE                       str    'California', 'U.S.', etc.
    soc_count                        int    Number of contributing SOCs after suppression filter
    linked_socs                      str    Comma-joined SOCCode list (truncated at 20)
    crosswalk_status                 str    'matched' | 'unmatched'

    # OEWS-derived (per-state)
    tot_emp                          float  Sum of employment across contributing SOCs
    wage_a_mean                      float  Aggregated annual mean wage
    wage_a_median                    float  Aggregated annual median wage
    wage_a_pct10/25/75/90            float  Aggregated annual wage percentiles
    wage_h_median                    float  Aggregated hourly median wage
    n_suppressed_wage                int    Count of suppressed wage cells excluded
    n_suppressed_emp                 int    Count of suppressed emp cells excluded

    # BLS Projections (national; same value for every state row of a CIP)
    bls_employment_change_pct        float  Aggregated growth rate (decimal)
    bls_openings_annual_avg          float  Aggregated annual openings (thousands)
    bls_median_wage_base             float  Aggregated 2024 median wage

    # CA EDD (only meaningful when PRIM_STATE == 'CA')
    ca_employment_change_pct         float
    ca_openings_annual_avg           float
    ca_median_annual_wage            float

    # Census (per-state context; no aggregation needed)
    state_pop_total                  int
    state_pop_18_24                  int
    state_bachelors_or_higher_pct    float

    # Methodology metadata (constant within a frame)
    aggregation_mode                 str    'median' | 'employment_weighted' | 'flat'

The vintage dict has shape:
    {
      'oews': 'May 2025',
      'projections': 'BLS Projections 2024-2034',
      'edd': 'CA EDD Long-term 2023-2033',
      'census': 'Census ACS 5-year 2023',
      'crosswalk': 'NCES CIP 2020 → SOC 2018',
      'aggregation_mode': 'employment_weighted',
      'disclosure': '<full footer sentence>',
    }
"""
from __future__ import annotations

from typing import Iterable, Optional, Tuple

import numpy as np
import pandas as pd

# Mode constants
MODE_FLAT = 'flat'
MODE_MEDIAN = 'median'
MODE_WEIGHTED = 'employment_weighted'
ALL_MODES = (MODE_FLAT, MODE_MEDIAN, MODE_WEIGHTED)

# Metrics aggregated from OEWS per-(CIP, state) — same list for median and weighted.
_OEWS_METRICS = (
    'a_mean', 'a_median',
    'a_pct10', 'a_pct25', 'a_pct75', 'a_pct90',
    'h_median',
)

# Metrics aggregated from BLS Projections (national only; weighted by national tot_emp).
_PROJ_METRICS = (
    'employment_change_pct',
    'openings_annual_avg',
    'median_annual_wage_base',
)

# Metrics aggregated from CA EDD (only flows through to PRIM_STATE='CA' rows).
_EDD_METRICS = (
    'employment_change_pct',
    'openings_annual_avg',
    'median_annual_wage',
)


# ---------------------------------------------------------------------------
# Vintage disclosure
# ---------------------------------------------------------------------------

def _first_or_empty(series: pd.Series) -> str:
    """Return the first non-null value as str, or empty string if none."""
    try:
        v = series.dropna().iloc[0]
        return str(v)
    except (IndexError, AttributeError):
        return ''


def _build_vintage_dict(
    oews_df: pd.DataFrame,
    projections_df: pd.DataFrame,
    edd_df: pd.DataFrame,
    census_df: pd.DataFrame,
    *,
    mode: str,
    crosswalk_label: str = 'NCES CIP 2020 → SOC 2018',
) -> dict:
    oews_vintage = _first_or_empty(oews_df['vintage']) if 'vintage' in oews_df.columns else ''
    proj_vintage = _first_or_empty(projections_df['vintage']) if 'vintage' in projections_df.columns else ''
    edd_vintage = _first_or_empty(edd_df['vintage']) if 'vintage' in edd_df.columns else ''
    census_vintage = _first_or_empty(census_df['vintage']) if 'vintage' in census_df.columns else ''

    disclosure = (
        'Sources reflect the most recent release as of report date. Vintages: '
        f'OEWS {oews_vintage}; {proj_vintage}; {edd_vintage}; {census_vintage}; '
        f'Crosswalk {crosswalk_label}. Aggregation: {mode}. '
        'CIP-SOC crosswalk is many-to-many with no NCES-published weights; '
        'aggregation method chosen by tool, alternative methods may yield different results. '
        'Year-over-year comparisons across sources are not valid without explicit normalization.'
    )

    return {
        'oews': oews_vintage,
        'projections': proj_vintage,
        'edd': edd_vintage,
        'census': census_vintage,
        'crosswalk': crosswalk_label,
        'aggregation_mode': mode,
        'disclosure': disclosure,
    }


# ---------------------------------------------------------------------------
# Aggregation helpers — apply to a group of SOC-level rows within one CIP×state
# ---------------------------------------------------------------------------

def _weighted_mean(values: pd.Series, weights: pd.Series) -> float:
    """
    Weighted mean of `values` using `weights`. NaN values are excluded from
    both numerator and denominator for that cell only (so a single suppressed
    cell doesn't void the whole aggregate).
    """
    mask = values.notna() & weights.notna() & (weights > 0)
    if not mask.any():
        return float('nan')
    v = values[mask]
    w = weights[mask]
    return float((v * w).sum() / w.sum())


def _unweighted_median(values: pd.Series) -> float:
    clean = values.dropna()
    if clean.empty:
        return float('nan')
    return float(clean.median())


# ---------------------------------------------------------------------------
# Stage 1: assemble the long-format "flat" frame
# ---------------------------------------------------------------------------

def _build_long_frame(
    crosswalk_df: pd.DataFrame,
    oews_df: pd.DataFrame,
    projections_df: pd.DataFrame,
    edd_df: pd.DataFrame,
    census_df: pd.DataFrame,
    *,
    states: Optional[Iterable[str]] = None,
) -> pd.DataFrame:
    """
    Inner-join the crosswalk to OEWS (giving us one row per CIP×SOC×state),
    then left-join projections (national, SOC-keyed), EDD (CA, SOC-keyed),
    and Census (state-keyed).

    EDD columns are only meaningful where PRIM_STATE == 'CA'; this function
    keeps them populated everywhere but the aggregated builder masks them
    for non-CA rows.
    """
    # Defensive: required columns
    for col in ('CIPCODE', 'SOCCode'):
        if col not in crosswalk_df.columns:
            raise ValueError(f'crosswalk missing required column {col!r}')

    # 1. Crosswalk × OEWS (per-(CIP, SOC, area))
    cw = crosswalk_df[['CIPCODE', 'CIPTitle', 'SOCCode']].drop_duplicates()
    base = cw.merge(oews_df, on='SOCCode', how='inner', suffixes=('', '_oews'))

    # State filter (optional)
    if states is not None:
        wanted = {s.upper() for s in states}
        base = base[base['PRIM_STATE'].isin(wanted)]

    # 2. Add BLS Projections (national, SOC-keyed). Suffix to avoid collisions.
    proj_cols = ['SOCCode'] + [c for c in _PROJ_METRICS if c in projections_df.columns]
    proj = projections_df[proj_cols].rename(columns={
        c: f'bls_{c}' for c in _PROJ_METRICS
    })
    base = base.merge(proj, on='SOCCode', how='left')

    # 3. Add CA EDD (CA, SOC-keyed). Same shape merge; the aggregated builder
    #    will mask these columns for non-CA states.
    edd_cols = ['SOCCode'] + [c for c in _EDD_METRICS if c in edd_df.columns]
    edd_only = edd_df[edd_cols].rename(columns={
        'employment_change_pct': 'ca_employment_change_pct',
        'openings_annual_avg': 'ca_openings_annual_avg',
        'median_annual_wage': 'ca_median_annual_wage',
    })
    base = base.merge(edd_only, on='SOCCode', how='left')

    # 4. Add Census (state-keyed; data is constant within a PRIM_STATE).
    cens_cols = ['state_abbr', 'pop_total', 'pop_18_24', 'bachelors_or_higher_pct']
    cens_cols = [c for c in cens_cols if c in census_df.columns]
    cens = census_df[cens_cols].rename(columns={
        'state_abbr': 'PRIM_STATE',
        'pop_total': 'state_pop_total',
        'pop_18_24': 'state_pop_18_24',
        'bachelors_or_higher_pct': 'state_bachelors_or_higher_pct',
    })
    base = base.merge(cens, on='PRIM_STATE', how='left')

    return base.reset_index(drop=True)


# ---------------------------------------------------------------------------
# Stage 2a: flat mode (no aggregation)
# ---------------------------------------------------------------------------

def _build_flat(long_df: pd.DataFrame) -> pd.DataFrame:
    df = long_df.copy()
    df['aggregation_mode'] = MODE_FLAT
    # Mask CA-EDD columns on non-CA rows. Keeps the flat view honest.
    non_ca = df['PRIM_STATE'] != 'CA'
    for col in ('ca_employment_change_pct', 'ca_openings_annual_avg', 'ca_median_annual_wage'):
        if col in df.columns:
            df.loc[non_ca, col] = np.nan
    return df


# ---------------------------------------------------------------------------
# Stage 2b: aggregated modes (median / employment_weighted) per (CIP, state)
# ---------------------------------------------------------------------------

def _aggregate_group(
    group: pd.DataFrame,
    *,
    mode: str,
    weight_lookup: pd.Series,
    cipcode: str,
    prim_state: str,
) -> dict:
    """
    Reduce a group of SOC rows (one CIP × one state) to a single row.

    weight_lookup is a Series indexed by SOCCode giving national tot_emp.
    Used by employment_weighted mode as actual weights; median mode ignores it.

    cipcode and prim_state are passed explicitly (rather than read from the
    group dataframe) because the calling loop iterates the groupby keys
    directly. Reading them from `group` would also work, but explicit is
    faster and matches the loop's mental model.
    """
    result: dict = {'CIPCODE': cipcode, 'PRIM_STATE': prim_state}

    # Identity columns that are constant within group but not the group keys
    for col in ('CIPTitle', 'AREA_TITLE'):
        if col in group.columns:
            v = group[col].dropna()
            if not v.empty:
                result[col] = v.iloc[0]

    socs = group['SOCCode'].dropna().unique().tolist()
    result['soc_count'] = len(socs)
    if len(socs) <= 20:
        result['linked_socs'] = ','.join(socs)
    else:
        result['linked_socs'] = ','.join(socs[:20]) + f',...({len(socs)-20} more)'
    result['crosswalk_status'] = 'matched'

    # Weights for each SOC (from national OEWS). Missing SOC → weight 0.
    weights = group['SOCCode'].map(weight_lookup).fillna(0.0)

    # Suppression counts
    if 'tot_emp' in group.columns:
        result['n_suppressed_emp'] = int(group['tot_emp'].isna().sum())
        result['tot_emp'] = float(group['tot_emp'].fillna(0).sum())
    else:
        result['n_suppressed_emp'] = 0
        result['tot_emp'] = float('nan')

    wage_suppression = 0
    for m in _OEWS_METRICS:
        col = m  # tot_emp metric naming; column already named a_mean etc.
        if col not in group.columns:
            result[f'wage_{m}'] = float('nan')
            continue
        wage_suppression += int(group[col].isna().sum())
        if mode == MODE_MEDIAN:
            result[f'wage_{m}'] = _unweighted_median(group[col])
        else:  # employment_weighted
            result[f'wage_{m}'] = _weighted_mean(group[col], weights)
    result['n_suppressed_wage'] = wage_suppression

    # BLS Projections (national values — same across states; aggregate within CIP)
    for m in _PROJ_METRICS:
        col = f'bls_{m}'
        if col not in group.columns:
            result[col] = float('nan')
            continue
        if mode == MODE_MEDIAN:
            result[col] = _unweighted_median(group[col])
        else:
            result[col] = _weighted_mean(group[col], weights)

    # CA EDD — only emit values when this group is CA. Otherwise NaN.
    is_ca = (prim_state == 'CA')
    for m in _EDD_METRICS:
        if m == 'median_annual_wage':
            col = 'ca_median_annual_wage'
        else:
            col = f'ca_{m}'
        if not is_ca or col not in group.columns:
            result[col] = float('nan')
            continue
        if mode == MODE_MEDIAN:
            result[col] = _unweighted_median(group[col])
        else:
            result[col] = _weighted_mean(group[col], weights)

    # Census (constant within state — pick first row's values)
    for col in ('state_pop_total', 'state_pop_18_24', 'state_bachelors_or_higher_pct'):
        if col in group.columns:
            v = group[col].dropna()
            result[col] = (v.iloc[0] if not v.empty else float('nan'))
        else:
            result[col] = float('nan')

    result['aggregation_mode'] = mode
    return result


def _build_aggregated(long_df: pd.DataFrame, *, mode: str, oews_df: pd.DataFrame) -> pd.DataFrame:
    """Per-(CIP, state) aggregation."""
    if long_df.empty:
        return pd.DataFrame()

    # National tot_emp lookup (used as weight source for weighted mode).
    nat = oews_df[oews_df['area_kind'] == 'national']
    weight_lookup = nat.groupby('SOCCode')['tot_emp'].first()

    # Explicit loop over groups — clearer than groupby.apply and avoids the
    # include_groups deprecation footgun (group keys were silently missing
    # from the apply-function's dataframe, which broke the CA-EDD check).
    rows = []
    for (cipcode, prim_state), group in long_df.groupby(
        ['CIPCODE', 'PRIM_STATE'], dropna=False, sort=False,
    ):
        rows.append(_aggregate_group(
            group, mode=mode, weight_lookup=weight_lookup,
            cipcode=cipcode, prim_state=prim_state,
        ))
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def aggregate_cip_labor(
    crosswalk_df: pd.DataFrame,
    oews_df: pd.DataFrame,
    projections_df: pd.DataFrame,
    edd_df: pd.DataFrame,
    census_df: pd.DataFrame,
    *,
    mode: str = MODE_WEIGHTED,
    states: Optional[Iterable[str]] = None,
) -> Tuple[pd.DataFrame, dict]:
    """
    Build a per-(CIP, state) labor-market view in the requested mode.

    Args:
        crosswalk_df: From core.crosswalk.load_crosswalk(drop_sentinels=True).
        oews_df: From labor.loaders.oews.load_oews(...). Must include both
            national and state rows.
        projections_df: From labor.loaders.projections.load_projections(...).
        edd_df: From labor.loaders.edd.load_edd(...).
        census_df: From labor.loaders.census.load_census(...).
        mode: 'flat', 'median', or 'employment_weighted' (default).
        states: Optional list of 2-letter state codes (and/or 'US') to keep.
            None = include every area present in the OEWS frame.

    Returns:
        (DataFrame, vintage_dict). DataFrame schema documented in module docstring.

    Raises:
        ValueError if mode is unknown.
    """
    if mode not in ALL_MODES:
        raise ValueError(f'unknown mode {mode!r}; expected one of {ALL_MODES}')

    vintage = _build_vintage_dict(
        oews_df, projections_df, edd_df, census_df, mode=mode,
    )

    long = _build_long_frame(
        crosswalk_df, oews_df, projections_df, edd_df, census_df,
        states=states,
    )

    if mode == MODE_FLAT:
        return _build_flat(long), vintage

    aggregated = _build_aggregated(long, mode=mode, oews_df=oews_df)
    return aggregated, vintage


# ---------------------------------------------------------------------------
# Convenience reporting helpers
# ---------------------------------------------------------------------------

def unmatched_cips_in_crosswalk(crosswalk_with_sentinels: pd.DataFrame) -> list[str]:
    """
    CIPs whose only SOC mapping is the NCES sentinel.

    Pass a frame loaded with drop_sentinels=False. For per-(CIP, state)
    output, downstream code emits one "unmatched" row per such CIP using
    this list, since the matched-frame won't contain those CIPs.
    """
    # Re-exported here so the orchestrator only needs one import.
    from core.crosswalk import unmatched_cips
    return unmatched_cips(crosswalk_with_sentinels)
