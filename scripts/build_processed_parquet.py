"""
Preprocess the raw Completions A CSVs into compact parquet files.

WHY: the raw IPEDS c{year}_a.csv files are large (~256 MB total) and parsing all
five at runtime peaks well over Streamlit Community Cloud's 1 GB memory ceiling —
the app got OOM-killed on load ("Oh no. Error running app."). Each parquet here
holds exactly the cleaned, MAJORNUM==1-filtered, 7-column frame that
loader._load_ca_from_csv() produces, so loader.load_ca() reads it directly with a
fraction of the memory and none of the CSV parse transient. Output is identical to
the CSV path (the v1 regression test stays byte-identical).

WHEN TO RE-RUN: after dropping new/updated raw c{year}_a.csv files into data/raw/
(e.g. a new IPEDS vintage) or changing the year list in config/years.yaml. Commit
the regenerated data/processed/*.parquet files.

USAGE:  python3 scripts/build_processed_parquet.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'src'))

from loader import _load_ca_from_csv, load_years_config  # noqa: E402

RAW_DIR = ROOT / 'data' / 'raw'
OUT_DIR = ROOT / 'data' / 'processed'


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    years = load_years_config(ROOT / 'config' / 'years.yaml')['years']
    print(f'Preprocessing Completions A for years: {years}')
    for year in years:
        df = _load_ca_from_csv(year, RAW_DIR)
        out_path = OUT_DIR / f'c{year}_a.parquet'
        df.to_parquet(out_path, index=False)
        size_mb = out_path.stat().st_size / 1e6
        print(f'  wrote {out_path.name}: {len(df):,} rows  {df.shape[1]} cols  {size_mb:.1f} MB')
    print('Done. Commit data/processed/*.parquet.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
