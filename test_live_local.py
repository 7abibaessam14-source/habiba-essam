"""
test_live_local.py - sanity test for the simulated live stream (live.py) on SYNTHETIC data.

Uses the same synthetic data as test_ml_local.py (known spikes in the unseen
period), replays it hour by hour and checks the stream catches the spikes.
Then checks that the "Inject spike" button raises an alert. Run:  py test_live_local.py
"""

from alerts import compute_threshold
from live import LiveReplay
from ml_models import add_features, to_hourly, train_and_compare
from test_ml_local import make_demo_data

if __name__ == "__main__":
    df, spike_hours = make_demo_data()
    res = train_and_compare(add_features(to_hourly(df, household="MAC000002")))
    threshold = compute_threshold(res["train"]["kwh"])
    print(f"best model: {res['best_name']} | threshold: {threshold:.2f} kWh")

    # 1) replay the whole unseen period
    live = LiveReplay(res["test"], res["best_model"], threshold, household="MAC000002")
    print(f"unseen hours: {len(res['test'])} | history (warm-up): {live.start} | to stream: {live.to_stream}")
    while live.step() is not None:
        pass

    streamed = live.seen.iloc[live.start:]
    flagged = set(streamed.index[streamed["is_anomaly"]])
    spikes_streamed = spike_hours & set(streamed.index)
    hit = spikes_streamed & flagged
    print(f"\n[replay] hours streamed: {live.streamed} | finished: {live.finished}")
    print(f"[replay] spikes in the streamed part: {len(spikes_streamed)} | detected: {len(hit)} | "
          f"total flagged: {len(flagged)} | false flags: {len(flagged - spike_hours)}")
    print(f"[replay] alerts raised: {len(live.alerts)}")
    print(live.alerts.head(6).to_string(index=False))

    # 2) the "Inject spike" button on a fresh replay
    live2 = LiveReplay(res["test"], res["best_model"], threshold, household="MAC000002")
    for _ in range(10):
        live2.step()
    live2.inject_next = True
    rec, new_alerts = live2.step()
    print(f"\n[inject] kWh {rec['kwh'].iloc[0]:.2f} vs expected {rec['pred'].iloc[0]:.2f} | "
          f"z {rec['z'].iloc[0]:.1f} | alerts: {len(new_alerts)}")
    print(new_alerts.to_string(index=False))

    ok = len(hit) == len(spikes_streamed) and len(new_alerts) > 0 and not live2.inject_next
    print("\nRESULT:", "PASS" if ok else "FAIL")
