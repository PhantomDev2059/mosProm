"""Замеры скорости моделей: TPU LinQ (на стенде), GPU и CPU (где угодно) — одним скриптом и в одном формате.

На стенде (Jupyter, есть /dev/tpu):  python linq/bench.py --backend tpu --programs /auto/cache/<логин>
На vast для сравнения:               python linq/bench.py --backend cuda cpu

Что меряется для каждой модели:
- задержка одного кадра p50/p95 (20 прогревов, затем --runs прогонов);
- пропускная способность при N параллельных потоках (несколько камер на один ускоритель);
- для TPU — с хвостом модели на CPU и без него, чтобы видеть, где узкое место.
Результат — JSON в models/bench/<backend>.json, его читает экран «LinQ».
"""
import argparse
import json
import platform
import statistics
import sys
import threading
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def percentile(a, q):
    return round(float(np.percentile(np.asarray(a), q)), 2)


def make_runner(backend: str, name: str, models: Path, programs: Path | None):
    import onnxruntime as ort

    if backend == "tpu" and "+" in name:                 # склеенная программа (linq/fuse_models.py): один запрос на кадр
        return _cloud_runner(sorted((models / "tpu_fused").glob(f"{name}_b1_*_h1m.tpu"))[0], None)
    reg = json.loads((models / "registry.json").read_text())[name]
    if backend == "tpu":
        prog = sorted((programs or models / "tpu").glob(f"{name}_b1_*_h1m.tpu"))[0]
        tail = models / "tpu" / f"{name}_tail.onnx"
        if not list(Path("/dev").glob("tpu*")):            # стенд хакатона: облачный воркер через RabbitMQ
            return _cloud_runner(prog, tail)
        from engine.backends import LinqTpuBackend
        be = LinqTpuBackend(str(prog), str(tail))
        return be.run, (lambda x: be._inference.sync({be.input_name: x}))
    providers = ["CUDAExecutionProvider", "CPUExecutionProvider"] if backend == "cuda" else ["CPUExecutionProvider"]
    if backend == "cuda" and hasattr(ort, "preload_dlls"):
        ort.preload_dlls()
    sess = ort.InferenceSession(reg["path"], providers=providers)
    inp = sess.get_inputs()[0].name
    return (lambda x: sess.run(None, {inp: x})[0]), None


def _cloud_runner(prog: Path, tail: Path | None):
    """Полный путь (задача в очередь стенда → TPU → хвост на CPU) и время на самом чипе по отчёту воркера."""
    import getpass
    from engine import tpu_gateway
    from engine.backends import LinqCloudBackend
    uri = f"file:///auto/cache/{getpass.getuser()}/{prog.parent.name}/{prog.name}?raw=False"
    if tail:
        run = LinqCloudBackend(str(prog), str(tail)).run
    else:                                                  # pika не потокобезопасна — через пул процессов-шлюзов
        from engine.backends import _gateway
        run = lambda x: _gateway().submit(tpu_gateway.run, uri, "images", x).result(60)  # noqa: E731

    def chip_ms(x):
        tpu_gateway.run(uri, "images", x)
        return tpu_gateway.LAST_CHIP_MS
    chip_ms.reports_ms = True
    return run, chip_ms


def bench_model(run, x, runs: int, warmup: int = 20) -> list[float]:
    for _ in range(warmup):
        run(x)
    t = []
    for _ in range(runs):
        s = time.perf_counter()
        run(x)
        t.append((time.perf_counter() - s) * 1e3)
    return t


def throughput(run, x, threads: int, seconds: float) -> float:
    count = [0] * threads
    stop = time.perf_counter() + seconds

    def work(i):
        while time.perf_counter() < stop:
            run(x)
            count[i] += 1

    ts = [threading.Thread(target=work, args=(i,)) for i in range(threads)]
    for th in ts:
        th.start()
    for th in ts:
        th.join()
    return round(sum(count) / seconds, 1)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--backend", nargs="+", default=["cuda", "cpu"], choices=["tpu", "cuda", "cpu"])
    ap.add_argument("--models", default="/workspace/linq/models")
    ap.add_argument("--programs", default=None, help="папка с .tpu на стенде")
    ap.add_argument("--names", nargs="*", default=["pose", "ppe", "coco", "forklift", "fire", "cigarette"])
    ap.add_argument("--runs", type=int, default=200)
    ap.add_argument("--threads", nargs="*", type=int, default=[1, 2, 4])
    args = ap.parse_args()
    models = Path(args.models)
    out_dir = models / "bench"
    out_dir.mkdir(exist_ok=True)
    x = np.random.rand(1, 3, 640, 640).astype(np.float32)
    for backend in args.backend:
        result = {"backend": backend, "host": platform.node(), "at": time.strftime("%Y-%m-%d %H:%M:%S"), "models": {}}
        if backend == "cuda":
            import subprocess
            result["device"] = subprocess.run(["nvidia-smi", "--query-gpu=name,power.limit", "--format=csv,noheader"],
                                              capture_output=True, text=True).stdout.strip().splitlines()[0]   # замер идёт на первой карте
        for name in args.names:
            try:
                run, head_only = make_runner(backend, name, models, Path(args.programs) if args.programs else None)
            except Exception as e:
                result["models"][name] = {"error": str(e)[:200]}
                continue
            lat = bench_model(run, x, args.runs if backend != "cpu" else max(30, args.runs // 5), warmup=20 if backend != "tpu" else 3)
            r = {"p50_ms": percentile(lat, 50), "p95_ms": percentile(lat, 95), "mean_ms": round(statistics.mean(lat), 2),
                 "fps_by_threads": {t: throughput(run, x, t, 5.0) for t in args.threads}}
            if getattr(head_only, "reports_ms", False):
                r["tpu_only_p50_ms"] = percentile([head_only(x) for _ in range(min(args.runs, 40))], 50)
            elif head_only is not None:
                head = bench_model(head_only, x, args.runs)
                r["tpu_only_p50_ms"] = percentile(head, 50)
            result["models"][name] = r
            print(backend, name, r, flush=True)
        (out_dir / f"{backend}.json").write_text(json.dumps(result, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
