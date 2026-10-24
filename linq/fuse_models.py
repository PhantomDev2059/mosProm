"""Несколько моделей с одним входом → одна ONNX-модель → одна программа TPU.

Кадр уходит на ускоритель один раз вместо N (вход 640×640 float32 — ~5 МБ на каждую модель), одна задача в очереди
вместо N. Имена тензоров каждой модели получают префикс «<имя>__»; вход общий.

    python linq/fuse_models.py --models pose forklift --onnx-dir .../onnx --out .../pose+forklift.onnx
"""
import argparse
import re
from pathlib import Path

import onnx
from onnx import compose, helper

HEAD_CONV = re.compile(r"^/model\.\d+/cv[234]\.\d+/cv[234]\.\d+\.2/Conv$")


def head_cuts(model: onnx.ModelProto) -> list[str]:
    return [n.output[0] for n in model.graph.node if n.op_type == "Conv" and HEAD_CONV.match(n.name)]


def fuse(names: list[str], onnx_dir: Path) -> tuple[onnx.ModelProto, dict[str, list[str]]]:
    nodes, inits, vinfo, outs, cuts = [], [], [], [], {}
    first = onnx.load(onnx_dir / f"{names[0]}.onnx")
    inp = first.graph.input[0]
    for name in names:
        m = onnx.load(onnx_dir / f"{name}.onnx")
        cut = head_cuts(m)
        # головы режем сразу: на TPU уходит только свёрточная часть, хвосты — свои у каждой модели на CPU
        m = onnx.utils.Extractor(m).extract_model([m.graph.input[0].name], cut)
        p = compose.add_prefix(m, prefix=f"{name}__")
        src_in = p.graph.input[0].name
        for n in p.graph.node:
            n.input[:] = [inp.name if x == src_in else x for x in n.input]
        nodes += p.graph.node
        inits += p.graph.initializer
        vinfo += p.graph.value_info
        outs += p.graph.output
        cuts[name] = [f"{name}__{c}" for c in cut]
    graph = helper.make_graph(nodes, "fused_" + "_".join(names), [inp], outs, inits, value_info=vinfo)
    model = helper.make_model(graph, opset_imports=first.opset_import, producer_name="linq-guard-fuse")
    model.ir_version = first.ir_version
    onnx.checker.check_model(model)
    return model, cuts


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--models", nargs="+", required=True)
    ap.add_argument("--onnx-dir", required=True)
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    model, cuts = fuse(args.models, Path(args.onnx_dir))
    onnx.save(model, args.out)
    print("выходы:", sum(len(c) for c in cuts.values()), "узлов:", len(model.graph.node), "→", args.out)


if __name__ == "__main__":
    main()
