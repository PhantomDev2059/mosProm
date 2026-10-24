"""Правило падения с проверкой «лежит» и без неё на тех же записях UR Fall (кэш позы из sweep_fall.py)."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from eval.sweep_fall import CACHE, DATA, build_cache, score  # noqa: E402
from eval.urfall import ground_truth  # noqa: E402


def main() -> None:
    cache = build_cache()
    gt = ground_truth(DATA / "urfall-cam0-falls.csv")
    best = json.loads((DATA.parent / "fall_sweep.json").read_text())
    names = sorted(cache)
    split = {"tune": [n for n in names if int(n.split("-")[1]) % 2 == 1],
             "test": [n for n in names if int(n.split("-")[1]) % 2 == 0], "all": names}
    out = {}
    for share in (0.0, 0.4, 0.6, 0.8):
        params = best["best_params"] | {"lying_share": share}
        out[share] = {k: {m: score(cache, v, gt, params, best["confirm_s"])[m] for m in ("f1", "recall", "precision", "fp")}
                      for k, v in split.items()}
        print(share, json.dumps(out[share], ensure_ascii=False))
    (DATA.parent / "fall_posture.json").write_text(json.dumps(out, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
