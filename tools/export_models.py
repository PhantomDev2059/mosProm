"""Экспорт моделей в ONNX со статическим входом 640×640 — тот же формат идёт в SDK LinQ.

Запуск на сервере: python tools/export_models.py --models /workspace/linq/models
"""
import argparse
import json
import shutil
from pathlib import Path

from ultralytics import YOLO

# имя → (источник .pt, задача, отображение классов модели в канонические классы)
MODELS = {
    "pose": ("yolov8n-pose.pt", "pose", {0: "person"}),
    "coco": ("yolov8s.pt", "detect", {2: "vehicle", 5: "vehicle", 7: "vehicle", 67: "phone"}),
    "ppe": ("hf/Hansung-Cho__yolov8-ppe-detection/best.pt", "detect",
            {0: "helmet", 2: "no_helmet", 4: "no_vest", 7: "vest", 8: "machinery", 9: "vehicle"}),
    "forklift": ("hf/keremberke__yolov8n-forklift-detection/best.pt", "detect", {0: "forklift"}),
    "fire": ("hf/rabahdev__fire-smoke-yolov8n/best.pt", "detect", {0: "smoke", 1: "fire"}),
    "cigarette": ("hf/Beehzod__best_smoke_cigarette-detection/best.pt", "detect", {0: "cigarette"}),
}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--models", default="/workspace/linq/models")
    args = ap.parse_args()
    root = Path(args.models)
    out_dir = root / "onnx"
    out_dir.mkdir(exist_ok=True)
    registry = {}
    for name, (src, kind, class_map) in MODELS.items():
        src_path = root / src if (root / src).exists() else Path(src)
        model = YOLO(str(src_path))
        onnx_path = Path(model.export(format="onnx", imgsz=640, opset=12, simplify=True, dynamic=False))
        target = out_dir / f"{name}.onnx"
        shutil.move(str(onnx_path), target)
        registry[name] = {"kind": kind, "path": str(target), "source": str(src),
                          "names": model.names, "class_map": class_map}
        print(name, "→", target, model.names)
    (root / "registry.json").write_text(json.dumps(registry, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
