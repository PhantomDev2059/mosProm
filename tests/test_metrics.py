from eval.metrics import Interval, evaluate


def test_metrics_basic():
    gt = [Interval("v1", "fall", 10, 20, "critical"), Interval("v2", "no_helmet", 0, 30, "medium")]
    pred = [Interval("v1", "fall", 12, 25), Interval("v3", "fall", 5, 6)]           # одно попадание, одна ложная
    r = evaluate(gt, pred, hours=1.0)
    assert r["per_type"]["fall"] == {"gt": 1, "pred": 2, "tp": 1, "fp": 1, "fn": 0, "precision": 0.5, "recall": 1.0, "f1": 0.667}
    assert r["per_type"]["no_helmet"]["recall"] == 0.0
    # критичное падение весит 10, пропущенная каска — 1: взвешенный recall 10/11
    assert r["weighted_recall"] == round(10 / 11, 3)
    # тревога в 12 с при нарушении 10–20 покрывает 80% его времени
    assert r["weighted_time_coverage"] == round(10 * 0.8 / 11, 3)
    assert r["false_alarms_per_hour"] == 1.0
    assert r["latency_median_s"] == 2.0
