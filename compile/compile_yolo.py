"""ONNX YOLOv8 -> программа LinQ TPU (.tpu). Запуск в WSL из окружения SDK:

    ~/linq/sdk/bin/python /mnt/d/dev/mosprom/compile/compile_yolo.py models/yolov8n_640.onnx

Голова YOLOv8 отрезается на последних свёртках веток box (cv2) и cls (cv3) по масштабам 8/16/32:
DFL, sigmoid и NMS делаются на CPU (app/yolo.py), на TPU уходят только свёртки.
"""
import argparse
import glob
import json
import os
import sys
import urllib.request
import zipfile
from pathlib import Path

import numpy as np
import onnx
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'app'))
from yolo import letterbox  # noqa: E402

COCO128 = 'https://github.com/ultralytics/assets/releases/download/v0.0.0/coco128.zip'


def find_head_outputs(model):
    """Последние Conv веток головы: cv2 (бокс, 64 = 4x16 DFL), cv3 (классы), у Pose ещё cv4 (точки 17x3).

    Возвращает {'box': (name, shape), 'cls': ..., ['kpt': ...]} для каждого уровня,
    по убыванию разрешения (stride 8, 16, 32)."""
    inferred = onnx.shape_inference.infer_shapes(model)
    shapes = {v.name: [d.dim_value for d in v.type.tensor_type.shape.dim]
              for v in list(inferred.graph.value_info) + list(inferred.graph.output)}
    producer = {o: n for n in model.graph.node for o in n.output}
    levels = {}
    for node in model.graph.node:
        src = producer.get(node.input[0]) if node.input else None
        if node.op_type != 'Reshape' or src is None or src.op_type != 'Conv':
            continue
        branch = {'/cv2.': 'box', '/cv3.': 'cls', '/cv4.': 'kpt'}
        branch = next((v for k, v in branch.items() if k in src.name), None)
        if branch:
            shape = shapes[src.output[0]]
            levels.setdefault(shape[2], {})[branch] = (src.output[0], shape)
    assert len(levels) == 3 and all({'box', 'cls'} <= set(v) for v in levels.values()), f'голова не найдена: {levels}'
    return [levels[h] for h in sorted(levels, reverse=True)]


def calibration_images(n):
    calib_dir = ROOT / 'models' / 'calib'
    if not calib_dir.exists():
        zip_path = ROOT / 'models' / 'coco128.zip'
        urllib.request.urlretrieve(COCO128, zip_path)
        with zipfile.ZipFile(zip_path) as z:
            z.extractall(calib_dir)
    return sorted(glob.glob(str(calib_dir / '**' / '*.jpg'), recursive=True))[:n]


def main():
    p = argparse.ArgumentParser()
    p.add_argument('onnx')
    p.add_argument('--calib', type=int, default=64, help='число картинок для калибровки')
    p.add_argument('--preset', choices=['o1', 'o5'], default='o5')
    p.add_argument('--cut-only', action='store_true', help='только обрезать голову и записать метаданные (без SDK)')
    args = p.parse_args()

    src = Path(args.onnx).resolve()
    name = src.stem
    out_dir = src.parent / name
    out_dir.mkdir(exist_ok=True)

    model = onnx.load(src)
    input_name = model.graph.input[0].name
    imgsz = model.graph.input[0].type.tensor_type.shape.dim[2].dim_value
    heads = find_head_outputs(model)
    print('голова:', heads)

    outputs = [level[b] for level in heads for b in ('box', 'cls', 'kpt') if b in level]
    cut_path = src.parent / f'{name}_cut.onnx'
    onnx.utils.extract_model(str(src), str(cut_path), [input_name], [o[0] for o in outputs])
    meta = {
        'program': f'{name}_b1_{args.preset}_h1m.tpu', 'input': input_name, 'imgsz': imgsz,
        'outputs': [o[0] for o in outputs], 'output_shapes': [o[1] for o in outputs],
        'levels': [[level['box'][0], level['cls'][0]] for level in heads],
        'strides': [imgsz // level['box'][1][2] for level in heads], 'num_classes': heads[0]['cls'][1][1],
    }
    if 'kpt' in heads[0]:
        meta['kpt_levels'] = [level['kpt'][0] for level in heads]
        meta['kpt_shape'] = [heads[0]['kpt'][1][1] // 3, 3]
    meta_path = src.parent / f'{name}.json'
    meta_path.write_text(json.dumps(meta, indent=2))
    print('1/4 обрезанная модель:', cut_path)
    if args.cut_only:
        print(json.dumps(meta, indent=2))
        return

    from tpu_framework import (O1, O5, TPU_H1M, Network, QuantizedModel, RegularModel, TpuProgram,
                               compiler, preliminary_model_conditioning)

    conditioned = preliminary_model_conditioning(onnx.load(cut_path))
    onnx.save(conditioned, out_dir / f'{name}_opt.onnx')
    print('2/4 conditioning OK; выходы:', [o.name for o in conditioned.graph.output])

    images = calibration_images(args.calib)
    calib = np.concatenate([letterbox(Image.open(f), imgsz)[0] for f in images], axis=0)
    regular = RegularModel(model=conditioned)
    thresholds = regular.calibrate_torch(calibration_data={input_name: calib})
    RegularModel.dump_thresholds(str(out_dir / 'thresholds.json'), thresholds)
    quant = regular.quantize(thresholds)
    quant_name = f'quant_{name}.qm.onnx'
    quant.save(file_dir=str(out_dir), file_name=quant_name)
    print(f'3/4 квантование OK ({len(images)} картинок калибровки)')

    network, _ = Network.from_quantized_model(QuantizedModel.load(str(out_dir / quant_name)))
    presets = {'o5': O5, 'o1': O1}
    order = [args.preset] + [k for k in presets if k != args.preset]
    for preset in order:
        try:
            executable, tensor_descriptions = compiler.compile_(
                hardware_parameters=TPU_H1M, network=network, parameters=presets[preset])
            break
        except Exception as e:  # O5 иногда не собирается — пробуем O1, как советует compile.py
            print(f'компиляция {preset} не удалась: {e}')
    else:
        sys.exit('компиляция не удалась')

    program = src.parent / f'{name}_b1_{preset}_h1m.tpu'
    TpuProgram.from_executable(executable, tensor_descriptions).to_file(str(program))
    meta['program'] = program.name
    meta_path.write_text(json.dumps(meta, indent=2))
    print('4/4 программа:', program, f'({os.path.getsize(program) / 1e6:.1f} МБ)')
    print(json.dumps(meta, indent=2))


if __name__ == '__main__':
    main()
