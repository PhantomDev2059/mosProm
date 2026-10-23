"""Точность дообученного детектора СИЗ на тестовой части набора → metrics.json для экрана «Качество»."""
import json
import sys
from pathlib import Path

from ultralytics import YOLO

ROOT = Path("/workspace/linq/train")


def main() -> None:
    weights = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "ppe_v8s" / "weights" / "best.pt"
    model = YOLO(str(weights))
    m = model.val(data=str(ROOT / "ppe.yaml"), split="test", imgsz=640, batch=16, project=str(ROOT),
                  name="ppe_v8s_test", exist_ok=True, plots=True)
    names = model.names
    out = {
        "weights": str(weights), "map50": round(float(m.box.map50), 4), "map": round(float(m.box.map), 4),
        "precision": round(float(m.box.mp), 4), "recall": round(float(m.box.mr), 4),
        "per_class": {names[int(c)]: round(float(v), 4) for c, v in zip(m.box.ap_class_index, m.box.ap50)},
        "images": len(list((ROOT / "ppe" / "test" / "images").glob("*"))),
    }
    (ROOT / "ppe_v8s_test" / "metrics.json").write_text(json.dumps(out, ensure_ascii=False, indent=1))
    print(json.dumps(out, ensure_ascii=False))


if __name__ == "__main__":
    main()
