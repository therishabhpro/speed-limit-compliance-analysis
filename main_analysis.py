"""
================================================================================
Speed Limit Compliance Analysis Across Roadway Functional Classes
================================================================================
University Safety Engineering Project — Full Analysis Pipeline
Uses BPR/Greenshields speed-flow model (Option B) to derive estimated operating
speeds from 15-minute ATR volume counts, then analyzes compliance by HPMS
functional class.

Dependencies: pandas, geopandas, pyogrio, scipy, statsmodels, matplotlib, seaborn,
              shapely, openpyxl, numpy
================================================================================
"""

import os
import sys
import warnings
import numpy as np
import pandas as pd
import geopandas as gpd
from shapely import wkt
from scipy import stats
from statsmodels.stats.multicomp import pairwise_tukeyhsd
import matplotlib
matplotlib.use('Agg')  # Non-interactive backend for saving PNGs
import matplotlib.pyplot as plt
import seaborn as sns

warnings.filterwarnings('ignore')

# Force UTF-8 output on Windows to avoid cp1252 encoding errors
if sys.stdout.encoding != 'utf-8':
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
if sys.stderr.encoding != 'utf-8':
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')

# ──────────────────────────────────────────────────────────────────────────────
# CONFIGURATION
# ──────────────────────────────────────────────────────────────────────────────
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, 'data')
OUTPUT_DIR = os.path.join(BASE_DIR, 'outputs', 'figures')
os.makedirs(OUTPUT_DIR, exist_ok=True)

def _resolve_data_path(filename):
    """Locate dataset file in data/ directory first, falling back to repository root."""
    in_data = os.path.join(DATA_DIR, filename)
    if os.path.exists(in_data):
        return in_data
    in_base = os.path.join(BASE_DIR, filename)
    if os.path.exists(in_base):
        return in_base
    return in_data

ATR_CSV = _resolve_data_path('Automated_Traffic_Volume_Counts_20260903.csv')
ERROR_XLSX = _resolve_data_path('ATR_Location_Errors2025.xlsx')
HPMS_GDB = _resolve_data_path('HPMS2024.gdb')
HPMS_LAYER = 'HPMS_FULL_NY_2024'

# NYC FIPS county codes: Bronx=5, Brooklyn=47, Manhattan=61, Queens=81, StatenIsland=85
NYC_COUNTIES = [5, 47, 61, 81, 85]

# FHWA Functional Classification labels
FSYSTEM_LABELS = {
    1: 'Interstate',
    2: 'Freeways/Expressways',
    3: 'Principal Arterial',
    4: 'Minor Arterial',
    5: 'Major Collector',
    6: 'Minor Collector',
    7: 'Local'
}

# NYC peak hours: AM 7–10, PM 4–7
PEAK_HOURS = list(range(7, 10)) + list(range(16, 19))

# BPR (Bureau of Public Roads) model parameters
# Speed = FreeFlowSpeed / (1 + alpha * (V/C)^beta)
BPR_ALPHA = 0.15
BPR_BETA = 4.0

# Default free-flow speeds (mph) by functional class (if speed_limit missing)
DEFAULT_FFS = {
    1: 55,   # Interstate
    2: 50,   # Freeways/Expressways
    3: 35,   # Principal Arterial
    4: 30,   # Minor Arterial
    5: 30,   # Major Collector
    6: 25,   # Minor Collector
    7: 25    # Local
}

# Capacity per lane per hour (passenger car equivalents) by functional class
CAPACITY_PER_LANE = {
    1: 2200,  # Interstate
    2: 2000,  # Freeways/Expressways
    3: 1800,  # Principal Arterial
    4: 1600,  # Minor Arterial
    5: 1400,  # Major Collector
    6: 1200,  # Minor Collector
    7: 1000   # Local
}

# Default through-lanes by functional class (if missing in HPMS)
DEFAULT_LANES = {
    1: 4, 2: 4, 3: 2, 4: 2, 5: 2, 6: 2, 7: 2
}

# Critical safety threshold: speeds exceeding posted limit by this amount (mph)
# Using 5 mph — aligned with NYC Vision Zero enforcement standards
CRITICAL_THRESHOLD_MPH = 5

# Plotting aesthetics
sns.set_theme(style='whitegrid', font_scale=1.15)
plt.rcParams.update({
    'figure.dpi': 300,
    'savefig.dpi': 300,
    'figure.figsize': (12, 7),
    'font.family': 'sans-serif',
    'axes.titleweight': 'bold',
})


def print_header(title):
    """Print a formatted section header."""
    width = 78
    print('\n' + '=' * width)
    print(f'  {title}')
    print('=' * width)


def print_subheader(title):
    """Print a formatted subsection header."""
    print(f'\n--- {title} ---')


# ==============================================================================
# STEP 1: DATA PREPARATION & QUALITY ASSESSMENT
# ==============================================================================
def step1_data_preparation():
    """Load, clean, filter, and spatially join ATR + HPMS data."""

    print_header('STEP 1: DATA PREPARATION & QUALITY ASSESSMENT')

    # ── 1a. Read bad sensor locations ─────────────────────────────────────────
    print_subheader('1a. Loading bad sensor locations from ATR_Location_Errors2025.xlsx')
    errors_raw = pd.read_excel(ERROR_XLSX, header=None, skiprows=1)
    errors_raw.columns = ['drop', 'RequestID', 'SegmentID', 'street',
                          'fromSt', 'toSt', 'Comment']
    errors_df = errors_raw.drop(columns=['drop']).iloc[1:]  # skip header row
    errors_df['RequestID'] = pd.to_numeric(errors_df['RequestID'], errors='coerce')
    errors_df['SegmentID'] = pd.to_numeric(errors_df['SegmentID'], errors='coerce')

    bad_request_ids = set(errors_df['RequestID'].dropna().astype(int).tolist())
    bad_segment_ids = set(errors_df['SegmentID'].dropna().astype(int).tolist())
    print(f'  Bad RequestIDs to filter: {len(bad_request_ids)}')
    print(f'  Bad SegmentIDs to filter: {len(bad_segment_ids)}')

    # ── 1b. Load ATR CSV and filter ───────────────────────────────────────────
    print_subheader('1b. Loading ATR traffic volume data')
    atr = pd.read_csv(ATR_CSV)
    initial_rows = len(atr)
    print(f'  Raw rows loaded: {initial_rows:,}')

    # Convert Vol to numeric
    atr['Vol'] = pd.to_numeric(atr['Vol'], errors='coerce')

    # Missing values summary before cleaning
    print(f'\n  Missing values BEFORE cleaning:')
    missing = atr.isnull().sum()
    for col in missing[missing > 0].index:
        print(f'    {col}: {missing[col]:,} ({100*missing[col]/len(atr):.2f}%)')

    # Drop rows matching bad sensor locations (by RequestID OR SegmentID)
    mask_bad = atr['RequestID'].isin(bad_request_ids) | atr['SegmentID'].isin(bad_segment_ids)
    n_bad = mask_bad.sum()
    atr = atr[~mask_bad].copy()
    print(f'\n  Rows removed (bad sensor locations): {n_bad:,}')

    # Drop rows with missing Vol
    n_vol_na = atr['Vol'].isna().sum()
    atr = atr.dropna(subset=['Vol'])
    print(f'  Rows removed (non-numeric Vol): {n_vol_na:,}')

    # Drop rows with missing geometry
    n_geom_na = atr['WktGeom'].isna().sum()
    atr = atr.dropna(subset=['WktGeom'])
    print(f'  Rows removed (missing WktGeom): {n_geom_na:,}')

    # Drop exact duplicates
    n_before_dedup = len(atr)
    atr = atr.drop_duplicates()
    n_dupes = n_before_dedup - len(atr)
    print(f'  Duplicate rows removed: {n_dupes:,}')

    final_rows = len(atr)
    print(f'\n  ✓ Final ATR row count: {final_rows:,}')
    print(f'  ✓ Unique sensors (SegmentID): {atr["SegmentID"].nunique():,}')
    print(f'  ✓ Year range: {atr["Yr"].min()} – {atr["Yr"].max()}')

    # ── 1c. Load HPMS NY data (NYC counties only) ────────────────────────────
    print_subheader('1c. Loading HPMS New York geodatabase (NYC counties only)')
    print(f'  Reading layer: {HPMS_LAYER}')
    print(f'  Filtering to NYC county IDs: {NYC_COUNTIES}')

    hpms = gpd.read_file(
        HPMS_GDB,
        layer=HPMS_LAYER,
        columns=['f_system', 'speed_limit', 'county_id', 'aadt',
                 'through_lanes', 'facility_type'],
        engine='pyogrio',
        where=f"county_id IN ({','.join(str(c) for c in NYC_COUNTIES)})"
    )
    print(f'  HPMS NYC segments loaded: {len(hpms):,}')
    print(f'  CRS: {hpms.crs}')

    # Drop HPMS rows with no geometry
    n_no_geom = hpms.geometry.isna().sum()
    hpms = hpms.dropna(subset=['geometry'])
    print(f'  HPMS rows with null geometry dropped: {n_no_geom:,}')

    # Drop HPMS rows with no f_system (unclassified)
    n_no_fsys = hpms['f_system'].isna().sum()
    hpms = hpms.dropna(subset=['f_system'])
    hpms['f_system'] = hpms['f_system'].astype(int)
    print(f'  HPMS rows with null f_system dropped: {n_no_fsys:,}')

    # Fill missing speed_limit from defaults
    missing_sl = hpms['speed_limit'].isna().sum()
    hpms['speed_limit'] = hpms.apply(
        lambda r: r['speed_limit'] if pd.notna(r['speed_limit'])
        else DEFAULT_FFS.get(int(r['f_system']), 25),
        axis=1
    )
    print(f'  HPMS speed_limit imputed from defaults: {missing_sl:,}')

    # Fill missing through_lanes from defaults
    missing_lanes = hpms['through_lanes'].isna().sum() + (hpms['through_lanes'] == 0).sum()
    hpms['through_lanes'] = hpms.apply(
        lambda r: r['through_lanes'] if pd.notna(r['through_lanes']) and r['through_lanes'] > 0
        else DEFAULT_LANES.get(int(r['f_system']), 2),
        axis=1
    )
    print(f'  HPMS through_lanes imputed from defaults: {missing_lanes:,}')

    print(f'\n  ✓ Final HPMS NYC segments: {len(hpms):,}')

    # ── 1d. Spatial join: ATR → HPMS ─────────────────────────────────────────
    print_subheader('1d. Spatial join — mapping ATR sensors to HPMS road segments')

    # Get unique ATR sensor locations (avoid joining 1.8M rows)
    sensor_locs = atr[['SegmentID', 'WktGeom']].drop_duplicates(subset='SegmentID')
    print(f'  Unique ATR sensor locations: {len(sensor_locs):,}')

    # Convert WKT to geometry (ATR is in EPSG:2263 — NY State Plane, feet)
    sensor_locs['geometry'] = sensor_locs['WktGeom'].apply(
        lambda w: wkt.loads(w) if pd.notna(w) else None
    )
    sensor_locs = sensor_locs.dropna(subset=['geometry'])
    sensors_gdf = gpd.GeoDataFrame(sensor_locs, geometry='geometry', crs='EPSG:2263')

    # Reproject both to UTM Zone 18N (meters) for accurate distance matching
    print('  Reprojecting ATR sensors: EPSG:2263 → EPSG:32618 (UTM 18N)')
    sensors_gdf = sensors_gdf.to_crs('EPSG:32618')
    print('  Reprojecting HPMS roads:  EPSG:4326 → EPSG:32618 (UTM 18N)')
    hpms_proj = hpms.to_crs('EPSG:32618')

    # Nearest spatial join (max distance 200m)
    print('  Running sjoin_nearest (max_distance=200m)...')
    joined = gpd.sjoin_nearest(
        sensors_gdf[['SegmentID', 'geometry']],
        hpms_proj[['f_system', 'speed_limit', 'through_lanes', 'aadt', 'geometry']],
        how='left',
        max_distance=200,
        distance_col='join_dist_m'
    )

    # Remove duplicate SegmentID matches (keep nearest)
    joined = joined.sort_values('join_dist_m').drop_duplicates(subset='SegmentID', keep='first')

    n_matched = joined['f_system'].notna().sum()
    n_unmatched = joined['f_system'].isna().sum()
    print(f'  ✓ Sensors matched to HPMS road: {n_matched:,}')
    print(f'  ✗ Sensors unmatched (no road within 200m): {n_unmatched:,} — DROPPED')

    if n_unmatched > 0:
        unmatched_ids = joined.loc[joined['f_system'].isna(), 'SegmentID'].tolist()
        print(f'    Unmatched SegmentIDs (sample): {unmatched_ids[:10]}')

    # Drop unmatched and keep lookup table
    joined = joined.dropna(subset=['f_system'])
    joined['f_system'] = joined['f_system'].astype(int)
    lookup = joined[['SegmentID', 'f_system', 'speed_limit',
                      'through_lanes', 'aadt', 'join_dist_m']].copy()

    # ── 1e. Merge lookup back to full ATR data ────────────────────────────────
    print_subheader('1e. Merging HPMS attributes into ATR volume data')
    atr = atr.merge(lookup, on='SegmentID', how='inner')
    print(f'  ✓ Final merged dataset rows: {len(atr):,}')
    print(f'  ✓ Unique sensors in final dataset: {atr["SegmentID"].nunique():,}')

    # Add functional class label
    atr['FunctionalClass'] = atr['f_system'].map(FSYSTEM_LABELS)

    # Add peak/off-peak classification
    atr['is_peak'] = atr['HH'].isin(PEAK_HOURS)
    atr['period'] = atr['is_peak'].map({True: 'Peak', False: 'Off-Peak'})

    print(f'\n  Functional class distribution in merged data:')
    fc_counts = atr.groupby('FunctionalClass')['SegmentID'].count()
    for fc, cnt in fc_counts.sort_values(ascending=False).items():
        print(f'    {fc}: {cnt:,} records')

    return atr


# ==============================================================================
# STEP 2: BPR SPEED DERIVATION & EXPLORATORY ANALYSIS
# ==============================================================================
def step2_speed_derivation_and_eda(atr):
    """
    Apply BPR speed-flow model to derive estimated operating speeds,
    then compute descriptive statistics and generate visualizations.
    """

    print_header('STEP 2: SPEED DERIVATION (BPR MODEL) & EXPLORATORY ANALYSIS')

    # ── 2a. BPR speed estimation ──────────────────────────────────────────────
    print_subheader('2a. Applying BPR speed-flow model')
    print(f'  BPR formula: Speed = FFS / (1 + {BPR_ALPHA} x (V/C)^{BPR_BETA})')
    print(f'  Where FFS = speed_limit x 1.35 (NCHRP Report 855, urban free-flow adjustment)')

    # Free-flow speed = posted speed limit * 1.35
    # Per NCHRP Report 855: observed free-flow speeds on urban roads are typically
    # 30-50% above posted limits due to driver behavior and road geometry
    atr['free_flow_speed'] = atr['speed_limit'] * 1.35

    # Capacity = lanes × capacity_per_lane_per_hour / 4 (for 15-min interval)
    atr['capacity_15min'] = atr.apply(
        lambda r: r['through_lanes'] * CAPACITY_PER_LANE.get(int(r['f_system']), 1000) / 4,
        axis=1
    )

    # Volume-to-capacity ratio (clamp at 0–2 to avoid extreme values)
    atr['vc_ratio'] = (atr['Vol'] / atr['capacity_15min']).clip(0, 2.0)

    # BPR estimated speed
    atr['est_speed_mph'] = atr['free_flow_speed'] / (
        1 + BPR_ALPHA * (atr['vc_ratio'] ** BPR_BETA)
    )

    # Speed excess over posted limit
    atr['speed_excess'] = atr['est_speed_mph'] - atr['speed_limit']

    # Compliance flag
    atr['is_compliant'] = atr['est_speed_mph'] <= atr['speed_limit']
    atr['is_critical_violation'] = atr['speed_excess'] > CRITICAL_THRESHOLD_MPH

    print(f'\n  Estimated speed statistics (all records):')
    print(f'    Mean:  {atr["est_speed_mph"].mean():.2f} mph')
    print(f'    Std:   {atr["est_speed_mph"].std():.2f} mph')
    print(f'    Min:   {atr["est_speed_mph"].min():.2f} mph')
    print(f'    Max:   {atr["est_speed_mph"].max():.2f} mph')
    print(f'    85th percentile: {atr["est_speed_mph"].quantile(0.85):.2f} mph')

    # ── 2b. Descriptive statistics by functional class ────────────────────────
    print_subheader('2b. Speed metrics by Functional Class')

    stats_table = atr.groupby('FunctionalClass')['est_speed_mph'].agg(
        Mean_Speed='mean',
        Std_Dev='std',
        P85_Speed=lambda x: np.percentile(x, 85),
        Median='median',
        Count='count'
    ).round(2)
    stats_table = stats_table.sort_values('Mean_Speed', ascending=False)

    print('\n  ┌─────────────────────────────────────────────────────────────────────┐')
    print('  │       SPEED METRICS BY FUNCTIONAL CLASS (BPR-Estimated, mph)       │')
    print('  ├─────────────────────────────────────────────────────────────────────┤')
    print(f'  │ {"Functional Class":<24} {"Mean":>8} {"Std":>8} {"P85":>8} {"Median":>8} {"N":>8} │')
    print('  ├─────────────────────────────────────────────────────────────────────┤')
    for fc, row in stats_table.iterrows():
        print(f'  │ {fc:<24} {row["Mean_Speed"]:>8.2f} {row["Std_Dev"]:>8.2f} '
              f'{row["P85_Speed"]:>8.2f} {row["Median"]:>8.2f} {int(row["Count"]):>8,} │')
    print('  └─────────────────────────────────────────────────────────────────────┘')

    # ── 2c. Peak vs. Off-Peak statistics ──────────────────────────────────────
    print_subheader('2c. Peak vs. Off-Peak speed metrics by Functional Class')
    print(f'  Peak hours defined: {[f"{h}:00" for h in PEAK_HOURS]}')

    peak_stats = atr.groupby(['FunctionalClass', 'period'])['est_speed_mph'].agg(
        Mean_Speed='mean',
        Std_Dev='std',
        P85_Speed=lambda x: np.percentile(x, 85),
        Count='count'
    ).round(2)

    print('\n  ┌───────────────────────────────────────────────────────────────────────────┐')
    print('  │     PEAK vs OFF-PEAK SPEED METRICS BY FUNCTIONAL CLASS (mph)             │')
    print('  ├───────────────────────────────────────────────────────────────────────────┤')
    print(f'  │ {"Functional Class":<24} {"Period":<10} {"Mean":>8} {"Std":>8} {"P85":>8} {"N":>8} │')
    print('  ├───────────────────────────────────────────────────────────────────────────┤')
    for (fc, period), row in peak_stats.iterrows():
        print(f'  │ {fc:<24} {period:<10} {row["Mean_Speed"]:>8.2f} {row["Std_Dev"]:>8.2f} '
              f'{row["P85_Speed"]:>8.2f} {int(row["Count"]):>8,} │')
    print('  └───────────────────────────────────────────────────────────────────────────┘')

    # ── 2d. Visualizations ────────────────────────────────────────────────────
    print_subheader('2d. Generating visualizations')

    # --- Plot 1: Boxplot of estimated speed by functional class ---
    fig, ax = plt.subplots(figsize=(14, 8))

    # Order by median speed descending
    order = (atr.groupby('FunctionalClass')['est_speed_mph']
             .median().sort_values(ascending=False).index.tolist())

    palette = sns.color_palette('coolwarm', n_colors=len(order))
    bp = sns.boxplot(
        data=atr, x='FunctionalClass', y='est_speed_mph',
        order=order, palette=palette, showfliers=False,
        width=0.6, linewidth=1.2, ax=ax
    )

    ax.set_title('BPR-Estimated Speed Distribution by Roadway Functional Class',
                 fontsize=15, pad=15)
    ax.set_xlabel('FHWA Functional Classification', fontsize=12, labelpad=10)
    ax.set_ylabel('Estimated Operating Speed (mph)', fontsize=12, labelpad=10)
    ax.tick_params(axis='x', rotation=25, labelsize=10)
    ax.tick_params(axis='y', labelsize=10)

    # Add median value labels
    medians = atr.groupby('FunctionalClass')['est_speed_mph'].median()
    for i, fc in enumerate(order):
        if fc in medians.index:
            ax.text(i, medians[fc] + 0.5, f'{medians[fc]:.1f}',
                    ha='center', va='bottom', fontweight='bold', fontsize=9,
                    color='black')

    plt.tight_layout()
    path1 = os.path.join(OUTPUT_DIR, 'boxplot_speed_variance_by_class.png')
    fig.savefig(path1, bbox_inches='tight')
    plt.close(fig)
    print(f'  ✓ Saved: {path1}')

    # --- Plot 2: Non-compliance bar chart ---
    compliance_df = atr.groupby('FunctionalClass').agg(
        total=('is_compliant', 'count'),
        non_compliant=('is_compliant', lambda x: (~x).sum()),
        critical=('is_critical_violation', 'sum')
    )
    compliance_df['non_compliance_pct'] = (
        100 * compliance_df['non_compliant'] / compliance_df['total']
    ).round(2)
    compliance_df['critical_pct'] = (
        100 * compliance_df['critical'] / compliance_df['total']
    ).round(2)
    compliance_df = compliance_df.sort_values('non_compliance_pct', ascending=False)

    fig, ax = plt.subplots(figsize=(14, 8))
    x = range(len(compliance_df))
    width = 0.38

    bars1 = ax.bar([i - width/2 for i in x],
                   compliance_df['non_compliance_pct'],
                   width, label='Any Exceedance (> Posted Limit)',
                   color='#ff6b6b', edgecolor='white', linewidth=0.5)
    bars2 = ax.bar([i + width/2 for i in x],
                   compliance_df['critical_pct'],
                   width, label=f'Critical Violation (> {CRITICAL_THRESHOLD_MPH} mph Over Limit)',
                   color='#c0392b', edgecolor='white', linewidth=0.5)

    ax.set_title('Speed Limit Non-Compliance Rates by Functional Class\n(BPR-Estimated Speeds)',
                 fontsize=15, pad=15)
    ax.set_xlabel('FHWA Functional Classification', fontsize=12, labelpad=10)
    ax.set_ylabel('Non-Compliance Rate (%)', fontsize=12, labelpad=10)
    ax.set_xticks(list(x))
    ax.set_xticklabels(compliance_df.index, rotation=25, ha='right', fontsize=10)
    ax.legend(fontsize=11, loc='upper right')

    # Add value labels on bars
    for bar in bars1:
        h = bar.get_height()
        if h > 0:
            ax.text(bar.get_x() + bar.get_width()/2, h + 0.5, f'{h:.1f}%',
                    ha='center', va='bottom', fontsize=9, fontweight='bold')
    for bar in bars2:
        h = bar.get_height()
        if h > 0:
            ax.text(bar.get_x() + bar.get_width()/2, h + 0.5, f'{h:.1f}%',
                    ha='center', va='bottom', fontsize=9, fontweight='bold')

    ax.set_ylim(0, max(compliance_df['non_compliance_pct'].max(),
                       compliance_df['critical_pct'].max()) * 1.2)
    plt.tight_layout()
    path2 = os.path.join(OUTPUT_DIR, 'barchart_noncompliance_by_class.png')
    fig.savefig(path2, bbox_inches='tight')
    plt.close(fig)
    print(f'  ✓ Saved: {path2}')

    # Print compliance table
    print('\n  ┌─────────────────────────────────────────────────────────────────────────────┐')
    print('  │          NON-COMPLIANCE RATES BY FUNCTIONAL CLASS                          │')
    print('  ├─────────────────────────────────────────────────────────────────────────────┤')
    print(f'  │ {"Functional Class":<24} {"Total":>10} {"Non-Compl":>10} '
          f'{"Rate(%)":>10} {"Critical%":>10} │')
    print('  ├─────────────────────────────────────────────────────────────────────────────┤')
    for fc, row in compliance_df.iterrows():
        print(f'  │ {fc:<24} {int(row["total"]):>10,} {int(row["non_compliant"]):>10,} '
              f'{row["non_compliance_pct"]:>10.2f} {row["critical_pct"]:>10.2f} │')
    print('  └─────────────────────────────────────────────────────────────────────────────┘')

    return atr


# ==============================================================================
# STEP 3: STATISTICAL RISK EVIDENCE (ANOVA + TUKEY)
# ==============================================================================
def step3_statistical_tests(atr):
    """Conduct ANOVA and Tukey post-hoc tests on estimated speeds."""

    print_header('STEP 3: STATISTICAL RISK EVIDENCE')

    # ── 3a. One-Way ANOVA ─────────────────────────────────────────────────────
    print_subheader('3a. One-Way ANOVA: Speed differentials across functional classes')

    # Group estimated speeds by functional class
    groups = [grp['est_speed_mph'].values
              for _, grp in atr.groupby('FunctionalClass')]
    group_labels = [name for name, _ in atr.groupby('FunctionalClass')]

    f_stat, p_value = stats.f_oneway(*groups)

    print(f'\n  H₀: Mean estimated speed is equal across all functional classes')
    print(f'  H₁: At least one functional class has a different mean speed')
    print(f'\n  ┌─────────────────────────────────────────────┐')
    print(f'  │          ONE-WAY ANOVA RESULTS              │')
    print(f'  ├─────────────────────────────────────────────┤')
    print(f'  │  F-statistic:  {f_stat:>15.4f}             │')
    print(f'  │  p-value:      {p_value:>15.2e}             │')
    print(f'  │  Groups (k):   {len(groups):>15d}             │')
    print(f'  │  Total N:      {len(atr):>15,}             │')
    print(f'  ├─────────────────────────────────────────────┤')
    if p_value < 0.001:
        print(f'  │  Result: HIGHLY SIGNIFICANT (p < 0.001)     │')
    elif p_value < 0.05:
        print(f'  │  Result: SIGNIFICANT (p < 0.05)             │')
    else:
        print(f'  │  Result: NOT SIGNIFICANT (p ≥ 0.05)         │')
    print(f'  └─────────────────────────────────────────────┘')

    # ── 3b. Tukey HSD post-hoc test ──────────────────────────────────────────
    print_subheader('3b. Tukey HSD Post-Hoc Test: Pairwise comparisons')

    # For large datasets, sample to keep Tukey manageable
    if len(atr) > 100000:
        print(f'  Note: Sampling 100,000 records for Tukey test (original N={len(atr):,})')
        atr_sample = atr.sample(n=100000, random_state=42)
    else:
        atr_sample = atr

    tukey = pairwise_tukeyhsd(
        endog=atr_sample['est_speed_mph'],
        groups=atr_sample['FunctionalClass'],
        alpha=0.05
    )

    print(f'\n  Tukey HSD Results (α = 0.05):')
    print(f'  {"="*90}')
    print(tukey.summary())
    print(f'  {"="*90}')

    # Count significant differences
    reject_count = sum(tukey.reject)
    total_pairs = len(tukey.reject)
    print(f'\n  ✓ Significant pairwise differences: {reject_count} / {total_pairs}')

    return f_stat, p_value, tukey


# ==============================================================================
# STEP 4: RISK QUANTIFICATION & THRESHOLDS
# ==============================================================================
def step4_risk_quantification(atr):
    """Calculate compliance ratios, odds ratios, and relative risk."""

    print_header('STEP 4: RISK QUANTIFICATION & THRESHOLDS')

    print(f'  Critical safety threshold: > {CRITICAL_THRESHOLD_MPH} mph over posted limit')

    # ── 4a. Compliance ratio per functional class ─────────────────────────────
    print_subheader('4a. Compliance ratios by Functional Class')

    risk_df = atr.groupby('FunctionalClass').agg(
        total=('est_speed_mph', 'count'),
        compliant=('is_compliant', 'sum'),
        critical_violations=('is_critical_violation', 'sum'),
        mean_speed=('est_speed_mph', 'mean'),
        mean_limit=('speed_limit', 'mean'),
        p85=('est_speed_mph', lambda x: np.percentile(x, 85))
    )
    risk_df['compliance_ratio'] = (100 * risk_df['compliant'] / risk_df['total']).round(2)
    risk_df['critical_rate'] = (100 * risk_df['critical_violations'] / risk_df['total']).round(4)
    risk_df['mean_excess'] = (risk_df['mean_speed'] - risk_df['mean_limit']).round(2)
    risk_df = risk_df.sort_values('compliance_ratio', ascending=True)

    print('\n  ┌──────────────────────────────────────────────────────────────────────────────────────────┐')
    print('  │                  COMPLIANCE & RISK SUMMARY BY FUNCTIONAL CLASS                          │')
    print('  ├──────────────────────────────────────────────────────────────────────────────────────────┤')
    print(f'  │ {"Class":<24} {"Total":>9} {"Compliant%":>12} {"CritViol%":>11} '
          f'{"MeanSpd":>9} {"MeanLim":>9} {"P85":>7} {"Excess":>8} │')
    print('  ├──────────────────────────────────────────────────────────────────────────────────────────┤')
    for fc, row in risk_df.iterrows():
        print(f'  │ {fc:<24} {int(row["total"]):>9,} {row["compliance_ratio"]:>12.2f} '
              f'{row["critical_rate"]:>11.4f} {row["mean_speed"]:>9.2f} '
              f'{row["mean_limit"]:>9.1f} {row["p85"]:>7.2f} {row["mean_excess"]:>8.2f} │')
    print('  └──────────────────────────────────────────────────────────────────────────────────────────┘')

    # ── 4b. Odds Ratio: Arterials vs Local ────────────────────────────────────
    print_subheader('4b. Odds Ratio — Critical violation: Arterials vs Local')

    # Combine Principal Arterial + Minor Arterial as "Arterial"
    arterial_mask = atr['FunctionalClass'].isin(['Principal Arterial', 'Minor Arterial'])
    local_mask = atr['FunctionalClass'] == 'Local'

    arterial_data = atr[arterial_mask]
    local_data = atr[local_mask]

    if len(arterial_data) > 0 and len(local_data) > 0:
        a_viol = arterial_data['is_critical_violation'].sum()
        a_no_viol = len(arterial_data) - a_viol
        l_viol = local_data['is_critical_violation'].sum()
        l_no_viol = len(local_data) - l_viol

        print(f'\n  Contingency Table:')
        print(f'  ┌────────────────────┬──────────────┬──────────────┐')
        print(f'  │                    │  Violation   │ No Violation │')
        print(f'  ├────────────────────┼──────────────┼──────────────┤')
        print(f'  │ Arterials          │ {a_viol:>12,} │ {a_no_viol:>12,} │')
        print(f'  │ Local              │ {l_viol:>12,} │ {l_no_viol:>12,} │')
        print(f'  └────────────────────┴──────────────┴──────────────┘')

        # Odds Ratio
        if l_viol > 0 and a_no_viol > 0 and l_no_viol > 0:
            odds_arterial = a_viol / a_no_viol
            odds_local = l_viol / l_no_viol
            odds_ratio = odds_arterial / odds_local

            # Relative Risk
            risk_arterial = a_viol / (a_viol + a_no_viol)
            risk_local = l_viol / (l_viol + l_no_viol)
            relative_risk = risk_arterial / risk_local if risk_local > 0 else float('inf')

            # 95% CI for Odds Ratio (Woolf's method)
            ln_or = np.log(odds_ratio)
            se_ln_or = np.sqrt(1/max(a_viol,1) + 1/max(a_no_viol,1) +
                               1/max(l_viol,1) + 1/max(l_no_viol,1))
            ci_lower = np.exp(ln_or - 1.96 * se_ln_or)
            ci_upper = np.exp(ln_or + 1.96 * se_ln_or)

            print(f'\n  ┌─────────────────────────────────────────────────────────┐')
            print(f'  │      ODDS RATIO & RELATIVE RISK RESULTS                │')
            print(f'  ├─────────────────────────────────────────────────────────┤')
            print(f'  │  Odds (Arterials):    {odds_arterial:>10.6f}                    │')
            print(f'  │  Odds (Local):        {odds_local:>10.6f}                    │')
            print(f'  │  Odds Ratio (A/L):    {odds_ratio:>10.4f}                    │')
            print(f'  │  95% CI:              ({ci_lower:.4f}, {ci_upper:.4f})         │')
            print(f'  │  Relative Risk (A/L): {relative_risk:>10.4f}                    │')
            print(f'  │  Risk (Arterials):    {100*risk_arterial:>10.4f}%                   │')
            print(f'  │  Risk (Local):        {100*risk_local:>10.4f}%                   │')
            print(f'  ├─────────────────────────────────────────────────────────┤')
            if odds_ratio > 1:
                print(f'  │  Interpretation: Arterials have {odds_ratio:.2f}x higher odds   │')
                print(f'  │  of critical speed violations than Local roads.       │')
            else:
                print(f'  │  Interpretation: Local roads have higher odds of      │')
                print(f'  │  critical speed violations than Arterials.            │')
            print(f'  └─────────────────────────────────────────────────────────┘')
        else:
            print('  ⚠ Cannot compute Odds Ratio: zero counts in one or more cells.')
            print(f'    Arterial violations: {a_viol}, Local violations: {l_viol}')
    else:
        print('  ⚠ Insufficient data for Arterial or Local functional class.')

    # ── 4c. Chi-square test for independence ──────────────────────────────────
    print_subheader('4c. Chi-Square test: Violation independence across functional classes')

    contingency = pd.crosstab(
        atr['FunctionalClass'],
        atr['is_critical_violation'],
        margins=True
    )
    # Exclude margins for test
    ct = contingency.iloc[:-1, :-1]
    if ct.shape[0] >= 2 and ct.shape[1] >= 2:
        chi2, p_chi, dof, expected = stats.chi2_contingency(ct)
        print(f'\n  Chi-square statistic: {chi2:.4f}')
        print(f'  Degrees of freedom:  {dof}')
        print(f'  p-value:             {p_chi:.2e}')
        if p_chi < 0.05:
            print(f'  Result: SIGNIFICANT — violation rates differ by functional class')
        else:
            print(f'  Result: NOT SIGNIFICANT — no evidence of difference')


# ==============================================================================
# MAIN EXECUTION
# ==============================================================================
if __name__ == '__main__':
    print('\n' + '█' * 78)
    print('█  SPEED LIMIT COMPLIANCE ANALYSIS ACROSS ROADWAY FUNCTIONAL CLASSES       █')
    print('█  BPR Speed-Flow Model (Option B)                                         █')
    print('█' * 78)

    # Step 1
    atr = step1_data_preparation()

    # Step 2
    atr = step2_speed_derivation_and_eda(atr)

    # Step 3
    f_stat, p_val, tukey = step3_statistical_tests(atr)

    # Step 4
    step4_risk_quantification(atr)

    print_header('PIPELINE COMPLETE')
    print(f'  All results printed above. PNG plots saved to:')
    print(f'    • boxplot_speed_variance_by_class.png')
    print(f'    • barchart_noncompliance_by_class.png')
    print(f'\n  Output directory: {OUTPUT_DIR}')
    print('=' * 78 + '\n')
