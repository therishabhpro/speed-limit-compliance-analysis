# Data Acquisition & Schema Guide

This directory documents the data sources, spatial datasets, quality control tables, and dictionary definitions required to reproduce the **Speed Limit Compliance Analysis Across Roadway Functional Classes**.

Due to GitHub's file size limits (< 100 MB per file), the raw traffic telemetry CSV (~274 MB) and the statewide HPMS Esri File Geodatabase (~2.4 GB) are excluded from version control via `.gitignore`. The quality control lookup tables and data dictionaries are provided directly in this folder.

---

## 1. Directory Structure

```
data/
├── README.md                                             # This acquisition and schema documentation
├── ATR_Location_Errors2025.xlsx                          # [Included] Sensor quality control audit table
├── Data_Dictionary_-_Automated_Traffic_Volume_Counts.xlsx # [Included] Official NYC DOT ATR data dictionary
├── Automated_Traffic_Volume_Counts_20260903.csv           # [External] Raw NYC ATR traffic volume telemetry
└── HPMS2024.gdb/                                         # [External] Esri File Geodatabase of NY Roadway Network
```

---

## 2. Included Datasets

### `ATR_Location_Errors2025.xlsx`
- **Purpose**: Quality assurance / quality control audit log identifying faulty, miscalibrated, or spatially erroneous Automated Traffic Recorder (ATR) stations.
- **Role in Pipeline**: The preprocessing script reads this table and purges all observations corresponding to flagged `RequestID` (20 IDs) and `SegmentID` (24 IDs) pairs, filtering out 15,098 compromised telemetry records.
- **Key Columns**:
  - `RequestID`: ATR deployment survey identifier.
  - `SegmentID`: Roadway segment identifier associated with the hardware deployment.
  - `Error_Description`: Root cause classification (e.g., coordinate inversion, hardware failure).

### `Data_Dictionary_-_Automated_Traffic_Volume_Counts.xlsx`
- **Purpose**: Official data dictionary published by the New York City Department of Transportation (NYC DOT).
- **Contents**: Column definitions, measurement intervals, data types, and sensor operational metadata for Automated Traffic Volume Counts.

---

## 3. Large External Datasets (Download Instructions)

To run the complete data processing pipeline, acquire the following two external datasets and place them directly into this `data/` directory (or into the repository root).

### A. NYC Automated Traffic Volume Counts (`Automated_Traffic_Volume_Counts_20260903.csv`)
- **Source**: New York City Department of Transportation (NYC DOT) via NYC Open Data
- **Dataset Portal**: [NYC Open Data - Automated Traffic Volume Counts](https://data.cityofnewyork.us/Transportation/Automated-Traffic-Volume-Counts/7ym2-wayt)
- **File Format**: Comma-Separated Values (`.csv`), ~287 MB raw (1.87M+ rows)
- **Target Filename**: Place as `data/Automated_Traffic_Volume_Counts_20260903.csv` (or standard `Automated_Traffic_Volume_Counts.csv`)
- **Key Required Columns**:
  | Column Name | Data Type | Description |
  |---|---|---|
  | `RequestID` | Integer | Unique identifier for count request survey |
  | `SegmentID` | Integer | Segment identifier linking to NYC street centerline |
  | `Date` | String / Date | Count date (`MM/DD/YYYY`) |
  | `Direction` | String | Cardinal travel direction (`NB`, `SB`, `EB`, `WB`) |
  | `Vol` | Numeric / Float | Aggregate recorded traffic volume for observation interval |
  | `WktGeom` | String (WKT) | Well-Known Text geometry representation (`POINT` / `LINESTRING`) |
  | `street`, `fromSt`, `toSt` | String | Corridor name and bounding intersecting streets |
  | `Borough` | String | NYC borough name (Bronx, Brooklyn, Manhattan, Queens, Staten Island) |

### B. Highway Performance Monitoring System (`HPMS2024.gdb`)
- **Source**: Federal Highway Administration (FHWA) / New York State Department of Transportation (NYSDOT)
- **Dataset Portal**: [FHWA HPMS Public Data Portal](https://www.fhwa.dot.gov/policyinformation/hpms.cfm) / [NYSDOT GIS Data](https://www.dot.ny.gov/gisapps)
- **File Format**: Esri File Geodatabase (`.gdb` directory), ~2.4 GB
- **Layer Name**: `HPMS_FULL_NY_2024`
- **Coordinate Reference System (CRS)**: EPSG:4326 (WGS 84), projected internally to EPSG:32618 (UTM Zone 18N) for metric spatial joins.
- **Target Location**: Place folder as `data/HPMS2024.gdb/`
- **Key Required Columns**:
  | Column Name | Data Type | Description |
  |---|---|---|
  | `geometry` | LineString | Roadway linear polyline geometry |
  | `COUNTY_CODE` | Integer | County FIPS code (filtered to NYC: 5=Bronx, 47=Kings, 61=New York, 81=Queens, 85=Richmond) |
  | `F_SYSTEM` | Integer | FHWA Functional Classification code (1 to 7) |
  | `SPEED_LIMIT` | Numeric | Legal posted speed limit in miles per hour (mph) |
  | `THROUGH_LANES` | Integer | Number of directional through travel lanes |

---

## 4. Automated Path Resolution

`main_analysis.py` features automatic path discovery:
1. It first searches the `data/` directory for input files.
2. If absent in `data/`, it falls back to inspecting the project root directory.
3. If an input file is missing from both locations, a clear diagnostic message is logged with instructions for placing the data file.
