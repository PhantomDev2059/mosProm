"""Компиляция склеенной модели (linq/fuse_models.py) в программу TPU. Хвосты у моделей прежние — свои на CPU.

    python linq/compile_fused.py --onnx .../pose+forklift.onnx --videos .../videos --out .../tpu_fused [--finetune 2]
"""
import argparse
import json
import sys
import time
from pathlib import Path

import onnx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from linq.compile_tpu import calibration_frames  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--onnx", required=True)
    ap.add_argument("--videos", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--calib", type=int, default=120)
    ap.add_argument("--finetune", type=int, default=0)
    args = ap.parse_args()
    from tpu_framework import O5, TPU_H1M, Network, QuantizedModel, RegularModel, TpuProgram, compiler
    from tpu_framework import preliminary_model_conditioning

    t0 = time.time()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    name = Path(args.onnx).stem
    model = preliminary_model_conditioning(onnx.load(args.onnx))
    input_name = model.graph.input[0].name
    calib = calibration_frames(Path(args.videos), args.calib, 640)
    regular = RegularModel(model=model)
    thr = regular.calibrate_torch(calibration_data={input_name: calib})
    if args.finetune:
        thr = regular.fine_tune_torch(thresholds=thr, fine_tuning_data={input_name: calib}, fine_tuning_epochs=args.finetune)
    regular.quantize(thr).save(file_dir=str(out), file_name=f"{name}.qm.onnx")
    network, _ = Network.from_quantized_model(QuantizedModel.load(str(out / f"{name}.qm.onnx")))
    executable, desc = compiler.compile_(hardware_parameters=TPU_H1M, network=network, parameters=O5)
    program = out / f"{name}_b1_o5_h1m.tpu"
    TpuProgram.from_executable(executable, desc).to_file(str(program))
    rep = {"name": name, "outputs": [o.name for o in onnx.load(args.onnx).graph.output], "calib": len(calib),
           "finetune": args.finetune, "total_s": round(time.time() - t0, 1)}
    (out / f"{name}_report.json").write_text(json.dumps(rep, ensure_ascii=False, indent=1))
    print("программа:", program, f"{program.stat().st_size / 1e6:.1f} МБ", rep["total_s"], "с")


if __name__ == "__main__":
    main()
