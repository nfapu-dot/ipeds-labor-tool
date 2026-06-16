"""
reports.combine — orchestrator joining v1 completions + v2 labor.

Public entry points:

    build_combined_dataset(...) -> dict
        Runs v1's pipeline AND v2's labor pipeline, joins, returns a dict
        containing every dataframe the writer needs.

    build_per_cip_combined_view(...) -> pd.DataFrame
        Produces the joined per-(CIP, AWLEVEL) "Combined Market View" sheet —
        v1's market view PLUS new labor columns (wage / growth / openings)
        for the user's selected geography and for U.S. national.

Design notes:
- The orchestrator imports v1's flat-layout modules (loader, joiner, aggregator
  as v1_aggregator, reporter) AND v2's labor.* modules. v1's modules are NOT
  modified — they're called as-is. See [[feedback-parallel-apps]].
- "Selected geography" = the single state the user picked (if any) OR 'US'.
  Multi-state selections collapse to 'US' for the labor join — too ambiguous
  to pick one state's labor stats for a multi-state market view.
- Labor data is keyed on SOC; v1's market view is keyed on (CIP × AWLEVEL).
  We use the labor aggregator's CIP-level rollup (employment-weighted across
  linked SOCs) so labor columns can be joined directly on CIPCODE. Award
  level doesn't affect labor stats (SOC doesn't vary by graduate's degree
  level) — labor columns are repeated across AWLEVELs of the same CIP.
"""
from __future__ import annotations

from pathlib import Path
from typing import Dict, List, Optional

import pandas as pd

# v1 modules (flat layout under src/)
from aggregator import (  # type: ignore[import-not-found]
    apply_filters,
    compute_cagr_table,
    compute_market_view,
    compute_national_market_view,
    market_view_to_long,
    merge_selected_and_national_market_view,
    vec_cagr,
)
from joiner import (  # type: ignore[import-not-found]
    build_cip_title_lookup,
    build_institution_metadata,
    join_all_years,
)
from loader import (  # type: ignore[import-not-found]
    load_all,
    load_cip_filter_config,
    load_years_config,
)

# v2 modules
from core import crosswalk as core_crosswalk
from labor import aggregator as labor_aggregator
from labor.loaders import oews as oews_loader
from labor.loaders import projections as projections_loader
from labor.loaders import edd as edd_loader
from labor.loaders import census as census_loader


# Geographies handled specially in the join.
US_AREA = 'US'

# Saturation ratio caveat — surfaced in the Streamlit tab AND the Excel
# disclosure sheet. The ratio is a directional signal, NOT a precise
# supply/demand measure. Per docs/PHASE_PLAN.md §Phase 6.
SATURATION_CAVEAT = (
    'Saturation ratio = annual program completions ÷ annual labor openings. '
    'DIRECTIONAL SIGNAL ONLY — not a precise supply/demand measure. Caveats: '
    '(1) Completions reflect your award-level filter, but openings span ALL '
    'degree levels of the linked SOC occupations. '
    '(2) Completions count graduates PRODUCED BY institutions in the geography; '
    'openings count jobs LOCATED IN the geography. Graduates move across state '
    'lines, so the two are not a closed system. '
    '(3) The CIP-SOC crosswalk is many-to-many — openings attributed to one CIP '
    'overlap with openings attributed to other CIPs. Do NOT sum the ratio across '
    'CIPs. '
    '(4) Source vintages differ: completions 2024; BLS openings 2024–2034; '
    'CA EDD openings 2023–2033.'
)


def _saturation_reading(ratio: float) -> str:
    """Plain-English descriptor of a completions/openings ratio. Heuristic bands."""
    if ratio is None or pd.isna(ratio):
        return '—'
    if ratio >= 2.0:
        return 'Far more completions than openings'
    if ratio >= 1.2:
        return 'More completions than openings'
    if ratio >= 0.8:
        return 'Roughly balanced'
    if ratio >= 0.5:
        return 'Fewer completions than openings'
    return 'Far fewer completions than openings'


def build_saturation_view(
    *,
    combined_market_view: pd.DataFrame,
    primary_state: str,
    end_year: int,
) -> pd.DataFrame:
    """
    Per-CIP saturation: annual completions ÷ annual labor openings.

    Long format — one row per (CIP × geography). Completions are summed across
    the award levels present in the combined view (which already reflect the
    user's award-level filter). Openings are taken once per CIP (they're
    constant across award-level rows). Both are absolute annual counts.

    Geography handling:
      - Always emits a 'U.S. (national)' row: national completions ÷ BLS
        national openings.
      - If primary_state == 'CA': also emits a 'California' row: selected (CA)
        completions ÷ CA EDD openings.
      - If primary_state is another specific state: emits a row with the
        selected completions but openings unavailable (EDD covers CA only).
      - If primary_state == 'US': the national row already covers it.

    Heavily caveated — see SATURATION_CAVEAT.
    """
    cols = ['CIP Code', 'CIP Title', 'Geography', 'Annual Completions',
            'Annual Openings', 'Completions per Opening',
            'Median Annual Wage', 'Reading']
    if combined_market_view.empty:
        return pd.DataFrame(columns=cols)

    cmv = combined_market_view
    sel_col = f'SUM_CTOTALT_{end_year}'
    nat_col = f'NAT_SUM_CTOTALT_{end_year}'

    def _first(g: pd.DataFrame, col: str) -> float:
        if col in g.columns and not g[col].dropna().empty:
            return float(g[col].dropna().iloc[0])
        return float('nan')

    def _sum(g: pd.DataFrame, col: str) -> float:
        if col in g.columns:
            return float(g[col].sum(skipna=True))
        return float('nan')

    def _ratio(numer: float, denom: float) -> float:
        if denom and not pd.isna(denom) and denom > 0 and not pd.isna(numer):
            return numer / denom
        return float('nan')

    rows: list[dict] = []
    for cipcode, g in cmv.groupby('CIPCODE', sort=False):
        title = g['CIPTitle'].iloc[0] if 'CIPTitle' in g.columns else ''
        sel_compl = _sum(g, sel_col)
        nat_compl = _sum(g, nat_col)
        bls_open = _first(g, 'LABOR_BLS_OPENINGS_ANNUAL')
        ca_open = _first(g, 'LABOR_CA_OPENINGS_ANNUAL')
        sel_wage = _first(g, 'LABOR_WAGE_MEDIAN_SEL')
        nat_wage = _first(g, 'LABOR_WAGE_MEDIAN_NAT')

        # National row — always.
        nat_ratio = _ratio(nat_compl, bls_open)
        rows.append({
            'CIP Code': cipcode, 'CIP Title': title,
            'Geography': 'U.S. (national)',
            'Annual Completions': nat_compl,
            'Annual Openings': bls_open,
            'Completions per Opening': nat_ratio,
            'Median Annual Wage': nat_wage,
            'Reading': _saturation_reading(nat_ratio),
        })

        # Selected-state row.
        if primary_state == 'CA':
            ca_ratio = _ratio(sel_compl, ca_open)
            rows.append({
                'CIP Code': cipcode, 'CIP Title': title,
                'Geography': 'California',
                'Annual Completions': sel_compl,
                'Annual Openings': ca_open,
                'Completions per Opening': ca_ratio,
                'Median Annual Wage': sel_wage,
                'Reading': _saturation_reading(ca_ratio),
            })
        elif primary_state != US_AREA:
            rows.append({
                'CIP Code': cipcode, 'CIP Title': title,
                'Geography': f'{primary_state} (state openings unavailable)',
                'Annual Completions': sel_compl,
                'Annual Openings': float('nan'),
                'Completions per Opening': float('nan'),
                'Median Annual Wage': sel_wage,
                'Reading': 'No state-level openings data (EDD covers CA only)',
            })

    return pd.DataFrame(rows, columns=cols).reset_index(drop=True)


def _resolve_primary_state(states: Optional[List[str]]) -> str:
    """
    Map the user's state selection to a single 'primary' state for the
    combined view's selected-labor column.

    Single state → that state. None or multi-state → 'US' (the labor join
    falls back to national so the view stays interpretable).
    """
    if not states:
        return US_AREA
    if len(states) == 1:
        return states[0].upper()
    return US_AREA


def build_completions_layer(
    *,
    unitids: List[int],
    cip_codes: Optional[List[str]],
    award_levels: Optional[List[int]],
    include_residual: bool,
    project_root: Path,
    years_cfg: Optional[dict] = None,
    cip_cfg: Optional[dict] = None,
    quiet: bool = True,
    v1_data: Optional[dict] = None,
) -> dict:
    """
    Run v1's completions pipeline. Returns a dict containing the same
    artifacts v1's main.py builds (raw loaded data + filtered + CAGR +
    market view + varlist + institution metadata + CIP titles).

    `v1_data` (optional): a pre-loaded dict in the shape produced by the
    Streamlit app's cached_load_v1_data() — keys hd, ca, varlist, crosswalk,
    metadata, cip_titles, years_cfg, cip_cfg. When supplied, the expensive
    load_all() + metadata build is SKIPPED (the data is reused). When None,
    everything is loaded fresh (CLI path). Injecting cached data is what makes
    repeat Streamlit queries fast — the IPEDS CSVs are read once per session,
    not once per "Generate" click.
    """
    if v1_data is not None:
        years_cfg = v1_data['years_cfg']
        cip_cfg = v1_data['cip_cfg']
        loaded = {
            'hd': v1_data['hd'],
            'ca': v1_data['ca'],
            'varlist': v1_data['varlist'],
            'crosswalk': v1_data['crosswalk'],
        }
        metadata = v1_data['metadata']
        cip_titles = v1_data['cip_titles']
    else:
        if years_cfg is None:
            years_cfg = load_years_config(project_root / 'config' / 'years.yaml')
        if cip_cfg is None:
            cip_cfg = load_cip_filter_config(project_root / 'config' / 'cip_filter.yaml')
        loaded = load_all(
            years_cfg,
            raw_dir=project_root / 'data' / 'raw',
            dict_dir=project_root / 'data' / 'dictionary',
        )
        metadata = build_institution_metadata(loaded['hd'])
        cip_titles = build_cip_title_lookup(loaded['crosswalk'])

    final_cips = [str(c).strip() for c in (cip_codes or cip_cfg['cip_codes'])]
    final_aws = list(award_levels) if award_levels else list(cip_cfg['award_levels'])

    joined = join_all_years(
        loaded['ca'], metadata, cip_titles,
        selected_unitids=unitids, quiet=quiet,
    )
    filtered = apply_filters(
        joined,
        cip_codes=final_cips, award_levels=final_aws,
        include_residual=include_residual,
        quiet=quiet,
    )

    start_year = years_cfg['cagr_start_year']
    end_year = years_cfg['cagr_end_year']
    cagr_df = compute_cagr_table(
        filtered, start_year=start_year, end_year=end_year,
    )
    selected_mv = compute_market_view(
        filtered, start_year=start_year, end_year=end_year,
    )
    national_mv = compute_national_market_view(
        loaded['ca'], cip_titles,
        cip_codes=final_cips, award_levels=final_aws,
        include_residual=include_residual,
        start_year=start_year, end_year=end_year,
        quiet=quiet,
    )
    mv_wide = merge_selected_and_national_market_view(selected_mv, national_mv)
    mv_long = market_view_to_long(
        mv_wide, start_year=start_year, end_year=end_year,
    )

    # The institutions view used by v1's reporter — only institutions that
    # contributed rows to the filter.
    unitids_with_data: set = set()
    for year_df in filtered.values():
        unitids_with_data.update(int(u) for u in year_df['UNITID'].dropna())
    institutions_view = metadata[metadata['UNITID'].isin(unitids_with_data)]

    return {
        'years_cfg': years_cfg,
        'loaded': loaded,
        'metadata': metadata,
        'cip_titles': cip_titles,
        'filtered': filtered,
        'cagr_df': cagr_df,
        'market_view_wide': mv_wide,
        'market_view_long': mv_long,
        'institutions_view': institutions_view,
        'varlist_df': loaded['varlist'],
    }


def build_labor_layer(
    *,
    project_root: Path,
    states: Optional[List[str]] = None,
    aggregation_mode: str = labor_aggregator.MODE_WEIGHTED,
) -> dict:
    """
    Load all four labor sources and run the labor aggregator.

    `states` filters the aggregator output. None = US only (matches the
    "national" default for the combined view). To get a per-state slice
    pass e.g. ['US', 'CA'].

    Returns a dict with the raw loaded frames, the aggregated frame, the
    flat frame (for drilldown), and the vintage_dict.
    """
    raw_labor = project_root / 'data' / 'raw_labor'
    dict_dir = project_root / 'data' / 'dictionary'

    cw_sentinels = core_crosswalk.load_crosswalk(dict_dir, drop_sentinels=False)
    cw = cw_sentinels[
        (cw_sentinels['CIPCODE'] != core_crosswalk.SENTINEL_CIP)
        & (cw_sentinels['SOCCode'] != core_crosswalk.SENTINEL_SOC)
    ].reset_index(drop=True)

    oews_df = oews_loader.load_oews(oews_loader.OEWSPaths.from_dir(raw_labor))
    proj_df = projections_loader.load_projections(
        raw_labor / 'projections' / 'occupation_2024-2034.xlsx',
    )
    edd_df = edd_loader.load_edd(
        raw_labor / 'edd' / 'edd_long_term_occ_projections_2023-2033.xlsx',
    )
    cens_df = census_loader.load_census()

    aggregated, vintage = labor_aggregator.aggregate_cip_labor(
        cw, oews_df, proj_df, edd_df, cens_df,
        mode=aggregation_mode,
        states=states,
    )
    flat, _ = labor_aggregator.aggregate_cip_labor(
        cw, oews_df, proj_df, edd_df, cens_df,
        mode=labor_aggregator.MODE_FLAT,
        states=states,
    )
    unmatched = labor_aggregator.unmatched_cips_in_crosswalk(cw_sentinels)

    return {
        'crosswalk': cw,
        'crosswalk_with_sentinels': cw_sentinels,
        'oews': oews_df,
        'projections': proj_df,
        'edd': edd_df,
        'census': cens_df,
        'aggregated': aggregated,
        'flat': flat,
        'unmatched_cips': unmatched,
        'vintage': vintage,
        'aggregation_mode': aggregation_mode,
    }


def build_labor_long_view(
    *,
    labor_aggregated: pd.DataFrame,
    primary_state: str,
    cip_codes: Optional[List[str]] = None,
) -> pd.DataFrame:
    """
    Melt the (wide) labor aggregator output to long format: one row per
    (CIP × Geography × Source × Metric). Easier to scan than the 27-column
    aggregated frame.

    Schema:
        CIP Code, CIP Title, Geography, Source, Metric, Value, # Linked SOCs

    Geography values: 'U.S.' / '<primary_state>'.
    Source values: 'BLS OEWS', 'BLS Projections', 'CA EDD'.
    Metric values: 'Median Annual Wage', '10-Year Growth %', 'Annual Openings',
        'Total Employment'.

    Per [[feedback-v2-ux-must-match-v1]]: long format, plain English column
    names, filtered to user's CIP selection.
    """
    if labor_aggregated.empty:
        return pd.DataFrame(columns=[
            'CIP Code', 'CIP Title', 'Geography', 'Source', 'Metric',
            'Value', '# Linked SOCs',
        ])

    df = labor_aggregated.copy()
    if cip_codes:
        wanted = {str(c).strip() for c in cip_codes}
        df = df[df['CIPCODE'].isin(wanted)]

    rows: list[dict] = []
    # Map state codes to human area names.
    def _area_name(state_abbr: str) -> str:
        return 'U.S.' if state_abbr == 'US' else state_abbr

    # Walk each (CIP × state) row of the aggregated frame, emit one long row
    # per (Source, Metric).
    for _, row in df.iterrows():
        geo = _area_name(row.get('PRIM_STATE', ''))
        base = {
            'CIP Code': row.get('CIPCODE', ''),
            'CIP Title': row.get('CIPTitle', ''),
            '# Linked SOCs': row.get('soc_count', None),
        }

        # OEWS — wages + employment for this state
        for metric_col, metric_name in (
            ('wage_a_median', 'Median Annual Wage'),
            ('wage_a_mean', 'Mean Annual Wage'),
            ('wage_a_pct10', 'Wage — 10th Percentile'),
            ('wage_a_pct90', 'Wage — 90th Percentile'),
            ('tot_emp', 'Total Employment'),
        ):
            v = row.get(metric_col)
            if pd.notna(v):
                rows.append({**base, 'Geography': geo, 'Source': 'BLS OEWS',
                             'Metric': metric_name, 'Value': v})

        # BLS Projections — national only. Emit only for the national row of
        # each CIP to avoid duplicating the same value per state. `scale`
        # normalizes BLS openings from thousands to absolute counts so every
        # openings figure in the app is in the same unit as CA EDD.
        if row.get('PRIM_STATE') == US_AREA:
            for metric_col, metric_name, scale in (
                ('bls_employment_change_pct', '10-Year Growth %', 1.0),
                ('bls_openings_annual_avg', 'Annual Openings', 1000.0),
                ('bls_median_annual_wage_base', 'Median Annual Wage', 1.0),
            ):
                v = row.get(metric_col)
                if pd.notna(v):
                    rows.append({**base, 'Geography': 'U.S.',
                                 'Source': 'BLS Projections',
                                 'Metric': metric_name, 'Value': v * scale})

        # CA EDD — only when this row is CA
        if row.get('PRIM_STATE') == 'CA':
            for metric_col, metric_name in (
                ('ca_employment_change_pct', '10-Year Growth %'),
                ('ca_openings_annual_avg', 'Annual Openings'),
                ('ca_median_annual_wage', 'Median Annual Wage'),
            ):
                v = row.get(metric_col)
                if pd.notna(v):
                    rows.append({**base, 'Geography': 'California',
                                 'Source': 'CA EDD', 'Metric': metric_name,
                                 'Value': v})

    out = pd.DataFrame(rows)
    if out.empty:
        return out
    # Stable, human-friendly ordering.
    out = out[['CIP Code', 'CIP Title', 'Geography', 'Source', 'Metric',
               'Value', '# Linked SOCs']]
    return out.reset_index(drop=True)


def _reported_counts_wide(
    filtered_by_year: Dict[int, pd.DataFrame],
    years: List[int],
    col_prefix: str,
) -> pd.DataFrame:
    """
    Per (CIPCODE, AWLEVEL): distinct UNITIDs that FILED a record in each year,
    regardless of CTOTALT (so a 0-graduate row still counts as "program on the
    books that year"). PER-YEAR — a program with no record in year Y is not
    counted for year Y, which is exactly why shut-down programs don't leak in.

    Returns wide: CIPCODE, AWLEVEL, {col_prefix}{year} for each year.
    """
    result: Optional[pd.DataFrame] = None
    for y in years:
        df = filtered_by_year.get(y)
        if df is None or df.empty:
            continue
        sub = df.copy()
        sub['CIPCODE'] = sub['CIPCODE'].astype(str).str.strip()
        sub['AWLEVEL'] = pd.to_numeric(sub['AWLEVEL'], errors='coerce')
        cnt = (
            sub.groupby(['CIPCODE', 'AWLEVEL'], dropna=False)['UNITID']
               .nunique()
               .reset_index()
               .rename(columns={'UNITID': f'{col_prefix}{y}'})
        )
        result = cnt if result is None else result.merge(
            cnt, on=['CIPCODE', 'AWLEVEL'], how='outer',
        )
    if result is None:
        return pd.DataFrame(columns=['CIPCODE', 'AWLEVEL'] + [f'{col_prefix}{y}' for y in years])
    return result


def build_reported_counts_wide(
    *,
    selected_filtered: Dict[int, pd.DataFrame],
    ca_dict: Dict[int, pd.DataFrame],
    cip_codes: Optional[List[str]],
    award_levels: Optional[List[int]],
    include_residual: bool,
    years: List[int],
) -> pd.DataFrame:
    """
    Wide frame of reported-program counts (institutions filing any record,
    incl. 0-graduate) for SELECTED and NATIONAL, per (CIPCODE, AWLEVEL, year).

    Selected counts come from the already-filtered selected frame (which keeps
    0/suppressed rows — apply_filters only screens CIP / AWLEVEL / 6-digit).
    National counts come from re-applying the same filters to the full C_A.
    """
    selected_reported = _reported_counts_wide(selected_filtered, years, 'REPORTED_SEL_')

    national_filtered = apply_filters(
        {y: ca_dict[y] for y in years if y in ca_dict},
        cip_codes=cip_codes,
        award_levels=award_levels,
        include_residual=include_residual,
        quiet=True,
    )
    national_reported = _reported_counts_wide(national_filtered, years, 'REPORTED_NAT_')

    if selected_reported.empty and national_reported.empty:
        return pd.DataFrame(columns=['CIPCODE', 'AWLEVEL'])
    if selected_reported.empty:
        return national_reported
    if national_reported.empty:
        return selected_reported
    return selected_reported.merge(
        national_reported, on=['CIPCODE', 'AWLEVEL'], how='outer',
    )


# Metric labels used by the augmented market view. The existing v1 "Programs"
# metric is relabeled in the v2 view for clarity now that two program counts
# coexist. v1's own output is NOT relabeled (different code path).
METRIC_DEGREE_CONFERRING = 'Programs (degree-conferring)'
METRIC_ALL_REPORTED = 'Programs (all reported, incl. 0 graduates)'


def augment_market_view_long(
    *,
    market_view_long: pd.DataFrame,
    reported_wide: pd.DataFrame,
    start_year: int,
    end_year: int,
) -> pd.DataFrame:
    """
    Append two rows per (CIP × Award Level) to v1's market-view-long frame:
      - Selected / 'Programs (all reported, incl. 0 graduates)'
      - National / 'Programs (all reported, incl. 0 graduates)'

    Also relabels the existing 'Programs' rows to 'Programs (degree-conferring)'
    so the two counts are unambiguous. Same columns as v1's long format, so
    the v1 Excel writer + the v2 renderer consume it unchanged.

    Rows are ordered so each program's metrics group together:
      Selected: Completions, Programs (degree-conferring), Programs (all reported)
      National: Completions, Programs (degree-conferring), Programs (all reported)
    """
    if market_view_long.empty:
        return market_view_long.copy()

    years = list(range(start_year, end_year + 1))
    n_periods = end_year - start_year

    out = market_view_long.copy()
    out['Metric'] = out['Metric'].replace({'Programs': METRIC_DEGREE_CONFERRING})

    title_map = dict(zip(out['CIPCODE'], out['CIP Title']))
    key_pairs = out[['CIPCODE', 'Award Level']].drop_duplicates()

    rep = reported_wide.copy()
    if not rep.empty:
        rep['AWLEVEL'] = pd.to_numeric(rep['AWLEVEL'], errors='coerce')
        rep_lookup = rep.set_index(['CIPCODE', 'AWLEVEL'])
    else:
        rep_lookup = None

    new_rows: List[dict] = []
    for cip, awl in key_pairs.itertuples(index=False):
        awl_num = pd.to_numeric(awl, errors='coerce')
        rep_row = None
        if rep_lookup is not None and (cip, awl_num) in rep_lookup.index:
            rep_row = rep_lookup.loc[(cip, awl_num)]
        for geo_label, prefix in (('Selected', 'REPORTED_SEL_'), ('National', 'REPORTED_NAT_')):
            rec: Dict[str, object] = {
                'CIPCODE': cip,
                'CIP Title': title_map.get(cip),
                'Award Level': awl,
                'Geography': geo_label,
                'Metric': METRIC_ALL_REPORTED,
            }
            for y in years:
                rec[str(y)] = (
                    rep_row.get(f'{prefix}{y}') if rep_row is not None else None
                )
            start_v = rec.get(str(start_year))
            end_v = rec.get(str(end_year))
            cagr, flag = vec_cagr(
                pd.Series([start_v], dtype='float64'),
                pd.Series([end_v], dtype='float64'),
                n_periods,
            )
            rec['CAGR'] = cagr.iloc[0]
            rec['Flag'] = flag.iloc[0]
            new_rows.append(rec)

    augmented = pd.concat([out, pd.DataFrame(new_rows)], ignore_index=True)

    # Stable grouped ordering.
    geo_order = {'Selected': 0, 'National': 1}
    metric_order = {
        'Completions': 0,
        METRIC_DEGREE_CONFERRING: 1,
        METRIC_ALL_REPORTED: 2,
    }
    augmented['_g'] = augmented['Geography'].map(geo_order).fillna(9)
    augmented['_m'] = augmented['Metric'].map(metric_order).fillna(9)
    augmented = (
        augmented.sort_values(['CIPCODE', 'Award Level', '_g', '_m'])
                 .drop(columns=['_g', '_m'])
                 .reset_index(drop=True)
    )
    return augmented


def build_per_cip_combined_view(
    *,
    market_view_wide: pd.DataFrame,
    labor_aggregated: pd.DataFrame,
    primary_state: str,
) -> pd.DataFrame:
    """
    Join v1's per-(CIP, AWLEVEL) market view with labor data for the
    primary_state (and US national).

    Produces one row per (CIP, AWLEVEL) with both v1's columns AND new
    labor columns suffixed _SEL (primary state) and _NAT (US national).
    CA-specific columns (LABOR_CA_*) are populated only when primary_state
    is 'CA'.
    """
    if market_view_wide.empty:
        return market_view_wide.copy()

    def _slice(state: str) -> pd.DataFrame:
        sub = labor_aggregated[labor_aggregated['PRIM_STATE'] == state]
        cols = [
            'CIPCODE',
            'wage_a_median', 'wage_a_mean', 'wage_a_pct10', 'wage_a_pct90',
            'tot_emp',
            'soc_count', 'linked_socs',
            'bls_employment_change_pct', 'bls_openings_annual_avg',
            'ca_median_annual_wage', 'ca_employment_change_pct',
            'ca_openings_annual_avg',
            'state_pop_total', 'state_pop_18_24', 'state_bachelors_or_higher_pct',
        ]
        cols = [c for c in cols if c in sub.columns]
        return sub[cols]

    sel_slice = _slice(primary_state).rename(columns={
        'wage_a_median': 'LABOR_WAGE_MEDIAN_SEL',
        'wage_a_mean': 'LABOR_WAGE_MEAN_SEL',
        'wage_a_pct10': 'LABOR_WAGE_P10_SEL',
        'wage_a_pct90': 'LABOR_WAGE_P90_SEL',
        'tot_emp': 'LABOR_TOT_EMP_SEL',
        'soc_count': 'LABOR_SOC_COUNT_SEL',
        'linked_socs': 'LABOR_LINKED_SOCS_SEL',
        'bls_employment_change_pct': 'LABOR_BLS_GROWTH_PCT',  # national; same in SEL/NAT
        'bls_openings_annual_avg': 'LABOR_BLS_OPENINGS_ANNUAL',
        'ca_median_annual_wage': 'LABOR_CA_MEDIAN_WAGE',
        'ca_employment_change_pct': 'LABOR_CA_GROWTH_PCT',
        'ca_openings_annual_avg': 'LABOR_CA_OPENINGS_ANNUAL',
        'state_pop_total': 'STATE_POP_TOTAL_SEL',
        'state_pop_18_24': 'STATE_POP_18_24_SEL',
        'state_bachelors_or_higher_pct': 'STATE_BACHELORS_PCT_SEL',
    })
    nat_slice = _slice(US_AREA).rename(columns={
        'wage_a_median': 'LABOR_WAGE_MEDIAN_NAT',
        'wage_a_mean': 'LABOR_WAGE_MEAN_NAT',
        'wage_a_pct10': 'LABOR_WAGE_P10_NAT',
        'wage_a_pct90': 'LABOR_WAGE_P90_NAT',
        'tot_emp': 'LABOR_TOT_EMP_NAT',
        'soc_count': 'LABOR_SOC_COUNT_NAT',
        'linked_socs': 'LABOR_LINKED_SOCS_NAT',
        # Drop CA-only and population columns from the NAT slice — they aren't
        # state-context-meaningful for the national row.
    })
    nat_slice = nat_slice[[c for c in nat_slice.columns if not c.startswith('ca_')
                           and not c.startswith('state_')]]

    out = market_view_wide.merge(sel_slice, on='CIPCODE', how='left')
    # When primary_state is US, drop the duplicative SEL-side BLS/CA columns —
    # they're identical to NAT. We do this by dropping then re-merging the NAT slice.
    if primary_state == US_AREA:
        drop_cols = [
            c for c in (
                'LABOR_WAGE_MEDIAN_SEL', 'LABOR_WAGE_MEAN_SEL',
                'LABOR_WAGE_P10_SEL', 'LABOR_WAGE_P90_SEL',
                'LABOR_TOT_EMP_SEL', 'LABOR_SOC_COUNT_SEL',
                'LABOR_LINKED_SOCS_SEL',
                'STATE_POP_TOTAL_SEL', 'STATE_POP_18_24_SEL',
                'STATE_BACHELORS_PCT_SEL',
            ) if c in out.columns
        ]
        out = out.drop(columns=drop_cols)

    out = out.merge(nat_slice, on='CIPCODE', how='left')

    # Units normalization: BLS Projections reports employment + openings in
    # THOUSANDS (per the source schema). EDD reports the same metrics in
    # ABSOLUTE counts. The combined view standardizes on absolute counts so
    # LABOR_BLS_OPENINGS_ANNUAL and LABOR_CA_OPENINGS_ANNUAL are directly
    # comparable. Multiplying BLS columns by 1000 here keeps the underlying
    # loader contract stable.
    for bls_col in ('LABOR_BLS_OPENINGS_ANNUAL',):
        if bls_col in out.columns:
            out[bls_col] = out[bls_col] * 1000.0

    out['LABOR_PRIMARY_STATE'] = primary_state
    return out


def build_combined_dataset(
    *,
    unitids: List[int],
    cip_codes: Optional[List[str]] = None,
    award_levels: Optional[List[int]] = None,
    states: Optional[List[str]] = None,
    include_residual: bool = False,
    aggregation_mode: str = labor_aggregator.MODE_WEIGHTED,
    project_root: Path,
    label: str = 'custom',
    quiet: bool = True,
    v1_data: Optional[dict] = None,
    labor_layer: Optional[dict] = None,
) -> dict:
    """
    Run both v1 and v2 pipelines, build the combined per-CIP view, and
    return everything the writer needs.

    Args mirror v1's CLI:
      unitids       — selected institution UNITIDs (resolve via main_v2's CLI flags)
      cip_codes     — CIP filter list (None = use config default)
      award_levels  — AWLEVEL filter list (None = use config default)
      states        — geography filter for labor (default: ['US'])
      include_residual — include bare-2-digit CIP rollups (CIP 99 etc.)
      aggregation_mode — labor aggregation: 'flat' / 'median' / 'employment_weighted'
      project_root  — repo root
      label         — filename suffix for the output

    Performance injection (both optional; used by the Streamlit app, ignored
    by the CLI):
      v1_data     — pre-loaded IPEDS data (see build_completions_layer).
      labor_layer — pre-built labor layer (see build_labor_layer). MUST have
                    been built for a `states` list covering this call's
                    primary_state + US, and for the same aggregation_mode.

    Returns a dict ready to hand to writer.write_combined_workbook.
    """
    primary_state = _resolve_primary_state(states)
    # The labor layer needs both primary_state and US so the joined view can
    # show selected-vs-national.
    labor_states: List[str] = [US_AREA]
    if primary_state != US_AREA:
        labor_states.append(primary_state)

    completions = build_completions_layer(
        unitids=unitids,
        cip_codes=cip_codes,
        award_levels=award_levels,
        include_residual=include_residual,
        project_root=project_root,
        quiet=quiet,
        v1_data=v1_data,
    )
    if labor_layer is not None:
        labor = labor_layer
    else:
        labor = build_labor_layer(
            project_root=project_root,
            states=labor_states,
            aggregation_mode=aggregation_mode,
        )

    combined_market_view = build_per_cip_combined_view(
        market_view_wide=completions['market_view_wide'],
        labor_aggregated=labor['aggregated'],
        primary_state=primary_state,
    )

    # Long-format labor view — what the user actually wants to scan.
    # Filtered to the user's CIPs (if any) so the table is relevant.
    labor_long = build_labor_long_view(
        labor_aggregated=labor['aggregated'],
        primary_state=primary_state,
        cip_codes=cip_codes,
    )

    # Filtered labor-aggregated (wide) for drilldown — same CIP filter.
    labor_aggregated_filtered = labor['aggregated']
    if cip_codes:
        wanted = {str(c).strip() for c in cip_codes}
        labor_aggregated_filtered = labor_aggregated_filtered[
            labor_aggregated_filtered['CIPCODE'].isin(wanted)
        ].reset_index(drop=True)

    # Unmatched CIPs filtered to user's CIPs (if any)
    unmatched_filtered = labor['unmatched_cips']
    if cip_codes:
        wanted_set = {str(c).strip() for c in cip_codes}
        unmatched_filtered = [u for u in unmatched_filtered if u in wanted_set]

    # Reported-program counts (incl. 0-graduate) — augment the market view long
    # with two extra rows per program (Selected + National). v1's market view
    # frame and code path are untouched; this is a v2-only augmentation.
    start_year = completions['years_cfg']['cagr_start_year']
    end_year = completions['years_cfg']['cagr_end_year']
    years = list(range(start_year, end_year + 1))
    reported_wide = build_reported_counts_wide(
        selected_filtered=completions['filtered'],
        ca_dict=completions['loaded']['ca'],
        cip_codes=cip_codes,
        award_levels=award_levels,
        include_residual=include_residual,
        years=years,
    )
    market_view_long_augmented = augment_market_view_long(
        market_view_long=completions['market_view_long'],
        reported_wide=reported_wide,
        start_year=start_year,
        end_year=end_year,
    )

    # Saturation ratio — per-CIP completions vs openings.
    saturation = build_saturation_view(
        combined_market_view=combined_market_view,
        primary_state=primary_state,
        end_year=end_year,
    )

    return {
        'completions': completions,
        'labor': labor,
        'combined_market_view': combined_market_view,
        'market_view_long_augmented': market_view_long_augmented,
        'labor_long': labor_long,
        'labor_aggregated_filtered': labor_aggregated_filtered,
        'unmatched_filtered': unmatched_filtered,
        'saturation': saturation,
        'saturation_caveat': SATURATION_CAVEAT,
        'primary_state': primary_state,
        'label': label,
    }
