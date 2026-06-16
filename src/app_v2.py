"""
app_v2.py — Streamlit web interface for IPEDS Completions + Labor (v2).

Parallel app to v1's `src/app.py`. v1 stays untouched. Run with:
    python3 -m streamlit run src/app_v2.py --server.port 8502
or double-click `Launch IPEDS Tool v2.command` at the project root.

v2 keeps EVERY v1 tab and ADDS labor tabs on top — per
[[feedback-v2-ux-must-match-v1]]. v2 = v1 + labor.

Tabs:
  Institutions             v1
  CAGR by Institution      v1
  Market View              v1 (completions only — long format)
  Labor View               v2 (long format, per CIP × geography × metric)
  Wage Detail              v2 (per CIP × state drilldown)
  Growth & Openings        v2 (BLS national + CA EDD comparison)
  Unmatched CIPs           v2 (CIPs filtered to user's selection)
  Definitions              v1 + labor field definitions
  Disclosure               v2 (vintage + methodology)
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import pandas as pd
import streamlit as st

# Bump pandas Styler cell cap (same as v1's app.py).
pd.set_option('styler.render.max_elements', 5_000_000)

# Above this row count, skip per-cell Styler coloring.
STYLER_ROW_THRESHOLD = 2000

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / 'src'))

from joiner import (  # noqa: E402  type: ignore[import-not-found]
    build_cip_title_lookup,
    build_institution_metadata,
)
from labor import aggregator as labor_aggregator  # noqa: E402
from loader import load_all, load_cip_filter_config, load_years_config  # noqa: E402  type: ignore[import-not-found]
from reporter import (  # noqa: E402  type: ignore[import-not-found]
    AWLEVEL_LABELS, CARNEGIE_LABELS, CONTROL_LABELS, ICLEVEL_LABELS,
)
from reports import combine as reports_combine  # noqa: E402
from reports import writer as reports_writer  # noqa: E402
from reports.labels import rename_for_display  # noqa: E402

# APU brand colors
APU_BRICK_RED = '#A8353A'

# Labor aggregation mode — internal key → (display label, help text)
LABOR_MODE_OPTIONS: Dict[str, Tuple[str, str]] = {
    labor_aggregator.MODE_WEIGHTED: (
        'Employment-weighted (recommended)',
        'For each CIP, combines its linked SOC occupations using national '
        'employment counts as weights. High-employment occupations matter '
        'more in the average — best reflects where graduates actually land.',
    ),
    labor_aggregator.MODE_MEDIAN: (
        'Median of medians (unweighted)',
        'For each CIP, takes the median across linked SOC occupations for '
        'each metric. Treats every linked SOC as equally relevant. Useful '
        'when employment data is missing or you specifically want to avoid '
        'employment-based weighting.',
    ),
    labor_aggregator.MODE_FLAT: (
        'Flat — one row per CIP × SOC',
        'No aggregation. Returns every (CIP, SOC, state) combination as a '
        'separate row. Use for source-data auditing or when you need to see '
        'individual SOC details rather than CIP-level summaries.',
    ),
}
LABOR_MODE_LABEL_TO_KEY = {v[0]: k for k, v in LABOR_MODE_OPTIONS.items()}

# CAGR cell colors (match v1 + Excel)
CAGR_POS_FILL = '#C6EFCE'
CAGR_NEG_FILL = '#FFC7CE'
CAGR_NA_FILL = '#BCBDC0'

YEARS_YAML = PROJECT_ROOT / 'config' / 'years.yaml'
CIP_FILTER_YAML = PROJECT_ROOT / 'config' / 'cip_filter.yaml'
DATA_RAW = PROJECT_ROOT / 'data' / 'raw'
DATA_DICT = PROJECT_ROOT / 'data' / 'dictionary'
REPORTS_DIR = PROJECT_ROOT / 'output' / 'reports'


# ---------------------------------------------------------------------------
# Cached data loaders
# ---------------------------------------------------------------------------

@st.cache_data(show_spinner=False)
def cached_load_v1_data() -> dict:
    years_cfg = load_years_config(YEARS_YAML)
    cip_cfg = load_cip_filter_config(CIP_FILTER_YAML)
    loaded = load_all(years_cfg, raw_dir=DATA_RAW, dict_dir=DATA_DICT)
    metadata = build_institution_metadata(loaded['hd'])
    cip_titles = build_cip_title_lookup(loaded['crosswalk'])
    return {
        'hd': loaded['hd'], 'ca': loaded['ca'],
        'varlist': loaded['varlist'], 'crosswalk': loaded['crosswalk'],
        'metadata': metadata, 'cip_titles': cip_titles,
        'years_cfg': years_cfg, 'cip_cfg': cip_cfg,
    }


@st.cache_data(show_spinner='Loading labor data (cached after first use)…')
def cached_load_labor_layer(primary_state: str, aggregation_mode: str) -> dict:
    """
    Cache the expensive labor load + aggregation by (primary_state, mode).

    This is the performance fix: the four labor sources (OEWS state file is
    ~8 MB) are read and aggregated ONCE per (state, mode) combination per
    session, not on every "Generate" click. Cache key must match the labor
    states that build_combined_dataset derives internally:
      primary_state == 'US'  → states = ['US']
      otherwise              → states = ['US', primary_state]
    """
    states = ['US'] if primary_state == 'US' else ['US', primary_state]
    return reports_combine.build_labor_layer(
        project_root=PROJECT_ROOT,
        states=states,
        aggregation_mode=aggregation_mode,
    )


# ---------------------------------------------------------------------------
# Formatting helpers (mirror v1's app.py)
# ---------------------------------------------------------------------------

def _cip_label(code: str, cip_titles: pd.DataFrame) -> str:
    if cip_titles.empty:
        return code
    hit = cip_titles[cip_titles['CIPCODE'] == code]
    if hit.empty:
        return code
    title = hit.iloc[0].get('CIPTitle')
    return f'{code} — {title}' if title else code


def _awlevel_label(code: int) -> str:
    label = AWLEVEL_LABELS.get(code)
    return f'{code} — {label}' if label else str(code)


def _awlevel_label_or_blank(v) -> str:
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return ''
    try:
        code = int(v)
    except (TypeError, ValueError):
        return str(v)
    label = AWLEVEL_LABELS.get(code)
    return f'{code} — {label}' if label else f'{code}'


def _fmt_pct(v) -> str:
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return 'N/A'
    return f'{v * 100:+.1f}%'


def _fmt_int_blank(v) -> str:
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return ''
    try:
        return f'{int(v):,}'
    except (TypeError, ValueError):
        return str(v)


def _style_cagr_cell(v) -> str:
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return f'background-color: {CAGR_NA_FILL};'
    if isinstance(v, (int, float)):
        if v > 0:
            return f'background-color: {CAGR_POS_FILL};'
        if v < 0:
            return f'background-color: {CAGR_NEG_FILL};'
    return ''


def styled_cagr_table(df: pd.DataFrame) -> 'pd.io.formats.style.Styler':
    sty = df.style
    if 'CAGR' in df.columns:
        sty = sty.applymap(_style_cagr_cell, subset=['CAGR'])
    year_cols = [
        c for c in df.columns
        if c.endswith(' Completions') or c.startswith('CTOTALT_')
    ]
    sty = sty.format({c: _fmt_int_blank for c in year_cols})
    return sty


# ---------------------------------------------------------------------------
# Sidebar
# ---------------------------------------------------------------------------

def render_sidebar(v1_data: dict) -> Optional[dict]:
    metadata: pd.DataFrame = v1_data['metadata']
    cip_titles: pd.DataFrame = v1_data['cip_titles']
    cip_cfg: dict = v1_data['cip_cfg']
    years_cfg: dict = v1_data['years_cfg']

    st.sidebar.header('Selection')

    selection_mode = st.sidebar.radio(
        'Mode',
        options=[
            'All institutions nationally',
            'By state(s)',
            'By institution name',
            'Specific UNITIDs',
            'From institutions.csv config',
        ],
        index=1,
        help='How to choose institutions for the IPEDS slice. '
             'Multi-state selections collapse to "U.S." for the labor join.',
    )

    states_selected: List[str] = []
    name_query = ''
    chosen_unitids: List[int] = []

    if selection_mode == 'All institutions nationally':
        st.sidebar.info(
            'Institutions will be auto-selected based on the **CIP codes** you '
            'choose below — every institution with at least one completion in '
            'those programs across any year. Leave the CIP filter empty to '
            'include every institution with any completions.',
            icon='💡',
        )
    elif selection_mode == 'By state(s)':
        all_states = sorted(metadata['STABBR'].dropna().unique().tolist())
        states_selected = st.sidebar.multiselect(
            'State(s)', options=all_states,
            default=['CA'] if 'CA' in all_states else [],
        )
    elif selection_mode == 'By institution name':
        name_query = st.sidebar.text_input('Search institution name', value='')
    elif selection_mode == 'Specific UNITIDs':
        all_unitids = sorted(int(u) for u in metadata['UNITID'].dropna())
        label_map = {
            f'{u} — {metadata[metadata.UNITID==u].iloc[0]["INSTNM"]}': u
            for u in all_unitids[:200]
        }
        picks = st.sidebar.multiselect(
            'Institutions (first 200 shown)',
            options=list(label_map.keys()),
        )
        chosen_unitids = [label_map[p] for p in picks]

    st.sidebar.divider()
    st.sidebar.header('Program filters')

    cip_label_map = {
        _cip_label(c, cip_titles): c
        for c in sorted(cip_titles['CIPCODE'].dropna().unique())
    } if not cip_titles.empty else {}
    default_cips = []
    for c in cip_cfg.get('cip_codes', []):
        for lbl, code in cip_label_map.items():
            if code == c:
                default_cips.append(lbl)
                break
    cip_picks = st.sidebar.multiselect(
        'CIP codes (leave empty for all)',
        options=list(cip_label_map.keys()),
        default=default_cips,
    )
    cip_codes = [cip_label_map[p] for p in cip_picks]

    aw_label_map = {_awlevel_label(code): code for code in sorted(AWLEVEL_LABELS.keys())}
    default_aws = [_awlevel_label(c) for c in cip_cfg.get('award_levels', []) if c in AWLEVEL_LABELS]
    aw_picks = st.sidebar.multiselect(
        'Award levels (leave empty for all)',
        options=list(aw_label_map.keys()),
        default=default_aws,
    )
    award_levels = [aw_label_map[p] for p in aw_picks]

    include_residual = st.sidebar.checkbox('Include CIP 99 residual rollups', value=False)

    st.sidebar.divider()
    st.sidebar.header('Labor settings')

    # Build the option list using display labels (professional, not lowercase
    # with underscores) and the per-option help text appended via newlines.
    mode_label_options = [v[0] for v in LABOR_MODE_OPTIONS.values()]
    default_label = LABOR_MODE_OPTIONS[labor_aggregator.MODE_WEIGHTED][0]
    mode_label = st.sidebar.selectbox(
        'CIP → SOC aggregation method',
        options=mode_label_options,
        index=mode_label_options.index(default_label),
        help=(
            'The NCES crosswalk maps each CIP to multiple SOC occupations. '
            'Pick how the labor signals (wages, growth, openings) should be '
            'combined across those SOCs when reporting one number per CIP.\n\n'
            '**Employment-weighted (recommended):** weights each SOC by its '
            'national employment count, so common occupations dominate the '
            'average. Closest to "what most graduates actually earn / face."\n\n'
            '**Median of medians (unweighted):** takes the central SOC value '
            'across linked SOCs, treating each equally. Useful if you '
            'distrust employment counts or want a non-weighted view.\n\n'
            '**Flat — one row per CIP × SOC:** no aggregation. Returns every '
            '(CIP, SOC, state) row separately for drilldown / auditing.'
        ),
    )
    aggregation_mode = LABOR_MODE_LABEL_TO_KEY[mode_label]
    # Show the help text for the currently-selected option below the picker.
    _, current_help = LABOR_MODE_OPTIONS[aggregation_mode]
    st.sidebar.caption(current_help)

    st.sidebar.caption(
        f'Years: {years_cfg["years"][0]}–{years_cfg["years"][-1]}  '
        f'(CAGR {years_cfg["cagr_start_year"]} → {years_cfg["cagr_end_year"]})'
    )

    generate = st.sidebar.button(
        'Generate Combined Report', type='primary', use_container_width=True,
    )
    if not generate:
        return None

    return {
        'selection_mode': selection_mode,
        'states': states_selected,
        'name_query': name_query,
        'chosen_unitids': chosen_unitids,
        'cip_codes': cip_codes,
        'award_levels': award_levels,
        'include_residual': include_residual,
        'aggregation_mode': aggregation_mode,
    }


def _resolve_unitids_from_ui(
    selections: dict,
    metadata: pd.DataFrame,
    ca_dict: Optional[Dict[int, pd.DataFrame]] = None,
) -> Tuple[List[int], str]:
    mode = selections['selection_mode']
    if mode == 'All institutions nationally':
        # Auto-pick every institution with at least one completion in the
        # user's CIP filter (or every institution if no CIP filter set).
        if ca_dict is None:
            return [], 'national'
        cip_set = {str(c).strip() for c in (selections.get('cip_codes') or [])} or None
        aw_set = {int(a) for a in (selections.get('award_levels') or [])} or None
        uids: set = set()
        for df in ca_dict.values():
            sub = df
            if 'IS_CIP_6DIGIT' in sub.columns:
                sub = sub[sub['IS_CIP_6DIGIT'] == True]  # noqa: E712
            if cip_set:
                sub = sub[sub['CIPCODE'].isin(cip_set)]
            if aw_set:
                sub = sub[sub['AWLEVEL'].isin(aw_set)]
            uids.update(int(u) for u in sub['UNITID'].dropna())
        return sorted(uids), 'national'
    if mode == 'By state(s)':
        states = selections['states']
        if not states:
            return [], 'custom'
        df = metadata[metadata['STABBR'].isin(states)]
        label = '_'.join(sorted(states))
        return sorted(int(u) for u in df['UNITID'].dropna()), label
    if mode == 'By institution name':
        q = selections['name_query'].strip().lower()
        if not q:
            return [], 'search'
        mask = metadata['INSTNM'].astype(str).str.lower().str.contains(q, na=False, regex=False)
        return sorted(int(u) for u in metadata[mask]['UNITID'].dropna()), 'search'
    if mode == 'Specific UNITIDs':
        return sorted(set(int(u) for u in selections['chosen_unitids'])), 'custom'
    if mode == 'From institutions.csv config':
        csv_path = PROJECT_ROOT / 'config' / 'institutions.csv'
        if not csv_path.exists():
            return [], 'custom'
        cfg = pd.read_csv(csv_path)
        if 'UNITID' not in cfg.columns:
            return [], 'custom'
        ids = [int(u) for u in cfg['UNITID'].dropna()]
        in_meta = set(metadata['UNITID'].dropna().astype(int))
        return sorted(set(ids) & in_meta), 'custom'
    return [], 'custom'


# ---------------------------------------------------------------------------
# Tab renderers
# ---------------------------------------------------------------------------

def _render_institutions_tab(institutions_view: pd.DataFrame, n_selected: int) -> None:
    n_with_data = len(institutions_view)
    no_data = n_selected - n_with_data
    if no_data > 0:
        st.caption(
            f'You selected **{n_selected:,}** institutions; **{n_with_data:,}** of them '
            f'had at least one completion in your filtered CIP × Award Level. '
            f'{no_data:,} selected institutions had no matching completions and are not shown.'
        )
    else:
        st.caption(
            f'All **{n_with_data:,}** selected institutions had at least one matching completion.'
        )
    view = institutions_view.copy()
    if 'CONTROL' in view.columns:
        view['Control'] = view['CONTROL'].map(CONTROL_LABELS)
    if 'ICLEVEL' in view.columns:
        view['Level'] = view['ICLEVEL'].map(ICLEVEL_LABELS)
    if 'CARNEGIE' in view.columns:
        view['Carnegie Classification'] = view['CARNEGIE'].map(CARNEGIE_LABELS)
    show_cols = [c for c in (
        'UNITID', 'INSTNM', 'STABBR', 'CITY',
        'Control', 'Level', 'CARNEGIE', 'Carnegie Classification',
        'HD_SOURCE_YEAR',
    ) if c in view.columns]
    st.dataframe(
        view[show_cols].rename(columns={
            'INSTNM': 'Institution', 'STABBR': 'State', 'CITY': 'City',
            'CARNEGIE': 'Carnegie Code', 'HD_SOURCE_YEAR': 'HD Source Year',
        }).sort_values('UNITID').reset_index(drop=True),
        hide_index=True, use_container_width=True,
    )


def _render_cagr_tab(cagr_df: pd.DataFrame, start_year: int, end_year: int) -> None:
    if cagr_df.empty:
        st.info('No rows match your filters. Try widening the CIP or award-level selection.')
        return
    flag_counts = cagr_df['Flag'].value_counts().to_dict()
    st.caption('  •  '.join(f'{k}: {v}' for k, v in flag_counts.items()))
    display = cagr_df.copy()
    rename = {f'CTOTALT_{y}': f'{y} Completions' for y in range(start_year, end_year + 1)}
    rename.update({
        'INSTNM': 'Institution', 'STABBR': 'State',
        'CIPTitle': 'CIP Title', 'AWLEVEL': 'Award Level',
    })
    display = display.rename(columns=rename)
    if 'Award Level' in display.columns:
        display['Award Level'] = display['Award Level'].apply(_awlevel_label_or_blank)
    if 'CAGR' in display.columns:
        display['CAGR'] = pd.to_numeric(display['CAGR'], errors='coerce') * 100
    cagr_col_config = {
        'CAGR': st.column_config.NumberColumn(
            'CAGR', help=f'{start_year} → {end_year} CAGR of completions.',
            format='%+.1f%%',
        ),
    }
    if len(display) > STYLER_ROW_THRESHOLD:
        st.caption(f'⚡ {len(display):,} rows — coloring disabled for perf. Excel keeps colors.')
        st.dataframe(display, hide_index=True, use_container_width=True, column_config=cagr_col_config)
    else:
        st.dataframe(
            styled_cagr_table(display),
            hide_index=True, use_container_width=True, column_config=cagr_col_config,
        )


def _render_market_view_tab(mv_long: pd.DataFrame, start_year: int, end_year: int) -> None:
    st.caption(
        f'Six rows per program (CIP × Award Level), grouped Selected then National. '
        f'For each: **Completions**, **Programs (degree-conferring)** = institutions that '
        f'conferred ≥1 degree, and **Programs (all reported, incl. 0 graduates)** = '
        f'institutions that reported the program even if no one graduated yet (captures new '
        f'programs pre-first-cohort; counted per year, so closed programs drop out). '
        f'The CAGR column is the {start_year}→{end_year} CAGR for that row\'s metric.'
    )
    if mv_long.empty:
        st.info('No rows match your filters.')
        return
    display = mv_long.copy()
    if 'Award Level' in display.columns:
        display['Award Level'] = display['Award Level'].apply(_awlevel_label_or_blank)
    if 'CAGR' in display.columns:
        display['CAGR'] = pd.to_numeric(display['CAGR'], errors='coerce') * 100
    col_config = {
        'CAGR': st.column_config.NumberColumn(
            'CAGR', help=f'{start_year} → {end_year} CAGR.', format='%+.1f%%',
        ),
    }
    year_cols = [c for c in display.columns if str(c).isdigit()]
    if len(display) > STYLER_ROW_THRESHOLD:
        st.caption(f'⚡ {len(display):,} rows — gradient disabled for perf. Excel keeps it.')
        st.dataframe(display, hide_index=True, use_container_width=True, column_config=col_config)
    else:
        sty = display.style
        if 'CAGR' in display.columns:
            sty = sty.background_gradient(subset=['CAGR'], cmap='RdYlGn', vmin=-30, vmax=30)
        sty = sty.format({c: _fmt_int_blank for c in year_cols})
        st.dataframe(sty, hide_index=True, use_container_width=True, column_config=col_config)


def _render_labor_long_tab(labor_long: pd.DataFrame, primary_state: str) -> None:
    st.caption(
        f'One row per (CIP × Geography × Source × Metric). Geography shows U.S. (national) '
        f'and the primary state ({primary_state}). Labor data is SOC-keyed so it '
        f'does NOT vary by award level — values shown apply across all degree levels '
        f'of a given CIP.\n\n'
        f'**Note on the two "Median Annual Wage" rows:** the one with **Source = BLS OEWS** '
        f'is the current authoritative wage (May 2025, available by state) — use this. The one '
        f'with **Source = BLS Projections** is the national 2024 base-year wage the projection '
        f'model used — close but a year older and national-only (context, not a second estimate).'
    )
    if labor_long.empty:
        st.info(
            'No labor rows match your CIP filter. Either no CIPs were selected, '
            'or the selected CIPs have no SOC mapping (see Unmatched CIPs tab).'
        )
        return
    display = labor_long.copy()
    # Format Value column nicely by Metric type
    def _format_row(row):
        v = row['Value']
        m = row['Metric']
        if pd.isna(v):
            return ''
        if 'Growth' in m:
            return f'{v * 100:+.1f}%'
        if 'Wage' in m:
            return f'${v:,.0f}'
        if 'Openings' in m:
            return f'{v:,.0f}'
        if 'Employment' in m:
            return f'{v:,.0f}'
        return f'{v:,.2f}'

    display['Display Value'] = display.apply(_format_row, axis=1)
    show_cols = ['CIP Code', 'CIP Title', 'Geography', 'Source', 'Metric',
                 'Display Value', '# Linked SOCs']
    st.dataframe(
        display[show_cols].rename(columns={'Display Value': 'Value'}),
        hide_index=True, use_container_width=True,
    )


def _styled(df: pd.DataFrame, fmt_map: dict) -> 'pd.io.formats.style.Styler':
    """
    Display a DataFrame with thousands separators / currency / percent via a
    pandas Styler. Styler.format is display-only, so columns stay numerically
    sortable in st.dataframe. Use '{:,.0f}' / '${:,.0f}' for counts/wages and
    '{:+.1%}' for DECIMAL growth rates (0.052 → '+5.2%'). NaN renders blank.
    """
    fmt_map = {k: v for k, v in fmt_map.items() if k in df.columns}
    return df.style.format(fmt_map, na_rep='')


def _render_wage_detail_tab(labor_aggregated: pd.DataFrame) -> None:
    if labor_aggregated.empty:
        st.info('No wage rows match your CIP filter.')
        return
    cols = [
        'CIPCODE', 'CIPTitle', 'PRIM_STATE', 'soc_count',
        'wage_a_median', 'wage_a_mean', 'wage_a_pct10', 'wage_a_pct90',
        'tot_emp', 'n_suppressed_wage',
    ]
    cols = [c for c in cols if c in labor_aggregated.columns]
    display = rename_for_display(labor_aggregated[cols])
    st.caption('Wages from BLS OEWS (May 2025), aggregated across each CIP\'s linked SOC occupations.')
    st.dataframe(
        _styled(display, {
            'Annual Median Wage': '${:,.0f}',
            'Annual Mean Wage': '${:,.0f}',
            'Wage — 10th Percentile': '${:,.0f}',
            'Wage — 90th Percentile': '${:,.0f}',
            'Total Employment': '{:,.0f}',
            '# of Linked SOC Occupations': '{:,.0f}',
            '# Suppressed Wage Cells': '{:,.0f}',
        }),
        hide_index=True, use_container_width=True,
    )


def _render_saturation_tab(saturation: pd.DataFrame, caveat: str) -> None:
    st.warning(caveat, icon='⚠️')
    if saturation.empty:
        st.info('No saturation rows — pick at least one CIP with a SOC mapping.')
        return
    st.dataframe(
        _styled(saturation.copy(), {
            'Annual Completions': '{:,.0f}',
            'Annual Openings': '{:,.0f}',
            'Completions per Opening': '{:,.2f}',
            'Median Annual Wage': '${:,.0f}',
        }),
        hide_index=True, use_container_width=True,
    )
    st.caption(
        '**Completions per Opening** = annual completions ÷ annual openings '
        '(>1 = more graduates than annual openings; <1 = fewer). The Reading '
        'column describes the arithmetic, not a recommendation — a program with '
        '"more completions than openings" may still be worth growing (graduates '
        'may leave the state, fill adjacent occupations, or pursue further study).'
    )


def _render_growth_openings_tab(labor_aggregated: pd.DataFrame) -> None:
    if labor_aggregated.empty:
        st.info('No labor rows match your CIP filter.')
        return
    st.caption(
        'BLS columns are national 10-year projections (2024–2034). CA columns are '
        'CA EDD 10-year projections (2023–2033). Growth is the total % change over '
        'the window; openings are annual averages (absolute counts). Vintages do '
        'not align — see Disclosure.'
    )
    cols = [
        'CIPCODE', 'CIPTitle', 'PRIM_STATE',
        'bls_employment_change_pct', 'bls_openings_annual_avg',
        'ca_employment_change_pct', 'ca_openings_annual_avg',
        'ca_median_annual_wage',
    ]
    cols = [c for c in cols if c in labor_aggregated.columns]
    raw = labor_aggregated[cols].copy()
    # Normalize BLS openings from thousands → absolute so it's comparable to CA.
    if 'bls_openings_annual_avg' in raw.columns:
        raw['bls_openings_annual_avg'] = raw['bls_openings_annual_avg'] * 1000.0
    display = rename_for_display(raw)
    st.dataframe(
        _styled(display, {
            'Projected 10-yr Growth % (BLS)': '{:+.1%}',
            'Annual Openings (BLS national)': '{:,.0f}',
            'Projected 10-yr Growth % (CA)': '{:+.1%}',
            'Annual Openings (CA)': '{:,.0f}',
            'Median Annual Wage (CA)': '${:,.0f}',
        }),
        hide_index=True, use_container_width=True,
    )


def _render_unmatched_tab(unmatched: List[str], cip_titles: pd.DataFrame, cip_filter: List[str]) -> None:
    if cip_filter and not unmatched:
        st.success(
            'All of your selected CIPs have SOC mappings in the NCES crosswalk.'
        )
        return
    if not unmatched:
        st.info('No unmatched CIPs (none of the crosswalk\'s 194 unmatched CIPs were selected).')
        return
    title_lookup = dict(zip(cip_titles['CIPCODE'], cip_titles['CIPTitle']))
    rows = [{'CIP Code': c, 'CIP Title': title_lookup.get(c, ''),
             'Note': 'No SOC mapping in the NCES CIP-SOC crosswalk'}
            for c in unmatched]
    st.caption(
        f'{len(rows)} CIP code(s) in your selection have no SOC mapping and '
        f'therefore have no labor signal in this report.'
    )
    st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)


def _render_definitions_tab(varlist_df: pd.DataFrame) -> None:
    st.caption(
        'IPEDS variable labels + Award Level / Control / ICLEVEL / Carnegie codes + '
        'CAGR flag meanings + labor source field definitions.'
    )
    rows = []
    if not varlist_df.empty:
        for _, r in varlist_df.iterrows():
            rows.append({'Category': 'IPEDS Variable',
                         'Code': str(r.get('varName') or ''),
                         'Label': str(r.get('varTitle') or ''),
                         'Source': 'IPEDS varlist'})
    for code, label in AWLEVEL_LABELS.items():
        rows.append({'Category': 'Award Level', 'Code': str(code), 'Label': label,
                     'Source': 'SPEC §AWLEVEL + IPEDS legacy codes'})
    for code, label in CONTROL_LABELS.items():
        rows.append({'Category': 'Control', 'Code': str(code), 'Label': label, 'Source': 'SPEC §HD'})
    for code, label in ICLEVEL_LABELS.items():
        rows.append({'Category': 'Level (ICLEVEL)', 'Code': str(code), 'Label': label, 'Source': 'SPEC §HD'})
    for code in sorted(CARNEGIE_LABELS.keys()):
        rows.append({'Category': 'Carnegie Classification', 'Code': str(code),
                     'Label': CARNEGIE_LABELS[code],
                     'Source': '2021 Carnegie Basic Classification'})
    for code, desc in [
        ('OK', 'Both endpoints > 0; CAGR computed normally.'),
        ('New Program', 'Start completions = 0; CAGR undefined.'),
        ('Program Ended', 'Start > 0 and end = 0; CAGR shown as -100%.'),
        ('Missing Data', 'Either endpoint is suppressed (<3) or absent; CAGR N/A.'),
    ]:
        rows.append({'Category': 'CAGR Flag', 'Code': code, 'Label': desc,
                     'Source': 'SPEC §Calculations'})
    # Labor source field definitions
    for label, desc, source in [
        ('Median Annual Wage — Source: BLS OEWS',
         'Current 50th-percentile annual wage, available by state. The authoritative wage — '
         'USE THIS for wage analysis. In Labor View it appears with Source = "BLS OEWS".',
         'BLS OEWS May 2025'),
        ('Median Annual Wage — Source: BLS Projections',
         'National base-year (2024) wage carried inside the projection dataset. Close to OEWS '
         'but a year older and national-only — it is reference/context for the projection, '
         'not a second wage estimate. In Labor View it appears with Source = "BLS Projections".',
         'BLS Projections 2024-2034'),
        ('Total Employment', 'Detailed-occupation employment count from OEWS.', 'BLS OEWS May 2025'),
        ('Projected 10-yr Growth % (BLS)', 'National projected % change in employment, base→target year (shown as %).', 'BLS Projections 2024-2034'),
        ('Annual Openings (BLS national)', 'National avg annual openings (growth + exits + transfers), absolute count.', 'BLS Projections 2024-2034'),
        ('Projected 10-yr Growth % (CA)', 'California-only projected % change in employment.', 'CA EDD 2023-2033'),
        ('Annual Openings (CA)', 'CA annual openings = EDD 10-yr total ÷ 10, absolute count.', 'CA EDD 2023-2033'),
        ('Median Annual Wage (CA)', 'EDD CA median annual wage (Q1 2025 reference).', 'CA EDD 2023-2033'),
        ('State Population', 'Total state population.', 'Census ACS 5-year 2023'),
        ('State Population 18–24', 'State 18-to-24 age cohort.', 'Census ACS 5-year 2023'),
        ("State Adults 25+ w/ Bachelor's+", "Share of 25+ population w/ Bachelor's degree or higher.", 'Census ACS 5-year 2023'),
    ]:
        rows.append({'Category': 'Labor Field', 'Code': label, 'Label': desc, 'Source': source})
    st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)


def _render_disclosure_tab(vintage: dict, saturation_caveat: str = '') -> None:
    st.markdown('**Vintage map**')
    st.dataframe(
        pd.DataFrame([
            ('OEWS', vintage.get('oews', '')),
            ('BLS Projections', vintage.get('projections', '')),
            ('CA EDD', vintage.get('edd', '')),
            ('Census ACS', vintage.get('census', '')),
            ('CIP-SOC crosswalk', vintage.get('crosswalk', '')),
            ('Aggregation mode', vintage.get('aggregation_mode', '')),
        ], columns=['Source', 'Vintage']),
        hide_index=True, use_container_width=True,
    )
    st.markdown('**Footer disclosure (paste into any report citing this data)**')
    st.info(vintage.get('disclosure', ''))
    if saturation_caveat:
        st.markdown('**Saturation ratio caveat**')
        st.warning(saturation_caveat, icon='⚠️')


# ---------------------------------------------------------------------------
# Main rendering
# ---------------------------------------------------------------------------

def render_results(selections: dict, v1_data: dict) -> None:
    metadata = v1_data['metadata']
    cip_titles = v1_data['cip_titles']
    ca_dict = v1_data['ca']
    unitids, label = _resolve_unitids_from_ui(selections, metadata, ca_dict=ca_dict)
    if not unitids:
        st.warning('No institutions match the current selection.')
        return

    states = selections['states']
    primary_state = states[0].upper() if len(states) == 1 else 'US'

    # Guard: a national query with no CIP filter pulls every institution with
    # any completion (~6,000) and every program — slow and rarely intended.
    if (selections['selection_mode'] == 'All institutions nationally'
            and not selections['cip_codes']):
        st.warning(
            'You picked **All institutions nationally** with **no CIP filter**. '
            'That pulls every institution with any completion (~6,000) across '
            'every program — the report will be large and slow to build. '
            'Add one or more CIP codes in the sidebar to scope it down.',
            icon='⚠️',
        )

    # Pre-loaded data (cached) → fast repeat queries. The IPEDS CSVs and the
    # labor sources are read once per session, not per Generate click.
    labor_layer = cached_load_labor_layer(primary_state, selections['aggregation_mode'])

    with st.spinner('Building combined dataset (v1 completions + v2 labor)…'):
        dataset = reports_combine.build_combined_dataset(
            unitids=unitids,
            cip_codes=selections['cip_codes'] or None,
            award_levels=selections['award_levels'] or None,
            states=states if states else None,
            include_residual=selections['include_residual'],
            aggregation_mode=selections['aggregation_mode'],
            project_root=PROJECT_ROOT,
            label=label,
            quiet=True,
            v1_data=v1_data,
            labor_layer=labor_layer,
        )

    st.caption(
        f'Selection: **{len(unitids):,} institutions**  ·  '
        f'Primary geography: **{primary_state}**  ·  '
        f'Labor mode: **{selections["aggregation_mode"]}**'
    )

    with st.spinner('Writing Excel workbook…'):
        out_path = reports_writer.write_combined_workbook(dataset, REPORTS_DIR)
    st.download_button(
        label=f'⬇ Download Combined Excel  ({out_path.name})',
        data=out_path.read_bytes(),
        file_name=out_path.name,
        mime='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        use_container_width=True,
    )
    st.caption('Use the download button above to save the workbook to your computer.')

    completions = dataset['completions']
    labor_long = dataset.get('labor_long', pd.DataFrame())
    labor_filtered = dataset.get('labor_aggregated_filtered', labor_long)
    unmatched_filtered = dataset.get('unmatched_filtered', [])
    saturation = dataset.get('saturation', pd.DataFrame())
    saturation_caveat = dataset.get('saturation_caveat', '')
    vintage = dataset['labor']['vintage']
    years_cfg = completions['years_cfg']
    start_year = years_cfg['cagr_start_year']
    end_year = years_cfg['cagr_end_year']
    n_selected = len(unitids)

    tabs = st.tabs([
        'Institutions',
        'CAGR by Institution',
        'Market View',
        'Labor View',
        'Wage Detail',
        'Growth & Openings',
        'Saturation',
        'Unmatched CIPs',
        'Definitions',
        'Disclosure',
    ])
    with tabs[0]:
        st.subheader('Institutions with completions in your filtered programs')
        _render_institutions_tab(completions['institutions_view'], n_selected)
    with tabs[1]:
        st.subheader('CAGR by institution × CIP × award level')
        _render_cagr_tab(completions['cagr_df'], start_year, end_year)
    with tabs[2]:
        st.subheader('Market view — selected institutions vs. national')
        market_view_long = dataset.get(
            'market_view_long_augmented', completions['market_view_long'],
        )
        _render_market_view_tab(market_view_long, start_year, end_year)
    with tabs[3]:
        st.subheader('Labor view — long format, filtered to your CIPs')
        _render_labor_long_tab(labor_long, primary_state)
    with tabs[4]:
        st.subheader('Wage detail — per CIP × state')
        _render_wage_detail_tab(labor_filtered)
    with tabs[5]:
        st.subheader('Growth & openings — BLS national vs CA EDD')
        _render_growth_openings_tab(labor_filtered)
    with tabs[6]:
        st.subheader('Saturation — completions vs. labor openings')
        _render_saturation_tab(saturation, saturation_caveat)
    with tabs[7]:
        st.subheader('Unmatched CIPs')
        _render_unmatched_tab(unmatched_filtered, cip_titles, selections['cip_codes'])
    with tabs[8]:
        st.subheader('Definitions')
        _render_definitions_tab(completions['varlist_df'])
    with tabs[9]:
        st.subheader('Disclosure & methodology')
        _render_disclosure_tab(vintage, saturation_caveat)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    st.set_page_config(
        page_title='APU · IPEDS + Labor (v2)',
        page_icon='📊',
        layout='wide',
        initial_sidebar_state='expanded',
    )
    st.markdown(
        f"<h1 style='color: {APU_BRICK_RED}; margin-bottom: 0;'>"
        "IPEDS Completions + Labor Market</h1>"
        "<p style='color: #77787B; margin-top: 0.25rem;'>"
        "Azusa Pacific University · Strategic Planning · v2 (parallel to v1)</p>",
        unsafe_allow_html=True,
    )

    with st.spinner('Loading IPEDS data (one-time per session, ~10 seconds)…'):
        v1_data = cached_load_v1_data()

    selections = render_sidebar(v1_data)
    if selections is None:
        st.info(
            'Pick a selection mode + program filters in the sidebar, then click '
            '**Generate Combined Report**. v2 includes every v1 tab (Institutions, '
            'CAGR, Market View, Definitions) PLUS new labor tabs.'
        )
        return
    render_results(selections, v1_data)


if __name__ == '__main__':
    main()
