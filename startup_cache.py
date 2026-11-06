"""
startup_cache.py - the prepared data and trained models, saved in one small file so the app does not read 2 million
rows and retrain every time it starts (which is what makes the free server slow the app down).

Safe by design: load() returns None unless the file matches this data file and these library versions, and then the
app simply reads the data and trains as usual. After changing the data, the features or the models, rebuild the file:
    py startup_cache.py
"""

from pathlib import Path

import joblib
import numpy
import pandas
import sklearn
import xgboost

CACHE_PATH = Path(__file__).parent / "prepared" / "startup_cache.joblib"
FORMAT = 1


def versions():
    return {"pandas": pandas.__version__, "numpy": numpy.__version__, "sklearn": sklearn.__version__, "xgboost": xgboost.__version__}


def data_sizes(folder):
    """{file name: bytes} of the UCI files. Git does not keep file dates, so the size says whether the data changed."""
    from uci_loader import UCI_FILENAME, UCI_ZIPNAME

    found = (Path(folder) / name for name in (UCI_FILENAME, UCI_ZIPNAME))
    return {p.name: p.stat().st_size for p in found if p.exists()}


def save(path, folder, fact, agg, households, n_hours, res):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    joblib.dump({"format": FORMAT, "versions": versions(), "sizes": data_sizes(folder), "fact": fact, "agg": agg,
                 "households": households, "n_hours": n_hours, "res": res}, path, compress=3)


def load(data_file, path=CACHE_PATH):
    """The saved bundle when it fits this data file and these libraries, else None. Never raises."""
    try:
        data_file = Path(data_file)
        bundle = joblib.load(path)
        if bundle["format"] != FORMAT or bundle["versions"] != versions():
            return None
        if bundle["sizes"].get(data_file.name) != data_file.stat().st_size:
            return None
        return bundle
    except Exception:  # no file, a damaged file, or one written by other library versions
        return None


if __name__ == "__main__":
    from ml_models import add_features, to_hourly, train_and_compare
    from uci_loader import load_uci, uci_path

    folder = Path(__file__).parent / "data"
    source = uci_path(folder)
    fact_df, agg_df, households_df = load_uci(source)
    hourly = to_hourly(fact_df, household=None)
    result = train_and_compare(add_features(hourly))
    save(CACHE_PATH, folder, fact_df, agg_df, households_df, len(hourly), result)
    print(f"Saved {CACHE_PATH} ({CACHE_PATH.stat().st_size / 1e6:.2f} MB), {len(fact_df):,} rows, best model {result['best_name']}")
