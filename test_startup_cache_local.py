"""
test_startup_cache_local.py - checks that the saved startup file is used only when it is safe, and that the app's
real saved file gives exactly the same results as training again. Run: py test_startup_cache_local.py
"""

import tempfile
from pathlib import Path

import joblib
import numpy as np
import pandas as pd

import startup_cache as sc

ok = True


def check(name, cond):
    global ok
    ok &= bool(cond)
    print(f"{'ok  ' if cond else 'FAIL'} {name}")


with tempfile.TemporaryDirectory() as tmp:
    folder, cache = Path(tmp), Path(tmp) / "prepared" / "c.joblib"
    data = folder / "household_power_consumption.zip"
    data.write_bytes(b"x" * 100)
    fact = pd.DataFrame({"KWH_hh": [1.0, 2.0]})

    check("no file gives None", sc.load(data, cache) is None)
    sc.save(cache, folder, fact, fact, fact, 2, {"best_name": "XGBoost"})
    good = sc.load(data, cache)
    check("a matching file is used", good is not None and good["res"]["best_name"] == "XGBoost" and good["n_hours"] == 2)
    check("the saved rows come back", good["fact"].equals(fact))

    data.write_bytes(b"x" * 101)
    check("changed data is not trusted", sc.load(data, cache) is None)
    data.write_bytes(b"x" * 100)

    b = joblib.load(cache)
    joblib.dump({**b, "versions": {**b["versions"], "xgboost": "0.0"}}, cache)
    check("other library versions are not trusted", sc.load(data, cache) is None)
    joblib.dump({**b, "format": 0}, cache)
    check("an old file format is not trusted", sc.load(data, cache) is None)
    cache.write_bytes(b"not a real file")
    check("a damaged file gives None instead of crashing", sc.load(data, cache) is None)
    joblib.dump({"format": sc.FORMAT}, cache)
    check("a file with missing parts gives None", sc.load(data, cache) is None)
    check("a missing data file gives None", sc.load(folder / "nothing.zip", sc.CACHE_PATH) is None)

# the real file, only where it and the data file exist (the GitHub tests may not have the same libraries)
from uci_loader import uci_path

source = uci_path(Path(__file__).parent / "data")
bundle = sc.load(source) if source is not None else None
if bundle is None:
    print("note: the real saved file was not checked (missing, or other library versions here)")
else:
    from ml_models import add_features, to_hourly, train_and_compare
    from uci_loader import load_uci

    fresh_fact, _, _ = load_uci(source)
    check("the saved data equals the data read fresh", bundle["fact"].equals(fresh_fact))
    hourly = to_hourly(fresh_fact, household=None)
    check("the saved hour count is right", bundle["n_hours"] == len(hourly))
    fresh = train_and_compare(add_features(hourly))
    check("the same best model", bundle["res"]["best_name"] == fresh["best_name"])
    saved_res = bundle["res"]
    a, b = saved_res["metrics"].set_index("model"), fresh["metrics"].set_index("model")
    # training on another computer (Windows here, Linux on GitHub) rounds slightly differently, so close, not equal
    check("scores close to training again (within 5%)", np.allclose(a.loc[b.index, b.columns].to_numpy(dtype=float), b.to_numpy(dtype=float), rtol=0.05))
    from ml_models import FEATURES, _metrics

    again = _metrics(saved_res["test"]["kwh"], saved_res["best_model"].predict(saved_res["test"][FEATURES]))
    stored = a.loc[saved_res["best_name"]]
    check("the saved model gives its saved scores", all(np.isclose(again[k], stored[k]) for k in again))

print("\nRESULT:", "PASS" if ok else "FAIL")
