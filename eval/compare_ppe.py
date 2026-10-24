"""Две модели СИЗ на одной тестовой части jhboyo/ppe-dataset: открытая (Hugging Face) и наша дообученная.

Классы приводятся к общим: каска, голова без каски, жилет. AP50 по каждому классу (всеточечная интерполяция VOC),
плюс точность и полнота при рабочем пороге модели — то, что реально увидит правило.

    python eval/compare_ppe.py --out /workspace/linq/data/eval/ppe_compare.json
"""
import argparse
import json
import sys
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from engine.backends import OnnxBackend  # noqa: E402
from engine.detector import Detector, ModelSpec  # noqa: E402
from engine.geometry import iou  # noqa: E402

CLASSES = ["helmet", "head", "vest"]
# индекс класса модели → общий класс
MAPS = {
    "ppe": {0: "helmet", 2: "head", 7: "vest"},     # Hardhat, NO-Hardhat, Safety Vest
    "ppe2": {0: "helmet", 1: "head", 2: "vest"},
}


def load_truth(label: Path, w: int, h: int) -> list[tuple[str, np.ndarray]]:
    out = []
    for line in label.read_text().split("\n") if label.exists() else []:
        p = line.split()
        if len(p) != 5:
            continue
        c, x, y, bw, bh = int(p[0]), *map(float, p[1:])
        out.append((CLASSES[c], np.array([(x - bw / 2) * w, (y - bh / 2) * h, (x + bw / 2) * w, (y + bh / 2) * h])))
    return out


def ap(scores: list[float], hits: list[bool], total: int) -> float:
    if not total:
        return float("nan")
    order = np.argsort(scores)[::-1]
    tp = np.cumsum(np.array(hits, float)[order])
    fp = np.cumsum(1 - np.array(hits, float)[order])
    rec = np.concatenate([[0], tp / total, [1]])
    prec = np.concatenate([[1], tp / np.maximum(tp + fp, 1e-9), [0]])
    prec = np.maximum.accumulate(prec[::-1])[::-1]
    return float(np.sum((rec[1:] - rec[:-1]) * prec[1:]))


def evaluate(name: str, reg: dict, images: list[Path], labels: Path) -> dict:
    spec = ModelSpec(name=name, kind="detect", path=reg["path"], class_map=MAPS[name], conf=0.05)
    det = Detector(spec, OnnxBackend(reg["path"]))
    work_conf = reg.get("conf", 0.45)
    per = {c: {"scores": [], "hits": [], "gt": 0, "tp": 0, "fp": 0} for c in CLASSES}
    for n, img in enumerate(images):
        if n % 250 == 0:
            print(f"{name}: {n}/{len(images)}", flush=True)
        frame = cv2.imread(str(img))
        h, w = frame.shape[:2]
        truth = load_truth(labels / (img.stem + ".txt"), w, h)
        for c, _ in truth:
            per[c]["gt"] += 1
        used = set()
        for d in sorted(det(frame), key=lambda d: -d.score):
            best, best_v = None, 0.5
            for j, (c, box) in enumerate(truth):
                if j in used or c != d.cls:
                    continue
                v = iou(d.box, box)
                if v >= best_v:
                    best, best_v = j, v
            hit = best is not None
            if hit:
                used.add(best)
            s = per[d.cls]
            s["scores"].append(d.score)
            s["hits"].append(hit)
            if d.score >= work_conf:
                s["tp" if hit else "fp"] += 1
    res = {}
    for c, s in per.items():
        res[c] = {"ap50": round(ap(s["scores"], s["hits"], s["gt"]), 4), "gt": s["gt"],
                  "precision": round(s["tp"] / max(s["tp"] + s["fp"], 1), 4),
                  "recall": round(s["tp"] / max(s["gt"], 1), 4)}
    res["map50"] = round(float(np.nanmean([res[c]["ap50"] for c in CLASSES])), 4)
    res["conf"] = work_conf
    res["infer_ms_p50"] = det.stats().get("infer", {}).get("p50")
    return res


def main() -> None:
    ap_ = argparse.ArgumentParser()
    ap_.add_argument("--data", default="/workspace/linq/train/ppe/test")
    ap_.add_argument("--registry", default="/workspace/linq/models/registry.json")
    ap_.add_argument("--out", default="/workspace/linq/data/eval/ppe_compare.json")
    args = ap_.parse_args()
    reg = json.loads(Path(args.registry).read_text())
    data = Path(args.data)
    images = sorted((data / "images").glob("*"))
    out = {"dataset": "jhboyo/ppe-dataset, test", "images": len(images),
           "models": {n: evaluate(n, reg[n], images, data / "labels") for n in MAPS}}
    Path(args.out).write_text(json.dumps(out, ensure_ascii=False, indent=1))
    print(json.dumps(out, ensure_ascii=False))


if __name__ == "__main__":
    main()
