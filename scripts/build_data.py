#!/usr/bin/env python3
"""Write the Cooperative Election Study (CES) data release in the common format (STANDARD.md §8).

Reads each release's Stata file in data/raw and writes
data/processed/<dataset>-<release>.parquet, holding the variables this repo has
records for. Each file is read back and every cell compared with the Stata file,
so the conversion is lossless. The numeric codes the records type as missing are recognized from the records; Stata's system missing (.) becomes the code ".".
"""

import sys
from pathlib import Path

try:
    from pdt.readers import read_stata
    from pdt.release import data_source, write_release
except ImportError:
    sys.exit("Needs the PublicDataToolkit library: pip install -e ../PublicDataToolkit, "
             "or run through `make data`.")

ROOT = Path(__file__).resolve().parent.parent


def read(release, table, names):
    source = data_source(ROOT, release, table, format="stata")
    path = ROOT / source["path"]
    if not path.exists():
        sys.exit(f"{path} is missing; see python scripts/download.py --check")
    return read_stata(path, names), source["id"]


if __name__ == "__main__":
    write_release(ROOT, read)
